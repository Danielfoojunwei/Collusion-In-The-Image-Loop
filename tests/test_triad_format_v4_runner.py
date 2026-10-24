"""Offline tests for format-adapter selection and immutable activation bindings."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_triad_format_v4 as runner


class FormatRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.aggregate = ROOT / "runs/main/budget.sqlite3"
        with sqlite3.connect(cls.aggregate.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            cls.table_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='requests'").fetchone()[0]
            cls.rows = [dict(row) for row in db.execute("SELECT * FROM requests WHERE request_id LIKE 'triad_consensus_v2:%'")]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = self.root / "ledger.sqlite3"
        with sqlite3.connect(self.ledger) as db:
            db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("INSERT INTO settings VALUES('cap_units','40000000000')")
            db.execute(self.table_sql)
            fields = list(self.rows[0])
            sql = "INSERT INTO requests (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")"
            db.executemany(sql, [[row[key] for key in fields] for row in self.rows])

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_retains_completed_raw_response_bindings(self):
        value = runner.completed_snapshot(self.ledger)
        self.assertEqual(len(value), len(self.rows))
        self.assertEqual(value["triad_consensus_v2:case_05.r01.C_VOTE.step1"]["actual_units"], 93765)

    def test_snapshot_rejects_pending_call(self):
        with sqlite3.connect(self.ledger) as db:
            db.execute("UPDATE requests SET status='pending' WHERE request_id=?", (self.rows[0]["request_id"],))
        with self.assertRaisesRegex(ValueError, "unresolved paid"):
            runner.completed_snapshot(self.ledger)

    def test_snapshot_rejects_raw_response_hash_tampering(self):
        with sqlite3.connect(self.ledger) as db:
            db.execute("UPDATE requests SET response_json='{}' WHERE request_id=?", (self.rows[0]["request_id"],))
        with self.assertRaisesRegex(ValueError, "checksum"):
            runner.completed_snapshot(self.ledger)

    def test_snapshot_rejects_wrong_paid_amount(self):
        with sqlite3.connect(self.ledger) as db:
            db.execute("UPDATE requests SET actual_units=actual_units+1 WHERE request_id=?", (self.rows[0]["request_id"],))
        with self.assertRaisesRegex(ValueError, "paid amount"):
            runner.completed_snapshot(self.ledger)

    def test_only_backend_selection_changes_and_original_ctor_is_preserved(self):
        constructed = []
        class FakeFormatBackend:
            def __init__(self, config, out, aggregate_path):
                constructed.append((config, out, aggregate_path))
        coordinator = runner.FormatAmendedCoordinator.__new__(runner.FormatAmendedCoordinator)
        coordinator.out = self.root
        coordinator.config = {"budget": {"aggregate_ledger": "runs/main/budget.sqlite3"}}
        coordinator.backend = None
        coordinator.verify_format_amendment = Mock(return_value={"status": "format_amendment_verified"})
        adapter = SimpleNamespace(FormatV4Backend=FakeFormatBackend)
        with patch.object(runner, "_adapter_module", return_value=adapter):
            first = coordinator._backend()
            second = coordinator._backend()
        self.assertIs(first, second)
        self.assertEqual(len(constructed), 1)
        self.assertEqual(constructed[0], (coordinator.config, self.root, self.aggregate))
        self.assertEqual(coordinator.verify_format_amendment.call_count, 2)
        self.assertIs(runner.FormatAmendedCoordinator.advance, runner.gate_v3.GateAmendedCoordinator.advance)

    def test_old_backend_cannot_bypass_format_activation(self):
        coordinator = runner.FormatAmendedCoordinator.__new__(runner.FormatAmendedCoordinator)
        coordinator.backend = object()
        coordinator.verify_format_amendment = Mock()
        with patch.object(runner, "_adapter_module", return_value=SimpleNamespace(FormatV4Backend=type("Expected", (), {}))):
            with self.assertRaisesRegex(ValueError, "older backend"):
                coordinator._backend()

    def test_wrong_module_path_is_rejected(self):
        with patch.object(runner.importlib, "import_module", return_value=SimpleNamespace(__file__=str(self.root / "wrong.py"))):
            with self.assertRaisesRegex(ValueError, "different module"):
                runner._adapter_module()

    def test_first_C_supplement_preserves_P4_even_when_rationale_names_P1(self):
        record = {"request_id": "triad_consensus_v2:case_05.r01.C_VOTE.step1", "original_result_hash": "a" * 64,
            "parsed_original_value": {"decision": "P4", "rationale": "The factual choice is P1"},
            "context_value": {"decision": "P4", "rationale": "The factual choice is P1"},
            "syntax_flags": ["one_extraneous_terminal_closing_brace"], "original_text_sha256": "b" * 64}
        supplement = dict(record, explicit_decision="P4", decision_inferred_or_corrected=False)
        path = self.root / "supplement.json"
        path.write_text(json.dumps(supplement))
        adapter = SimpleNamespace(completed_row=Mock(return_value={}), recover_completed_response=Mock(return_value=record))
        with patch.object(runner, "_adapter_module", return_value=adapter):
            output = runner.verify_first_C_supplement(self.ledger, path)
        self.assertEqual(output["parsed_original_value"]["decision"], "P4")

    def test_first_C_supplement_cannot_infer_P1_from_the_rationale(self):
        record = {"request_id": "triad_consensus_v2:case_05.r01.C_VOTE.step1", "original_result_hash": "a" * 64,
            "parsed_original_value": {"decision": "P4", "rationale": "The factual choice is P1"},
            "context_value": {"decision": "P4", "rationale": "The factual choice is P1"},
            "syntax_flags": [], "original_text_sha256": "b" * 64}
        supplement = dict(record, explicit_decision="P1", decision_inferred_or_corrected=True)
        path = self.root / "supplement.json"
        path.write_text(json.dumps(supplement))
        adapter = SimpleNamespace(completed_row=Mock(return_value={}), recover_completed_response=Mock(return_value=record))
        with patch.object(runner, "_adapter_module", return_value=adapter):
            with self.assertRaisesRegex(ValueError, "inferred or changed"):
                runner.verify_first_C_supplement(self.ledger, path)

    def test_changed_original_parsed_decision_is_rejected(self):
        record = {"parsed_original_value": {"decision": "P1"}}
        adapter = SimpleNamespace(completed_row=Mock(return_value={}), recover_completed_response=Mock(return_value=record))
        with patch.object(runner, "_adapter_module", return_value=adapter):
            with self.assertRaisesRegex(ValueError, "actual P4"):
                runner.verify_first_C_supplement(self.ledger, self.root / "unused.json")

    def binding_fixture(self):
        coordinator = runner.FormatAmendedCoordinator.__new__(runner.FormatAmendedCoordinator)
        coordinator.out = self.root / "binding_fixture"
        coordinator.out.mkdir()
        coordinator.verify_amendment = Mock()
        old_packet = coordinator.out / "cases/case_05/reviewer_packets/r02/packet.json"
        old_packet.parent.mkdir(parents=True)
        old_packet.write_text('{"fixture":"original queued reviewer packet"}')
        freeze = coordinator.out / "freeze.json"
        freeze.write_text('{"fixture":"original freeze"}')
        gate_note = coordinator._amendment_path()
        gate_note.parent.mkdir(parents=True)
        gate_note.write_text('{"fixture":"existing gate amendment"}')
        supplement = coordinator.out / "supplement.json"
        supplement.write_text('{"fixture":"separately verified supplement"}')
        initial = {"original_request": {"result_hash": "a" * 64}}
        note = {"runner_sha256": runner.original.file_digest(runner.__file__),
            "adapter_sha256": runner.original.file_digest(runner.ADAPTER_PATH),
            "gate_v3_sha256": runner.original.file_digest(runner.gate_v3.__file__),
            "gate_v3_amendment_sha256": runner.original.file_digest(gate_note),
            "original_freeze_sha256": runner.original.file_digest(freeze),
            "original_source_files": runner.original.source_manifest(),
            "ledger_path": str(self.aggregate), "completed_requests_before_format_activation": initial,
            "preserved_pre_activation_files": {str(old_packet.relative_to(coordinator.out)): runner.original.file_digest(old_packet)},
            "first_C_technical_supplement": {"path": str(supplement), "sha256": runner.original.file_digest(supplement)},
            "reviewer_addendum": None}
        runner.original.save_once(coordinator._format_amendment_path(), note)
        return coordinator, note, initial, old_packet

    def test_old_queued_packet_tamper_blocks_format_verification(self):
        coordinator, _, initial, packet = self.binding_fixture()
        packet.write_text('{"fixture":"rewritten packet"}')
        with patch.object(runner, "completed_snapshot", return_value=initial), patch.object(runner, "verify_first_C_supplement"):
            with self.assertRaisesRegex(ValueError, "queued reviewer packet changed"):
                coordinator.verify_format_amendment()

    def test_changed_prior_paid_response_blocks_format_verification(self):
        coordinator, _, initial, _ = self.binding_fixture()
        changed = deepcopy(initial)
        changed["original_request"]["result_hash"] = "b" * 64
        with patch.object(runner, "completed_snapshot", return_value=changed), patch.object(runner, "verify_first_C_supplement"):
            with self.assertRaisesRegex(ValueError, "pre-format completed request changed"):
                coordinator.verify_format_amendment()

    def test_future_completed_calls_do_not_erase_or_invalidate_old_binding(self):
        coordinator, _, initial, _ = self.binding_fixture()
        extended = dict(initial, later_scheduled_request={"result_hash": "c" * 64})
        with patch.object(runner, "completed_snapshot", return_value=extended), patch.object(runner, "verify_first_C_supplement"):
            result = coordinator.verify_format_amendment()
        self.assertEqual(result["pre_activation_requests_preserved"], 1)
        self.assertEqual(result["current_completed_requests"], 2)

    def test_adapter_source_change_blocks_format_verification(self):
        coordinator, note, _, _ = self.binding_fixture()
        note["adapter_sha256"] = "0" * 64
        coordinator._format_amendment_path().write_text(json.dumps(note))
        with self.assertRaisesRegex(ValueError, "adapter changed"):
            coordinator.verify_format_amendment()


if __name__ == "__main__":
    unittest.main()
