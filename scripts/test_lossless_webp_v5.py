"""Offline wire/controller/gate regressions; no live calls or ledger edits."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_provider_budget import FakeTransport, png
import run_lossless_webp_v5 as v5
import continue_repaired_execution as v4
import lossless_wire_codec as codec
from run_authorized_key import AuthorizedKeyProvider
from visual_collusion.budget import BudgetError, PendingPaidRequest
from visual_collusion.common import canonical, digest, read_json, write_json
from visual_collusion.providers import DEFAULT_MODELS, ProviderError, build_chat_payload, image_content
from visual_collusion.runtime import prepare_run


class V5ControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.master = v5.V5Ledger(self.root / 'budget.sqlite3')
        self.out = self.root / 'repaired'
        self.ledger = v5.SharedV5Ledger(self.out / 'budget.sqlite3', self.master, 'repair1')
        self.route = v5.repair.repaired_route()
        self.route.__enter__()
        self.addCleanup(self.route.__exit__, None, None, None)
        self.transport = FakeTransport(self.ledger)
        self.provider = v5.LosslessV5Provider('offline-placeholder', self.ledger, transport=self.transport)
        self.provider.preflight()
        self.messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Exact task'}, image_content(png())]}]

    def logical(self, messages=None):
        return build_chat_payload(v5.repair.MODEL, self.messages if messages is None else messages, 128)

    def test_new_chat_keeps_logical_hash_and_transmits_exact_verified_wire_once(self):
        original = deepcopy(self.messages)
        result = self.provider.chat('new.chat', v5.repair.MODEL, self.messages, 128)
        self.assertEqual(self.messages, original)
        record = self.ledger.lookup('new.chat')
        logical = self.logical()
        self.assertEqual(record['request_hash'], digest({'endpoint': '/chat/completions', 'payload': logical}))
        wire = self.transport.posts[0][2]
        self.assertTrue(wire['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/webp;base64,'))
        mapping_record = read_json(v5._wire_path(self.out, 'new.chat'))
        mapping = mapping_record['mapping']
        self.assertNotEqual(mapping['logical_request_hash'], mapping['wire_request_hash'])
        self.assertEqual(mapping['wire_body_sha256'], hashlib.sha256(canonical(wire).encode()).hexdigest())
        self.assertEqual(codec.verify_wire(self.out, '/chat/completions', logical, mapping), wire)
        self.assertEqual(result['receipt']['request_hash'], record['request_hash'])
        self.assertEqual(self.ledger.wire_binding('new.chat')['record_sha256'], digest(mapping_record))
        self.assertEqual(v5.verify_wire_records(self.out, self.ledger, [])['paid_intents_mapped'], 1)
        with patch.object(codec, 'prepare_wire', side_effect=AssertionError('Cached request reshaped')):
            self.assertTrue(self.provider.chat('new.chat', v5.repair.MODEL, self.messages, 128)['cached'])
        self.assertEqual(len(self.transport.posts), 1)

    def test_legacy_completed_request_has_no_new_wire_mapping_or_post(self):
        legacy = AuthorizedKeyProvider('offline-placeholder', self.ledger, transport=self.transport)
        legacy.preflight()
        legacy.chat('legacy', v5.repair.MODEL, self.messages, 128)
        baseline = v5._baseline(self.ledger)
        before = self.ledger.lookup('legacy')
        with patch.object(codec, 'prepare_wire', side_effect=AssertionError('Legacy request reshaped')):
            self.assertTrue(self.provider.chat('legacy', v5.repair.MODEL, self.messages, 128)['cached'])
        self.assertEqual(self.ledger.lookup('legacy'), before)
        self.assertIsNone(self.ledger.wire_binding('legacy'))
        self.assertEqual(len(self.transport.posts), 1)
        self.assertEqual(v5.verify_wire_records(self.out, self.ledger, baseline)['legacy_requests_preserved'], 1)

    def test_v4_and_v5_missing_reasons_stay_distinct_without_wire_preparation(self):
        logical_hash = digest({'endpoint': '/chat/completions', 'payload': self.logical()})
        for key, version in [('old.loss', 4), ('new.loss', 5)]:
            self.ledger.reserve(key, 'chat', v5.repair.MODEL, logical_hash, 0.01)
            self.ledger.uncertain(key, v4.LOSS_REASON)
            if version == 4:
                v4.V4Ledger.seal_transport_loss(self.master, 'repair1:' + key)
                v4.V4Ledger.seal_transport_loss(self.ledger, key)
            else:
                self.ledger.seal_transport_loss(key)
            before = self.ledger.lookup(key)
            with patch.object(codec, 'prepare_wire', side_effect=AssertionError('Lost request reshaped')):
                with self.assertRaises(v4.TransportMissingTurn) as raised:
                    self.provider.chat(key, v5.repair.MODEL, self.messages, 128)
            self.assertEqual(str(raised.exception), v4.missing_reason(key) if version == 4 else v5.missing_reason(key))
            self.assertEqual(self.ledger.lookup(key), before)
            self.assertIsNone(self.ledger.wire_binding(key))
        self.assertEqual(self.transport.posts, [])
        self.assertAlmostEqual(self.master.summary()['reserved_unresolved_usd'], 0.02)

    def test_actual_total_wire_cap_rejects_before_any_reservation_or_post(self):
        with patch.object(v5, 'MAX_WIRE_BYTES', 64):
            with self.assertRaises(codec.WireEncodingError):
                self.provider.chat('too.large', v5.repair.MODEL, self.messages, 128)
        self.assertEqual(self.ledger.summary()['request_count'], 0)
        self.assertEqual(self.master.summary()['request_count'], 0)
        self.assertEqual(self.transport.posts, [])
        self.assertFalse(v5._wire_path(self.out, 'too.large').exists())

    def test_qwen_image_payload_remains_exact_identity_with_wire_provenance(self):
        self.provider.image('new.image', 'Exact existing image prompt', DEFAULT_MODELS['image'], input_reference=png())
        record = read_json(v5._wire_path(self.out, 'new.image'))
        mapping = record['mapping']
        self.assertEqual(mapping['images'], [])
        self.assertEqual(mapping['logical_request_hash'], mapping['wire_request_hash'])
        self.assertTrue(self.transport.posts[0][2]['input_references'][0]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertEqual(v5.verify_wire_records(self.out, self.ledger, [])['paid_intents_mapped'], 1)

    def test_transient_failure_is_mapped_sealed_once_and_still_reserved(self):
        self.transport.failure = ProviderError('API HTTP 502; no automatic retry')
        with self.assertRaises(v4.TransportMissingTurn) as raised:
            self.provider.chat('failed', v5.repair.MODEL, self.messages, 128)
        self.assertEqual(str(raised.exception), v5.missing_reason('failed'))
        self.assertTrue(self.ledger.is_v5_terminal_loss('failed'))
        self.assertEqual(v5.verify_wire_records(self.out, self.ledger, [])['paid_intents_mapped'], 1)
        with self.assertRaises(v4.TransportMissingTurn):
            self.provider.chat('failed', v5.repair.MODEL, self.messages, 128)
        self.assertEqual(len(self.transport.posts), 1)
        self.assertGreater(self.master.summary()['reserved_unresolved_usd'], 0)
        self.ledger.assert_ready()

    def test_nontransient_http_does_not_seal_or_retry(self):
        self.transport.failure = ProviderError('API HTTP 401; no automatic retry')
        with self.assertRaises(ProviderError):
            self.provider.chat('unauthorized', v5.repair.MODEL, self.messages, 128)
        self.assertFalse(self.ledger.is_terminal_loss('unauthorized'))
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()
        with self.assertRaises(PendingPaidRequest):
            self.provider.chat('unauthorized', v5.repair.MODEL, self.messages, 128)
        self.assertEqual(len(self.transport.posts), 1)

    def test_unbilled_map_can_resume_preparation_but_never_authorizes_pending_retry(self):
        class StopBeforeReservation(Exception):
            pass
        reserve = self.ledger.reserve
        with patch.object(self.ledger, 'reserve', side_effect=StopBeforeReservation):
            with self.assertRaises(StopBeforeReservation):
                self.provider.chat('prepared', v5.repair.MODEL, self.messages, 128)
        self.assertEqual(v5.verify_wire_records(self.out, self.ledger, [])['unbilled_prepared_maps'], 1)
        self.assertEqual(self.transport.posts, [])
        record = read_json(v5._wire_path(self.out, 'prepared'))
        reserve('prepared', 'chat', v5.repair.MODEL, record['mapping']['logical_request_hash'], 0.01)
        with self.assertRaises(PendingPaidRequest):
            self.provider.chat('prepared', v5.repair.MODEL, self.messages, 128)
        self.assertEqual(self.transport.posts, [])

    def test_append_only_binding_and_baseline_verifier_reject_tampering(self):
        self.provider.chat('new', v5.repair.MODEL, self.messages, 128)
        path = v5._wire_path(self.out, 'new')
        record = read_json(path)
        changed = deepcopy(record)
        changed['mapping']['wire_body_sha256'] = '0' * 64
        with self.assertRaises(BudgetError):
            self.ledger.bind_wire('new', changed)
        with self.assertRaises(sqlite3.IntegrityError), self.ledger._connect() as db:
            db.execute("DELETE FROM wire_bindings_v5 WHERE request_id='new'")
        write_json(path, changed)
        with self.assertRaises(BudgetError):
            v5.verify_wire_records(self.out, self.ledger, [])
        write_json(path, record)
        with self.assertRaises(BudgetError):
            v5.verify_wire_records(self.out, self.ledger, v5._baseline(self.ledger))

    def test_exact_known401_amendment_preserves_rows_and_previous_v4_seal(self):
        old_key = v4.KNOWN_LOSS
        self.ledger.reserve(old_key, 'chat', v5.repair.MODEL, '1' * 64, v4.KNOWN_RESERVE)
        self.ledger.uncertain(old_key, v4.LOSS_REASON)
        v4.V4Ledger.seal_transport_loss(self.master, 'repair1:' + old_key)
        v4.V4Ledger.seal_transport_loss(self.ledger, old_key)
        write_json(self.root / 'execution_notes' / 'image_route_repair_v3.json', {'offline_fixture': True})
        v4.record_authorization(self.root, self.out, self.ledger)
        self.ledger.reserve(v5.KNOWN_LOSS, 'chat', v5.repair.MODEL, v5.KNOWN_HASH, v5.KNOWN_RESERVE)
        self.ledger.uncertain(v5.KNOWN_LOSS, v4.LOSS_REASON)
        diagnostic = {'request_id': v5.KNOWN_LOSS, 'http_status': 401,
                      'request_body_utf8_bytes': 10_943_403, 'image_count': 9, 'message_count': 36}
        folder = self.out / 'execution_notes'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'transport_diagnostics_v4.jsonl').write_text(json.dumps(diagnostic) + '\n')
        before = self.ledger.lookup(v5.KNOWN_LOSS)
        previous_note = (self.root / 'execution_notes' / v4.NOTE_NAME).read_bytes()
        note = v5.record_authorization(self.root, self.out, self.ledger)
        self.assertEqual(v5.record_authorization(self.root, self.out, self.ledger), note)
        self.assertEqual(self.ledger.lookup(v5.KNOWN_LOSS), before)
        self.assertTrue(self.ledger.is_v5_terminal_loss(v5.KNOWN_LOSS))
        self.assertTrue(self.ledger.is_v4_terminal_loss(old_key))
        self.assertFalse(self.ledger.is_v5_terminal_loss(old_key))
        self.assertEqual((self.root / 'execution_notes' / v4.NOTE_NAME).read_bytes(), previous_note)
        self.assertEqual(v5.verify_wire_records(self.out, self.ledger, note['existing_requests'])['legacy_requests_preserved'], 2)


class FormatGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.master = v5.V5Ledger(self.root / 'budget.sqlite3')
        cards = self.root / 'infrastructure_checks'
        cards.mkdir()
        (cards / 'vision_1.png').write_bytes(png(color=(10, 20, 30)))
        (cards / 'vision_2.png').write_bytes(png(color=(40, 50, 60)))
        self.posts = []
        self.wrong_first = False
        original = v5.LosslessV5Provider
        def factory(key, ledger, **kwargs):
            if 'transport' in kwargs:
                return original(key, ledger, **kwargs)
            transport = FakeTransport(ledger)
            def coded_transport(method, path, payload):
                response = transport(method, path, payload)
                if method == 'POST':
                    self.posts.append(deepcopy(payload))
                    self.assertTrue(payload['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/webp;base64,'))
                    code = '731946' if payload['model'] == DEFAULT_MODELS['producer'] else '284517'
                    if self.wrong_first:
                        code = '000000'
                    response['choices'][0]['message']['content'] = json.dumps({'code': code})
                return response
            return original(key, ledger, transport=coded_transport)
        provider_patch = patch.object(v5, 'LosslessV5Provider', side_effect=factory)
        provider_patch.start()
        self.addCleanup(provider_patch.stop)
        env_patch = patch.dict(os.environ, {'OPENROUTER_API_KEY': 'offline-placeholder'})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.route = v5.repair.repaired_route()
        self.route.__enter__()
        self.addCleanup(self.route.__exit__, None, None, None)

    def test_two_separate_scoped_checks_pass_once_and_gate_reverification_never_posts(self):
        result = v5.validate_transport(self.root, self.master)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(len(self.posts), 2)
        self.assertEqual({p['model'] for p in self.posts}, {DEFAULT_MODELS['producer'], DEFAULT_MODELS['monitor']})
        self.assertFalse((self.root / 'repaired' / 'budget.sqlite3').exists())
        status_file = self.root / v5.INFRA_DIRECTORY / 'latest_status.json'
        before = status_file.read_bytes()
        self.assertEqual(v5.validate_transport(self.root, self.master), result)
        self.assertEqual(v5.verify_format_gate(self.root, self.master), result)
        self.assertEqual(status_file.read_bytes(), before)
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(self.master.summary()['request_count'], 2)
        self.assertEqual(self.master.summary()['completed_count'], 2)

    def test_failed_first_compatibility_response_is_retained_and_never_retried(self):
        self.wrong_first = True
        with self.assertRaises(ValueError):
            v5.validate_transport(self.root, self.master)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.master.summary()['completed_count'], 1)
        self.assertTrue((self.root / v5.INFRA_DIRECTORY / 'failure.json').exists())
        self.assertFalse((self.root / v5.INFRA_DIRECTORY / 'latest_status.json').exists())
        self.wrong_first = False
        with self.assertRaises(BudgetError):
            v5.validate_transport(self.root, self.master)
        self.assertEqual(len(self.posts), 1)

    def test_canonical_export_contains_scoped_compatibility_wire_proof_and_dependencies(self):
        v5.validate_transport(self.root, self.master)
        out = self.root / 'repaired'
        config = read_json(Path(__file__).resolve().parents[1] / 'configs' / 'canonical.json')
        prepare_run(config, out, evidence_origin='mock')
        ledger = v5.SharedV5Ledger(out / 'budget.sqlite3', self.master, 'repair1')
        for name in ('image_route_repair_v3.json', v4.NOTE_NAME, v5.NOTE_NAME):
            write_json(self.root / 'execution_notes' / name, {'offline_fixture': True, 'name': name})
        destination = self.root / 'delivery.zip'
        with v5.transport_route(ledger, []):
            self.assertEqual(v5.validate_evidence(out)['calls'], 0)
            scope = v5.save_delivery_scope(self.root, out, ledger, [])
            snapshot = out / 'execution_notes' / 'aggregate_budget_snapshot.sqlite3'
            v5._sqlite_snapshot(self.master.path, snapshot)
            self.assertEqual(v5.export_run(out, destination)['status'], 'exported')
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(scope['wire_verification']['paid_intents_mapped'], 0)
        with zipfile.ZipFile(destination) as archive:
            prefix = 'execution_notes/' + v5.INFRA_DIRECTORY + '/'
            self.assertIn(prefix + 'budget_snapshot.sqlite3', archive.namelist())
            self.assertNotIn(prefix + 'budget.sqlite3', archive.namelist())
            self.assertIn('execution_notes/aggregate_budget_snapshot.sqlite3', archive.namelist())
            self.assertIn('execution_notes/host_tools/lossless_wire_codec.py', archive.namelist())
            self.assertIn('execution_notes/host_tools/run_authorized_key.py', archive.namelist())
            self.assertIn('execution_notes/host_tools/run_continue_independent.py', archive.namelist())
            gate = json.loads(archive.read(prefix + 'latest_status.json'))
            self.assertEqual(len(gate['checks']), 2)
            extracted = self.root / 'extracted'
            archive.extractall(extracted)
        copied = extracted / 'execution_notes' / v5.INFRA_DIRECTORY
        copied_ledger = v5.SharedV5Ledger(copied / 'budget_snapshot.sqlite3', self.master, v5.INFRA_NAMESPACE)
        self.assertEqual(v5.verify_wire_records(copied, copied_ledger, [])['paid_intents_mapped'], 2)
        self.assertEqual(self.master.summary()['request_count'], 2)


if __name__ == '__main__':
    unittest.main()
