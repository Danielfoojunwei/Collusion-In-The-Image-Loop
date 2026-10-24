"""Offline chronology, privacy and unchanged-history checks for the v5 runner."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_triad_format_v5 as runner


class MemoryRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def engine(self):
        engine = runner.OwnLogRecoveryEngine.__new__(runner.OwnLogRecoveryEngine)
        engine._recovery_loader = Mock(side_effect=lambda role: {
            "private_note": role + " recovered private note",
            "technical_recovery": {"own_role": role, "supplement_sha256": role * 64,
                                   "learned_after_round_3_ballots": True}})
        return engine

    def test_round_two_or_earlier_inputs_never_gain_recovered_notes(self):
        engine = self.engine()
        logs = [{"round": 1, "private_carryover_log": "original"}]
        with patch.object(runner.TriadEngine, "_own_logs", return_value=logs):
            self.assertEqual(engine._own_logs(2, "A"), logs)
        engine._recovery_loader.assert_not_called()

    def test_only_missing_round_two_same_role_entry_is_replaced(self):
        engine = self.engine()
        logs = [{"round": 1, "private_carryover_log": "original first note"},
                {"round": 2, "carryover_log_unavailable": True},
                {"round": 3, "private_carryover_log": "original later note"}]
        unchanged = deepcopy(logs)
        with patch.object(runner.TriadEngine, "_own_logs", return_value=logs):
            view = engine._own_logs(4, "A")
        self.assertEqual(logs, unchanged)
        self.assertEqual(view[0], unchanged[0])
        self.assertEqual(view[2], unchanged[2])
        self.assertEqual(view[1]["private_carryover_log"], "A recovered private note")
        self.assertTrue(view[1]["technical_recovery"]["learned_after_round_3_ballots"])
        engine._recovery_loader.assert_called_once_with("A")

    def test_B_receives_its_own_note_and_C_has_no_recovery_loader_access(self):
        engine = self.engine()
        logs = [{"round": 2, "carryover_log_unavailable": True}]
        with patch.object(runner.TriadEngine, "_own_logs", return_value=logs):
            b_view = engine._own_logs(3, "B")
            c_view = engine._own_logs(3, "C")
        self.assertEqual(b_view[0]["private_carryover_log"], "B recovered private note")
        self.assertNotIn("A recovered", json.dumps(b_view))
        self.assertEqual(c_view, logs)
        engine._recovery_loader.assert_called_once_with("B")

    def test_existing_valid_round_two_log_is_not_replaced(self):
        engine = self.engine()
        logs = [{"round": 2, "private_carryover_log": "already valid"}]
        with patch.object(runner.TriadEngine, "_own_logs", return_value=logs):
            self.assertEqual(engine._own_logs(3, "A"), logs)
        engine._recovery_loader.assert_not_called()

    def test_engine_config_prompts_and_case_are_identical_to_frozen_builder(self):
        coordinator = runner.MemoryAmendedCoordinator.__new__(runner.MemoryAmendedCoordinator)
        coordinator.out = self.root
        coordinator._engines = {}
        coordinator.config = {"rounds": 12, "unchanged": "value"}
        coordinator.evidence_origin = "live"
        case = {"case_id": "case_05", "condition": "explicit", "task": {"brief": "unchanged"}}
        prompts = {"A": "a", "B": "b", "C": "c", "reviewer": "r"}
        with patch.object(runner.original, "load_prompts", return_value=prompts), patch.object(runner, "OwnLogRecoveryEngine") as factory:
            first = coordinator._engine(case)
            second = coordinator._engine(case)
        self.assertIs(first, second)
        factory.assert_called_once_with(self.root / "cases/case_05",
            {"rounds": 12, "unchanged": "value", "prompts": prompts, "reviewer_prompt": "r", "evidence_origin": "live"},
            case, recovery_loader=coordinator._recovery_for)
        self.assertEqual(coordinator.config, {"rounds": 12, "unchanged": "value"})
        self.assertIs(runner.MemoryAmendedCoordinator.advance, runner.frozen_v4.FormatAmendedCoordinator.advance)

    def test_backend_selection_only_uses_v5_identical_constructor(self):
        calls = []
        class FakeV5:
            def __init__(self, config, out, aggregate):
                calls.append((config, out, aggregate))
        coordinator = runner.MemoryAmendedCoordinator.__new__(runner.MemoryAmendedCoordinator)
        coordinator.out = self.root
        coordinator.config = {"budget": {"aggregate_ledger": "runs/main/budget.sqlite3"}}
        coordinator.backend = None
        coordinator.verify_memory_amendment = Mock()
        with patch.object(runner, "_v5_module", return_value=SimpleNamespace(FormatV5Backend=FakeV5)):
            first = coordinator._backend()
            second = coordinator._backend()
        self.assertIs(first, second)
        self.assertEqual(calls, [(coordinator.config, self.root, ROOT / "runs/main/budget.sqlite3")])

    def test_private_notes_are_not_added_to_reviewer_payload_function(self):
        self.assertIs(runner.OwnLogRecoveryEngine._reviewer_payload, runner.TriadEngine._reviewer_payload)
        self.assertIs(runner.OwnLogRecoveryEngine.export_reviewer_packet, runner.TriadEngine.export_reviewer_packet)

    def record(self, role):
        return {"request_id": f"triad_consensus_v2:case_05.r02.POSTVERDICT_{role}_LOG.step1",
            "original_result_hash": role * 64, "parsed_original_value": {"properties": {"private_note": role + " full note"}},
            "unwrapped_original_value": {"private_note": role + " full note"},
            "context_value": {"private_note": role + " full note"},
            "schema_flags": ["memory_only_exact_properties_private_note_wrapper"],
            "syntax_flags": [], "original_text_sha256": "f" * 64}

    def test_supplement_must_match_exact_same_role_paid_record(self):
        record = self.record("A")
        path = self.root / "A.json"
        path.write_text(json.dumps(dict(record, semantic_fields_inferred_or_changed=False)))
        adapter = SimpleNamespace(recover_completed_memory_response=Mock(return_value=record))
        with patch.object(runner, "_v5_module", return_value=adapter), patch.object(runner.frozen_v4, "_adapter_module", return_value=SimpleNamespace(completed_row=Mock(return_value={}))):
            self.assertEqual(runner.verified_memory_supplement(self.root / "ledger", path, "A")["context_value"]["private_note"], "A full note")
            with self.assertRaisesRegex(ValueError, "different role"):
                runner.verified_memory_supplement(self.root / "ledger", path, "B")

    def test_reviewer_and_C_cannot_request_A_B_supplement(self):
        for role in ("C", "REVIEWER"):
            with self.assertRaisesRegex(ValueError, "Only A and B"):
                runner.verified_memory_supplement(self.root / "ledger", self.root / "missing", role)

    def test_all_old_request_observed_outcome_and_packet_bytes_are_hashed_without_writes(self):
        paths = ["cases/case_05/rounds/r03/A_IMAGE_AND_VOTE.request.json",
                 "cases/case_05/rounds/r02/POSTVERDICT_A_LOG.observed.json",
                 "cases/case_05/rounds/r02/POSTVERDICT_B_LOG.outcome.json",
                 "cases/case_05/rounds/r03/REVIEWER.dispatch.json",
                 "cases/case_05/reviewer_packets/r03/packet.json",
                 "provider_evidence/stage_requests/fixture.json"]
        for name in paths:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"fixture": name}))
        mutable = self.root / "run_status.json"
        mutable.write_text("{}")
        before = {name: (self.root / name).read_bytes() for name in paths}
        hashed = runner.preserved_scientific_files(self.root)
        self.assertEqual(set(hashed), set(paths))
        for name, raw in before.items():
            self.assertEqual(hashed[name], hashlib.sha256(raw).hexdigest())
            self.assertEqual((self.root / name).read_bytes(), raw)
        self.assertNotIn("run_status.json", hashed)

    def test_actual_paid_A_B_supplements_replay_exact_notes_without_POST(self):
        ledger = ROOT / "runs/main/budget.sqlite3"
        for role, size in (("A", 669), ("B", 667)):
            path = ROOT / f"runs/main/triad_consensus_live/execution_notes/format_v5/round02_{role}_memory_technical_supplement.json"
            recovered = runner.verified_memory_supplement(ledger, path, role)
            self.assertEqual(len(recovered["context_value"]["private_note"].encode()), size)
            self.assertEqual(recovered["unwrapped_original_value"], recovered["context_value"])
            self.assertFalse(recovered["new_POST_or_repair_query"])


if __name__ == "__main__":
    unittest.main()
