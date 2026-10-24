"""Versioned host-only continuation of the existing repaired execution.

Keep v3, every receipt, reviewer and unknown-charge reservation intact. A sealed
response-less request is terminal missing evidence, never a retry opportunity.
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
import re
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
from unittest.mock import patch

import repair_image_route as repair
from run_continue_independent import ContinuationLedger, LOSS_REASON, _eligible_loss, _NoRedirect
from visual_collusion import evidence, runtime
from visual_collusion.analysis import build_report, validate_evidence
from visual_collusion.budget import BudgetError, BudgetLedger, PendingPaidRequest, request_digest
from visual_collusion.cli import export_run
from visual_collusion.common import append_event, canonical, digest, read_json, utc_now, write_json
from visual_collusion.providers import ProviderError


KNOWN_LOSS = 'episode_194cef8360:branch_1:round4:A:step1'
KNOWN_RESERVE = Decimal('0.0258284')
AUTHORIZATION = {
    'user_instruction': 'Fix and debug this is the experiment technical issues find all issues and then continue the main experiment',
    'prior_continuation_instruction': 'proceed with the entire thing now',
    'scope': 'Resume the same repaired execution after the exact known response-less loss, and continue independent planned work after safely observed transient transport losses without retrying any lost request.',
    'known_loss': KNOWN_LOSS,
    'known_loss_cause': 'Unknown: v3 retained no HTTP or underlying-exception diagnostic. This exact-ID retrospective exception does not establish its cause.',
    'future_losses': 'Require a recognized transport/decoding failure or transient HTTP 408/429/5xx, exact matching response-less local and aggregate rows, and immutable snapshot-bound seals. Nontransient HTTP, model/route/price/account/budget errors, missing costs, and interrupts remain blocking.',
    'preserved': 'Every request row, unknown reservation, paid response, reviewer, author, source/config/plan, model, OpenInference FP4 route, pixels, messages, per-turn allowance, and original aggregate $40 inference/$50 cash limits.',
    'audit_validation': 'Canonical checks unchanged except that an invalid final audit may be backed by its exact mirrored immutable response-less loss seal and matching archived request, rather than a fabricated known-billed failed_call.',
    'diagnostics': 'Only fixed categories, HTTP status, strictly allowlisted header/generation IDs, exception classes and request-size/count metadata are persisted. At most 16 KiB error JSON is inspected in memory; no provider text, bodies, headers carrying credentials, or credentials are logged.',
}
POLICY_SHA256 = digest(AUTHORIZATION)
NOTE_NAME = 'repaired_execution_continuation_v4.json'
LOSS_TABLE = 'terminal_transport_losses_v4'


class TransportMissingTurn(runtime.InvalidAgentOutput):
    """An unknown-charge missing observation, never a billed invalid output."""


def missing_reason(request_id):
    return ('Transport response unavailable for ' + request_id +
            '; terminal missing turn, no retry; unknown charge remains reserved [continuation_v4]')


class V4Ledger(ContinuationLedger):
    """Accept earlier immutable seals and separately bind new v4 authority."""
    def __init__(self, path, cap_usd=40):
        super().__init__(path, cap_usd)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS terminal_transport_losses_v4 (
                    request_id TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL,
                    policy_sha256 TEXT NOT NULL, recorded_at REAL NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS terminal_transport_losses_v4_no_update
                BEFORE UPDATE ON terminal_transport_losses_v4
                BEGIN SELECT RAISE(ABORT, 'V4 terminal transport losses are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS terminal_transport_losses_v4_no_delete
                BEFORE DELETE ON terminal_transport_losses_v4
                BEGIN SELECT RAISE(ABORT, 'V4 terminal transport losses are immutable'); END;
            ''')

    @staticmethod
    def _sealed_v4(db, row):
        seal = db.execute('SELECT snapshot_json,policy_sha256 FROM terminal_transport_losses_v4 WHERE request_id=?',
                          (row['request_id'],)).fetchone()
        return (seal is not None and seal['policy_sha256'] == POLICY_SHA256 and
                seal['snapshot_json'] == canonical(dict(row)) and _eligible_loss(row))

    @staticmethod
    def _sealed(db, row):
        return ContinuationLedger._sealed(db, row) or V4Ledger._sealed_v4(db, row)

    def is_v4_terminal_loss(self, request_id):
        with self._connect() as db:
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            return row is not None and self._sealed_v4(db, row)

    def seal_transport_loss(self, request_id):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            if not _eligible_loss(row):
                raise PendingPaidRequest('Only an exact no-response transport loss can be sealed')
            if self._sealed(db, row):
                return
            db.execute('INSERT INTO terminal_transport_losses_v4 VALUES (?,?,?,?)',
                       (request_id, canonical(dict(row)), POLICY_SHA256, time.time()))


class SharedContinuationLedger(repair.SharedBudgetLedger, V4Ledger):
    """Mirror new immutable seals as well as receipts and reservations."""
    def assert_ready(self):
        # Recover the aggregate-first seal crash using an existing durable seal,
        # never infer missingness from an unexplained pending/uncertain request.
        with self._connect() as db:
            unresolved = [row[0] for row in db.execute("SELECT request_id FROM requests WHERE status='uncertain'")]
        for request_id in unresolved:
            if self.aggregate.is_terminal_loss(self._global_id(request_id)):
                self.seal_transport_loss(request_id)
        super().assert_ready()

    def seal_transport_loss(self, request_id):
        self.lookup(request_id)  # Verify exact local/aggregate intent agreement.
        self.aggregate.seal_transport_loss(self._global_id(request_id))
        V4Ledger.seal_transport_loss(self, request_id)

    def is_terminal_loss(self, request_id):
        self.lookup(request_id)
        return (ContinuationLedger.is_terminal_loss(self, request_id) and
                self.aggregate.is_terminal_loss(self._global_id(request_id)))

    def is_v4_terminal_loss(self, request_id):
        self.lookup(request_id)
        return (V4Ledger.is_v4_terminal_loss(self, request_id) and
                self.aggregate.is_v4_terminal_loss(self._global_id(request_id)))


def _error_category(message):
    """Return only fixed labels; provider text never leaves this function."""
    text = message.lower() if isinstance(message, str) else ''
    if any(term in text for term in ('rate limit', 'too many requests')):
        return 'rate_limit'
    if any(term in text for term in ('no endpoints', 'no provider', 'provider unavailable')):
        return 'no_provider'
    if any(term in text for term in ('insufficient credit', 'unauthorized', 'authentication', 'invalid api key', 'payment required')):
        return 'auth_or_credit'
    if 'context' in text and any(term in text for term in ('limit', 'length', 'exceed')):
        return 'context_limit'
    if 'image' in text and any(term in text for term in ('too many', 'size', 'count', 'maximum', 'limit', 'large')):
        return 'image_count_or_size'
    if 'unsupported' in text and any(term in text for term in ('input', 'image', 'modality')):
        return 'unsupported_input'
    return 'other'


class _ObservingOpener:
    def __init__(self, owner, delegate):
        self.owner, self.delegate = owner, delegate

    def _observe(self, status, headers):
        safe = {'http_status': int(status)}
        for name in ('X-Generation-Id', 'X-Request-Id', 'Request-Id'):
            value = headers.get(name) if headers is not None else None
            if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', value):
                safe[name.lower()] = value
        self.owner._observed_http = safe

    def open(self, request, timeout):
        try:
            response = self.delegate.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            self._observe(error.code, error.headers)
            # Canonical transport does not read HTTP error bodies. This explicit
            # v4 diagnostic amendment inspects a bounded JSON object in memory.
            try:
                raw = error.read(16_384 + 1)
                body = json.loads(raw) if len(raw) <= 16_384 else None
                detail = body.get('error') if isinstance(body, dict) else None
                self.owner._observed_http['error_category'] = _error_category(
                    detail.get('message') if isinstance(detail, dict) else None)
                candidates = [body.get('id'), body.get('generation_id')] if isinstance(body, dict) else []
                if isinstance(detail, dict):
                    candidates += [detail.get('id'), detail.get('generation_id')]
                for value in candidates:
                    if isinstance(value, str) and re.fullmatch(r'gen-[A-Za-z0-9_-]{1,160}', value):
                        self.owner._observed_http['generation_id'] = value
                        break
            except (OSError, ValueError, TypeError):
                pass
            raise
        self._observe(response.status, response.headers)
        return response


class ContinuedRepairedProvider(repair.RepairedProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_paid_id = None
        self._observed_http = None
        self._last_post_failure = None
        if self._transport is None:
            delegate = self._opener or urllib.request.build_opener(_NoRedirect())
            self._opener = _ObservingOpener(self, delegate)

    def _request(self, method, path, payload=None):
        self._observed_http = None
        try:
            return super()._request(method, path, payload)
        except BaseException as error:
            if method == 'POST' and self._current_paid_id:
                observed = deepcopy(self._observed_http) or {}
                message = str(error) if isinstance(error, ProviderError) else ''
                match = re.fullmatch(r'API HTTP ([0-9]{3}); no automatic retry', message)
                if match:
                    observed.setdefault('http_status', int(match.group(1)))
                if isinstance(error, urllib.error.HTTPError):
                    observed.setdefault('http_status', int(error.code))
                status = observed.get('http_status')
                interrupt = isinstance(error, (KeyboardInterrupt, SystemExit))
                transport = (message == 'Transport or JSON decoding failed; no automatic retry' or
                             (not isinstance(error, urllib.error.HTTPError) and
                              isinstance(error, (urllib.error.URLError, TimeoutError, OSError))))
                category = ('interrupt_ambiguity' if interrupt else
                            'http_error' if match or isinstance(error, urllib.error.HTTPError) else
                            'transport_or_json_decoding_failure' if transport else 'unclassified_error')
                eligible = (not interrupt and (transport or status in {408, 429, 500, 502, 503, 504}) and
                            observed.get('error_category') not in {
                                'no_provider', 'auth_or_credit', 'context_limit',
                                'image_count_or_size', 'unsupported_input'})
                safe_class = type(error).__name__ if type(error) in {
                    ProviderError, TimeoutError, OSError, urllib.error.URLError, urllib.error.HTTPError,
                    KeyboardInterrupt, SystemExit} else 'OtherError'
                messages = payload.get('messages', []) if isinstance(payload, dict) else []
                images = sum(isinstance(block, dict) and block.get('type') == 'image_url'
                             for message in messages if isinstance(message, dict)
                             for block in (message.get('content') if isinstance(message.get('content'), list) else []))
                self._last_post_failure = {
                    'request_id': self._current_paid_id, 'endpoint': path,
                    'category': category, 'exception_class': safe_class,
                    'eligible_terminal_loss': eligible,
                    'request_body_utf8_bytes': len(canonical(payload).encode('utf-8')),
                    'message_count': len(messages), 'image_count': images, **observed,
                }
                append_event(self.ledger.path.parent / 'execution_notes' / 'transport_diagnostics_v4.jsonl',
                             self._last_post_failure)
            raise

    def _paid(self, request_id, kind, endpoint, payload, reserve_usd):
        old = self.ledger.lookup(request_id)
        expected = request_digest({'endpoint': endpoint, 'payload': payload})
        if old is not None:
            if old['request_hash'] != expected or old['model'] != payload['model'] or old['kind'] != kind:
                raise BudgetError('Stable request ID does not match the original request')
            if self.ledger.is_terminal_loss(request_id):
                raise TransportMissingTurn(missing_reason(request_id))
        self._current_paid_id, self._last_post_failure = request_id, None
        try:
            return super()._paid(request_id, kind, endpoint, payload, reserve_usd)
        except ProviderError:
            row = self.ledger.lookup(request_id)
            if (self._last_post_failure is not None and self._last_post_failure['eligible_terminal_loss'] and
                    row is not None and row['status'] == 'uncertain' and row['reason'] == LOSS_REASON and
                    row['actual_usd'] is None and row['response'] is None and row['receipt'] is None):
                self.ledger.seal_transport_loss(request_id)
                raise TransportMissingTurn(missing_reason(request_id)) from None
            raise
        finally:
            self._current_paid_id = None


def _check_trajectories_with_transport_losses(self, ledger):
    """Canonical trajectory checks, with one explicit sealed-audit allowance."""
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
                # check_requests already binds this allowed chat request to its
                # archived inputs/hash. Both immutable seals must still match.
                if (self.out.resolve() != ledger.path.parent.resolve() or
                        self.requests.get(request, {}).get('kind') != 'chat' or
                        audit.get('reason') != missing_reason(request) or
                        not ledger.is_v4_terminal_loss(request)):
                    raise ValueError('Invalid final audit lacks a known-billed failure or exact mirrored v4 transport seal')
        count += 1
    return count


@contextmanager
def continuation_route(ledger):
    def checked(instance):
        return _check_trajectories_with_transport_losses(instance, ledger)
    with repair.repaired_route(), patch.object(evidence._Evidence, 'check_trajectories', checked):
        yield


def record_authorization(parent, out, ledger):
    note = Path(parent) / 'execution_notes' / NOTE_NAME
    old = ledger.lookup(KNOWN_LOSS)
    if (old is None or old['kind'] != 'chat' or old['model'] != repair.MODEL or
            Decimal(str(old['reserved_usd'])) != KNOWN_RESERVE or old['status'] != 'uncertain' or
            old['reason'] != LOSS_REASON or any(old[key] is not None for key in ('actual_usd', 'response', 'receipt'))):
        raise BudgetError('Exact authorized known no-response request differs; preserve and investigate')
    v3 = Path(parent) / 'execution_notes' / 'image_route_repair_v3.json'
    body = {
        'authorization': AUTHORIZATION, 'policy_sha256': POLICY_SHA256,
        'execution': str(Path(out).resolve()), 'namespace': ledger.namespace,
        'previous_amendment': 'image_route_repair_v3.json; unchanged and preserved',
        'previous_amendment_sha256': hashlib.sha256(v3.read_bytes()).hexdigest(),
        'wrapper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'v3_wrapper_sha256': hashlib.sha256(Path(repair.__file__).read_bytes()).hexdigest(),
        'known_loss_local_request_sha256': digest(old),
        'known_loss_aggregate_request_sha256': digest(ledger.aggregate.lookup(ledger._global_id(KNOWN_LOSS))),
        'no_completed_outcome_retired': True, 'paid_retry': False, 'reservations_released': False,
    }
    if note.exists():
        if {k: v for k, v in read_json(note).items() if k != 'recorded_at'} != body:
            raise BudgetError('Recorded v4 continuation amendment differs')
    else:
        write_json(note, {**body, 'recorded_at': utc_now()})
    ledger.seal_transport_loss(KNOWN_LOSS)
    return read_json(note)


def save_delivery_scope(parent, out, ledger, *, snapshot=False):
    value = repair.save_accounting_scope(parent, out, ledger.aggregate, snapshot=snapshot)
    notes = Path(out) / 'execution_notes'
    shutil.copy2(Path(parent) / 'execution_notes' / NOTE_NAME, notes / NOTE_NAME)
    shutil.copy2(Path(__file__), notes / Path(__file__).name)
    with ledger._connect() as db:
        ids = [row[0] for row in db.execute("SELECT request_id FROM requests WHERE status='uncertain'")]
    value['terminal_missing_request_ids'] = [key for key in ids if ledger.is_terminal_loss(key)]
    value['unknown_charge_interpretation'] = 'Terminal missing observations remain uncertain with their full original reservations; they are neither refusals, negative collusion outcomes nor known-billed invalid outputs.'
    value['continuation_policy_sha256'] = POLICY_SHA256
    value['continuation_wrapper_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write_json(notes / 'aggregate_budget.json', value)
    write_json(Path(out) / 'analysis' / 'aggregate_budget.json', value)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['advance', 'verify', 'report', 'export'])
    parser.add_argument('--out', type=Path, default=repair.ROOT / 'runs' / 'main')
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'export' and args.destination is None:
        parser.error('export requires --destination')
    parent = args.out.resolve()
    with repair.repaired_route():
        repair.record_amendment(parent)  # Check immutable v3 and original freeze.
        gate = read_json(parent / 'infrastructure_checks' / 'latest_status.json')
        if gate['status'] != 'passed' or not all(check['passed'] for check in gate['checks']):
            raise ValueError('Existing infrastructure checks must have passed')
        out, config = repair.prepare_repaired(parent)
        ledger = SharedContinuationLedger(out / 'budget.sqlite3', V4Ledger(parent / 'budget.sqlite3'), 'repair1')
        record_authorization(parent, out, ledger)
        with continuation_route(ledger):
            if args.command == 'advance':
                provider = ContinuedRepairedProvider(os.environ['OPENROUTER_API_KEY'], ledger)
                write_json(out / 'provider_preflight.json', provider.preflight())
                value = runtime.advance(provider, config, out)
            elif args.command == 'verify':
                value = validate_evidence(out)
            elif args.command == 'report':
                value = build_report(out)
                value['accounting'] = save_delivery_scope(parent, out, ledger)
                value['aggregate_budget'] = ledger.aggregate.summary()
                write_json(out / 'analysis' / 'report.json', value)
            else:
                scope = save_delivery_scope(parent, out, ledger, snapshot=True)
                value = {**export_run(out, args.destination), 'accounting': scope}
            value['aggregate_budget'] = ledger.aggregate.summary()
            write_json(parent / 'execution_notes' / 'repair_latest_status.json', value)
            print(json.dumps(value, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
