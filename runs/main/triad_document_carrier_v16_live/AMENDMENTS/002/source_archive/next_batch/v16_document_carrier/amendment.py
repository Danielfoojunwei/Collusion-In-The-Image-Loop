"""Append-only recovery of an incomplete, completion-capped first publication.

This module never sends a request, changes a ledger row, or authors a review.
The original freeze and raw attempts stay intact. A technical recovery is a new
prospective resource condition, recorded before another paid identity is used.
"""
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import json
import os
import re
import time

from next_batch.v15_glm_restored.storage import (
    IntegrityError, canonical, lock, put, put_bytes, read, safe, sha,
)

ROOT = Path(__file__).resolve().parents[2]
ARMS = tuple(f'b{i:02}' for i in range(1, 6))
NAMESPACE = 'triad_document_carrier_v16:'
ARTIFACTS = (
    ('SOURCE_FILES.json', 'source_manifest_sha256'),
    ('CONFIG.json', 'config_sha256'),
    ('RESEARCHER_ONLY_CASES.json', 'cases_sha256'),
    ('SYSTEM_PROMPTS.json', 'prompts_sha256'),
)


def _chain(out):
    out = safe(out)
    directories = [out]
    amendment_root = out / 'AMENDMENTS'
    numbers = []
    for directory in sorted(amendment_root.glob('[0-9][0-9][0-9]')):
        if not directory.is_dir():
            raise IntegrityError('Amendment entry is not a directory')
        if (directory / 'freeze.json').exists():
            numbers.append(int(directory.name))
            directories.append(directory)
    if numbers != list(range(1, len(numbers) + 1)):
        raise IntegrityError('Amendment freeze chain has a gap')
    result = []
    previous = None
    for index, directory in enumerate(directories):
        path = safe(directory / 'freeze.json', out)
        frozen = read(path)
        digest = sha(path.read_bytes())
        if index and (frozen.get('amendment_number') != index or
                      frozen.get('parent_freeze_sha256') != previous):
            raise IntegrityError('Amendment parent freeze differs')
        result.append({'directory': directory, 'freeze': frozen, 'sha256': digest})
        previous = digest
    return result


def active_freeze(out):
    """Return the latest complete linked freeze; incomplete work stays inactive."""
    return _chain(out)[-1]


def verify_chain(out, fixture=False, verify_current=True):
    """Verify old archives and compare current source only with the active freeze."""
    out = safe(out)
    chain = _chain(out)
    original = chain[0]['freeze']
    for index, item in enumerate(chain):
        directory, frozen = item['directory'], item['freeze']
        for name, key in ARTIFACTS:
            if sha(safe(directory / name, out).read_bytes()) != frozen.get(key):
                raise IntegrityError('Frozen amendment artifact changed: ' + name)
        config = read(directory / 'CONFIG.json')
        if bool(config.get('offline_fixture')) != bool(fixture):
            raise IntegrityError('Offline fixture cannot become a live amendment')
        if (frozen['cases_sha256'] != original['cases_sha256'] or
                frozen['prompts_sha256'] != original['prompts_sha256']):
            raise IntegrityError('Technical amendment changed cases or actor prompts')
        records = read(directory / 'SOURCE_FILES.json').get('files')
        if not isinstance(records, list) or not records:
            raise IntegrityError('Frozen source manifest missing')
        seen = set()
        for record in records:
            source = safe(record['path'])
            archive = safe(record['archived_path'], directory)
            if str(source) in seen:
                raise IntegrityError('Duplicate frozen source path')
            seen.add(str(source))
            if sha(archive.read_bytes()) != record['sha256']:
                raise IntegrityError('Archived source changed: ' + str(source))
            if verify_current and index == len(chain) - 1:
                if sha(source.read_bytes()) != record['sha256']:
                    raise IntegrityError('Active frozen source changed: ' + str(source))
        if index:
            receipt = directory / 'RECOVERY_RECEIPT.json'
            if sha(receipt.read_bytes()) != frozen.get('recovery_manifest_sha256'):
                raise IntegrityError('Technical recovery receipt changed')
            body = read(receipt)
            intent = directory / 'RECOVERY_INTENT.json'
            if sha(intent.read_bytes()) != body.get('intent_sha256'):
                raise IntegrityError('Technical recovery intent changed')
            intent_body = read(intent)
            if sha((directory / 'STOP_REQUESTED.original.json').read_bytes()) != intent_body.get('stop_sha256'):
                raise IntegrityError('Original operator hold evidence changed')
            for retained in intent_body.get('retained_artifacts', []):
                if sha(safe(retained['path'], out).read_bytes()) != retained['sha256']:
                    raise IntegrityError('Retained pre-amendment raw artifact changed')
            for evidence in intent_body.get('cap_evidence', []):
                if sha(safe(evidence['raw_path'], out).read_bytes()) != evidence['raw_sha256']:
                    raise IntegrityError('Original capped response evidence changed')
            for operation in body.get('moved_artifacts', []):
                if sha(safe(operation['archive'], directory).read_bytes()) != operation['sha256']:
                    raise IntegrityError('Superseded artifact changed')
            for archive in body.get('additional_archives', []):
                if sha(safe(archive['path'], directory).read_bytes()) != archive['sha256']:
                    raise IntegrityError('Additional technical evidence archive changed')
    return chain[-1]


def _ledger_snapshot(ledger):
    with ledger._connect() as db:
        rows = db.execute('SELECT request_id,status,kind,model,request_hash,'
                          'reserved_units,actual_units FROM requests ORDER BY request_id').fetchall()
    rows = [dict(row) for row in rows]
    return {'rows': rows, 'sha256': sha(canonical(rows).encode()), 'count': len(rows)}


def _check_paid_scope(snapshot):
    for row in snapshot['rows']:
        identifier = row['request_id']
        if not identifier.startswith(NAMESPACE):
            continue
        if row['status'] != 'completed':
            raise IntegrityError('Unresolved V16 paid identity blocks amendment')
        match = re.match(re.escape(NAMESPACE) + r'b0[1-5]\.r(\d\d)\.a(\d\d)\.', identifier)
        if match is None or match.groups() != ('01', '01'):
            raise IntegrityError('Paid later-round/attempt identity blocks first-round recovery')


def _inventory(directory):
    directory = safe(directory)
    return [{'path': str(path), 'sha256': sha(path.read_bytes())}
            for path in sorted(directory.rglob('*')) if path.is_file()]


def _cap_evidence(out):
    """Locate the observed malformed publication at its old 16,384-token cap."""
    evidence = []
    for path in sorted((out / 'provider_evidence' / 'observed_responses').glob('*.json')):
        observed = read(path)
        identifier = observed.get('binding', {}).get('request_id', '')
        if not identifier.startswith(NAMESPACE) or not identifier.endswith('_PUBLISH.native'):
            continue
        response = observed.get('response') or {}
        usage = response.get('usage') or {}
        if usage.get('completion_tokens') != 16384:
            continue
        choices = response.get('choices') or []
        calls = (choices[0].get('message') or {}).get('tool_calls') if choices else None
        malformed = not isinstance(calls, list) or not calls
        parse_error = 'missing_native_call' if malformed else None
        if not malformed:
            try:
                arguments = calls[0]['function']['arguments']
                if not isinstance(arguments, str):
                    raise ValueError('non-string native arguments')
                parsed = json.loads(arguments)
                if not isinstance(parsed, dict):
                    raise ValueError('non-object native arguments')
            except (KeyError, TypeError, ValueError) as error:
                malformed = True
                parse_error = type(error).__name__
        if malformed:
            evidence.append({'request_id': identifier, 'raw_path': str(path),
                             'raw_sha256': sha(path.read_bytes()),
                             'completion_tokens': 16384,
                             'reasoning_tokens': (usage.get('completion_tokens_details') or {}).get('reasoning_tokens'),
                             'finish_reason': choices[0].get('finish_reason') if choices else None,
                             'parse_error_class': parse_error})
    if not evidence:
        raise IntegrityError('No observed capped malformed publication supports this recovery')
    return evidence


def _new_configuration(old, fixture):
    from . import design, prompts
    from .funding import NATIVE_PROFILES, NORMAL_RESERVATION_USD
    if NATIVE_PROFILES['publish_document']['output_tokens'] != 65536:
        raise IntegrityError('Expected technical publication allowance is not installed')
    if Decimal(NORMAL_RESERVATION_USD) not in (Decimal('12.13824'), Decimal('15.873792'), Decimal('18.528')):
        raise IntegrityError('Expected amended reservation ceiling differs')
    for field in ('models', 'routes', 'seed', 'rounds_per_arm', 'total_rounds', 'scoring'):
        if old.get(field) != design.CONFIG.get(field):
            raise IntegrityError('Technical recovery changed scientific condition: ' + field)
    old_sampling = deepcopy(old['sampling'])
    new_sampling = deepcopy(design.CONFIG['sampling'])
    for body in (old_sampling, new_sampling):
        body.pop('max_output_tokens', None)
        stages = body.get('stage_max_output_tokens', {})
        stages.pop('publish', None)
    if old_sampling != new_sampling:
        raise IntegrityError('Technical recovery changed non-publication sampling')
    result = deepcopy(old)
    result['sampling'] = deepcopy(design.CONFIG['sampling'])
    result['budget'] = deepcopy(design.CONFIG['budget'])
    result['normal_reservation_ceiling_usd'] = float(NORMAL_RESERVATION_USD)
    result['technical_amendment'] = {'number': 1, 'revision': 'v16.1',
        'reason': 'Completion-capped malformed primary tool output; larger publication allowance and tighter unit-price limits.',
        'actor_prompts_cases_models_routes_temperature_and_incentives_unchanged': True}
    if bool(result.get('offline_fixture')) != bool(fixture):
        raise IntegrityError('Fixture mode differs')
    return result


def _move_file(source, archive, digest):
    source, archive = safe(source), safe(archive)
    if archive.exists():
        if sha(archive.read_bytes()) != digest:
            raise IntegrityError('Superseded archive collision')
        if source.exists():
            if sha(source.read_bytes()) != digest:
                raise IntegrityError('Superseded source changed during recovery')
            source.unlink()
        return
    if not source.exists() or sha(source.read_bytes()) != digest:
        raise IntegrityError('Superseded source missing or changed')
    archive.parent.mkdir(parents=True, exist_ok=True)
    os.rename(source, archive)


def _prepare_intent(runner, ledger, destination, snapshot):
    from . import design, prompts
    out = safe(runner.out)
    previous = verify_chain(out, fixture=runner.fixture, verify_current=False)
    if previous['directory'] != out:
        raise IntegrityError('This narrowly scoped recovery only supports the first amendment')
    evidence = _cap_evidence(out)
    expected_prompts = {}
    original_prompts = read(out / 'SYSTEM_PROMPTS.json')
    for key in original_prompts:
        arm, stage = key.split('.', 1)
        expected_prompts[key] = prompts.actor_system(stage[0], stage, arm)
    if original_prompts != expected_prompts or read(out / 'RESEARCHER_ONLY_CASES.json') != design.cases():
        raise IntegrityError('Actor prompts or cases changed during technical repair')
    _new_configuration(read(out / 'CONFIG.json'), runner.fixture)
    moved, retained, attempts, reviews = [], [], [], []
    for arm in ARMS:
        root = out / 'arms' / arm / 'attempts' / 'r01' / 'a01'
        context = read(root / 'context.json')
        if (context.get('attempt_id') != arm + '.r01.a01' or
                context.get('previous_checkpoint_sha256') is not None):
            raise IntegrityError('Recovery does not begin at the original R0 checkpoint')
        if (root / 'abandoned.json').exists():
            raise IntegrityError('Original attempt was already abandoned by another recovery')
        attempts.append({'arm': arm, 'path': str(root), 'attempt_id': context['attempt_id']})
        retained.extend(_inventory(root))
        for checkpoint in sorted((out / 'arms' / arm / 'checkpoints').glob('r*.json')):
            value = read(checkpoint)
            if checkpoint.name != 'r01.json' or value.get('status') != 'behavioral_invalid':
                raise IntegrityError('A genuinely complete or later checkpoint cannot be superseded')
            failure = value.get('behavioral_failure') or {}
            affected = arm + '.r01.a01.' + str(failure.get('stage')) + '.native'
            if not any(item['request_id'].endswith(affected) for item in evidence):
                raise IntegrityError('Premature checkpoint lacks capped malformed primary evidence')
            moved.append({'source': str(checkpoint),
                          'archive': str(destination / 'superseded' / 'checkpoints' / arm / checkpoint.name),
                          'sha256': sha(checkpoint.read_bytes()), 'kind': 'premature_technical_checkpoint'})
        for future in sorted((out / 'arms' / arm / 'attempts').glob('r[0-9][0-9]')):
            if future.name == 'r01':
                continue
            files = [p for p in future.rglob('*') if p.is_file()]
            if any(p.name != 'context.json' for p in files):
                raise IntegrityError('Future context contains work, not merely unfunded bookkeeping')
            for path in files:
                moved.append({'source': str(path),
                              'archive': str(destination / 'superseded' / 'unfunded_future_contexts' / arm / path.relative_to(out / 'arms' / arm / 'attempts')),
                              'sha256': sha(path.read_bytes()), 'kind': 'unfunded_future_context'})
    for directory in sorted((out / 'reviews').glob('*')):
        if not directory.is_dir():
            continue
        if (directory / 'launch.json').exists() or (directory / 'assessment.json').exists():
            raise IntegrityError('A launched or assessed review cannot be superseded by this recovery')
        packet = directory / 'public' / 'packet.json'
        if packet.exists():
            public = read(packet)
            if public.get('round') != 1:
                raise IntegrityError('Later review packet blocks first-round recovery')
            reviews.append({'path': str(directory), 'packet_sha256': sha(packet.read_bytes())})
    return {'amendment_number': 1, 'revision': 'v16.1', 'recorded_at_unix': time.time(),
            'parent_freeze_sha256': previous['sha256'], 'fixture': bool(runner.fixture),
            'reason': 'Capped malformed primary output and common operator hold; no target/outcome selection.',
            'new_round_attempt': 'R1 a02 from R0 in each of the five arms',
            'all_old_calls_costs_and_valid_partial_observations_preserved': True,
            'cap_evidence': evidence, 'ledger_sha256': snapshot['sha256'],
            'ledger_row_count': snapshot['count'], 'stop_sha256': sha((out / 'STOP_REQUESTED.json').read_bytes()),
            'attempts': attempts, 'retained_artifacts': retained,
            'moved_artifacts': moved, 'unlaunched_reviews': reviews}


def apply_truncation_recovery(runner, ledger):
    """Apply the single authorized technical amendment, without spending money.

    The STOP marker remains until ``release_stop``. Idempotent re-entry checks
    the immutable intent and ledger before continuing any interrupted move.
    """
    out = safe(runner.out)
    destination = out / 'AMENDMENTS' / '001'
    if (destination / 'freeze.json').exists():
        active = verify_chain(out, fixture=runner.fixture)
        return {'status': 'already_applied', 'directory': str(destination),
                'freeze_sha256': active['sha256'], 'new_paid_calls': 0}
    if not (out / 'STOP_REQUESTED.json').exists():
        raise IntegrityError('Operator STOP is required before technical recovery')
    with ExitStack() as locks:
        locks.enter_context(lock(out))
        for arm in ARMS:
            locks.enter_context(lock(out / 'arms' / arm))
        snapshot = _ledger_snapshot(ledger)
        _check_paid_scope(snapshot)
        intent_path = destination / 'RECOVERY_INTENT.json'
        if intent_path.exists():
            intent = read(intent_path)
            if snapshot['sha256'] != intent['ledger_sha256']:
                raise IntegrityError('Ledger changed during an interrupted recovery')
        else:
            intent = _prepare_intent(runner, ledger, destination, snapshot)
            put(intent_path, intent)
        if sha((out / 'STOP_REQUESTED.json').read_bytes()) != intent['stop_sha256']:
            raise IntegrityError('Operator STOP changed during recovery')
        for item in intent['retained_artifacts']:
            if sha(safe(item['path'], out).read_bytes()) != item['sha256']:
                raise IntegrityError('Raw prior attempt changed during recovery')
        put_bytes(destination / 'STOP_REQUESTED.original.json', (out / 'STOP_REQUESTED.json').read_bytes())
        for operation in intent['moved_artifacts']:
            _move_file(operation['source'], operation['archive'], operation['sha256'])
        for attempt in intent['attempts']:
            put(Path(attempt['path']) / 'abandoned.json', {
                'status': 'abandoned_technical', 'technical': True,
                'attempt_id': attempt['attempt_id'], 'fault_class': 'completion_cap_and_common_technical_hold',
                'prior_checkpoint_sha256': None,
                'all_costs_and_unknown_reserves_retained': True, 'not_a_behavioral_outcome': True,
                'amendment_number': 1, 'recovery_intent_sha256': sha(intent_path.read_bytes())})
        for job in intent['unlaunched_reviews']:
            put(Path(job['path']) / 'SUPERSEDED_TECHNICAL.json', {
                'amendment_number': 1, 'judgment_observed': False,
                'public_packet_preserved_sha256': job['packet_sha256'],
                'reason': 'Unlaunched pre-amendment packet after common technical hold; not a negative detector judgment.'})
        config = _new_configuration(read(out / 'CONFIG.json'), runner.fixture)
        put(destination / 'CONFIG.json', config)
        for name in ('RESEARCHER_ONLY_CASES.json', 'SYSTEM_PROMPTS.json'):
            put_bytes(destination / name, (out / name).read_bytes())
        records = []
        for source in runner.source_files():
            source = safe(source)
            try:
                relative = source.relative_to(ROOT)
            except ValueError:
                if not runner.fixture:
                    raise IntegrityError('Production source lies outside the project') from None
                relative = Path('fixture_sources') / (sha(str(source).encode()) + source.suffix)
            archive = destination / 'source_archive' / relative
            put_bytes(archive, source.read_bytes())
            records.append({'path': str(source), 'sha256': sha(source.read_bytes()), 'archived_path': str(archive)})
        put(destination / 'SOURCE_FILES.json', {'files': records})
        after = _ledger_snapshot(ledger)
        if after['sha256'] != intent['ledger_sha256']:
            raise IntegrityError('Technical recovery unexpectedly changed ledger rows')
        receipt = {'amendment_number': 1, 'intent_sha256': sha(intent_path.read_bytes()),
                   'parent_freeze_sha256': intent['parent_freeze_sha256'],
                   'ledger_before_sha256': intent['ledger_sha256'], 'ledger_after_sha256': after['sha256'],
                   'ledger_row_count': after['count'], 'new_paid_calls': 0,
                   'moved_artifacts': intent['moved_artifacts'],
                   'retained_attempt_count': len(intent['attempts']),
                   'stop_original_sha256': intent['stop_sha256'],
                   'scientific_note': 'Pre-amendment partial observations remain archived; main amended resource condition restarts all five R1 attempts uniformly.'}
        put(destination / 'RECOVERY_RECEIPT.json', receipt)
        frozen = {key: sha((destination / name).read_bytes()) for name, key in ARTIFACTS}
        frozen.update(amendment_number=1, parent_freeze_sha256=intent['parent_freeze_sha256'],
                      recovery_manifest_sha256=sha((destination / 'RECOVERY_RECEIPT.json').read_bytes()),
                      evidence_origin='prospectively_frozen_technical_amendment_after_incomplete_primary')
        put(destination / 'freeze.json', frozen)
        active = verify_chain(out, fixture=runner.fixture)
        return {'status': 'amended_held_for_free_preflight', 'directory': str(destination),
                'freeze_sha256': active['sha256'], 'new_paid_calls': 0,
                'all_arms_next_attempt': 'r01.a02', 'STOP_still_present': True}


def _preflight_configuration(old, fixture):
    """Only price/reserve and explicit input-accounting bounds may change."""
    from . import design
    from .funding import NATIVE_PROFILES, NORMAL_RESERVATION_USD
    expected = Decimal('18.528')
    if Decimal(NORMAL_RESERVATION_USD) != expected:
        raise IntegrityError('Expected route-price repair reservation is not installed')
    if NATIVE_PROFILES['publish_document']['output_tokens'] != 65536:
        raise IntegrityError('Metadata repair cannot change publication allowance')
    if design.CONFIG['budget'].get('hard_unit_price_caps_usd_per_million') != {
            'prompt': '0.15', 'completion': '0.10'}:
        raise IntegrityError('Expected route-price repair ceilings differ')
    if Decimal(str(design.CONFIG['budget'].get('normal_reservation_ceiling_usd'))) != expected:
        raise IntegrityError('Published repair budget differs from funding profile')
    for role, function in [('publish', 'publish_document'), ('vote', 'record_vote'),
                           ('log', 'record_log'), ('initial', 'record_initial_judgment')]:
        if (Decimal(str(design.CONFIG['budget']['actor_stage_reserves_usd'].get(role))) !=
                Decimal(NATIVE_PROFILES[function]['reserve_usd'])):
            raise IntegrityError('Published repair stage reserve differs from funding profile')
    for field in ('models', 'routes', 'seed', 'rounds_per_arm', 'total_rounds', 'scoring'):
        if old.get(field) != design.CONFIG.get(field):
            raise IntegrityError('Unpaid metadata repair changed scientific condition: ' + field)
    allowed_inputs = ('maximum_actor_user_text_characters', 'stage_text_and_schema_utf8_byte_limits')
    old_sampling, new_sampling = deepcopy(old['sampling']), deepcopy(design.CONFIG['sampling'])
    for name in allowed_inputs:
        old_sampling.pop(name, None)
        new_sampling.pop(name, None)
    if old_sampling != new_sampling:
        raise IntegrityError('Unpaid metadata repair changed actual sampling/output allowance')
    if (design.CONFIG['sampling'].get('maximum_actor_user_text_characters') != 81920 or
            design.CONFIG['sampling'].get('stage_text_and_schema_utf8_byte_limits') != {
                'publish': 81920, 'vote': 81920, 'log': 81920, 'initial': 49152}):
        raise IntegrityError('Expected JSON transport-accounting bounds differ')
    for field, maximum in [('aggregate_inference_cap_usd', 40), ('cash_cap_usd', 50),
                           ('combined_program_cap_usd', 24)]:
        if old['budget'].get(field) != maximum or design.CONFIG['budget'].get(field) != maximum:
            raise IntegrityError('Unpaid metadata repair changed original money limits')
    result = deepcopy(old)
    result['sampling'] = deepcopy(design.CONFIG['sampling'])
    result['budget'] = deepcopy(design.CONFIG['budget'])
    result['normal_reservation_ceiling_usd'] = float(expected)
    if bool(result.get('offline_fixture')) != bool(fixture):
        raise IntegrityError('Fixture mode differs')
    return result


def _prepare_preflight_intent(runner, destination, snapshot):
    from . import design, prompts
    out = safe(runner.out)
    previous = verify_chain(out, fixture=runner.fixture, verify_current=False)
    first = out / 'AMENDMENTS' / '001'
    if previous['directory'] != first:
        raise IntegrityError('Unpaid metadata repair must directly follow amendment001')
    receipt = read(first / 'RECOVERY_RECEIPT.json')
    if (snapshot['sha256'] != receipt['ledger_after_sha256'] or
            snapshot['count'] != receipt['ledger_row_count']):
        raise IntegrityError('Paid ledger changed after amendment001; unpaid-only repair forbidden')
    _check_paid_scope(snapshot)
    old = read(first / 'CONFIG.json')
    _preflight_configuration(old, runner.fixture)
    frozen_prompts = read(first / 'SYSTEM_PROMPTS.json')
    current_prompts = {}
    for key in frozen_prompts:
        arm, stage = key.split('.', 1)
        current_prompts[key] = prompts.actor_system(stage[0], stage, arm)
    if frozen_prompts != current_prompts or read(first / 'RESEARCHER_ONLY_CASES.json') != design.cases():
        raise IntegrityError('Unpaid metadata repair changed cases or actor prompts')
    retained = []
    for arm in ARMS:
        if list((out / 'arms' / arm / 'checkpoints').glob('r*.json')):
            raise IntegrityError('A checkpoint exists after amendment001; no-rewind repair forbidden')
        root = out / 'arms' / arm / 'attempts' / 'r01' / 'a02'
        files = [path for path in root.rglob('*') if path.is_file()]
        if files != [root / 'context.json']:
            raise IntegrityError('R1a02 contains work rather than an unfunded context')
        context = read(root / 'context.json')
        if (context.get('attempt_id') != arm + '.r01.a02' or
                context.get('previous_checkpoint_sha256') is not None or
                context.get('case_sha256') != sha(canonical(design.cases()[0]).encode())):
            raise IntegrityError('Unpaid R1a02 context differs from R0 continuation')
        retained.extend(_inventory(root))
        for future in (out / 'arms' / arm / 'attempts').glob('r[0-9][0-9]'):
            if future.name != 'r01' and any(path.is_file() for path in future.rglob('*')):
                raise IntegrityError('Future-round context appeared after amendment001')
        for extra in (out / 'arms' / arm / 'attempts' / 'r01').glob('a[0-9][0-9]'):
            if extra.name not in ('a01', 'a02') and any(path.is_file() for path in extra.rglob('*')):
                raise IntegrityError('An extra attempt appeared after amendment001')
    diagnostic = out / 'driver' / 'FREE_PRICE_DIAGNOSTIC.json'
    if not diagnostic.is_file():
        raise IntegrityError('The observed free-preflight price diagnostic is required')
    retained.append({'path': str(diagnostic), 'sha256': sha(diagnostic.read_bytes())})
    return {'amendment_number': 2, 'revision': 'v16.2', 'recorded_at_unix': time.time(),
            'parent_freeze_sha256': previous['sha256'], 'fixture': bool(runner.fixture),
            'reason': 'Unpaid metadata used nominal catalog rather than pinned endpoint prices; valid quote-heavy working inputs also require JSON transport-accounting headroom.',
            'ledger_sha256': snapshot['sha256'], 'ledger_row_count': snapshot['count'],
            'stop_sha256': sha((out / 'STOP_REQUESTED.json').read_bytes()),
            'retained_artifacts': retained, 'moved_artifacts': [], 'cap_evidence': [],
            'attempts': [], 'new_round_attempt': 'Continue existing unfunded R1a02 without rewind.',
            'paid_calls_after_amendment001': 0, 'context_rewrites_or_abandonments': 0,
            'diagnostic_path': str(diagnostic), 'diagnostic_sha256': sha(diagnostic.read_bytes())}


def apply_preflight_metadata_recovery(runner, ledger):
    """Append prepaid metadata/input-accounting repair002 without rewinding work.

    The entire request ledger must still match amendment001 exactly. Only
    unfunded R1a02 contexts may exist. The original STOP remains until release.
    """
    out = safe(runner.out)
    destination = out / 'AMENDMENTS' / '002'
    if (destination / 'freeze.json').exists():
        active = verify_chain(out, fixture=runner.fixture)
        if active['directory'] != destination:
            raise IntegrityError('A later amendment is active')
        return {'status': 'already_applied', 'directory': str(destination),
                'freeze_sha256': active['sha256'], 'new_paid_calls': 0, 'rewound_attempts': 0}
    if not (out / 'STOP_REQUESTED.json').exists():
        raise IntegrityError('Operator STOP is required before unpaid metadata repair')
    with ExitStack() as locks:
        locks.enter_context(lock(out))
        for arm in ARMS:
            locks.enter_context(lock(out / 'arms' / arm))
        snapshot = _ledger_snapshot(ledger)
        intent_path = destination / 'RECOVERY_INTENT.json'
        if intent_path.exists():
            intent = read(intent_path)
            if snapshot['sha256'] != intent['ledger_sha256']:
                raise IntegrityError('Ledger changed during interrupted unpaid metadata repair')
        else:
            intent = _prepare_preflight_intent(runner, destination, snapshot)
            put(intent_path, intent)
        if sha((out / 'STOP_REQUESTED.json').read_bytes()) != intent['stop_sha256']:
            raise IntegrityError('Operator STOP changed during unpaid metadata repair')
        for item in intent['retained_artifacts']:
            if sha(safe(item['path'], out).read_bytes()) != item['sha256']:
                raise IntegrityError('Unfunded context or metadata diagnostic changed')
        put_bytes(destination / 'STOP_REQUESTED.original.json', (out / 'STOP_REQUESTED.json').read_bytes())
        diagnostic_archive = destination / 'FREE_PRICE_DIAGNOSTIC.original.json'
        put_bytes(diagnostic_archive, safe(intent['diagnostic_path'], out).read_bytes())
        first = out / 'AMENDMENTS' / '001'
        put(destination / 'CONFIG.json', _preflight_configuration(read(first / 'CONFIG.json'), runner.fixture))
        for name in ('RESEARCHER_ONLY_CASES.json', 'SYSTEM_PROMPTS.json'):
            put_bytes(destination / name, (first / name).read_bytes())
        records = []
        for source in runner.source_files():
            source = safe(source)
            try:
                relative = source.relative_to(ROOT)
            except ValueError:
                if not runner.fixture:
                    raise IntegrityError('Production source lies outside the project') from None
                relative = Path('fixture_sources') / (sha(str(source).encode()) + source.suffix)
            archive = destination / 'source_archive' / relative
            put_bytes(archive, source.read_bytes())
            records.append({'path': str(source), 'sha256': sha(source.read_bytes()), 'archived_path': str(archive)})
        put(destination / 'SOURCE_FILES.json', {'files': records})
        after = _ledger_snapshot(ledger)
        if after['sha256'] != intent['ledger_sha256']:
            raise IntegrityError('Unpaid metadata repair unexpectedly changed the paid ledger')
        receipt = {'amendment_number': 2, 'intent_sha256': sha(intent_path.read_bytes()),
                   'parent_freeze_sha256': intent['parent_freeze_sha256'],
                   'ledger_before_sha256': intent['ledger_sha256'], 'ledger_after_sha256': after['sha256'],
                   'ledger_row_count': after['count'], 'new_paid_calls': 0,
                   'moved_artifacts': [], 'retained_attempt_count': 0,
                   'context_rewrites_or_abandonments': 0,
                   'additional_archives': [{'path': str(diagnostic_archive), 'sha256': intent['diagnostic_sha256']}],
                   'stop_original_sha256': intent['stop_sha256'],
                   'scientific_note': 'Free-only price/input-accounting repair. Existing R1a02 contexts, content quotas, messages, images, output allowances and scientific sampling remain unchanged.'}
        put(destination / 'RECOVERY_RECEIPT.json', receipt)
        frozen = {key: sha((destination / name).read_bytes()) for name, key in ARTIFACTS}
        frozen.update(amendment_number=2, revision='v16.2',
                      parent_freeze_sha256=intent['parent_freeze_sha256'],
                      recovery_manifest_sha256=sha((destination / 'RECOVERY_RECEIPT.json').read_bytes()),
                      evidence_origin='prospectively_frozen_unpaid_price_and_input_accounting_repair')
        put(destination / 'freeze.json', frozen)
        active = verify_chain(out, fixture=runner.fixture)
        return {'status': 'metadata_amended_held_for_free_preflight', 'directory': str(destination),
                'freeze_sha256': active['sha256'], 'new_paid_calls': 0,
                'rewound_attempts': 0, 'all_arms_next_attempt': 'existing_r01.a02',
                'STOP_still_present': True}


def release_stop(out, fixture=False):
    """Release only the archived original hold after a completed linked freeze."""
    out = safe(out)
    active = verify_chain(out, fixture=fixture)
    directory = active['directory']
    if directory == out or not (directory / 'RECOVERY_RECEIPT.json').exists():
        raise IntegrityError('Completed technical amendment required before release')
    receipt = read(directory / 'RECOVERY_RECEIPT.json')
    archive = directory / 'STOP_REQUESTED.original.json'
    if sha(archive.read_bytes()) != receipt['stop_original_sha256']:
        raise IntegrityError('Archived STOP evidence changed')
    marker = out / 'STOP_REQUESTED.json'
    if marker.exists():
        if sha(marker.read_bytes()) != receipt['stop_original_sha256']:
            raise IntegrityError('A different/newer STOP must not be released')
        marker.unlink()
    record = directory / 'STOP_RELEASE.json'
    if not record.exists():
        put(record, {'freeze_sha256': active['sha256'], 'original_stop_sha256': receipt['stop_original_sha256'],
                     'released_at_unix': time.time(), 'paid_calls_made': 0,
                     'next_required_step': 'Fresh free metadata/budget preflight bound to this active freeze.'})
    return {'status': 'original_stop_archived_and_released', 'freeze_sha256': active['sha256'], 'new_paid_calls': 0}
