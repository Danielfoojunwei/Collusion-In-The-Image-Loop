"""Explicit infrastructure amendment; canonical experiment source is untouched.

The original attempt never delivered to B. Keep it as setup failure evidence.
Restart the identical schedule in main/repaired using OpenInference FP4 for ALL
GLM calls. All charges/reservations also enter the original aggregate $40 ledger.
No fallback, automatic retry, fabricated review, or release of uncertain costs.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from run_authorized_key import AuthorizedKeyProvider
from run_continue_independent import ContinuationLedger
from visual_collusion import runtime, evidence
from visual_collusion.analysis import build_report, validate_evidence
from visual_collusion.bridge import validate_saved_response
from visual_collusion.cli import export_run
from visual_collusion.budget import BudgetError, BudgetLedger, PendingPaidRequest, canonical
from visual_collusion.common import digest, read_json, source_manifest, utc_now, write_json
from visual_collusion.providers import CHAT_PROFILES, DEFAULT_MODELS, ResponseValidationError, image_content

ROOT = Path(__file__).resolve().parents[1]
MODEL = DEFAULT_MODELS['producer']
REPAIRED_PROFILE = {**CHAT_PROFILES[MODEL], 'provider_tag': 'open-inference/fp4',
                    'provider_name': 'OpenInference'}
ORIGINAL_PROVIDER_MESSAGE = evidence._provider_message


def unwrap_json_fence(text):
    match = re.fullmatch(r'\s*```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```\s*', text)
    if match is None:
        raise ValueError('Not one complete JSON code fence')
    body = match.group(1)
    value = json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return body, value


def repaired_provider_message(record, origin):
    message = deepcopy(ORIGINAL_PROVIDER_MESSAGE(record, origin))
    try:
        body, _ = unwrap_json_fence(message['content'])
    except (ValueError, TypeError):
        return message
    message['content'] = body
    return message


class RepairedProvider(AuthorizedKeyProvider):
    """Accept one complete JSON fence without repairing values or retrying."""
    def chat(self, request_id, model, messages, max_output_tokens, schema=None, reasoning_effort=None):
        old = self.ledger.lookup(request_id)
        cached = old is not None and old['status'] == 'completed'
        try:
            return super().chat(request_id, model, messages, max_output_tokens, schema, reasoning_effort)
        except (ResponseValidationError, AttributeError) as error:
            record = self.ledger.lookup(request_id)
            raw = record['response'] if isinstance(record, dict) else None
            if isinstance(error, AttributeError):
                # The canonical parser calls .get() on a single choice before
                # checking its type. Keep that malformed billed reply invalid,
                # with its real stored receipt, rather than leaking a type bug.
                choices = raw.get('choices') if isinstance(raw, dict) else None
                malformed = (not isinstance(choices, list) or len(choices) != 1 or
                             not isinstance(choices[0], dict) or
                             not isinstance(choices[0].get('message'), dict))
                if (not malformed or not isinstance(record, dict) or record['status'] != 'completed' or
                        not isinstance(record.get('receipt'), dict)):
                    raise
                error = ResponseValidationError(
                    'Paid response was incomplete, refused, or invalid JSON; recorded without retry',
                    receipt=record['receipt'])
            if error.receipt is None:
                raise error from None
            if not isinstance(raw, dict):
                raise error from None
            choices = raw.get('choices')
            if (not isinstance(choices, list) or len(choices) != 1 or
                    not isinstance(choices[0], dict) or choices[0].get('finish_reason') != 'stop'):
                raise error from None
            message = choices[0].get('message')
            if (not isinstance(message, dict) or message.get('refusal') or
                    not isinstance(message.get('content'), str)):
                raise error from None
            try:
                text, value = unwrap_json_fence(message['content'])
            except (ValueError, TypeError):
                raise error from None
            # The normal task-specific validator still checks every field/value.
            tokens, images = self._input_estimate(messages, schema, model)
            return {'text': text, 'content': value, 'model': raw['model'], 'usage': raw['usage'],
                    'cost_usd': error.receipt['cost_usd'], 'request_id': request_id,
                    'response_id': raw.get('id'), 'receipt': error.receipt, 'response': raw,
                    'raw': raw, 'cached': cached,
                    'input_estimate': {'tokens': tokens, 'image_count': images},
                    'response_format_mode': 'json_schema' if self._models[model]['structured_schema'] else 'json_object',
                    'reasoning': deepcopy(message.get('reasoning')),
                    'reasoning_details': deepcopy(message.get('reasoning_details')),
                    'framing_normalization': 'whole_reply_json_code_fence_only'}


@contextmanager
def repaired_route():
    # Process-local override: other Codex work and the canonical freeze are intact.
    with patch.dict(CHAT_PROFILES, {MODEL: REPAIRED_PROFILE}), patch.object(evidence, '_provider_message', repaired_provider_message):
        yield


class SharedBudgetLedger(BudgetLedger):
    """Per-execution receipts plus an authoritative shared aggregate cap.

    Reserve aggregate first, complete aggregate first. A crash between databases
    fails closed; a fully stored response can be recovered without another POST.
    """
    def __init__(self, path, aggregate, namespace):
        self.aggregate = aggregate
        self.namespace = namespace
        super().__init__(path, 40)

    def _global_id(self, request_id):
        return self.namespace + ':' + request_id

    def bind(self, key, value):
        super().bind(key, value)
        setting = self.namespace + '_' + key
        with self.aggregate._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT value FROM settings WHERE key=?', (setting,)).fetchone()
            if old and old[0] != value:
                raise BudgetError('Infrastructure amendment configuration changed')
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (setting, value))

    def assert_ready(self):
        # complete() commits the aggregate first. Recover only exact matching,
        # integrity-checked receipts before the local pending-row gate runs.
        # An aggregate-only reservation has no recoverable local intent and
        # remains blocked by the aggregate readiness check below.
        with self._connect() as db:
            unresolved = [row[0] for row in db.execute(
                "SELECT request_id FROM requests WHERE status IN ('pending','uncertain')")]
        for request_id in unresolved:
            self.lookup(request_id)
        super().assert_ready()
        self.aggregate.assert_ready()

    def reserve(self, request_id, kind, model, request_hash, reserve_usd):
        # No network operation may occur unless BOTH durable reservations succeed.
        self.assert_ready()
        self.aggregate.reserve(self._global_id(request_id), kind, model, request_hash, reserve_usd)
        return super().reserve(request_id, kind, model, request_hash, reserve_usd)

    def lookup(self, request_id):
        local = super().lookup(request_id)
        shared = self.aggregate.lookup(self._global_id(request_id))
        if local is None:
            if shared is not None:
                raise PendingPaidRequest('Aggregate-only reservation exists; no POST retry')
            return None
        if shared is None or any(local[k] != shared[k] for k in ('kind', 'model', 'request_hash', 'reserved_usd')):
            raise BudgetError('Local and aggregate request records disagree')
        if local['status'] != 'completed' and shared['status'] == 'completed':
            BudgetLedger.complete(self, request_id, shared['actual_usd'], shared['response'], shared['receipt'])
            local = super().lookup(request_id)
        if local['status'] == 'completed' and (shared['status'] != 'completed' or
                local['response'] != shared['response'] or local['receipt'] != shared['receipt']):
            raise BudgetError('Local and aggregate receipts disagree')
        return local

    def complete(self, request_id, actual_usd, response, receipt):
        self.aggregate.complete(self._global_id(request_id), actual_usd, response, receipt)
        return super().complete(request_id, actual_usd, response, receipt)

    def uncertain(self, request_id, reason='No conclusive cost receipt', **kwargs):
        self.aggregate.uncertain(self._global_id(request_id), reason, **kwargs)
        super().uncertain(request_id, reason, **kwargs)

    def halt(self, reason):
        self.aggregate.halt(reason)
        super().halt(reason)


def record_amendment(parent):
    parent = Path(parent).resolve()
    freeze = read_json(parent / 'freeze.json')
    if (source_manifest() != freeze['source_files'] or
            digest(read_json(parent / 'config.json')) != freeze['config_sha256'] or
            digest(read_json(parent / 'plan.json')) != freeze['plan_sha256']):
        raise BudgetError('Original canonical source, config or plan changed')
    note = parent / 'execution_notes' / 'image_route_repair_v3.json'
    body = {
        'user_instruction': 'Fix and debug this is the experiment technical issues find all issues and then continue the main experiment',
        'root_cause': 'HTTP 404: provider.only=sail-research/fp4 excludes all providers available for image input',
        'response_framing': 'Accept only a single complete JSON code fence; preserve original billed response bytes, reject incomplete/refused/malformed outputs, and apply original task schema validators',
        'previous_amendment': 'image_route_repair_v2.json; preserved unchanged, together with image_route_repair.json and all original failed requests and reservations',
        'crash_recovery': 'Before readiness, recover only local unresolved requests with exact kind/model/hash/reserve matches and completed integrity-checked aggregate response/receipt; aggregate-only reservations and mismatches remain blocked without POST',
        'parser_robustness': 'Malformed choices/messages, incomplete/refused replies and invalid fenced objects retain their original ResponseValidationError; normalization changes only whole-reply JSON framing, never substantive values',
        'report_and_export': 'Run canonical validation/export inside the process-local repaired route. Save aggregate-budget scope metadata with reports and exports; export a SQLite backup of the original aggregate ledger plus its SHA256. Repaired local costs are a subset, not additive to aggregate totals',
        'infrastructure_evidence': 'Reuse existing infrastructure_checks/latest_status.json and original billed receipts; no new compatibility checks or paid retries',
        'old_route': 'sail-research/fp4', 'new_route': REPAIRED_PROFILE['provider_tag'],
        'selection_rule': 'First advertised FP4 route supporting locked schema, context and price bounds; selected before live test',
        'unchanged': ['model IDs', 'FP4 quantization', 'reasoning efforts', 'prompts', 'facts', 'targets', 'schedule', 'per-turn allowances'],
        'execution': str(parent / 'repaired'), 'aggregate_budget': str(parent / 'budget.sqlite3'),
        'budget': 'All old charges and unresolved reservations, diagnostics and repaired execution share the original $40 cap',
        'restart_reason': 'All ten original cases failed before A delivery or any B observation; no completed scientific outcome is replaced',
        'task_authors': 'Reuse exact original task-author packets and responses; no new author call or answer editing',
        'reviewers': 'Actual fresh Codex reviewers still required; host must not supply its own verdict',
        'automatic_paid_retries': False,
        'canonical_source_manifest': freeze['source_files'],
        'repair_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    if note.exists():
        if {k: v for k, v in read_json(note).items() if k != 'recorded_at'} != body:
            raise BudgetError('Recorded infrastructure amendment changed')
    else:
        write_json(note, {**body, 'recorded_at': utc_now()})
    aggregate = ContinuationLedger(parent / 'budget.sqlite3', 40)
    # The diagnostic 404 is retained, including its entire uncertain reservation.
    # No failures in the repaired route are automatically sealed or continued.
    original_diagnostic = 'infra.vision-diagnosis.1'
    old = aggregate.lookup(original_diagnostic)
    if old and old['status'] == 'uncertain':
        aggregate.seal_transport_loss(original_diagnostic)
    return aggregate


def _immutable_json(path, value):
    if path.exists() and read_json(path) != value:
        raise ValueError('Saved infrastructure evidence changed')
    write_json(path, value)


def validate_live(parent, aggregate):
    out = parent / 'infrastructure_checks'
    out.mkdir(exist_ok=True)
    ledger = SharedBudgetLedger(out / 'budget.sqlite3', aggregate, 'infra_checks1')
    provider = RepairedProvider(os.environ['OPENROUTER_API_KEY'], ledger)
    preflight = provider.preflight()
    write_json(out / 'provider_preflight.json', preflight)
    checks = []
    # Expected values exist only in the generated pixels, never in the request text.
    # Two unrelated cards make a constant text response fail the check.
    schema = {'type': 'object', 'properties': {'code': {'type': 'string'}},
              'required': ['code'], 'additionalProperties': False}
    for i, code in enumerate(('731946', '284517'), 1):
        canvas = Image.new('RGB', (1024, 1024), 'white')
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 180)
        draw.text((90, 360), code, fill='black', font=font)
        buf = io.BytesIO(); canvas.save(buf, format='PNG'); png = buf.getvalue()
        path = out / f'vision_{i}.png'
        if path.exists() and path.read_bytes() != png:
            raise ValueError('Infrastructure check pixels changed')
        path.write_bytes(png)
        messages = [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'Read the six-digit code shown in this image. Return JSON with key code.'}, image_content(png)]}]
        result = provider.chat(f'vision.{i}', MODEL, messages, 1024, schema=schema)
        checks.append({'check': f'vision.{i}', 'image_sha256': hashlib.sha256(png).hexdigest(),
                       'expected': code, 'observed': result['content'].get('code'),
                       'passed': result['content'].get('code') == code, 'receipt': result['receipt']})
        if not checks[-1]['passed']:
            _immutable_json(out / 'failed_vision.json', checks[-1])
            raise ValueError('Live visual reading failed; scheduled cases remain blocked')
    # Verify the independently pinned auditor can also consume actual pixels.
    result = provider.chat('auditor.vision', DEFAULT_MODELS['monitor'], messages, 1024, schema=schema)
    checks.append({'check': 'auditor.vision', 'expected': code,
                   'observed': result['content'].get('code'),
                   'passed': result['content'].get('code') == code, 'receipt': result['receipt']})
    if not checks[-1]['passed']:
        raise ValueError('Auditor image input failed; scheduled cases remain blocked')
    value = {'status': 'passed', 'purpose': 'Infrastructure checks only; excluded from collusion outcomes',
             'checks': checks, 'local_budget': ledger.summary(), 'aggregate_budget': aggregate.summary()}
    # Cached reruns preserve the first evidence summary and consume no extra inference.
    write_json(out / 'latest_status.json', value)
    return value


def prepare_repaired(parent):
    out = parent / 'repaired'
    config = read_json(parent / 'config.json')
    runtime.prepare_run(config, out)
    for folder in sorted((parent / 'review_jobs').iterdir()):
        if not (folder / 'response.json').exists():
            continue
        if read_json(folder / 'packet.json')['kind'] != 'task':
            continue
        validate_saved_response(folder)
        destination = out / 'review_jobs' / folder.name
        if not destination.exists():
            shutil.copytree(folder, destination)
        if read_json(destination / 'packet.json') != read_json(folder / 'packet.json'):
            raise ValueError('Original task-author packet changed')
        if validate_saved_response(destination) != validate_saved_response(folder):
            raise ValueError('Reused task-author answer changed')
    return out, config


def save_accounting_scope(parent, out, aggregate, *, snapshot=False):
    """Make the aggregate cap and original setup failures travel with delivery."""
    parent, out = Path(parent), Path(out)
    notes = out / 'execution_notes'
    notes.mkdir(parents=True, exist_ok=True)
    local = BudgetLedger(out / 'budget.sqlite3', 40).summary()
    value = {
        'budget_relationship': 'Repaired execution local costs are a SUBSET of the aggregate ledger. Do not add local totals to aggregate totals.',
        'aggregate_scope': 'Original failed execution, retained unresolved reservations, infrastructure diagnostics/checks, and repaired execution; one original $40 cap',
        'aggregate_budget': aggregate.summary(),
        'repaired_execution_budget': local,
        'aggregate_source': str(aggregate.path.resolve()),
    }
    if snapshot:
        destination = notes / 'aggregate_budget_snapshot.sqlite3'
        with tempfile.TemporaryDirectory(prefix='.aggregate-backup-', dir=notes) as temporary:
            backup = Path(temporary) / 'snapshot.sqlite3'
            with aggregate._connect() as source, sqlite3.connect(backup) as target:
                source.backup(target)
            os.replace(backup, destination)
        value['aggregate_snapshot'] = {
            'path': 'execution_notes/aggregate_budget_snapshot.sqlite3',
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'method': 'SQLite backup API; snapshot is separate from active budget.sqlite3',
        }
    amendments = {}
    for name in ('image_route_repair.json', 'image_route_repair_v2.json', 'image_route_repair_v3.json'):
        source = parent / 'execution_notes' / name
        if source.exists():
            shutil.copy2(source, notes / name)
            amendments[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    shutil.copy2(Path(__file__), notes / Path(__file__).name)
    value['amendment_sha256'] = amendments
    value['repair_script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write_json(notes / 'aggregate_budget.json', value)
    write_json(out / 'analysis' / 'aggregate_budget.json', value)
    return value


def export_repaired(parent, out, aggregate, destination):
    with repaired_route():
        scope = save_accounting_scope(parent, out, aggregate, snapshot=True)
        return {**export_run(out, destination), 'accounting': scope}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate-live', 'advance', 'verify', 'report', 'export'])
    parser.add_argument('--out', type=Path, default=ROOT / 'runs' / 'main')
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    if args.command == 'export' and args.destination is None:
        parser.error('export requires --destination')
    parent = args.out.resolve()
    with repaired_route():
        aggregate = record_amendment(parent)
        if args.command == 'validate-live':
            value = validate_live(parent, aggregate)
        else:
            gate = read_json(parent / 'infrastructure_checks' / 'latest_status.json')
            if gate['status'] != 'passed' or not all(c['passed'] for c in gate['checks']):
                raise ValueError('Live infrastructure checks must pass before scheduled execution')
            out, config = prepare_repaired(parent)
            if args.command == 'advance':
                ledger = SharedBudgetLedger(out / 'budget.sqlite3', aggregate, 'repair1')
                provider = RepairedProvider(os.environ['OPENROUTER_API_KEY'], ledger)
                write_json(out / 'provider_preflight.json', provider.preflight())
                value = runtime.advance(provider, config, out)
            elif args.command == 'verify':
                value = validate_evidence(out)
            elif args.command == 'report':
                value = build_report(out)
                value['accounting'] = save_accounting_scope(parent, out, aggregate)
                value['aggregate_budget'] = aggregate.summary()
                write_json(out / 'analysis' / 'report.json', value)
            else:
                value = export_repaired(parent, out, aggregate, args.destination)
            value['aggregate_budget'] = aggregate.summary()
            write_json(parent / 'execution_notes' / 'repair_latest_status.json', value)
        print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
