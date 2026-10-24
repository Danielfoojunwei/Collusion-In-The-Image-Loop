"""Offline continuation regressions. No live inference or live-ledger writes."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
import urllib.error

from continue_repaired_execution import (
    ContinuedRepairedProvider, KNOWN_LOSS, KNOWN_RESERVE, NOTE_NAME, POLICY_SHA256,
    SharedContinuationLedger, TransportMissingTurn, V4Ledger, continuation_route,
    missing_reason, record_authorization,
)
from run_continue_independent import ContinuationLedger, LOSS_REASON
from visual_collusion.analysis import build_report, validate_evidence
from visual_collusion.budget import BudgetError, PendingPaidRequest, request_digest
from visual_collusion.common import canonical, digest, read_json, write_json
from visual_collusion.mock import MockProvider
from visual_collusion.providers import DEFAULT_MODELS, ProviderError
from visual_collusion.runtime import advance, prepare_run


class ContinuedRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.master = V4Ledger(self.root / 'aggregate.sqlite3')
        self.ledger = SharedContinuationLedger(self.root / 'run' / 'budget.sqlite3', self.master, 'repair1')
        self.payload = {'model': 'offline-fixture'}
        self.hash = request_digest({'endpoint': '/chat/completions', 'payload': self.payload})
        self.posts = 0
        self.failure = ProviderError('API HTTP 502; no automatic retry')
        self.response = None
        def transport(method, path, payload):
            if method != 'POST':
                self.fail('No metadata GET expected in this offline unit fixture')
            self.posts += 1
            if self.failure is not None:
                raise self.failure
            return deepcopy(self.response)
        self.provider = ContinuedRepairedProvider('offline-placeholder', self.ledger, transport=transport)
        self.configure_provider(self.provider)

    @staticmethod
    def configure_provider(provider):
        provider._preflight = {'status': 'offline-fixture'}
        provider._preflight_time = time.monotonic()
        provider._key_check = lambda required: {'remaining_usd': 40}

    def reserve_loss(self, key='lost', amount=0.01):
        self.ledger.reserve(key, 'chat', self.payload['model'], self.hash, amount)
        self.ledger.uncertain(key, LOSS_REASON)

    def paid(self, key='new'):
        return self.provider._paid(key, 'chat', '/chat/completions', self.payload, 0.01)

    def test_exact_seals_preserve_both_rows_costs_and_block_retry_after_reopen(self):
        self.reserve_loss()
        before = self.ledger.lookup('lost'), self.master.lookup('repair1:lost')
        self.ledger.seal_transport_loss('lost')
        reopened = SharedContinuationLedger(self.ledger.path, V4Ledger(self.master.path), 'repair1')
        reopened.assert_ready()
        self.assertTrue(reopened.is_v4_terminal_loss('lost'))
        self.assertEqual((reopened.lookup('lost'), reopened.aggregate.lookup('repair1:lost')), before)
        self.assertEqual(reopened.aggregate.summary()['reserved_unresolved_usd'], 0.01)
        with self.assertRaises(TransportMissingTurn):
            self.paid('lost')
        self.assertEqual(self.posts, 0)
        with self.assertRaises(sqlite3.IntegrityError), self.master._connect() as db:
            db.execute("DELETE FROM terminal_transport_losses_v4 WHERE request_id='repair1:lost'")

    def test_aggregate_first_seal_crash_recovers_only_matching_local_intent(self):
        self.reserve_loss()
        self.master.seal_transport_loss('repair1:lost')
        self.assertFalse(V4Ledger.is_v4_terminal_loss(self.ledger, 'lost'))
        self.ledger.assert_ready()
        self.assertTrue(self.ledger.is_v4_terminal_loss('lost'))
        self.assertEqual(self.posts, 0)

    def test_aggregate_first_response_crash_still_recovers_without_post(self):
        self.ledger.reserve('done', 'chat', 'offline-fixture', self.hash, 0.01)
        self.master.complete('repair1:done', 0.001, {'saved': True}, {'cost_usd': 0.001})
        self.ledger.assert_ready()
        self.assertEqual(self.ledger.lookup('done')['status'], 'completed')
        self.assertEqual(self.posts, 0)

    def test_legacy_original_seals_remain_valid_with_separate_v4_authority(self):
        self.master.reserve('old.loss', 'chat', 'fixture', self.hash, 0.01)
        self.master.uncertain('old.loss', LOSS_REASON)
        ContinuationLedger.seal_transport_loss(self.master, 'old.loss')
        self.master.assert_ready()
        self.assertTrue(self.master.is_terminal_loss('old.loss'))
        self.assertFalse(self.master.is_v4_terminal_loss('old.loss'))
        self.ledger.assert_ready()

    def test_pending_and_unobserved_uncertain_rows_remain_blocking(self):
        self.ledger.reserve('pending', 'chat', 'fixture', self.hash, 0.01)
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()
        self.ledger.uncertain('pending', LOSS_REASON)
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()
        self.assertFalse(self.ledger.is_terminal_loss('pending'))

    def test_transient_failure_seals_once_and_allows_independent_work(self):
        with self.assertRaises(TransportMissingTurn) as raised:
            self.paid()
        self.assertEqual(str(raised.exception), missing_reason('new'))
        self.assertTrue(self.ledger.is_v4_terminal_loss('new'))
        self.assertEqual(self.posts, 1)
        with self.assertRaises(TransportMissingTurn):
            self.paid()
        self.assertEqual(self.posts, 1)
        self.failure, self.response = None, {'id': 'offline', 'usage': {'cost': 0.001}}
        self.assertFalse(self.paid('independent')[2])
        self.assertEqual(self.posts, 2)
        self.assertAlmostEqual(self.master.summary()['committed_usd'], 0.011)

    def test_changed_payload_cannot_reuse_a_terminal_loss(self):
        self.reserve_loss()
        self.ledger.seal_transport_loss('lost')
        with self.assertRaises(BudgetError):
            self.provider._paid('lost', 'chat', '/chat/completions', {'model': 'changed'}, 0.01)
        self.assertEqual(self.posts, 0)

    def test_missing_cost_is_not_a_transport_seal(self):
        self.failure, self.response = None, {'id': 'offline', 'usage': {}}
        with self.assertRaisesRegex(ProviderError, 'no usable cost receipt'):
            self.paid()
        self.assertFalse(self.ledger.is_terminal_loss('new'))
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()

    def test_interrupt_and_nontransient_http_remain_blocking(self):
        for index, failure in enumerate((KeyboardInterrupt(), SystemExit(),
                                         ProviderError('API HTTP 413; no automatic retry'),
                                         ProviderError('API HTTP 404; no automatic retry'),
                                         ProviderError('Unknown failure'))):
            with self.subTest(failure=type(failure).__name__):
                master = V4Ledger(self.root / f'aggregate-{index}.sqlite3')
                ledger = SharedContinuationLedger(self.root / f'run-{index}' / 'budget.sqlite3', master, 'repair1')
                def failed_transport(method, path, payload):
                    raise failure
                provider = ContinuedRepairedProvider('offline-placeholder', ledger, transport=failed_transport)
                self.configure_provider(provider)
                with self.assertRaises(ProviderError):
                    provider._paid('new', 'chat', '/chat/completions', self.payload, 0.01)
                self.assertFalse(ledger.is_terminal_loss('new'))
                with self.assertRaises(PendingPaidRequest):
                    ledger.assert_ready()

    def test_budget_halt_and_mismatched_seal_still_block(self):
        self.reserve_loss()
        self.master.seal_transport_loss('repair1:lost')
        with self.master._connect() as db:
            db.execute("UPDATE requests SET request_hash=? WHERE request_id='repair1:lost'", ('a' * 64,))
        with self.assertRaises(BudgetError):
            self.ledger.assert_ready()
        self.assertFalse(V4Ledger.is_v4_terminal_loss(self.ledger, 'lost'))
        self.ledger.halt('Offline budget halt')
        with self.assertRaises(BudgetError):
            self.ledger.assert_ready()

    def test_http_diagnostics_keep_only_fixed_labels_ids_and_size_counts(self):
        secret = 'DO-NOT-LOG-SECRET'
        body = {'id': 'gen-offline_safe', 'error': {'message': 'Image size too large ' + secret}}
        class FailedOpener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError('https://openrouter.ai/offline', 413, secret,
                                             {'X-Request-Id': 'request-safe', 'Authorization': secret},
                                             io.BytesIO(json.dumps(body).encode()))
        provider = ContinuedRepairedProvider(secret, self.ledger, opener=FailedOpener())
        self.configure_provider(provider)
        payload = {'model': 'offline-fixture', 'messages': [{'role': 'user', 'content': secret}]}
        with self.assertRaises(ProviderError):
            provider._paid('sized', 'chat', '/chat/completions', payload, 0.01)
        diagnostics = (self.ledger.path.parent / 'execution_notes' / 'transport_diagnostics_v4.jsonl').read_text()
        self.assertNotIn(secret, diagnostics)
        observed = json.loads(diagnostics)
        self.assertEqual(observed['http_status'], 413)
        self.assertEqual(observed['error_category'], 'image_count_or_size')
        self.assertEqual(observed['generation_id'], 'gen-offline_safe')
        self.assertEqual(observed['x-request-id'], 'request-safe')
        self.assertEqual(observed['request_body_utf8_bytes'], len(canonical(payload).encode()))
        self.assertFalse(observed['eligible_terminal_loss'])

    def test_known_loss_authorization_is_exact_and_repeatable(self):
        self.ledger.reserve(KNOWN_LOSS, 'chat', DEFAULT_MODELS['producer'], self.hash, KNOWN_RESERVE)
        self.ledger.uncertain(KNOWN_LOSS, LOSS_REASON)
        write_json(self.root / 'execution_notes' / 'image_route_repair_v3.json', {'offline_fixture': True})
        before = self.ledger.lookup(KNOWN_LOSS)
        first = record_authorization(self.root, self.ledger.path.parent, self.ledger)
        self.assertEqual(record_authorization(self.root, self.ledger.path.parent, self.ledger), first)
        self.assertEqual(read_json(self.root / 'execution_notes' / NOTE_NAME)['policy_sha256'], POLICY_SHA256)
        self.assertEqual(self.ledger.lookup(KNOWN_LOSS), before)
        self.assertTrue(self.ledger.is_v4_terminal_loss(KNOWN_LOSS))


class MissingTransportMock(MockProvider):
    """Full real persistence/evidence fixture; scripted values are not findings."""
    def __init__(self, plan, ledger, victims):
        super().__init__(plan, ledger)
        self.victims = set(victims)

    def chat(self, request_id, model, messages, max_output_tokens, schema=None, reasoning_effort=None):
        if request_id in self.victims:
            if self.ledger.lookup(request_id) is None:
                payload = {'messages': messages, 'schema': schema, 'max_output_tokens': max_output_tokens,
                           'reasoning': reasoning_effort}
                self.ledger.reserve(request_id, 'chat', model, digest(payload), 0.001)
                self.ledger.uncertain(request_id, LOSS_REASON)
                self.ledger.seal_transport_loss(request_id)
            raise TransportMissingTurn(missing_reason(request_id))
        return super().chat(request_id, model, messages, max_output_tokens, schema, reasoning_effort)


class EvidenceContinuationTests(unittest.TestCase):
    def test_missing_branch_neutral_and_audit_preserve_receptions_reviews_and_validate(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent, out = Path(temporary), Path(temporary) / 'repaired'
            config = read_json(Path(__file__).resolve().parents[1] / 'configs' / 'canonical.json')
            plan = prepare_run(config, out, evidence_origin='mock')
            episode = next(ep for ep in plan['episodes'] if ep['condition'] == 'explicit')
            key = episode['episode_id']
            first, second = [branch['branch_id'] for branch in episode['branches']]
            victims = {f'{key}:{first}:round4:A:step1',
                       f'{key}:{second}:round3:B:neutral:step1',
                       f'{key}:{second}:audit:latest_pair'}
            ledger = SharedContinuationLedger(out / 'budget.sqlite3', V4Ledger(parent / 'budget.sqlite3'), 'repair1')
            provider = MissingTransportMock(plan, ledger, victims)
            provider.auto_reviews = True
            with continuation_route(ledger):
                status = advance(provider, config, out)
                self.assertEqual(status['status'], 'complete_with_invalids')
                self.assertEqual(validate_evidence(out)['primary_reception_rows'], 12)
                report = build_report(out)
                self.assertEqual(report['live_observations'], 0)
                self.assertEqual(report['evidence_origin'], 'mock')
                files = list((out / 'review_jobs').glob('*/response.json'))
                reviews = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
                calls = provider.fresh_calls
                self.assertEqual(advance(provider, config, out)['status'], 'complete_with_invalids')
                self.assertEqual(provider.fresh_calls, calls)
                self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}, reviews)
                self.assertEqual(validate_evidence(out)['primary_reception_rows'], 12)
            for victim in victims:
                self.assertTrue(ledger.is_v4_terminal_loss(victim))
                self.assertFalse((out / 'failed_calls' / (digest(victim)[:24] + '.json')).exists())
            self.assertAlmostEqual(ledger.aggregate.summary()['reserved_unresolved_usd'], 0.003)
            with self.assertRaisesRegex(ValueError, 'known-billed failure'):
                validate_evidence(out)
            trajectory = read_json(out / 'trajectories' / (digest(f'{key}:{second}')[:24] + '.json'))
            self.assertEqual(trajectory['audits']['latest_pair']['reason'], missing_reason(f'{key}:{second}:audit:latest_pair'))
            self.assertEqual(trajectory['audits']['image_history']['status'], 'valid')
            # An unsealed or mislabeled missing audit must still be rejected.
            trajectory['audits']['latest_pair']['reason'] = 'Unverified missing audit'
            write_json(out / 'trajectories' / (digest(f'{key}:{second}')[:24] + '.json'), trajectory)
            with continuation_route(ledger), self.assertRaisesRegex(ValueError, 'exact mirrored v4'):
                validate_evidence(out)


if __name__ == '__main__':
    unittest.main()
