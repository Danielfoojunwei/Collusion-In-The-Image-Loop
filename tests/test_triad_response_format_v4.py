"""Narrow format-sidecar tests; all provider POSTs below are offline fixtures."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from visual_collusion.budget import BudgetLedger, canonical, _units
from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard
from experiments.pressure_interview_v1.planning import load_config
from experiments.pressure_interview_v1.tests.test_live_provider import ScriptedWireTransport
from experiments.triad_consensus_v2.backend import MODEL, NAMESPACE
from scripts.triad_response_format_v4 import (
    FormatV4Backend, FormatRepairError, FormatEvidenceError, parse_response_object,
    normalize_private_text_fields, recover_completed_response, completed_row,
    build_posthoc_supplement,
)


def row(text, *, cost=0.0001, **response_changes):
    response = {"id": "fixture-response", "model": MODEL, "provider": "OpenInference",
                "usage": {"cost": cost},
                "choices": [{"finish_reason": "stop", "message": {"content": text}}]}
    response.update(response_changes)
    receipt = {"request_id": NAMESPACE + ":fixture.r01.C_VOTE.step1", "request_hash": "hash",
               "requested_model": MODEL, "kind": "chat", "returned_model": MODEL,
               "provider": "OpenInference", "response_id": "fixture-response", "cost_usd": cost,
               "usage": deepcopy(response["usage"])}
    a,b = canonical(response), canonical(receipt)
    return {"status": "completed", "kind": "chat", "model": MODEL, "actual_units": _units(cost),
            "request_id": receipt["request_id"], "request_hash": "hash", "response_json": a,
            "receipt_json": b, "result_hash": hashlib.sha256((a+"\n"+b).encode()).hexdigest()}


class ParserTests(unittest.TestCase):
    def test_ordinary_json_and_prior_whole_fence(self):
        self.assertEqual(parse_response_object('{"decision":"P4"}')["value"], {"decision":"P4"})
        parsed = parse_response_object('```json\n{"decision":"P4"}\n```')
        self.assertEqual(parsed["value"]["decision"], "P4")
        self.assertEqual(parsed["syntax_flags"], ["whole_reply_json_code_fence_existing_parser"])

    def test_exactly_one_extra_closing_brace_preserves_vote_and_raw_text(self):
        text = ' {"decision":"P4","rationale":"I argue P1 instead; a contradictory self-report."}}\n'
        parsed = parse_response_object(text)
        self.assertEqual(parsed["value"]["decision"], "P4")
        self.assertIn("P1", parsed["value"]["rationale"])
        self.assertEqual(parsed["original_text"], text)
        self.assertEqual(parsed["syntax_flags"], ["one_extraneous_terminal_closing_brace"])

    def test_braces_inside_strings_and_escaped_quotes_are_json_decoded(self):
        value = {"decision": "P4", "rationale": 'A } brace and an escaped " quote { remain text.'}
        self.assertEqual(parse_response_object(json.dumps(value)+"}")["value"], value)

    def test_rejects_duplicate_keys_nested_duplicate_nonfinite_and_overflow(self):
        for text in ['{"decision":"P4","decision":"P1"}}', '{"a":{"b":1,"b":2}}}',
                     '{"n":NaN}}', '{"n":Infinity}}', '{"n":1e1000}}']:
            with self.subTest(text=text), self.assertRaises(FormatRepairError):
                parse_response_object(text)

    def test_rejects_missing_brace_multiple_extra_prose_and_second_json(self):
        for text in ['{"decision":"P4"', '{"a":1}}}', '{"a":1} explanation',
                     '{"a":1}{"b":2}', '{"a":1}}null', '```json\n{"a":1}\n```}', '[1,2]']:
            with self.subTest(text=text), self.assertRaises(FormatRepairError):
                parse_response_object(text)

    def test_incomplete_and_refused_provider_answers_are_not_recovered(self):
        for choices in [[{"finish_reason": "length", "message": {"content": '{"a":1}}'}}],
                        [{"finish_reason": "stop", "message": {"content": '{"a":1}}', "refusal": "declined"}}],
                        [], [{"finish_reason":"stop","message":{"content":"{}"}}]*2]:
            with self.subTest(choices=choices), self.assertRaises(FormatRepairError):
                recover_completed_response(row("{}", choices=choices), "vote_only")

    def test_receipt_integrity_failure_remains_hard_technical_error(self):
        value = row('{"decision":"P4","rationale":"facts"}}')
        value["result_hash"] = "wrong"
        with self.assertRaises(FormatEvidenceError):
            recover_completed_response(value, "vote_only")

    def test_fractional_nanodollar_cost_uses_original_ledger_rounding(self):
        original = row('{"decision":"P4","rationale":"facts"}}', cost=0.0000937645)
        self.assertEqual(original["actual_units"], 93765)
        self.assertEqual(recover_completed_response(original, "vote_only")["parsed_original_value"]["decision"], "P4")

    def test_usage_token_metadata_mismatch_is_hard_error(self):
        original = row('{"decision":"P4","rationale":"facts"}}')
        receipt = json.loads(original["receipt_json"])
        receipt["usage"]["completion_tokens"] = 123
        original["receipt_json"] = canonical(receipt)
        original["result_hash"] = hashlib.sha256((original["response_json"]+"\n"+original["receipt_json"]).encode()).hexdigest()
        with self.assertRaises(FormatEvidenceError):
            recover_completed_response(original, "vote_only")

    def test_actual_first_C_response_read_only_recovery_retains_literal_P4(self):
        aggregate = Path(__file__).resolve().parents[1] / "runs/main/budget.sqlite3"
        if not aggregate.is_file():
            self.skipTest("Original live ledger is not present in this checkout")
        identifier = NAMESPACE+":case_05.r01.C_VOTE.step1"
        try:
            original = completed_row(aggregate, identifier)
        except FormatEvidenceError:
            self.skipTest("Original first-C observation is absent")
        before = original["result_hash"]
        recovered = recover_completed_response(original, "vote_only")
        self.assertEqual(recovered["parsed_original_value"]["decision"], "P4")
        self.assertIn("one_extraneous_terminal_closing_brace", recovered["syntax_flags"])
        self.assertGreater(len(recovered["parsed_original_value"]["rationale"].encode()), 800)
        self.assertLessEqual(len(recovered["context_value"]["rationale"].encode()), 800)
        self.assertEqual(completed_row(aggregate, identifier)["result_hash"], before)


class NoteTests(unittest.TestCase):
    def test_UTF8_excerpt_keeps_full_note_and_decision(self):
        original = {"decision": "P4", "rationale": "é🙂"*500}
        value = normalize_private_text_fields(original, "vote_only")
        self.assertEqual(value["value"]["decision"], "P4")
        self.assertLessEqual(len(value["value"]["rationale"].encode()), 800)
        self.assertIn("Technical excerpt", value["value"]["rationale"])
        self.assertEqual(value["private_text_normalizations"][0]["full_original_text"], original["rationale"])
        self.assertEqual(original["rationale"], "é🙂"*500)

    def test_memory2048_and_nested_image_delivery_notes_are_bounded(self):
        for mode, field, limit in [("image_vote", "rationale", 800), ("image_memory", "private_note", 2048)]:
            original = {"action": "deliver", "arguments": {field: "x"*3000}}
            value = normalize_private_text_fields(original, mode)
            self.assertLessEqual(len(value["value"]["arguments"][field].encode()), limit)
            self.assertEqual(value["private_text_normalizations"][0]["field_path"], ["arguments", field])
        self.assertLessEqual(len(normalize_private_text_fields({"private_note": "x"*3000}, "memory_only")["value"]["private_note"].encode()), 2048)

    def test_public_message_image_prompt_and_vote_are_never_normalized(self):
        original = {"decision": "P4", "rationale": "x"*1000, "message": "public"*500,
                    "image": {"path": "original.png", "sha256": "hash"}}
        value = normalize_private_text_fields(original, "message_vote")["value"]
        for field in ("decision", "message", "image"):
            self.assertEqual(value[field], original[field])
        generated = {"action": "generate_image", "arguments": {"prompt": "image"*1000, "reference": "none"}}
        self.assertEqual(normalize_private_text_fields(generated, "image_vote")["value"], generated)

    def test_length_handling_does_not_hide_invalid_control_chars(self):
        with self.assertRaises(FormatRepairError):
            normalize_private_text_fields({"rationale": "x"*1000+"\x01"}, "vote_only")


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

    def backend(self, answers):
        self.transport = ScriptedWireTransport(self.ledger, answers)
        return FormatV4Backend(self.config, self.root / "run", self.ledger.path,
            guard=self.guard, api_key="FIXTURE_ONLY_NOT_A_CREDENTIAL", transport=self.transport)

    @staticmethod
    def request(mode="vote_only", role="C"):
        return {"request_id": "fixture.r01.C_VOTE", "mode": mode, "role": role,
                "messages": [{"role": "system", "content": "Keep the original task goals."},
                             {"role": "user", "content": "Use the task facts."}]}

    def test_format_recovery_uses_one_POST_preserves_P4_and_full_contradictory_note(self):
        rationale = "I prefer P1 while explicit decision remains P4. "*50
        raw = json.dumps({"decision": "P4", "rationale": rationale})+"}"
        backend = self.backend([raw])
        request = self.request()
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["decision"], "P4")
        self.assertIn("Technical excerpt", result["output"]["rationale"])
        self.assertEqual(len(self.transport.posts_exact), 1)
        metadata = result["provenance"]["technical_format_v4"]
        self.assertFalse(metadata["decision_inferred_or_corrected"])
        saved = json.loads(Path(metadata["normalizations"][0]["path"]).read_text())
        self.assertEqual(saved["original_text"], raw)
        self.assertEqual(saved["parsed_original_value"]["rationale"], rationale)
        self.assertEqual(backend.run_actor(request), result)
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_old_invalid_stage_is_not_rewritten_by_new_adapter(self):
        from experiments.triad_consensus_v2.backend import TriadBackend
        raw = '{"decision":"P4","rationale":"actual explicit P4"}}'
        self.transport = ScriptedWireTransport(self.ledger, [raw])
        original_backend = TriadBackend(self.config, self.root / "run", self.ledger.path,
            guard=self.guard, api_key="FIXTURE_ONLY_NOT_A_CREDENTIAL", transport=self.transport)
        request = self.request()
        original = original_backend.run_actor(request)
        self.assertEqual(original["status"], "invalid_model_output")
        original_file = next((original_backend.evidence_dir / "stage_results").glob("*.json"))
        before = original_file.read_bytes()
        amended = FormatV4Backend(self.config, self.root / "run", self.ledger.path,
            guard=self.guard, api_key="FIXTURE_ONLY_NOT_A_CREDENTIAL", transport=self.transport)
        self.assertEqual(amended.run_actor(request), original)
        self.assertEqual(original_file.read_bytes(), before)
        supplement_path = self.root / "posthoc_supplement.json"
        supplement = build_posthoc_supplement(self.ledger.path, request["request_id"]+".step1", "vote_only", supplement_path)
        self.assertEqual(supplement["explicit_decision"], "P4")
        self.assertTrue(supplement["historical_original_invalid_outcome_preserved"])
        self.assertEqual(original_file.read_bytes(), before)
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_valid_JSON_overlong_note_normalizes_without_syntax_claim(self):
        backend = self.backend([{"private_note": "m"*3000}])
        result = backend.run_actor(self.request("memory_only", "A"))
        self.assertEqual(result["status"], "valid")
        saved = json.loads(Path(result["provenance"]["technical_format_v4"]["normalizations"][0]["path"]).read_text())
        self.assertEqual(saved["syntax_flags"], [])
        self.assertEqual(saved["private_text_normalizations"][0]["full_original_text"], "m"*3000)
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_public_message_overflow_stays_invalid_and_is_not_cut(self):
        backend = self.backend([{"decision": "P4", "rationale": "valid", "message": "p"*801}])
        result = backend.run_actor(self.request("message_vote", "B"))
        self.assertEqual(result["status"], "invalid_model_output")
        self.assertEqual(result["action_log"][0]["output"]["message"], "p"*801)
        self.assertEqual(len(self.transport.posts_exact), 1)


if __name__ == "__main__":
    unittest.main()
