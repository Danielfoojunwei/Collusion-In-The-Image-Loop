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
from experiments.triad_consensus_v2.backend import (
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

    def tearDown(self):
        self.temp.cleanup()

    def backend(self, actions):
        # Fixture convenience only: actual model answers must use the new
        # envelope, and the backend never accepts or rewrites legacy shapes.
        wrapped = [self.wire_action(v) for v in actions]
        self.transport = ScriptedWireTransport(self.ledger, wrapped)
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

    def test_B_message_and_vote_single_call_has_no_image(self):
        backend = self.backend([{"decision": "P4", "rationale": "The facts support P4.", "message": "Check the disclosed evidence."}])
        result = backend.run_actor(self.request("message_vote", "B"))
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["message"], "Check the disclosed evidence.")
        self.assertIsNone(result["output"]["image"])
        self.assertEqual(len(self.transport.posts_exact), 1)

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
            {"action": "deliver", "private_note": "Carry the reviewer feedback to the next round."},
        ])
        result = backend.run_actor(self.request("image_memory", "C"))
        self.assertEqual(result["status"], "valid")
        self.assertIsNone(result["output"]["decision"])
        self.assertIsNone(result["output"]["message"])
        self.assertEqual(result["output"]["private_note"], "Carry the reviewer feedback to the next round.")
        self.assertIsNotNone(result["output"]["image"])

    def test_A_private_carryover_single_call(self):
        backend = self.backend([{"private_note": "Remember my own prior result and the reviewer feedback."}])
        result = backend.run_actor(self.request("memory_only", "A"))
        self.assertEqual(result["status"], "valid")
        self.assertIsNone(result["output"]["image"])
        self.assertIsNone(result["output"]["decision"])
        self.assertEqual(len(self.transport.posts_exact), 1)

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
