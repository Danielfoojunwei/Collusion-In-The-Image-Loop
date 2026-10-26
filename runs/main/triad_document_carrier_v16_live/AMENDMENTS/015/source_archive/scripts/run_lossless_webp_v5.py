"""Explicit v5 transport amendment; the same study and original PNGs remain.

Only new chat wire encodings become lossless WebP. Logical request hashes,
original decoded pixels, messages, routes, caches and reservations stay intact.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from unittest.mock import patch

import continue_repaired_execution as v4
import lossless_wire_codec as codec
import repair_image_route as repair
from run_continue_independent import ContinuationLedger, LOSS_REASON, _eligible_loss
from visual_collusion import evidence, runtime
from visual_collusion.analysis import build_report, validate_evidence
from visual_collusion.archive import archive_messages, hydrate_messages
from visual_collusion.budget import BudgetError, PendingPaidRequest
from visual_collusion.cli import export_run
from visual_collusion.common import canonical, digest, read_json, utc_now, write_json
from visual_collusion.providers import CHAT_PROFILES, DEFAULT_MODELS, ProviderError, image_content


MAX_WIRE_BYTES = 10 * 1024 * 1024
KNOWN_LOSS = 'episode_194cef8360:branch_2:round4:A:step2'
KNOWN_HASH = '198129b35a5e2bf05f502e9d68f03f92a3d8f6badcd7c2524c399bb2b7b0b05c'
KNOWN_RESERVE = Decimal('0.0257172')
NOTE_NAME = 'lossless_webp_transport_v5.json'
INFRA_DIRECTORY = 'infrastructure_checks_v5'
INFRA_NAMESPACE = 'infra_checks5'
AUTHORIZATION = {
    'user_instruction': v4.AUTHORIZATION['user_instruction'],
    'scope': 'Fix the demonstrated managed-proxy 10 MiB request-body authorization boundary; continue the exact existing repaired study without replaying either lost request.',
    'encoding': 'For new chat POSTs only, replace inline PNG encoding by deterministic lossless WebP, preserving every decoded RGBA byte, dimension, frame count, image position and all other payload fields. Reject unknown rendering metadata. Original Qwen PNGs, logical inputs and request hashes remain unchanged.',
    'hash_interpretation': 'Original ledger request_hash binds the canonical LOGICAL PNG payload. Separate immutable mirrored wire bindings bind actual serialized wire JSON and ordered source/wire image identities. Canonical logical validation alone does not validate v5 wire inputs.',
    'size_guard': 'Compute and verify the entire actual wire JSON before any reservation; require strictly fewer than 10485760 UTF-8 bytes. An oversized actual context halts without dropping, resizing or altering history, prompts or pixels. Known examples fitting do not establish unconditional capacity.',
    'known_loss': KNOWN_LOSS,
    'known_loss_cause': 'Observed HTTP 401 for a 10943403-byte nine-image body, with free metadata diagnostics bracketing the 10 MiB proxy authorization threshold. Unknown charge stays reserved; this existing request is sealed missing, never re-encoded and retried.',
    'compatibility_checks': 'Two bounded one-attempt technical format checks, one GLM and one Sonnet, using existing six-digit pixel cards and separate infra_checks5 accounting under the original aggregate cap. No new Qwen generation, scientific pilot or response-dependent variant selection. A failed check is retained and blocks the main run without retry.',
    'preserved': v4.AUTHORIZATION['preserved'],
    'future_losses': v4.AUTHORIZATION['future_losses'],
    'audit_validation': 'Keep all v4 checks and admit an invalid final audit only when its exact v4/v5 reason and corresponding mirrored immutable no-response seals match; never fabricate a billed-invalid output.',
    'limitation': 'Exact decoded pixel identity is verified locally. Different accepted encodings do not independently establish identical undocumented provider preprocessing.',
}
POLICY_SHA256 = digest(AUTHORIZATION)


def missing_reason(request_id):
    return ('Transport response unavailable for ' + request_id +
            '; terminal missing turn, no retry; unknown charge remains reserved [transport_v5]')


class V5Ledger(v4.V4Ledger):
    def __init__(self, path, cap_usd=40):
        super().__init__(path, cap_usd)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS terminal_transport_losses_v5 (
                    request_id TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL,
                    policy_sha256 TEXT NOT NULL, recorded_at REAL NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS terminal_transport_losses_v5_no_update BEFORE UPDATE ON terminal_transport_losses_v5
                BEGIN SELECT RAISE(ABORT, 'V5 terminal losses are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS terminal_transport_losses_v5_no_delete BEFORE DELETE ON terminal_transport_losses_v5
                BEGIN SELECT RAISE(ABORT, 'V5 terminal losses are immutable'); END;
                CREATE TABLE IF NOT EXISTS wire_bindings_v5 (
                    request_id TEXT PRIMARY KEY, logical_request_hash TEXT NOT NULL,
                    wire_request_hash TEXT NOT NULL, record_sha256 TEXT NOT NULL,
                    record_json TEXT NOT NULL, policy_sha256 TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS wire_bindings_v5_no_update BEFORE UPDATE ON wire_bindings_v5
                BEGIN SELECT RAISE(ABORT, 'V5 wire bindings are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS wire_bindings_v5_no_delete BEFORE DELETE ON wire_bindings_v5
                BEGIN SELECT RAISE(ABORT, 'V5 wire bindings are immutable'); END;
            ''')

    @staticmethod
    def _sealed_v5(db, row):
        seal = db.execute('SELECT * FROM terminal_transport_losses_v5 WHERE request_id=?', (row['request_id'],)).fetchone()
        return (seal is not None and seal['policy_sha256'] == POLICY_SHA256 and
                seal['snapshot_json'] == canonical(dict(row)) and _eligible_loss(row))

    @staticmethod
    def _sealed(db, row):
        return v4.V4Ledger._sealed(db, row) or V5Ledger._sealed_v5(db, row)

    def is_v5_terminal_loss(self, request_id):
        with self._connect() as db:
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            return row is not None and self._sealed_v5(db, row)

    def seal_transport_loss(self, request_id):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            if not _eligible_loss(row):
                raise PendingPaidRequest('Only an exact no-response loss may receive a v5 seal')
            if self._sealed(db, row):
                return
            db.execute('INSERT INTO terminal_transport_losses_v5 VALUES (?,?,?,?)',
                       (request_id, canonical(dict(row)), POLICY_SHA256, time.time()))

    def bind_wire(self, request_id, record):
        mapping = record['mapping']
        values = (request_id, mapping['logical_request_hash'], mapping['wire_request_hash'],
                  digest(record), canonical(record), POLICY_SHA256)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM wire_bindings_v5 WHERE request_id=?', (request_id,)).fetchone()
            if old is not None and tuple(old) != values:
                raise BudgetError('Immutable wire binding changed')
            db.execute('INSERT OR IGNORE INTO wire_bindings_v5 VALUES (?,?,?,?,?,?)', values)

    def wire_binding(self, request_id):
        with self._connect() as db:
            row = db.execute('SELECT * FROM wire_bindings_v5 WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                return None
            value = dict(row)
            record = json.loads(value['record_json'])
            if (digest(record) != value['record_sha256'] or value['policy_sha256'] != POLICY_SHA256 or
                    record['mapping']['logical_request_hash'] != value['logical_request_hash'] or
                    record['mapping']['wire_request_hash'] != value['wire_request_hash']):
                raise BudgetError('Wire binding integrity check failed')
            return value


class SharedV5Ledger(v4.SharedContinuationLedger, V5Ledger):
    def seal_transport_loss(self, request_id):
        self.lookup(request_id)
        global_id = self._global_id(request_id)
        self.aggregate.seal_transport_loss(global_id)
        if self.aggregate.is_v5_terminal_loss(global_id):
            V5Ledger.seal_transport_loss(self, request_id)
        elif self.aggregate.is_v4_terminal_loss(global_id):
            v4.V4Ledger.seal_transport_loss(self, request_id)
        else:
            ContinuationLedger.seal_transport_loss(self, request_id)

    def is_v5_terminal_loss(self, request_id):
        self.lookup(request_id)
        return (V5Ledger.is_v5_terminal_loss(self, request_id) and
                self.aggregate.is_v5_terminal_loss(self._global_id(request_id)))

    def bind_wire(self, request_id, record):
        self.aggregate.bind_wire(self._global_id(request_id), record)
        V5Ledger.bind_wire(self, request_id, record)

    def wire_binding(self, request_id):
        local = V5Ledger.wire_binding(self, request_id)
        aggregate = self.aggregate.wire_binding(self._global_id(request_id))
        if (local is None) != (aggregate is None):
            raise BudgetError('Local and aggregate wire bindings disagree')
        if local is not None and {k: v for k, v in local.items() if k != 'request_id'} != {
                k: v for k, v in aggregate.items() if k != 'request_id'}:
            raise BudgetError('Local and aggregate wire bindings disagree')
        return local


def _archive_payload(out, endpoint, payload, *, restore=False):
    result = deepcopy(payload)
    operation = hydrate_messages if restore else archive_messages
    if endpoint == '/chat/completions':
        result['messages'] = operation(out, result['messages'])
    elif endpoint == '/images' and 'input_references' in result:
        result['input_references'] = operation(out, [{'role': 'user', 'content': result['input_references']}])[0]['content']
    elif endpoint != '/images':
        raise BudgetError('Unsupported fixed wire endpoint')
    return result


def _wire_path(out, request_id):
    return Path(out) / 'transport_requests' / (digest(request_id)[:24] + '.json')


class LosslessV5Provider(v4.ContinuedRepairedProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._active_wire = None

    def _paid(self, request_id, kind, endpoint, payload, reserve_usd):
        old = self.ledger.lookup(request_id)
        if old is None:
            self.ledger.assert_ready()
            # This must precede the canonical reservation. Oversized unbilled
            # contexts halt; they are never mislabeled as ambiguous paid calls.
            wire, mapping = codec.prepare_wire(self.ledger.path.parent, request_id, endpoint,
                                               payload, max_bytes=MAX_WIRE_BYTES)
            record = {'request_id': request_id, 'endpoint': endpoint,
                      'logical_payload': _archive_payload(self.ledger.path.parent, endpoint, payload),
                      'mapping': mapping}
            runtime.checked_saved(_wire_path(self.ledger.path.parent, request_id), record)
            self.ledger.bind_wire(request_id, record)
            self._active_wire = (request_id, endpoint, deepcopy(payload), wire, record)
        try:
            return super()._paid(request_id, kind, endpoint, payload, reserve_usd)
        except v4.TransportMissingTurn:
            if self.ledger.is_v5_terminal_loss(request_id):
                raise v4.TransportMissingTurn(missing_reason(request_id)) from None
            raise
        finally:
            self._active_wire = None

    def _request(self, method, path, payload=None):
        if method == 'POST':
            if self._active_wire is None:
                raise BudgetError('Unmapped paid POST blocked')
            request_id, endpoint, logical, wire, record = self._active_wire
            if path != endpoint or payload != logical or self._current_paid_id != request_id:
                raise BudgetError('Actual POST does not match the prepared logical request')
            binding = self.ledger.wire_binding(request_id)
            if binding is None or binding['record_sha256'] != digest(record):
                raise BudgetError('POST lacks its exact mirrored immutable wire binding')
            checked = codec.verify_wire(self.ledger.path.parent, endpoint, logical, record['mapping'])
            if checked != wire:
                raise BudgetError('Prepared wire payload changed before POST')
            payload = wire
        return super()._request(method, path, payload)


def _raw_requests(ledger):
    with ledger._connect() as db:
        return {row['request_id']: dict(row) for row in db.execute('SELECT * FROM requests ORDER BY request_id')}


def _baseline(ledger):
    return [{'request_id': key, 'kind': row['kind'], 'model': row['model'],
             'request_hash': row['request_hash'], 'row_sha256': digest(row)}
            for key, row in _raw_requests(ledger).items()]


def verify_wire_records(out, ledger, baseline):
    out = Path(out)
    rows = _raw_requests(ledger)
    prior = {entry['request_id']: entry for entry in baseline}
    for key, entry in prior.items():
        if key not in rows or digest(rows[key]) != entry['row_sha256']:
            raise BudgetError('Pre-v5 request row changed or disappeared')
    with ledger._connect() as db:
        ids = {row[0] for row in db.execute('SELECT request_id FROM wire_bindings_v5')}
    files = {_wire_path(out, key).resolve() for key in ids}
    if {path.resolve() for path in (out / 'transport_requests').glob('*.json')} != files:
        raise BudgetError('Wire map files and immutable bindings disagree')
    paid, prepared, encoded = 0, 0, 0
    for key in sorted(ids):
        if key in prior:
            raise BudgetError('A pre-v5 request acquired a replacement wire mapping')
        binding = ledger.wire_binding(key)
        path = _wire_path(out, key)
        if path.is_symlink():
            raise BudgetError('Wire record cannot be a symlink')
        record = read_json(path)
        if (binding is None or digest(record) != binding['record_sha256'] or record['request_id'] != key or
                record['mapping']['request_id'] != key):
            raise BudgetError('Wire record differs from its mirrored immutable binding')
        payload = _archive_payload(out, record['endpoint'], record['logical_payload'], restore=True)
        codec.verify_wire(out, record['endpoint'], payload, record['mapping'])
        encoded += len(record['mapping']['images'])
        if key in rows:
            paid_record = ledger.lookup(key)  # Check mirrored receipt agreement too.
            expected_endpoint = {'chat': '/chat/completions', 'image': '/images'}.get(paid_record['kind'])
            if (paid_record['request_hash'] != record['mapping']['logical_request_hash'] or
                    paid_record['model'] != payload['model'] or record['endpoint'] != expected_endpoint):
                raise BudgetError('Wire mapping does not bind the actual logical paid request')
            paid += 1
        else:
            prepared += 1
    if any(key not in prior and key not in ids for key in rows):
        raise BudgetError('A new paid intent lacks a v5 wire mapping')
    return {'status': 'verified', 'paid_intents_mapped': paid, 'unbilled_prepared_maps': prepared,
            'ordered_images_verified': encoded, 'legacy_requests_preserved': len(prior),
            'logical_hash_alone_verifies_wire': False, 'strict_wire_byte_limit': MAX_WIRE_BYTES}


def _check_trajectories(self, ledger):
    """Canonical trajectory checks plus exact, separately labeled v4/v5 seals."""
    from visual_collusion.runtime import audit_messages
    count, seen = 0, set()
    for path in sorted((self.out / 'trajectories').glob('*.json')):
        value = read_json(path)
        key = value['episode_id'] + ':' + value['branch']
        if key not in self.trajectory_specs or key in seen or path.stem != digest(key)[:24]:
            raise ValueError('Unknown or duplicate final trajectory')
        seen.add(key)
        spec, ep = self.trajectory_specs[key], self.trajectory_specs[key]['episode']
        if (value['case_id'] != ep['case_id'] or value['condition'] != ep['condition'] or
                value['target'] != spec['target'] or value['evidence_origin'] != self.origin or
                value['status'] not in {'complete', 'incomplete'}):
            raise ValueError('Final trajectory assignment/status differs from the frozen design')
        history = value['history']
        if not isinstance(history, list) or len(history) > len(spec['history']):
            raise ValueError('Trajectory has an invalid history length')
        for index, row in enumerate(history):
            if self.reviewed.get(spec['history'][index]) != row:
                raise ValueError('Trajectory history differs from its required reviewed round sequence')
        complete = value['status'] == 'complete'
        if complete != (len(history) == len(spec['history'])):
            raise ValueError('Trajectory completion status contradicts its reviewed history')
        audits = value.get('audits', {})
        if set(audits) != ({'latest_pair', 'image_history'} if complete else set()):
            raise ValueError('Final auditor views differ from the scheduled completed trajectory')
        for view, audit in audits.items():
            request = key + ':audit:' + view
            if audit['request_id'] != request or audit['status'] not in {'valid', 'invalid'}:
                raise ValueError('Auditor outcome has the wrong request identity/status')
            if audit['status'] == 'valid':
                task = self.job_records[evidence._job_id(ep['episode_id'] + ':task')]['response']['output']['brief']
                messages = audit_messages(self.out, self.cases[ep['case_id']], task, history, view)
                if self.calls[request]['output'] != audit['output'] or digest(messages) != self.input_digests[request]:
                    raise ValueError('Final audit output/input differs from its recorded request')
            elif request not in self.failed_calls:
                sealed = ((audit.get('reason') == v4.missing_reason(request) and ledger.is_v4_terminal_loss(request)) or
                          (audit.get('reason') == missing_reason(request) and ledger.is_v5_terminal_loss(request)))
                if (self.out.resolve() != ledger.path.parent.resolve() or
                        self.requests.get(request, {}).get('kind') != 'chat' or not sealed):
                    raise ValueError('Invalid audit lacks a known-billed failure or exact mirrored versioned transport seal')
        count += 1
    return count


@contextmanager
def transport_route(ledger, baseline):
    original = evidence._Evidence.check_requests
    def checked_requests(instance):
        original(instance)
        if instance.out.resolve() != ledger.path.parent.resolve():
            raise BudgetError('Wire verification target differs from the amended execution')
        verify_wire_records(instance.out, ledger, baseline)
    def checked_trajectories(instance):
        return _check_trajectories(instance, ledger)
    with repair.repaired_route(), patch.object(evidence._Evidence, 'check_requests', checked_requests), \
            patch.object(evidence._Evidence, 'check_trajectories', checked_trajectories):
        yield


def record_authorization(parent, out, ledger):
    parent, out = Path(parent), Path(out)
    # These functions compare the already recorded hashes; neither frozen
    # wrapper/note is overwritten or silently reinterpreted by v5.
    v4.record_authorization(parent, out, ledger)
    old = ledger.lookup(KNOWN_LOSS)
    if (old is None or old['kind'] != 'chat' or old['model'] != repair.MODEL or
            old['request_hash'] != KNOWN_HASH or Decimal(str(old['reserved_usd'])) != KNOWN_RESERVE or
            old['status'] != 'uncertain' or old['reason'] != LOSS_REASON or
            any(old[key] is not None for key in ('actual_usd', 'response', 'receipt'))):
        raise BudgetError('Exact authorized v5 known-loss record differs')
    diagnostics = [json.loads(line) for line in
                   (out / 'execution_notes' / 'transport_diagnostics_v4.jsonl').read_text().splitlines() if line.strip()]
    matching = [value for value in diagnostics if value.get('request_id') == KNOWN_LOSS]
    if (len(matching) != 1 or matching[0].get('http_status') != 401 or
            matching[0].get('request_body_utf8_bytes') != 10_943_403 or
            matching[0].get('image_count') != 9 or matching[0].get('message_count') != 36):
        raise BudgetError('Known-loss HTTP/size diagnostic differs; no automatic sealing')
    path = parent / 'execution_notes' / NOTE_NAME
    previous = read_json(path) if path.exists() else None
    baseline = previous['existing_requests'] if previous is not None else _baseline(ledger)
    current = _raw_requests(ledger)
    if any(entry['request_id'] not in current or digest(current[entry['request_id']]) != entry['row_sha256']
           for entry in baseline):
        raise BudgetError('Frozen pre-v5 request baseline changed')
    body = {
        'authorization': AUTHORIZATION, 'policy_sha256': POLICY_SHA256,
        'execution': str(out.resolve()), 'namespace': ledger.namespace,
        'previous_amendment': v4.NOTE_NAME,
        'previous_amendment_sha256': hashlib.sha256((parent / 'execution_notes' / v4.NOTE_NAME).read_bytes()).hexdigest(),
        'wrapper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'codec_sha256': hashlib.sha256(Path(codec.__file__).read_bytes()).hexdigest(),
        'v4_wrapper_sha256': hashlib.sha256(Path(v4.__file__).read_bytes()).hexdigest(),
        'known_loss_local_request_sha256': digest(old),
        'known_loss_aggregate_request_sha256': digest(ledger.aggregate.lookup(ledger._global_id(KNOWN_LOSS))),
        'known_loss_diagnostic_sha256': digest(matching[0]),
        'existing_requests': baseline, 'existing_requests_sha256': digest(baseline),
        'no_completed_outcome_retired': True, 'paid_retry': False, 'reservations_released': False,
    }
    if previous is not None:
        if {k: v for k, v in previous.items() if k != 'recorded_at'} != body:
            raise BudgetError('Recorded v5 amendment differs')
    else:
        write_json(path, {**body, 'recorded_at': utc_now()})
    ledger.seal_transport_loss(KNOWN_LOSS)
    return read_json(path)


CODE_SCHEMA = {'type': 'object', 'properties': {'code': {'type': 'string'}},
               'required': ['code'], 'additionalProperties': False}


def _format_specs(parent):
    return [('glm.pixel-code', DEFAULT_MODELS['producer'], '731946', Path(parent) / 'infrastructure_checks' / 'vision_1.png'),
            ('sonnet.pixel-code', DEFAULT_MODELS['monitor'], '284517', Path(parent) / 'infrastructure_checks' / 'vision_2.png')]


def _format_messages(data):
    return [{'role': 'user', 'content': [
        {'type': 'text', 'text': 'Read the six-digit code shown in this image. Return JSON with key code.'}, image_content(data)]}]


def _infra_ledger(parent, aggregate):
    return SharedV5Ledger(Path(parent) / INFRA_DIRECTORY / 'budget.sqlite3', aggregate, INFRA_NAMESPACE)


def verify_format_gate(parent, aggregate):
    out = Path(parent) / INFRA_DIRECTORY
    gate = read_json(out / 'latest_status.json')
    if (gate.get('status') != 'passed' or len(gate.get('checks', [])) != 2 or
            gate.get('policy_sha256') != POLICY_SHA256):
        raise BudgetError('Both one-attempt WebP format checks must pass before the main run')
    ledger = _infra_ledger(parent, aggregate)
    verify_wire_records(out, ledger, [])
    # Read the stored response through the exact cached canonical parser. A
    # transport stub fails if this verification ever attempts GET or POST.
    def forbidden_transport(*args):
        raise AssertionError('Cached format-gate verification attempted network access')
    provider = LosslessV5Provider('offline-cached-verification', ledger, transport=forbidden_transport)
    provider._models = read_json(out / 'provider_preflight.json')['chat_metadata']
    for spec, saved in zip(_format_specs(parent), gate['checks']):
        request_id, model, expected, source = spec
        data = source.read_bytes()
        if (saved['request_id'] != request_id or saved['model'] != model or
                saved['source_png_sha256'] != hashlib.sha256(data).hexdigest() or
                (out / 'cards' / source.name).read_bytes() != data):
            raise BudgetError('Saved compatibility card or model identity changed')
        result = provider.chat(request_id, model, _format_messages(data), 1024,
                               schema=CODE_SCHEMA, reasoning_effort=CHAT_PROFILES[model]['reasoning_effort'])
        if (not result['cached'] or result['content'] != {'code': expected} or
                saved['observed'] != expected or not saved['passed'] or saved['receipt'] != result['receipt']):
            raise BudgetError('Cached compatibility response does not establish the exact pixel code')
    return gate


def validate_transport(parent, aggregate):
    out = Path(parent) / INFRA_DIRECTORY
    out.mkdir(exist_ok=True)
    if (out / 'failure.json').exists():
        raise BudgetError('A one-attempt format check already failed; preserved without retry')
    if (out / 'latest_status.json').exists():
        return verify_format_gate(parent, aggregate)
    ledger = _infra_ledger(parent, aggregate)
    provider = LosslessV5Provider(os.environ['OPENROUTER_API_KEY'], ledger)
    write_json(out / 'provider_preflight.json', provider.preflight())
    checks = []
    for request_id, model, expected, source in _format_specs(parent):
        data = source.read_bytes()
        copied = out / 'cards' / source.name
        copied.parent.mkdir(exist_ok=True)
        if copied.exists() and copied.read_bytes() != data:
            raise BudgetError('Original compatibility card changed')
        copied.write_bytes(data)
        messages = _format_messages(data)
        runtime.checked_saved(out / 'logical_checks' / (digest(request_id)[:24] + '.json'), {
            'request_id': request_id, 'model': model, 'messages': archive_messages(out, messages),
            'schema': CODE_SCHEMA, 'max_output_tokens': 1024,
            'reasoning_effort': CHAT_PROFILES[model]['reasoning_effort']})
        try:
            result = provider.chat(request_id, model, messages, 1024, schema=CODE_SCHEMA,
                                   reasoning_effort=CHAT_PROFILES[model]['reasoning_effort'])
            if result['content'] != {'code': expected}:
                raise ValueError('Compatibility pixel code did not match')
            checks.append({'request_id': request_id, 'model': model,
                           'source_png_sha256': hashlib.sha256(data).hexdigest(),
                           'expected': expected, 'observed': result['content']['code'],
                           'passed': True, 'receipt': result['receipt']})
        except BaseException as error:
            runtime.checked_saved(out / 'failure.json', {
                'status': 'failed', 'request_id': request_id,
                'exception_category': 'interrupted' if isinstance(error, (KeyboardInterrupt, SystemExit)) else 'format_check_failed',
                'automatic_retry': False, 'local_budget': ledger.summary(),
                'aggregate_budget': aggregate.summary(), 'prior_passed_checks': checks})
            raise
    value = {'status': 'passed', 'scope': 'Technical WebP format compatibility only; excluded from scientific outcomes',
             'checks': checks, 'policy_sha256': POLICY_SHA256,
             'local_budget': ledger.summary(), 'aggregate_budget': aggregate.summary()}
    runtime.checked_saved(out / 'latest_status.json', value)
    verify_format_gate(parent, aggregate)
    return value


def _sqlite_snapshot(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.db-snapshot-') as temporary:
        backup = Path(temporary) / 'backup.sqlite3'
        with sqlite3.connect(source) as original, sqlite3.connect(backup) as target:
            original.backup(target)
        os.replace(backup, destination)


def save_delivery_scope(parent, out, ledger, baseline):
    value = v4.save_delivery_scope(parent, out, ledger)
    notes = Path(out) / 'execution_notes'
    shutil.copy2(Path(parent) / 'execution_notes' / NOTE_NAME, notes / NOTE_NAME)
    tools = notes / 'host_tools'
    tools.mkdir(exist_ok=True)
    for name in ('run_lossless_webp_v5.py', 'lossless_wire_codec.py', 'continue_repaired_execution.py',
                 'repair_image_route.py', 'run_continue_independent.py', 'run_authorized_key.py'):
        shutil.copy2(repair.ROOT / 'scripts' / name, tools / name)
    infrastructure = Path(parent) / INFRA_DIRECTORY
    copied = notes / INFRA_DIRECTORY
    shutil.copytree(infrastructure, copied, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('budget.sqlite3', 'budget.sqlite3-wal', 'budget.sqlite3-shm', 'run.lock'))
    _sqlite_snapshot(infrastructure / 'budget.sqlite3', copied / 'budget_snapshot.sqlite3')
    value['wire_verification'] = verify_wire_records(out, ledger, baseline)
    value['compatibility_scope'] = 'The two format checks have their own local ledger and are a SUBSET of original aggregate costs; never add either local ledger total to aggregate.'
    value['compatibility_gate'] = verify_format_gate(parent, ledger.aggregate)
    value['continuation_v5_policy_sha256'] = POLICY_SHA256
    value['terminal_missing_request_ids'] = [key for key, row in _raw_requests(ledger).items()
                                             if row['status'] == 'uncertain' and ledger.is_terminal_loss(key)]
    write_json(notes / 'aggregate_budget.json', value)
    write_json(Path(out) / 'analysis' / 'aggregate_budget.json', value)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate-transport', 'advance', 'verify', 'report', 'export'])
    parser.add_argument('--out', type=Path, default=repair.ROOT / 'runs' / 'main')
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'export' and args.destination is None:
        parser.error('export requires --destination')
    parent = args.out.resolve()
    with repair.repaired_route():
        repair.record_amendment(parent)
        out, config = repair.prepare_repaired(parent)
        aggregate = V5Ledger(parent / 'budget.sqlite3')
        ledger = SharedV5Ledger(out / 'budget.sqlite3', aggregate, 'repair1')
        note = record_authorization(parent, out, ledger)
        baseline = note['existing_requests']
        if args.command == 'validate-transport':
            value = validate_transport(parent, aggregate)
        else:
            verify_format_gate(parent, aggregate)
            with transport_route(ledger, baseline):
                if args.command == 'advance':
                    provider = LosslessV5Provider(os.environ['OPENROUTER_API_KEY'], ledger)
                    write_json(out / 'provider_preflight.json', provider.preflight())
                    value = runtime.advance(provider, config, out)
                elif args.command == 'verify':
                    value = {**validate_evidence(out), 'wire_verification': verify_wire_records(out, ledger, baseline)}
                elif args.command == 'report':
                    value = build_report(out)
                    value['accounting'] = save_delivery_scope(parent, out, ledger, baseline)
                    value['aggregate_budget'] = aggregate.summary()
                    write_json(out / 'analysis' / 'report.json', value)
                else:
                    value = {'accounting': save_delivery_scope(parent, out, ledger, baseline)}
                    snapshot = out / 'execution_notes' / 'aggregate_budget_snapshot.sqlite3'
                    _sqlite_snapshot(aggregate.path, snapshot)
                    value['accounting']['aggregate_snapshot'] = {
                        'path': str(snapshot.relative_to(out)),
                        'sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                        'method': 'SQLite backup API; separate from active budget.sqlite3'}
                    write_json(out / 'execution_notes' / 'aggregate_budget.json', value['accounting'])
                    write_json(out / 'analysis' / 'aggregate_budget.json', value['accounting'])
                    value.update(export_run(out, args.destination))
        value['aggregate_budget'] = aggregate.summary()
        write_json(parent / 'execution_notes' / 'repair_latest_status.json', value)
        print(json.dumps(value, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
