"""Actual adapter, exact-wire transport and temporary shared-ledger integration."""
from copy import deepcopy
import base64
import hashlib
import io
import json
from pathlib import Path
import random
import tempfile
import unittest

from visual_collusion.budget import BudgetLedger, PendingPaidRequest
from visual_collusion.providers import image_content
from experiments.triad_repaired_v8.tests.test_backend import NativeWireTransport, RawAnswer
from experiments.triad_emergent_v9.backend import Backend, BackendError, MODEL, IMAGE_MODEL, codec
from experiments.triad_emergent_v9.funding import FundingLedger, create_guard, NAMESPACE


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "budget.sqlite3"
        BudgetLedger(self.path)
        self.ledger = FundingLedger(self.path)
        self.config = {"models": {**{r: MODEL for r in "ABC"}, "image": IMAGE_MODEL},
            "routes": {"A_and_B_and_C": "open-inference/fp4", "image": "alibaba", "fallbacks": False},
            "budget": {"namespace": NAMESPACE}, "max_actions": 4, "max_images": 2,
            "sampling": {"planning_temperature": 2.0, "vote_temperature": 2.0,
                         "private_logging_temperature": 2.0}, "reasoning": {"effort": "high"}}

    def tearDown(self):
        self.tmp.cleanup()

    def backend(self, actions):
        out = self.root / "run"
        guard = create_guard(self.path, evidence_dir=out / "provider_evidence", ledger=self.ledger)
        self.transport = NativeWireTransport(self.ledger, actions)
        return Backend(self.config, out, self.path, transport=self.transport,
                       api_key="OFFLINE_FIXTURE_NOT_A_SECRET", guard=guard)

    @staticmethod
    def request(mode="visual_vote", role="A", suffix="one"):
        return {"request_id": "main.r01." + suffix, "role": role, "round": 1, "mode": mode,
            "messages": [{"role": "system", "content": "Complete the ordinary visual task."},
                         {"role": "user", "content": "The factual candidates are supplied."}],
            "image_refs": {}, "metadata_canary": "NEVER_IN_MODEL_MESSAGES"}

    @staticmethod
    def action(name, **args):
        return {"action": name, "arguments": args}

    def chats(self):
        return [json.loads(b) for p, b in self.transport.posts_exact if p == "/chat/completions"]

    def test_visual_generation_acceptance_and_independent_vote_are_real_and_cached(self):
        b = self.backend([self.action("create_visual", prompt="A factual comparison, freely designed."),
                          self.action("accept_visual"), {"decision": "P4"}])
        req = self.request()
        result = b.run_actor(req)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["component_status"], {"image": "delivered", "decision": "valid", "log": "not_applicable"})
        self.assertEqual(result["output"]["decision"], "P4")
        self.assertEqual(Path(result["output"]["image"]["path"]).read_bytes(), self.transport.image_bytes)
        self.assertEqual(result["actions"][0]["action"], "create_visual")
        self.assertEqual(result["actions"][0]["arguments"]["prompt"], "A factual comparison, freely designed.")
        self.assertEqual(result["actions"][0]["generated_image"]["sha256"], result["output"]["image"]["sha256"])
        self.assertEqual(b.run_actor(req), result)
        self.assertEqual(len(self.transport.posts_exact), 4)
        self.assertNotIn("NEVER_IN_MODEL_MESSAGES", repr(self.transport.posts_exact))
        chats = self.chats()
        self.assertEqual([p["temperature"] for p in chats], [2.0, 2.0, 2.0])
        self.assertEqual(chats[-1]["tool_choice"]["function"]["name"], "record_vote")
        self.assertEqual(set(chats[-1]["tools"][0]["function"]["parameters"]["properties"]), {"decision"})
        for p in chats:
            self.assertEqual(p["max_tokens"], 8192)
            self.assertEqual(p["reasoning"], {"effort": "high", "exclude": False})
            self.assertFalse(p["provider"]["allow_fallbacks"])
            self.assertNotIn("top_k", p)
            self.assertNotIn("top_p", p)
        # Standard response-only index0 remains in raw evidence and never in outgoing history.
        raw = self.ledger.lookup(result["receipts"][0]["request_id"])["response"]
        self.assertEqual(raw["choices"][0]["message"]["tool_calls"][0]["index"], 0)
        for p in chats[1:]:
            for m in p["messages"]:
                for c in m.get("tool_calls", []):
                    self.assertNotIn("index", c)

    def test_revision_uses_only_own_draft_and_preserves_peer_even_with_same_pixels(self):
        b = self.backend([self.action("create_visual", prompt="First actual draft."),
            self.action("revise_visual", prompt="Revised actual draft."),
            self.action("accept_visual"), {"decision": "P1"}])
        req = self.request(role="B")
        image = self.root / "peer.png"; image.write_bytes(self.transport.image_bytes)
        import hashlib
        req["image_refs"] = {"current_A": {"path": str(image), "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}}
        req["messages"].append({"role": "user", "content": [{"type": "text", "text": "current_A actual peer"}, image_content(image.read_bytes())]})
        result = b.run_actor(req)
        self.assertEqual(result["status"], "valid")
        for p in self.chats():
            parts = [v for m in p["messages"] if isinstance(m.get("content"), list) for v in m["content"]]
            self.assertLessEqual(sum(v.get("type") == "image_url" for v in parts), 2)
            self.assertIn({"type": "text", "text": "current_A actual peer"}, parts)
        calls = sorted((b.evidence_dir / "image_calls").glob("*.json"))
        records = [json.loads(p.read_text()) for p in calls]
        self.assertEqual({r["reference"] for r in records}, {"none", "current_draft"})
        self.assertTrue(next(r for r in records if r["reference"] == "current_draft")["reference_sha256"])

    def test_declining_visual_does_not_fabricate_or_block_valid_ballot(self):
        b = self.backend([self.action("decline_visual", reason="I decline the visual deliverable."), {"decision": "P2"}])
        result = b.run_actor(self.request())
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["output"]["image"])
        self.assertEqual(result["output"]["decision"], "P2")
        self.assertEqual(result["component_status"]["image"], "declined")
        self.assertEqual(len(self.transport.posts_exact), 2)

    def test_four_invalid_visual_actions_still_leave_separate_vote_opportunity(self):
        bad = self.action("create_visual", prompt="brief", reference="none")
        b = self.backend([bad, bad, bad, {"reason": "No valid current draft."}, {"decision": "P1"}])
        result = b.run_actor(self.request())
        self.assertEqual(result["component_status"]["decision"], "valid")
        self.assertEqual(result["component_status"]["image"], "declined")
        self.assertFalse((b.evidence_dir / "image_calls").exists())
        self.assertEqual(len(self.transport.posts_exact), 5)

    def test_repeated_large_rejected_arguments_are_archived_but_do_not_fill_working_history(self):
        invalid_prompt = "not-executed-" + "x" * 32000
        invalid = self.action("create_visual", prompt=invalid_prompt)
        backend = self.backend([invalid, invalid, invalid,
            {"reason": "There is no valid draft."}, {"decision": "P3"}])
        request = self.request()
        # Exactly the declared110000-byte visual-history allowance, in Unicode.
        request["messages"][1]["content"] = "🧪" * 27500
        result = backend.run_actor(request)
        self.assertEqual(result["component_status"]["decision"], "valid")
        self.assertEqual(result["component_status"]["image"], "declined")
        self.assertEqual(len(self.transport.posts_exact), 5)
        for action in result["actions"][:3]:
            self.assertEqual(action["arguments"]["prompt"], invalid_prompt)
            raw = self.ledger.lookup(action["receipt"]["request_id"])["response"]
            original = raw["choices"][0]["message"]["tool_calls"][0]
            raw_arguments = original["function"]["arguments"].encode("utf-8")
            summary = action["rejected_history_summary"]
            self.assertEqual(summary["rejected_arguments_sha256"], hashlib.sha256(raw_arguments).hexdigest())
            self.assertEqual(summary["rejected_arguments_utf8_bytes"], len(raw_arguments))
            self.assertEqual(json.loads(raw_arguments)["prompt"], invalid_prompt)
        for payload in self.chats()[1:]:
            self.assertNotIn(invalid_prompt, json.dumps(payload, ensure_ascii=False))
            calls = [call for message in payload["messages"] for call in message.get("tool_calls", [])]
            self.assertTrue(all("rejected_arguments_sha256" in call["function"]["arguments"]
                                for call in calls if call["function"]["name"] == "create_visual"))
        self.assertFalse((backend.evidence_dir / "image_calls").exists())

    def test_maximum_visual_view_and_two_full_unicode_briefs_fit_real_adapter(self):
        from experiments.triad_emergent_v9.prompts import actor_system
        brief = "🧪" * 1200
        backend = self.backend([self.action("create_visual", prompt=brief),
            self.action("revise_visual", prompt=brief), self.action("accept_visual"),
            {"decision": "P2"}])
        request = self.request()
        request["messages"][0]["content"] = actor_system("A", "visual_vote", "induced_shared_private")
        request["messages"][1]["content"] = "🧪" * 27500
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["decision"], "P2")
        self.assertEqual(len(self.transport.posts_exact), 6)
        self.assertEqual([a["arguments"]["prompt"] for a in result["actions"] if a.get("action") in {"create_visual", "revise_visual"}], [brief, brief])
        for payload in self.chats():
            self.assertEqual(payload["temperature"], 2.0)
        self.assertEqual(self.ledger.namespace_summary()["request_count"], 6)

    def test_visual_history_overflow_blocks_before_first_paid_request(self):
        backend = self.backend([])
        request = self.request()
        request["messages"][1]["content"] = "x" * 110001
        with self.assertRaisesRegex(BackendError, "Visual actor view exceeds110000"):
            backend.run_actor(request)
        self.assertEqual(self.transport.posts_exact, [])
        self.assertEqual(self.ledger.namespace_summary()["request_count"], 0)

    def test_reused_rejected_call_identity_is_retained_as_model_error_not_broken_tool_history(self):
        def rejected():
            return RawAnswer({"role": "assistant", "content": None, "tool_calls": [{
                "id": "same-rejected-id", "type": "function", "index": 0,
                "function": {"name": "create_visual", "arguments": '{"prompt":"too-long-' + 'x' * 1200 + '"}'}}]})
        backend = self.backend([rejected(), rejected(),
            self.action("decline_visual", reason="No valid draft."), {"decision": "P1"}])
        result = backend.run_actor(self.request())
        self.assertEqual(result["component_status"]["decision"], "valid")
        self.assertIn("identity was reused", result["actions"][1]["validation_error"])
        calls = [call for message in self.chats()[-1]["messages"] for call in message.get("tool_calls", [])]
        self.assertEqual(sum(call["id"] == "same-rejected-id" for call in calls), 1)
        self.assertEqual(len(self.transport.posts_exact), 4)

    def test_image_delivery_survives_refused_ballot_without_retry(self):
        refusal = RawAnswer({"role": "assistant", "content": None, "refusal": "No ballot."})
        b = self.backend([self.action("create_visual", prompt="Actual task diagram."), self.action("accept_visual"), refusal])
        result = b.run_actor(self.request())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["component_status"]["image"], "delivered")
        self.assertEqual(result["component_status"]["decision"], "refused")
        self.assertIsNone(result["output"]["decision"])
        self.assertEqual(len(self.transport.posts_exact), 4)

    def test_known_vote_format_failure_has_one_bounded_format_attempt_only(self):
        b = self.backend([{"decision": "INVALID"}, {"decision": "P3"}])
        result = b.run_actor(self.request("vote", "C"))
        self.assertEqual(result["output"]["decision"], "P3")
        self.assertEqual(len(self.transport.posts_exact), 2)
        first, second = self.chats()
        self.assertEqual(second["messages"][:-1], first["messages"])
        self.assertIn("Known completed format error only", second["messages"][-1]["content"])
        self.assertNotIn("correct", second["messages"][-1]["content"].replace("correctness", ""))

    def test_non_native_ballot_is_terminal_and_never_resampled(self):
        b = self.backend([RawAnswer({"role": "assistant", "content": '{"decision":"P4"}'}, "stop")])
        result = b.run_actor(self.request("vote", "C"))
        self.assertEqual(result["status"], "model_invalid")
        self.assertEqual(len(self.transport.posts_exact), 1)
        self.assertIsNone(result["output"]["decision"])

    def test_valid_arguments_in_new_envelope_metadata_halt_as_technical_not_model_failure(self):
        raw = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "actual-fixture-call", "type": "function", "index": 0,
            "new_provider_metadata": True,
            "function": {"name": "record_vote", "arguments": '{"decision":"P4"}'}}]}
        b = self.backend([RawAnswer(raw)])
        with self.assertRaisesRegex(BackendError, "Technical native-envelope"):
            b.run_actor(self.request("vote", "C"))
        self.assertEqual(len(self.transport.posts_exact), 1)
        self.assertEqual(self.ledger.namespace_summary()["request_count"], 1)
        with self.ledger._connect() as db:
            row = db.execute("SELECT status,response_json FROM requests").fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertIn("new_provider_metadata", row["response_json"])

    def test_known_renderer_failure_remains_a_paid_image_attempt_not_a_transport_retry(self):
        b = self.backend([self.action("create_visual", prompt="One factual visual."),
                          self.action("decline_visual", reason="The image output was unusable."),
                          {"decision": "P1"}])
        original = self.transport
        def broken_image(method, path, body):
            result = original(method, path, body)
            if method == "POST" and path == "/images":
                result["data"][0]["b64_json"] = "not-an-image"
            return result
        b.provider._transport = broken_image
        result = b.run_actor(self.request())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["component_status"]["image"], "declined")
        self.assertEqual(result["component_status"]["decision"], "valid")
        self.assertIsNone(result["output"]["image"])
        self.assertIn("image_error", result["actions"][0])
        self.assertEqual(result["actions"][0]["generation_attempt"], 1)
        self.assertEqual(sum(p == "/images" for p, _ in self.transport.posts_exact), 1)

    def test_flat_native_logs_preserve_unicode_and_have_no_embedded_marker_parser(self):
        row = {"carryover_note": "é" * 400, "image_reflection": "Actual prompt and pixels compared. " * 20}
        b = self.backend([row])
        result = b.run_actor(self.request("log", "A"))
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["carryover_note"], row["carryover_note"])
        self.assertEqual(self.chats()[0]["temperature"], 2.0)

    def test_all_roles_records_use_documented_maximum_with_high_reasoning(self):
        row = {"carryover_note": "Retain the actual outcome.",
               "image_reflection": "The actual visual and intended brief were compared."}
        backend = self.backend([row, row, row, {"decision": "P2"}])
        for role in "ABC":
            result = backend.run_actor(self.request("log", role, role))
            self.assertEqual(result["status"], "valid")
        self.assertEqual(backend.run_actor(self.request("vote", "C", "ballot"))["status"], "valid")
        for payload in self.chats():
            self.assertEqual(payload["temperature"], 2.0)
            self.assertEqual(payload["reasoning"], {"effort": "high", "exclude": False})
            self.assertEqual(payload["max_tokens"], 8192)
            self.assertNotIn("top_k", payload)
            self.assertNotIn("top_p", payload)
        report = backend.provider._preflight["sampling"]
        self.assertFalse(report["provider_numeric_range_published"])
        self.assertFalse(report["provider_sampling_effect_verified"])
        self.assertIn("0.0–2.0", report["temperature_maximum_basis"])

    def test_sampling_drift_fails_before_any_paid_request(self):
        backend = self.backend([])
        for key in ("planning_temperature", "vote_temperature", "private_logging_temperature"):
            for value in (.3, 1.0, 2.1):
                changed = deepcopy(self.config)
                changed["sampling"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(BackendError):
                    backend.verify_binding(changed)
        self.assertEqual(self.transport.posts_exact, [])
        self.assertEqual(self.ledger.namespace_summary()["request_count"], 0)

    def test_rgba_generator_pixels_are_delivered_and_inspected_without_conversion(self):
        from PIL import Image
        backend = self.backend([self.action("create_visual", prompt="An illustration with transparency."),
                                self.action("accept_visual"), {"decision": "P1"}])
        buffer = io.BytesIO()
        original = Image.new("RGBA", (1024, 1024), (20, 80, 140, 73))
        original.save(buffer, format="PNG")
        self.transport.image_bytes = buffer.getvalue()
        result = backend.run_actor(self.request())
        self.assertEqual(result["status"], "valid")
        delivered = Path(result["output"]["image"]["path"]).read_bytes()
        self.assertEqual(delivered, self.transport.image_bytes)
        self.assertEqual(result["output"]["image"]["sha256"], hashlib.sha256(delivered).hexdigest())
        draft_part = next(part for message in self.chats()[1]["messages"]
            if isinstance(message.get("content"), list) for part in message["content"]
            if part["type"] == "image_url")
        encoded = draft_part["image_url"]["url"].split(",", 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as observed:
            observed.load()
            self.assertEqual(observed.mode, "RGBA")
            self.assertEqual(observed.tobytes(), original.tobytes())

    def test_two_incompressible_rgba_inputs_enforce_actual_wire_limit_before_reservation(self):
        from PIL import Image
        backend = self.backend([{"decision": "P1"}])
        raster = Image.frombytes("RGBA", (1024, 1024), random.Random(1042).randbytes(1024 * 1024 * 4))
        buffer = io.BytesIO(); raster.save(buffer, format="PNG")
        raw = buffer.getvalue()
        path = self.root / "rgba.png"; path.write_bytes(raw)
        descriptor = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
        request = self.request("vote", "C")
        request["image_refs"] = {"current_A": descriptor, "current_B": descriptor}
        request["messages"].append({"role": "user", "content": [
            {"type": "text", "text": "Two permitted actual peer images."},
            image_content(raw), image_content(raw)]})
        self.assertEqual(codec.MAX_WIRE_BYTES, 10485760)
        with self.assertRaisesRegex(codec.WireEncodingError, "Complete canonical wire JSON meets or exceeds"):
            backend.run_actor(request)
        self.assertEqual(self.transport.posts_exact, [])
        self.assertEqual(self.ledger.namespace_summary()["request_count"], 0)

    def test_visual_return_has_no_ballot_and_no_invented_log(self):
        b = self.backend([self.action("create_visual", prompt="A handoff illustration."), self.action("accept_visual")])
        result = b.run_actor(self.request("visual_return", "C"))
        self.assertEqual(result["status"], "valid")
        self.assertIsNone(result["output"]["decision"])
        self.assertEqual(result["output"]["carryover_note"], "")
        self.assertEqual(len(self.transport.posts_exact), 3)

    def test_invalid_current_refs_and_image_bounds_fail_before_reservation(self):
        b = self.backend([])
        req = self.request();req["image_refs"] = {"last_A": {"path": "not-read", "sha256": "x"}}
        with self.assertRaises(BackendError): b.run_actor(req)
        self.assertEqual(self.transport.posts_exact, [])

    def test_only_used_models_are_preflighted(self):
        b = self.backend([])
        report = b.preflight()
        self.assertEqual(set(report["chat_metadata"]), {MODEL})
        self.assertEqual(report["unused_model_checks"], [])
        self.assertEqual(self.transport.posts_exact, [])

    def test_actual_engine_postreview_log_request_uses_accepted_own_pixels(self):
        # This is an explicitly simulated local workflow, not a reviewer job.
        from experiments.triad_emergent_v9.engine import Engine, read_json
        from experiments.triad_emergent_v9.design import make_plan
        from experiments.triad_emergent_v9.tests.test_review import negative_review
        row = {"carryover_note": "Carry forward the actual observations.",
               "image_reflection": "My brief and the actual current visual were compared; peer reliance is unknown."}
        actions = []
        for _ in range(2):
            actions += [self.action("create_visual", prompt="An ordinary factual visual."),
                        self.action("accept_visual"), {"decision": "P1"}]
        actions += [{"decision": "P1"}, row]
        backend = self.backend(actions)
        engine = Engine(self.root / "run", self.config, make_plan(), backend)
        for _ in range(3):
            engine.run_next_actor()
        job = engine.export_reviewer_packet()
        packet = read_json(job["packet_path"])
        engine._commit(engine.next_stage(), {"status": "valid", "output": negative_review(packet),
            "provenance": {"origin": "explicit_offline_fixture"}})
        request = engine.actor_request()
        self.assertEqual(request["mode"], "log")
        self.assertEqual(set(request["image_refs"]), {"own_current"})
        result = engine.run_next_actor()
        self.assertEqual(result["component_status"]["log"], "valid")
        self.assertEqual(result["output"]["carryover_note"], row["carryover_note"])
        self.assertEqual(result["output"]["image_reflection"], row["image_reflection"])
        self.assertEqual(len(self.transport.posts_exact), 10)

    def test_timeout_remains_uncertain_and_never_becomes_model_invalid(self):
        b = self.backend([{"decision": "P1"}])
        self.transport.failure = TimeoutError("OFFLINE_TIMEOUT")
        req = self.request("vote", "C")
        with self.assertRaises(PendingPaidRequest): b.run_actor(req)
        with self.assertRaises(PendingPaidRequest): b.run_actor(req)
        self.assertEqual(len(self.transport.posts_exact), 1)
        self.assertEqual(self.ledger.namespace_summary()["request_count"], 1)
        self.assertGreater(self.ledger.namespace_summary()["reserved_unresolved_usd"], 0)


if __name__ == "__main__":
    unittest.main()
