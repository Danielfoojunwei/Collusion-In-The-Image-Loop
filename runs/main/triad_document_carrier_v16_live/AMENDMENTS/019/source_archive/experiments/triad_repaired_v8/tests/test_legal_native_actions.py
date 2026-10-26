"""Prospective native action protocol using real serialization and fixture POSTs."""
from copy import deepcopy
import json
import unittest

from experiments.triad_repaired_v8.backend import (
    legal_image_tools, TriadGuardedProvider, MAX_CHAT_TEXT_BYTES)
from experiments.triad_repaired_v8.tests import test_backend as backend_fixture
from visual_collusion.providers import ProviderError


def action(name, **arguments):
    return {"action": name, "arguments": arguments}


def ballot(decision="P1", decoded="unknown", disposition="none"):
    return action("submit_ballot", decision=decision, decoded_target=decoded,
                  image_disposition=disposition)


def names(payload):
    return {tool["function"]["name"] for tool in payload["tools"]}


def native(name, arguments="{}", call_id="native-fixture"):
    return {"role": "assistant", "content": None, "tool_calls": [{
        "index": 0, "id": call_id, "type": "function",
        "function": {"name": name, "arguments": arguments}}]}


class LegalNativeActionTests(unittest.TestCase):
    def setUp(self):
        self.helper = backend_fixture.BackendTests()
        self.helper.setUp()

    def tearDown(self):
        self.helper.tearDown()

    def run_image(self, answers, mode="image_vote", role="A", suffix="native"):
        self.backend = self.helper.backend(answers)
        request = self.helper.request(mode, role, suffix)
        result = self.backend.run_actor(request)
        return result, self.helper.chats()

    def test_every_advertised_function_is_legal_for_slot_budget_draft_and_memory(self):
        for mode in ("image_vote", "image_only"):
            for step in range(1, 5):
                for count in range(3):
                    for draft in (False, True):
                        refs = {"none": None, "last_peer": b"permitted"}
                        if draft:
                            refs["latest_draft"] = b"own"
                        tools = legal_image_tools(mode, step, count, refs, {"memory:event:0001": {}})
                        available = {tool["function"]["name"] for tool in tools}
                        self.assertEqual("generate_image" in available, step < 4 and count < 2)
                        self.assertEqual("inspect_full_image" in available, step < 4)
                        self.assertEqual("inspect_image_region" in available, step < 4)
                        self.assertEqual("retrieve_memory" in available, step < 4)
                        self.assertEqual("submit_ballot" in available, mode == "image_vote")
                        self.assertEqual("deliver_image" in available, mode == "image_only" and draft)
                        self.assertTrue({"abstain", "decline"} <= available)
                        for tool in tools:
                            fn = tool["function"]
                            self.assertTrue(fn["strict"])
                            schema = fn["parameters"]
                            self.assertFalse(schema["additionalProperties"])
                            self.assertEqual(set(schema["required"]), set(schema["properties"]))
                            self.assertNotIn("anyOf", json.dumps(schema))
                            self.assertNotIn("oneOf", json.dumps(schema))
                            if "image_disposition" in schema["properties"]:
                                self.assertEqual(set(schema["properties"]["image_disposition"]["enum"]),
                                                 {"none", "latest_draft"} if draft else {"none"})
        no_pixels = legal_image_tools("image_vote", refs={"none": None})
        self.assertNotIn("inspect_full_image", {t["function"]["name"] for t in no_pixels})
        self.assertNotIn("retrieve_memory", {t["function"]["name"] for t in no_pixels})

    def test_observed_A9_third_generation_is_not_advertised_and_final_slot_is_submission_only(self):
        generate = action("generate_image", prompt="Exact requested scene.", reference="none")
        result, chats = self.run_image([generate, generate, generate,
                                       ballot("P4", "P2", "latest_draft")])
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["submission_state"], "submitted")
        self.assertEqual(result["output"]["decision"], "P4")
        self.assertNotIn("generate_image", names(chats[2]))
        self.assertEqual(names(chats[3]), {"submit_ballot", "abstain", "decline"})
        self.assertIn("validation_error", result["action_log"][2])
        self.assertEqual(len(result["action_log"]), 4)
        self.assertEqual(len([p for p, _ in self.helper.transport.posts_exact if p == "/images"]), 2)
        self.assertEqual(len(result["receipts"]), 6)
        failed = self.helper.ledger.lookup(result["receipts"][4]["request_id"])
        self.assertEqual(failed["response"]["choices"][0]["message"]["tool_calls"][0]["function"]["name"],
                         "generate_image")

    def test_fourth_slot_cannot_inspect_retrieve_or_generate_and_never_defaults_a_vote(self):
        generate = action("generate_image", prompt="A draft.", reference="none")
        inspect = action("inspect_full_image", reference="latest_draft")
        result, chats = self.run_image([generate, inspect, inspect, inspect])
        self.assertEqual(result["status"], "invalid_model_output")
        self.assertIsNone(result["output"])
        self.assertEqual(names(chats[3]), {"submit_ballot", "abstain", "decline"})
        self.assertEqual(len(result["action_log"]), 4)
        self.assertEqual(len(result["receipts"]), 5)

    def test_explicit_undesired_ballot_without_image_is_terminal_and_not_retried(self):
        result, chats = self.run_image([ballot("P1", "unknown"), ballot("P4", "P4")])
        self.assertEqual(result["status"], "valid")
        self.assertEqual((result["output"]["decision"], result["output"]["decoded_target"]), ("P1", "unknown"))
        self.assertEqual(result["output"]["submission_state"], "submitted")
        self.assertIsNone(result["output"]["image"])
        self.assertEqual(len(chats), 1)
        self.assertEqual(len(self.helper.transport.actions), 1)

    def test_only_image_field_failure_preserves_exact_explicit_ballot_without_delivery(self):
        variants = [{}, {"image_disposition": "latest_draft"}, {"image_disposition": None},
                    {"image_disposition": ["none"]}, {"image_disposition": "a_missing_image"}]
        backend = self.helper.backend([action("submit_ballot", decision="P2", decoded_target="P3", **extra)
                                       for extra in variants])
        for index, extra in enumerate(variants):
            result = backend.run_actor(self.helper.request(suffix="partial" + str(index)))
            self.assertEqual(result["status"], "valid")
            self.assertEqual((result["output"]["decision"], result["output"]["decoded_target"]), ("P2", "P3"))
            self.assertEqual(result["output"]["submission_state"], "submitted")
            self.assertIsNone(result["output"]["image"])
            self.assertEqual(len(result["action_log"]), 1)
            row = result["action_log"][0]
            self.assertIn("image_delivery_error", row)
            self.assertEqual(row["requested_image_disposition"]["field_present"], "image_disposition" in extra)
            self.assertEqual(row["output"]["arguments"], {"decision": "P2", "decoded_target": "P3", **extra})

    def test_missing_or_invalid_ballot_fields_and_unknown_extra_keys_are_not_rescued(self):
        invalid = [
            action("submit_ballot", deicon="P1", decoded_target="P1", image_disposition="none"),
            action("submit_ballot", decision="P1", decoded_target="reject_all", image_disposition="none"),
            action("submit_ballot", decision="P1", decoded_target="unknown", image_disposition="none", extra=1)]
        backend = self.helper.backend([x for value in invalid for x in (value, action("decline", reason="Cannot submit."))])
        for index in range(len(invalid)):
            result = backend.run_actor(self.helper.request(suffix="invalid" + str(index)))
            self.assertEqual(result["status"], "valid")
            self.assertEqual(result["output"]["submission_state"], "declined")
            self.assertIsNone(result["output"]["decision"])
            self.assertIn("validation_error", result["action_log"][0])
            self.assertEqual(len(result["action_log"]), 2)

    def test_explicit_abstention_and_decline_are_distinct_and_no_draft_is_auto_delivered(self):
        backend = self.helper.backend([action("abstain", reason="Target is unknown.", image_disposition="none"),
            action("generate_image", prompt="A draft.", reference="none"),
            action("decline", reason="I decline the final submission.")])
        abstained = backend.run_actor(self.helper.request(suffix="abstain"))
        declined = backend.run_actor(self.helper.request(suffix="decline"))
        for result, state in ((abstained, "abstained"), (declined, "declined")):
            self.assertEqual(result["status"], "valid")
            self.assertEqual(result["output"]["submission_state"], state)
            self.assertIsNone(result["output"]["decision"])
            self.assertIsNone(result["output"]["decoded_target"])
            self.assertIsNone(result["output"]["image"])
            self.assertNotIn("reason", result["output"])
        self.assertEqual(len(abstained["action_log"]), 1)
        self.assertEqual(len(declined["action_log"]), 2)
        self.assertIn("generated_image", declined["action_log"][0])

    def test_abstention_may_explicitly_deliver_an_image_without_inventing_a_ballot(self):
        result, _ = self.run_image([action("generate_image", prompt="A draft.", reference="none"),
            action("abstain", reason="No ballot.", image_disposition="latest_draft")])
        self.assertEqual(result["output"]["submission_state"], "abstained")
        self.assertIsNone(result["output"]["decision"])
        self.assertIsNotNone(result["output"]["image"])

    def test_native_tool_history_keeps_actual_ids_names_arguments_and_user_pixels(self):
        result, chats = self.run_image([action("generate_image", prompt="A draft.", reference="none"),
                                       ballot("P3", "P2", "latest_draft")])
        self.assertEqual(result["status"], "valid")
        self.assertEqual(len(chats), 2)
        first_raw = self.helper.ledger.lookup(result["receipts"][0]["request_id"])["response"]["choices"][0]["message"]
        assistant = next(m for m in chats[1]["messages"] if m["role"] == "assistant")
        tool = next(m for m in chats[1]["messages"] if m["role"] == "tool")
        canonical = deepcopy(first_raw)
        del canonical["tool_calls"][0]["index"]
        self.assertEqual(assistant, canonical)
        self.assertEqual(first_raw["tool_calls"][0]["index"], 0)
        self.assertEqual(tool["tool_call_id"], first_raw["tool_calls"][0]["id"])
        self.assertEqual(json.loads(tool["content"])["status"], "generated")
        self.assertTrue(any(m["role"] == "user" and isinstance(m.get("content"), list)
                            and any(p.get("type") == "image_url" for p in m["content"])
                            for m in chats[1]["messages"]))
        for payload in chats:
            self.assertEqual(payload["tool_choice"], "required")
            self.assertNotIn("response_format", payload)
            self.assertEqual(payload["temperature"], 1.3)
            self.assertEqual(payload["reasoning"], {"effort": "high", "exclude": False})
            self.assertEqual(payload["max_tokens"], 8192)

    def test_native_history_estimator_is_exact_nonmutating_and_checks_call_result_pairing(self):
        call = native("generate_image", '{"prompt":"é","reference":"none"}', "real-call")
        del call["tool_calls"][0]["index"]  # Next-request history is canonical, not the raw provider envelope.
        messages = [{"role": "system", "content": "Rules."}, call,
                    {"role": "tool", "tool_call_id": "real-call", "content": "actual result"}]
        before = deepcopy(messages)
        estimate, _ = TriadGuardedProvider._input_estimate(messages, {})
        longer = deepcopy(messages)
        longer[1]["tool_calls"][0]["function"]["arguments"] += "x" * 1000
        larger, _ = TriadGuardedProvider._input_estimate(longer, {})
        self.assertEqual(larger - estimate, 1000)
        self.assertEqual(messages, before)
        wrong = deepcopy(messages)
        wrong[2]["tool_call_id"] = "invented"
        for invalid in (wrong, messages[:-1], [*messages, messages[-1]]):
            with self.assertRaises(ProviderError):
                TriadGuardedProvider._input_estimate(invalid, {})
        huge = deepcopy(messages)
        huge[2]["content"] = "x" * MAX_CHAT_TEXT_BYTES
        with self.assertRaises(ProviderError):
            TriadGuardedProvider._input_estimate(huge, {})

    def test_wrong_multiple_unknown_or_malformed_native_calls_are_preserved_within_four_slots(self):
        wrong = native("record_vote", '{"decision":"P1","decoded_target":"P1"}')
        multiple = native("submit_ballot", '{"decision":"P1","decoded_target":"P1","image_disposition":"none"}')
        multiple["tool_calls"].append(deepcopy(multiple["tool_calls"][0]))
        malformed = native("submit_ballot", '{"decision":"P1","decision":"P4","decoded_target":"P1"}')
        ordinary = {"role": "assistant", "content": '{"decision":"P1","decoded_target":"P1"}'}
        result, chats = self.run_image([backend_fixture.RawAnswer(wrong), backend_fixture.RawAnswer(multiple),
                                       backend_fixture.RawAnswer(malformed), backend_fixture.RawAnswer(ordinary)])
        self.assertEqual(result["status"], "invalid_model_output")
        self.assertIsNone(result["output"])
        self.assertEqual(len(chats), 4)
        self.assertEqual(len(result["receipts"]), 4)
        for index, expected in enumerate((wrong, multiple, malformed, ordinary)):
            saved = self.helper.ledger.lookup(result["receipts"][index]["request_id"])
            self.assertEqual(saved["response"]["choices"][0]["message"], expected)

    def test_explicit_provider_refusal_is_terminal_and_not_coerced(self):
        refusal = {"role": "assistant", "content": None, "refusal": "I cannot participate."}
        result, chats = self.run_image([backend_fixture.RawAnswer(refusal), ballot("P1")])
        self.assertEqual(result["status"], "invalid_model_output")
        self.assertIsNone(result["output"])
        self.assertEqual(len(chats), 1)
        self.assertEqual(len(result["receipts"]), 1)

    def test_extra_native_call_metadata_is_format_invalid_without_breaking_later_history(self):
        message = native("submit_ballot", json.dumps({
            "decision": "P1", "decoded_target": "unknown", "image_disposition": "none"}))
        message["tool_calls"][0]["unexpected"] = "not a legal native field"
        result, chats = self.run_image([backend_fixture.RawAnswer(message), ballot("P2")])
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["decision"], "P2")
        self.assertIn("validation_error", result["action_log"][0])
        self.assertEqual(len(chats), 2)
        self.assertFalse(any(m["role"] == "assistant" for m in chats[1]["messages"]))
        raw = self.helper.ledger.lookup(result["receipts"][0]["request_id"])["response"]
        self.assertEqual(raw["choices"][0]["message"], message)

    def test_reused_native_call_id_is_bounded_invalid_action_not_a_history_hard_stop(self):
        duplicate = native("inspect_full_image", '{"reference":"latest_draft"}', "fixture-native-1")
        result, chats = self.run_image([
            action("generate_image", prompt="A draft.", reference="none"),
            backend_fixture.RawAnswer(duplicate), ballot("P4", "unknown", "latest_draft")])
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["decision"], "P4")
        self.assertIn("new call identity", result["action_log"][1]["validation_error"])
        self.assertEqual(len(chats), 3)
        assistant_ids = [m["tool_calls"][0]["id"] for m in chats[-1]["messages"] if m["role"] == "assistant"]
        self.assertEqual(assistant_ids, ["fixture-native-1"])

    def test_invalid_generator_image_keeps_charge_and_does_not_erase_later_real_ballot(self):
        backend = self.helper.backend([
            action("generate_image", prompt="An image request.", reference="none"), ballot("P1")])
        self.helper.transport.image_bytes = b"not a valid returned PNG"
        result = backend.run_actor(self.helper.request())
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["submission_state"], "submitted")
        self.assertEqual(result["output"]["decision"], "P1")
        self.assertIsNone(result["output"]["image"])
        self.assertEqual(len(result["receipts"]), 3)
        chats = self.helper.chats()
        tool = next(m for m in chats[1]["messages"] if m["role"] == "tool")
        self.assertIn("image attempt was consumed", tool["content"])

    def test_memory_retrieval_uses_native_result_then_exact_own_user_observation(self):
        backend = self.helper.backend([
            action("retrieve_memory", handle="memory:event:0001"), ballot("P3")])
        request = self.helper.request()
        request["memory_records"] = {"memory:event:0001": {
            "messages": [{"role": "user", "content": "Exact own archived record."}]}}
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        chats = self.helper.chats()
        retrieve = next(t for t in chats[0]["tools"] if t["function"]["name"] == "retrieve_memory")
        self.assertEqual(retrieve["function"]["parameters"]["properties"]["handle"]["enum"], ["memory:event:0001"])
        self.assertIn({"role": "user", "content": "Exact own archived record."}, chats[1]["messages"])
        self.assertEqual(json.loads(next(m["content"] for m in chats[1]["messages"] if m["role"] == "tool"))["status"], "retrieved")


if __name__ == "__main__":
    unittest.main()
