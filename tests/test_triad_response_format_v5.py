"""Offline exact-wrapper regression tests; no actual POSTs or secret access."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from visual_collusion.budget import BudgetLedger
from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard
from experiments.pressure_interview_v1.planning import load_config
from experiments.pressure_interview_v1.tests.test_live_provider import ScriptedWireTransport
from experiments.triad_consensus_v2.backend import MODEL, NAMESPACE
from scripts import triad_response_format_v4 as v4
from scripts.triad_response_format_v5 import (
    FormatV5Backend, recover_memory_wrapper, recover_completed_memory_response,
    build_posthoc_memory_supplement,
)


class WrapperTests(unittest.TestCase):
    def test_exact_properties_note_and_optional_literal_true(self):
        for wrapped in [{"properties": {"private_note": "actual authored note"}},
                        {"properties": {"private_note": "actual authored note"}, "property": True}]:
            result = recover_memory_wrapper(wrapped)
            self.assertEqual(result["value"], {"private_note": "actual authored note"})
            self.assertIn("memory_only_exact_properties_private_note_wrapper", result["schema_flags"])
            self.assertEqual(len(result["schema_flags"]), 2 if "property" in wrapped else 1)

    def test_rejects_all_broader_wrappers_and_false_or_numeric_markers(self):
        bad = [
            {"private_note": "already plain"},
            {"properties": {"private_note": "note"}, "property": False},
            {"properties": {"private_note": "note"}, "property": 1},
            {"properties": {"private_note": "note"}, "property": "true"},
            {"properties": {"private_note": "note"}, "description": "extra"},
            {"properties": {"private_note": "note", "decision": "P1"}},
            {"properties": {"private_note": {"type": "string", "default": "not an actual note"}}},
            {"properties": {"private_note": None}},
            {"properties": {"properties": {"private_note": "recursive"}}},
            {"properties": "not an object"},
        ]
        for wrapped in bad:
            with self.subTest(wrapped=wrapped), self.assertRaises(v4.FormatRepairError):
                recover_memory_wrapper(wrapped)

    def test_actual_r2_notes_read_only_keep_entire_original_and_role(self):
        aggregate = Path(__file__).resolve().parents[1] / "runs/main/budget.sqlite3"
        if not aggregate.is_file():
            self.skipTest("Original ledger absent")
        for role, expected_bytes in [("A", 669), ("B", 667)]:
            identifier = f"{NAMESPACE}:case_05.r02.POSTVERDICT_{role}_LOG.step1"
            original = v4.completed_row(aggregate, identifier)
            result = recover_completed_memory_response(original)
            note = result["parsed_original_value"]["properties"]["private_note"]
            self.assertEqual(len(note.encode()), expected_bytes)
            self.assertEqual(result["context_value"], {"private_note": note})
            self.assertEqual(result["response"], json.loads(original["response_json"]))
            self.assertEqual(result["receipt"], json.loads(original["receipt_json"]))
            self.assertEqual(v4.completed_row(aggregate, identifier)["result_hash"], original["result_hash"])


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = BudgetLedger(self.root / "budget.sqlite3")
        self.guard = SharedBudgetGuard(self.ledger.path, NAMESPACE, ledger=self.ledger)
        self.config = load_config()
        self.config["models"]["C"] = MODEL
        self.config["routes"] = {"A_and_B_and_C": "open-inference/fp4", "image": "alibaba", "fallbacks": False}
        self.config["budget"]["namespace"] = NAMESPACE

    def tearDown(self):
        self.temp.cleanup()

    def backend(self, answers, cls=FormatV5Backend):
        self.transport = ScriptedWireTransport(self.ledger, answers)
        return cls(self.config, self.root / "run", self.ledger.path, guard=self.guard,
                   api_key="FIXTURE_ONLY_NOT_A_CREDENTIAL", transport=self.transport)

    @staticmethod
    def request(mode="memory_only", role="A", round_number=2):
        stage = f"POSTVERDICT_{role}_LOG" if mode == "memory_only" else "C_VOTE"
        return {"request_id": f"fixture.r{round_number:02d}.{stage}", "mode": mode, "role": role,
                "messages": [{"role": "system", "content": "Original task and memory instructions."},
                             {"role": "user", "content": "Remember only your permitted observations."}]}

    def test_future_exact_wrapper_one_POST_and_flagged_cached_result(self):
        raw = {"properties": {"private_note": "My own actual carryover note."}, "property": True}
        backend = self.backend([raw])
        request = self.request()
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["private_note"], raw["properties"]["private_note"])
        self.assertIsNone(result["output"]["decision"])
        self.assertEqual(len(self.transport.posts_exact), 1)
        evidence = result["provenance"]["technical_format_v5"]["normalizations"][0]
        saved = json.loads(Path(evidence["path"]).read_text())
        self.assertEqual(saved["parsed_original_value"], raw)
        self.assertEqual(json.loads(saved["original_text"]), raw)
        self.assertFalse(saved["semantic_fields_inferred_or_changed"])
        self.assertEqual(backend.run_actor(request), result)
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_v4_terminal_brace_and_private_UTF8_excerpt_behavior_inherited(self):
        note = "é🙂"*500
        raw = json.dumps({"properties": {"private_note": note}})+"}"
        backend = self.backend([raw])
        result = backend.run_actor(self.request())
        self.assertEqual(result["status"], "valid")
        self.assertLessEqual(len(result["output"]["private_note"].encode()), 2048)
        self.assertIn("Technical excerpt", result["output"]["private_note"])
        self.assertIn("technical_format_v4", result["provenance"])
        path = result["provenance"]["technical_format_v5"]["normalizations"][0]["path"]
        record = json.loads(Path(path).read_text())
        self.assertEqual(record["unwrapped_original_value"]["private_note"], note)
        self.assertIn("one_extraneous_terminal_closing_brace", record["syntax_flags"])
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_other_modes_never_unwrap_votes_or_messages(self):
        backend = self.backend([{"properties": {"decision": "P4", "rationale": "cannot unwrap a vote"}}])
        result = backend.run_actor(self.request("vote_only", "C"))
        self.assertEqual(result["status"], "invalid_model_output")
        self.assertIsNone(result["output"])
        self.assertNotIn("technical_format_v5", result["provenance"])
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_disallowed_wrapper_stays_invalid_without_requery(self):
        raw = {"properties": {"private_note": "note"}, "property": False}
        backend = self.backend([raw])
        result = backend.run_actor(self.request())
        self.assertEqual(result["status"], "invalid_model_output")
        self.assertEqual(result["action_log"][0]["output"], raw)
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_historical_supplement_does_not_rewrite_original_invalid_stage(self):
        raw = {"properties": {"private_note": "Existing authored note."}}
        backend = self.backend([raw], cls=v4.FormatV4Backend)
        request = self.request()
        original = backend.run_actor(request)
        self.assertEqual(original["status"], "invalid_model_output")
        source = next((backend.evidence_dir / "stage_results").glob("*.json"))
        before = source.read_bytes()
        supplement = build_posthoc_memory_supplement(self.ledger.path, request["request_id"]+".step1", self.root / "supplement.json")
        self.assertEqual(supplement["context_value"]["private_note"], raw["properties"]["private_note"])
        self.assertTrue(supplement["previous_actor_inputs_not_retroactively_changed"])
        self.assertEqual(source.read_bytes(), before)
        amended = FormatV5Backend(self.config, self.root / "run", self.ledger.path, guard=self.guard,
                   api_key="FIXTURE_ONLY_NOT_A_CREDENTIAL", transport=self.transport)
        self.assertEqual(amended.run_actor(request), original)
        self.assertEqual(len(self.transport.posts_exact), 1)


if __name__ == "__main__":
    unittest.main()
