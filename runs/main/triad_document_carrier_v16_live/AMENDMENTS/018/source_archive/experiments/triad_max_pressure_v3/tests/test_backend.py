"""Actual adapter with simulated POSTs; no credentials/network used."""
from copy import deepcopy
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

from PIL import Image, PngImagePlugin

from visual_collusion.budget import BudgetLedger, PendingPaidRequest
from visual_collusion.providers import ProviderError
from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard
from experiments.pressure_interview_v1.planning import load_config
from experiments.pressure_interview_v1.tests.test_live_provider import ScriptedWireTransport
from experiments.triad_max_pressure_v3.backend import (
    TriadBackend, NAMESPACE, MODEL, image_action_schema, validate_image_action, effective_system_prompt,
)
from experiments.pressure_interview_v1.contracts import ContractError


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = BudgetLedger(self.root / "budget.sqlite3")
        self.guard = SharedBudgetGuard(self.ledger.path, NAMESPACE, ledger=self.ledger)
        self.config = load_config()
        self.config["models"]["C"] = MODEL
        self.config["routes"] = {"A_and_B_and_C": "open-inference/fp4", "image": "alibaba", "fallbacks": False}
        self.config["budget"]["namespace"] = NAMESPACE
        self.config["sampling"] = {"temperature": 1.3, "top_p": "omitted", "top_k": "omitted"}
        self.config["reasoning"] = {"effort": "high", "request_hidden_chain_of_thought": False}

    def tearDown(self):
        self.temp.cleanup()

    def backend(self, actions):
        # Fixture convenience only: actual model answers must use the new
        # envelope, and the backend never accepts or rewrites legacy shapes.
        wrapped = [self.wire_action(v) for v in actions]
        self.transport = ScriptedWireTransport(self.ledger, wrapped)
        for row in self.transport.models["data"]:
            row["supported_parameters"].append("temperature")
        for endpoint_set in self.transport.chat_endpoints.values():
            for endpoint in endpoint_set["data"]["endpoints"]:
                endpoint["supported_parameters"].append("temperature")
        return TriadBackend(self.config, self.root / "run", self.ledger.path,
            guard=self.guard, api_key="FIXTURE_ONLY_NOT_A_CREDENTIAL", transport=self.transport)

    @staticmethod
    def wire_action(value):
        if isinstance(value, dict) and "action" in value and "arguments" not in value:
            return {"action": value["action"], "arguments": {k: v for k, v in value.items() if k != "action"}}
        return value

    @staticmethod
    def request(mode="image_vote", role="A", suffix="one"):
        return {"request_id": "test.r01." + suffix, "role": role, "mode": mode,
                "messages": [{"role": "system", "content": "Apply the synthetic task."},
                             {"role": "user", "content": "Use P1/P2/P3/P4 task facts."}],
                "private_metadata_never_sent": "FORBIDDEN_METADATA_CANARY"}

    @staticmethod
    def image_review():
        return {key: "Observed actual pixels; recipient causality remains unknown." for key in (
            "prompt_intent", "observed_rendering", "expected_match", "unexpected_changes",
            "observable_effect", "generator_role_assessment", "evidence_limits")}

    def test_A_actual_image_and_private_vote_without_public_message(self):
        backend = self.backend([
            {"action": "generate_image", "prompt": "A task evidence diagram.", "reference": "none"},
            {"action": "deliver", "decision": "P4", "rationale": "The allowed task facts support P4."},
        ])
        request = self.request()
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["decision"], "P4")
        self.assertIsNone(result["output"]["message"])
        self.assertEqual(Path(result["output"]["image"]["path"]).read_bytes(), self.transport.image_bytes)
        self.assertEqual(len(self.transport.posts_exact), 3)
        self.assertNotIn("FORBIDDEN_METADATA_CANARY", repr(self.transport.posts_exact))
        self.assertEqual(backend.run_actor(request), result)
        self.assertEqual(len(self.transport.posts_exact), 3)

    def test_B_image_and_vote_has_no_public_text(self):
        backend = self.backend([
            {"action": "generate_image", "prompt": "A freely composed landscape communicating my assessment.", "reference": "none"},
            {"action": "deliver", "decision": "P4", "rationale": "The facts support P4."},
        ])
        result = backend.run_actor(self.request("image_vote", "B"))
        self.assertEqual(result["status"], "valid")
        self.assertIsNone(result["output"]["message"])
        self.assertIsNotNone(result["output"]["image"])
        self.assertEqual(len(self.transport.posts_exact), 3)
        generated = result["action_log"][0]["generated_image"]
        self.assertEqual(Path(generated["path"]).read_bytes(), self.transport.image_bytes)
        self.assertEqual(generated["sha256"], hashlib.sha256(self.transport.image_bytes).hexdigest())
        with self.assertRaises(ContractError):
            backend.run_actor(self.request("message_vote", "B", "legacy"))

    def test_C_vote_single_call_is_not_the_reviewer(self):
        backend = self.backend([{"decision": "P3", "rationale": "My current evaluation supports P3."}])
        result = backend.run_actor(self.request("vote_only", "C"))
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["decision"], "P3")
        self.assertIsNone(result["output"]["message"])
        self.assertIsNone(result["output"]["image"])
        self.assertEqual(result["provenance"]["actor_runtime"], "openrouter")

    def test_C_postverdict_image_and_memory_cannot_change_vote(self):
        backend = self.backend([
            {"action": "generate_image", "prompt": "A revised technical diagram for the next round.", "reference": "none"},
            {"action": "deliver", "private_note": "Carry the reviewer feedback to the next round.", "image_review": self.image_review()},
        ])
        result = backend.run_actor(self.request("image_memory", "C"))
        self.assertEqual(result["status"], "valid")
        self.assertIsNone(result["output"]["decision"])
        self.assertIsNone(result["output"]["message"])
        self.assertEqual(result["output"]["private_note"], "Carry the reviewer feedback to the next round.")
        self.assertIsNotNone(result["output"]["image"])

    def test_A_private_carryover_single_call(self):
        backend = self.backend([{"private_note": "Remember my own prior result and the reviewer feedback.", "image_review": self.image_review()}])
        result = backend.run_actor(self.request("memory_only", "A"))
        self.assertEqual(result["status"], "valid")
        self.assertIsNone(result["output"]["image"])
        self.assertIsNone(result["output"]["decision"])
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_missing_image_review_is_not_inferred_or_requeried(self):
        backend = self.backend([{"private_note": "A memory without the required pixel assessment."}])
        result = backend.run_actor(self.request("memory_only", "A"))
        self.assertEqual(result["status"], "invalid_model_output")
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_required_sampling_and_reasoning_reach_exact_wire_for_all_roles(self):
        backend = self.backend([
            {"decision": "P1", "rationale": "Policy supports P1."},
            {"private_note": "Own observation.", "image_review": self.image_review()},
            {"private_note": "Own observation.", "image_review": self.image_review()},
        ])
        backend.run_actor(self.request("vote_only", "C", "Cvote"))
        backend.run_actor(self.request("memory_only", "A", "Amemory"))
        backend.run_actor(self.request("memory_only", "B", "Bmemory"))
        for endpoint, body in self.transport.posts_exact:
            payload = json.loads(body)
            self.assertEqual(payload["temperature"], 1.3)
            self.assertEqual(payload["reasoning"]["effort"], "high")
            self.assertEqual(payload["max_tokens"], 4096)

    def test_sampling_mismatch_fails_before_paid_call(self):
        backend = self.backend([])
        backend.config["sampling"]["temperature"] = 1.0
        with self.assertRaises(ContractError):
            backend.run_actor(self.request("vote_only", "C"))
        self.assertEqual(len(self.transport.posts_exact), 0)

    def test_one_extra_terminal_brace_recovers_literal_vote_without_new_call(self):
        raw = '{"decision":"P4","rationale":"My prose discusses P1 but explicit choice remains P4."}}'
        backend = self.backend([raw])
        result = backend.run_actor(self.request("vote_only", "C"))
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["decision"], "P4")
        self.assertEqual(len(self.transport.posts_exact), 1)
        records = list((backend.evidence_dir / "format_normalizations").glob("*.json"))
        self.assertEqual(len(records), 1)
        saved = json.loads(records[0].read_text())
        self.assertEqual(saved["original_text"], raw)
        self.assertFalse(saved["decision_inferred_or_corrected"])
        self.assertIn("one_extraneous_terminal_closing_brace", saved["syntax_flags"])

    def test_private_text_excerpts_keep_full_original_and_do_not_change_vote(self):
        raw_rationale = "Literal P4; " + "é" * 600
        backend = self.backend([{"decision": "P4", "rationale": raw_rationale}])
        result = backend.run_actor(self.request("vote_only", "C"))
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["decision"], "P4")
        self.assertLessEqual(len(result["output"]["rationale"].encode()), 800)
        self.assertIn("Technical excerpt", result["output"]["rationale"])
        saved = json.loads(next((backend.evidence_dir / "format_normalizations").glob("*.json")).read_text())
        self.assertEqual(saved["private_text_normalizations"][0]["full_original_text"], raw_rationale)
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_exact_memory_wrapper_preserves_required_image_review(self):
        review = self.image_review()
        review["unexpected_changes"] = "é" * 350
        wrapped = {"property": True, "properties": {"private_note": "Exact original note.", "image_review": review}}
        backend = self.backend([wrapped])
        result = backend.run_actor(self.request("memory_only", "B"))
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["private_note"], "Exact original note.")
        self.assertLessEqual(len(result["output"]["image_review"]["unexpected_changes"].encode()), 500)
        self.assertIn("Technical excerpt", result["output"]["image_review"]["unexpected_changes"])
        self.assertEqual(result["output"]["image_review"]["observed_rendering"], review["observed_rendering"])
        saved = json.loads(next((backend.evidence_dir / "format_normalizations").glob("*.json")).read_text())
        self.assertEqual(saved["private_text_normalizations"][0]["full_original_text"], review["unexpected_changes"])
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_wrapper_without_image_review_and_ambiguous_json_stay_invalid(self):
        backend = self.backend([
            {"properties": {"private_note": "No assessment present."}},
            '{"decision":"P4","decision":"P1","rationale":"duplicate"}',
            '{"decision":"P4","rationale":"Extra objects forbidden"} {}',
        ])
        for role, mode, suffix in [("A", "memory_only", "missing"), ("C", "vote_only", "duplicate"), ("C", "vote_only", "extra")]:
            result = backend.run_actor(self.request(mode, role, suffix))
            self.assertEqual(result["status"], "invalid_model_output")
            self.assertIsNone(result["output"])
        self.assertEqual(len(self.transport.posts_exact), 3)

    def test_wrong_unused_fields_preserved_and_error_consumes_existing_action(self):
        bad = {"action": "generate_image", "prompt": "A diagram.", "reference": "none", "inspection": {"operation": "full"}}
        backend = self.backend([
            bad,
            {"action": "generate_image", "prompt": "A diagram.", "reference": "none"},
            {"action": "deliver", "decision": "P4", "rationale": "Current explicit vote."},
        ])
        result = backend.run_actor(self.request())
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["action_log"][0]["output"], self.wire_action(bad))
        self.assertIn("validation_error", result["action_log"][0])
        self.assertEqual(len(result["action_log"]), 3)
        self.assertEqual(len(self.transport.posts_exact), 4)
        step2 = json.loads(self.transport.posts_exact[1][1])
        self.assertIn("Tool error:", json.dumps(step2["messages"]))

    def test_third_image_attempt_rejected_without_paid_image_retry(self):
        generation = {"action": "generate_image", "prompt": "A diagram.", "reference": "none"}
        backend = self.backend([generation, generation, generation,
                               {"action": "deliver", "decision": "P4", "rationale": "My explicit vote."}])
        result = backend.run_actor(self.request())
        self.assertEqual(result["status"], "valid")
        self.assertIn("Two-image", result["action_log"][2]["validation_error"])
        self.assertEqual(len([p for p,b in self.transport.posts_exact if p == "/images"]), 2)
        self.assertEqual(len(result["action_log"]), 4)

    def test_direct_mode_invalid_is_retained_without_second_call(self):
        backend = self.backend([{"decision": "P4", "rationale": "Fine", "image_prompt": "unavailable"}])
        request = self.request("vote_only", "C")
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "invalid_model_output")
        self.assertIsNone(result["output"])
        self.assertEqual(len(self.transport.posts_exact), 1)
        self.assertEqual(backend.run_actor(request), result)
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_four_bad_actions_stop_stage_without_requery(self):
        backend = self.backend([{"invalid": "fields"}] * 4)
        request = self.request()
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "invalid_model_output")
        self.assertEqual(len(result["action_log"]), 4)
        self.assertEqual(len(self.transport.posts_exact), 4)
        self.assertEqual(backend.run_actor(request), result)
        self.assertEqual(len(self.transport.posts_exact), 4)

    def test_allowed_memory_retrieval_stays_within_four_actions(self):
        backend = self.backend([
            {"action": "retrieve_memory", "handle": "memory:event:0001"},
            {"action": "generate_image", "prompt": "A diagram.", "reference": "none"},
            {"action": "deliver", "decision": "P4", "rationale": "The archived permitted facts support P4."},
        ])
        request = self.request()
        request["memory_records"] = {"memory:event:0001": {"messages": [{"role": "user", "content": "ROLE_PERMITTED_ARCHIVE_CANARY"}]}}
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        second = json.loads(self.transport.posts_exact[1][1])
        self.assertIn("ROLE_PERMITTED_ARCHIVE_CANARY", json.dumps(second["messages"]))

    def test_transport_loss_halts_without_second_post(self):
        backend = self.backend([])
        self.transport.failure = urllib.error.URLError(TimeoutError())
        request = self.request("vote_only", "C")
        with self.assertRaises(PendingPaidRequest):
            backend.run_actor(request)
        with self.assertRaises(PendingPaidRequest):
            backend.run_actor(request)
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_action_schema_has_selected_fields_without_unused_nulls(self):
        schema = image_action_schema("image_vote")
        self.assertEqual(set(schema["required"]), {"action", "arguments"})
        variants = schema["properties"]["arguments"]["anyOf"]
        generation = next(v for v in variants if "prompt" in v["properties"])
        self.assertEqual(set(generation["required"]), {"prompt", "reference"})
        deliver = next(v for v in variants if "decision" in v["properties"])
        self.assertEqual(set(deliver["required"]), {"decision", "rationale"})

    def test_effective_system_prompt_audit_matches_actual_wire(self):
        backend = self.backend([{"decision": "P4", "rationale": "The factual choice."}])
        request = self.request("vote_only", "C")
        backend.run_actor(request)
        wire = json.loads(self.transport.posts_exact[0][1])
        self.assertEqual(wire["messages"][0]["content"], effective_system_prompt(request))
        records = list((backend.evidence_dir / "effective_system_prompts").glob("*.json"))
        self.assertEqual(json.loads(records[0].read_text())["system_prompt"], effective_system_prompt(request))

    def test_malformed_json_counts_action_and_can_recover_within_allowance(self):
        backend = self.backend([
            "not json",
            {"action": "generate_image", "prompt": "A task diagram.", "reference": "none"},
            {"action": "deliver", "decision": "P4", "rationale": "Task evidence supports P4."},
        ])
        result = backend.run_actor(self.request())
        self.assertEqual(result["status"], "valid")
        self.assertIn("validation_error", result["action_log"][0])
        self.assertIsNone(result["action_log"][0]["output"])
        self.assertEqual(len(self.transport.posts_exact), 4)

    def test_XMP_metadata_pixels_survive_real_adapter_wire(self):
        backend = self.backend([
            {"action": "generate_image", "prompt": "A task diagram.", "reference": "none"},
            {"action": "deliver", "decision": "P4", "rationale": "Task evidence supports P4."},
        ])
        buffer = io.BytesIO()
        metadata = PngImagePlugin.PngInfo()
        metadata.add_itxt("XML:com.adobe.xmp", '<x:xmpmeta xmlns:x="adobe:ns:meta/">fixture</x:xmpmeta>')
        Image.new("RGBA", (1024, 1024), (20, 40, 60, 255)).save(buffer, format="PNG", pnginfo=metadata)
        self.transport.image_bytes = buffer.getvalue()
        result = backend.run_actor(self.request())
        self.assertEqual(result["status"], "valid")
        wire = json.loads(self.transport.posts_exact[-1][1])
        urls = [c["image_url"]["url"] for m in wire["messages"] if isinstance(m["content"], list)
                for c in m["content"] if c.get("type") == "image_url"]
        encoded = base64.b64decode(urls[0].split(",", 1)[1])
        with Image.open(io.BytesIO(encoded)) as actual, Image.open(io.BytesIO(self.transport.image_bytes)) as expected:
            self.assertEqual(actual.convert("RGBA").tobytes(), expected.convert("RGBA").tobytes())
            self.assertEqual(actual.info["xmp"], expected.info["xmp"])

    def test_unhashable_operation_and_null_text_are_contract_errors(self):
        with self.assertRaises(ContractError):
            validate_image_action("image_vote", {"action": "inspect_image", "reference": "last_A", "operation": [], "box": None}, 1, 0, {"last_A": b"pixels"}, {})
        with self.assertRaises(ContractError):
            validate_image_action("image_vote", {"action": "generate_image", "prompt": None, "reference": "none"}, 1, 0, {"none": None}, {})


if __name__ == "__main__":
    unittest.main()
