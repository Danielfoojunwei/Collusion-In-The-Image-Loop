"""Offline regression checks for the infrastructure amendment and aggregate cap."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_provider_budget import FakeTransport, png
from repair_image_route import MODEL, REPAIRED_PROFILE, SharedBudgetLedger, RepairedProvider, export_repaired, repaired_route, unwrap_json_fence
from run_authorized_key import AuthorizedKeyProvider
from run_continue_independent import ContinuationLedger
from visual_collusion.budget import BudgetError, BudgetLedger, PendingPaidRequest, request_digest
from visual_collusion.common import read_json, write_json
from visual_collusion.providers import CHAT_PROFILES, ProviderError, ResponseValidationError, image_content
from visual_collusion.runtime import prepare_run
from visual_collusion.cli import export_run


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.master = ContinuationLedger(self.root / 'aggregate.sqlite3', 40)
        self.ledger = SharedBudgetLedger(self.root / 'local.sqlite3', self.master, 'repair1')

    def tearDown(self):
        self.temp.cleanup()

    def reserve(self, key='one', amount=0.1):
        return self.ledger.reserve(key, 'chat', MODEL, request_digest({'key': key}), amount)

    def test_route_override_is_process_local_and_keeps_model_quantization_and_limits(self):
        old = deepcopy(CHAT_PROFILES[MODEL])
        with repaired_route():
            self.assertEqual(CHAT_PROFILES[MODEL]['provider_tag'], 'open-inference/fp4')
            for key in old.keys() - {'provider_tag', 'provider_name'}:
                self.assertEqual(CHAT_PROFILES[MODEL][key], old[key])
        self.assertEqual(CHAT_PROFILES[MODEL], old)

    def test_preexisting_charge_and_uncertain_reservation_are_preserved_and_counted(self):
        self.master.reserve('old.completed', 'chat', MODEL, request_digest({}), 1)
        self.master.complete('old.completed', 0.5, {}, {})
        self.master.reserve('old.lost', 'chat', MODEL, request_digest({}), 1)
        self.master.uncertain('old.lost', 'POST interrupted or failed; billing outcome unresolved')
        self.master.seal_transport_loss('old.lost')
        original = self.master.lookup('old.lost')
        self.reserve()
        self.assertEqual(self.master.lookup('old.lost'), original)
        self.assertAlmostEqual(self.master.summary()['committed_usd'], 1.6)
        self.assertEqual(self.ledger.summary()['request_count'], 1)

    def test_aggregate_cap_blocks_when_local_budget_has_room(self):
        self.master.reserve('prior', 'chat', MODEL, request_digest({}), 39.9)
        self.master.complete('prior', 39.9, {}, {})
        with self.assertRaises(BudgetError):
            self.reserve(amount=0.2)
        self.assertEqual(self.ledger.summary()['request_count'], 0)

    def test_aggregate_only_crash_blocks_duplicate_request(self):
        self.master.reserve('repair1:one', 'chat', MODEL, request_digest({'key': 'one'}), 0.1)
        with self.assertRaises(PendingPaidRequest):
            self.ledger.lookup('one')
        with self.assertRaises(PendingPaidRequest):
            self.reserve()
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()

    def test_response_written_to_aggregate_recovers_without_post(self):
        self.reserve()
        self.master.complete('repair1:one', 0.01, {'saved': True}, {'cost_usd': 0.01})
        recovered = self.ledger.lookup('one')
        self.assertEqual(recovered['status'], 'completed')
        self.assertEqual(recovered['response'], {'saved': True})
        self.assertAlmostEqual(self.master.summary()['actual_usd'], 0.01)

    def test_readiness_recovers_exact_completed_aggregate_receipt_before_lookup(self):
        self.reserve()
        self.master.complete('repair1:one', 0.01, {'saved': True}, {'cost_usd': 0.01})
        self.ledger.assert_ready()
        self.assertEqual(BudgetLedger.lookup(self.ledger, 'one')['status'], 'completed')
        self.assertEqual(self.master.summary()['request_count'], 1)

    def test_preflight_recovers_aggregate_first_crash_without_post_or_explicit_lookup(self):
        self.reserve()
        self.master.complete('repair1:one', 0.01, {'saved': True}, {'cost_usd': 0.01})
        with repaired_route():
            transport = FakeTransport(self.ledger)
            provider = RepairedProvider('offline-placeholder', self.ledger, transport=transport)
            self.assertEqual(provider.preflight()['status'], 'ready')
        self.assertEqual(BudgetLedger.lookup(self.ledger, 'one')['status'], 'completed')
        self.assertEqual(transport.posts, [])

    def test_readiness_rejects_mismatched_or_corrupt_aggregate_receipt(self):
        self.reserve()
        self.master.complete('repair1:one', 0.01, {'saved': True}, {'cost_usd': 0.01})
        with self.master._connect() as db:
            db.execute("UPDATE requests SET request_hash=? WHERE request_id='repair1:one'", ('0' * 64,))
        with self.assertRaises(BudgetError):
            self.ledger.assert_ready()
        self.assertEqual(BudgetLedger.lookup(self.ledger, 'one')['status'], 'pending')
        with self.master._connect() as db:
            db.execute("UPDATE requests SET request_hash=?,receipt_json='{}' WHERE request_id='repair1:one'",
                       (request_digest({'key': 'one'}),))
        with self.assertRaises(BudgetError):
            self.ledger.assert_ready()
        self.assertEqual(BudgetLedger.lookup(self.ledger, 'one')['status'], 'pending')

    def test_image_transport_and_cached_resume_use_new_route_once(self):
        with repaired_route():
            transport = FakeTransport(self.ledger)
            provider = AuthorizedKeyProvider('offline-placeholder', self.ledger, transport=transport)
            provider.preflight()
            messages = [{'role': 'user', 'content': [image_content(png())]}]
            first = provider.chat('vision', MODEL, messages, 128)
            second = provider.chat('vision', MODEL, messages, 128)
            self.assertFalse(first['cached'])
            self.assertTrue(second['cached'])
            self.assertEqual(len(transport.posts), 1)
            routing = transport.posts[0][2]['provider']
            self.assertEqual(routing['only'], ['open-inference/fp4'])
            self.assertFalse(routing['allow_fallbacks'])
            self.assertEqual(self.master.summary()['completed_count'], 1)

    def test_repaired_failure_blocks_all_further_paid_work(self):
        with repaired_route():
            transport = FakeTransport(self.ledger)
            provider = AuthorizedKeyProvider('offline-placeholder', self.ledger, transport=transport)
            provider.preflight()
            transport.failure = TimeoutError('offline simulated failure')
            with self.assertRaises(ProviderError):
                provider.chat('failed', MODEL, [{'role': 'user', 'content': 'test'}], 128)
            with self.assertRaises(PendingPaidRequest):
                provider.chat('next', MODEL, [{'role': 'user', 'content': 'test'}], 128)
            self.assertEqual(len(transport.posts), 1)
            self.assertGreater(self.master.summary()['reserved_unresolved_usd'], 0)

    def test_framing_accepts_only_a_whole_json_object_fence(self):
        self.assertEqual(unwrap_json_fence('```json\n{"code":"123456"}\n```')[1], {'code': '123456'})
        for text in ('explanation\n```json\n{}\n```', '```json\n{}\n```\nmore',
                     '```json\n[]\n```', '```json\n{"x":NaN}\n```', '```json\n{bad}\n```'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                unwrap_json_fence(text)

    def test_fenced_response_is_recovered_without_rebilling_and_raw_is_unchanged(self):
        with repaired_route():
            transport = FakeTransport(self.ledger)
            def fenced_transport(method, path, payload):
                response = transport(method, path, payload)
                if method == 'POST' and path == '/chat/completions':
                    response['choices'][0]['message']['content'] = '```json\n{"decision":"review"}\n```'
                return response
            provider = RepairedProvider('offline-placeholder', self.ledger, transport=fenced_transport)
            provider.preflight()
            messages = [{'role': 'user', 'content': 'test'}]
            result = provider.chat('fenced', MODEL, messages, 128)
            cached = provider.chat('fenced', MODEL, messages, 128)
            self.assertEqual(result['content'], {'decision': 'review'})
            self.assertTrue(cached['cached'])
            self.assertEqual(len(transport.posts), 1)
            self.assertTrue(self.ledger.lookup('fenced')['response']['choices'][0]['message']['content'].startswith('```json'))

    def test_malformed_billed_replies_remain_invalid_and_cached_without_parser_errors(self):
        invalid_choices = [None, {}, 'x', [None], [],
                           [{'finish_reason': 'stop'}],
                           [{'finish_reason': 'stop', 'message': None}],
                           [{'finish_reason': 'length', 'message': {'content': '```json\n{}\n```'}}],
                           [{'finish_reason': 'stop', 'message': {'content': None}}],
                           [{'finish_reason': 'stop', 'message': {'content': '```json\n{}\n```', 'refusal': True}}],
                           [{'finish_reason': 'stop', 'message': {'content': '```json\n[]\n```'}}],
                           [{'finish_reason': 'stop', 'message': {'content': '```json\n{"x":NaN}\n```'}}]]
        with repaired_route():
            transport = FakeTransport(self.ledger)
            current = {}
            def malformed_transport(method, path, payload):
                response = transport(method, path, payload)
                if method == 'POST':
                    response['choices'] = deepcopy(current['choices'])
                return response
            provider = RepairedProvider('offline-placeholder', self.ledger, transport=malformed_transport)
            provider.preflight()
            for number, choices in enumerate(invalid_choices):
                key = f'invalid.{number}'
                current['choices'] = choices
                with self.subTest(choices=choices):
                    for _ in range(2):
                        with self.assertRaises(ResponseValidationError) as raised:
                            provider.chat(key, MODEL, [{'role': 'user', 'content': 'test'}], 128)
                        record = self.ledger.lookup(key)
                        self.assertEqual(record['status'], 'completed')
                        self.assertEqual(record['response']['choices'], choices)
                        self.assertEqual(raised.exception.receipt, record['receipt'])
                    self.assertEqual(len(transport.posts), number + 1)

    def test_export_validates_with_repaired_route_and_backs_up_aggregate_scope(self):
        out = self.root / 'repaired'
        config = read_json(Path(__file__).resolve().parents[1] / 'configs' / 'canonical.json')
        prepare_run(config, out, evidence_origin='mock')
        self.master.reserve('prior.failed', 'chat', MODEL, request_digest({}), 0.1)
        self.master.uncertain('prior.failed', 'Retained offline missing receipt')
        note = {'user_instruction': 'offline fixture', 'version': 3}
        write_json(self.root / 'execution_notes' / 'image_route_repair_v3.json', note)
        original = deepcopy(CHAT_PROFILES[MODEL])
        def checked_export(run, destination):
            self.assertEqual(CHAT_PROFILES[MODEL]['provider_tag'], REPAIRED_PROFILE['provider_tag'])
            return export_run(run, destination)
        destination = self.root / 'delivery.zip'
        with patch('repair_image_route.export_run', checked_export):
            result = export_repaired(self.root, out, self.master, destination)
        self.assertEqual(CHAT_PROFILES[MODEL], original)
        self.assertEqual(result['status'], 'exported')
        with zipfile.ZipFile(destination) as archive:
            scope = json.loads(archive.read('execution_notes/aggregate_budget.json'))
            self.assertIn('SUBSET', scope['budget_relationship'])
            self.assertEqual(scope['aggregate_budget']['request_count'], 1)
            self.assertEqual(scope['repaired_execution_budget']['request_count'], 0)
            self.assertEqual(json.loads(archive.read('execution_notes/image_route_repair_v3.json')), note)
            snapshot = archive.read(scope['aggregate_snapshot']['path'])
            self.assertEqual(hashlib.sha256(snapshot).hexdigest(), scope['aggregate_snapshot']['sha256'])
            self.assertIn('execution_notes/repair_image_route.py', archive.namelist())
        backup = out / scope['aggregate_snapshot']['path']
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute('SELECT request_id,status FROM requests').fetchone(), ('prior.failed', 'uncertain'))
        self.assertEqual(self.master.lookup('prior.failed')['status'], 'uncertain')


if __name__ == '__main__':
    unittest.main()
