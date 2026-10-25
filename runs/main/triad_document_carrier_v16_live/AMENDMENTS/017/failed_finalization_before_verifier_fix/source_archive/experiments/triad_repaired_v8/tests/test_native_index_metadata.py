"""Offline regressions for real OpenRouter native-call index=0 metadata.

Fixtures include the recorded provider envelope shape, while raw provider
responses remain separate from the canonical assistant history sent next.
No actual logs, provider calls, or real ledger are used by these tests.
"""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from visual_collusion.archive import hydrate_messages
from visual_collusion.providers import ProviderError, ResponseValidationError
from experiments.triad_repaired_v8 import backend as adapter
from experiments.triad_repaired_v8.tests import test_backend as fixtures


def message(name, arguments, *, index=0):
    return {"role": "assistant", "content": None, "refusal": None,
        "reasoning": "OFFLINE_FIXTURE self-report only.", "reasoning_details": [],
        "tool_calls": [{"index": index, "id": "fixture-standard-native-call", "type": "function",
            "function": {"name": name, "arguments": arguments}}]}


class NativeIndexMetadataTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.BackendTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)

    def parse(self, mode, answer, *, finish_reason="tool_calls"):
        backend = self.helper.backend([], preflight=False)
        response = {"id": "OFFLINE_FIXTURE_RESPONSE", "model": adapter.MODEL,
            "provider": "OpenInference", "usage": {"cost": .001},
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": deepcopy(answer)}]}
        before = deepcopy(response)
        receipt = {"request_id": "OFFLINE_FIXTURE_RECEIPT", "cost_usd": .001}
        flags = ({"native_log": True} if mode == "memory_only" else
                 {"native_vote": True} if mode == "vote_only" else
                 {"native_tools": adapter.legal_image_tools(mode)})
        with patch.object(backend.provider, "_paid", return_value=(response, receipt, False)) as paid:
            result = backend.provider._chat_result("OFFLINE_FIXTURE_PARSE", [
                {"role": "system", "content": "OFFLINE_FIXTURE only."},
                {"role": "user", "content": "Record your explicit result."}], **flags)
        self.assertEqual(paid.call_count, 1)
        self.assertEqual(response, before)
        self.assertEqual(self.helper.transport.posts_exact, [])
        return result, before

    def test_standard_index_zero_and_absent_index_parse_in_all_modes(self):
        cases = [("memory_only", "record_log", self.helper.log()),
                 ("vote_only", "record_vote", {"decision": "P1", "decoded_target": "unknown"}),
                 ("image_vote", "submit_ballot", {"decision": "P1", "decoded_target": "unknown", "image_disposition": "none"}),
                 ("image_only", "abstain", {"reason": "OFFLINE_FIXTURE", "image_disposition": "none"})]
        for mode, name, arguments in cases:
            for has_index in (True, False):
                with self.subTest(mode=mode, has_index=has_index):
                    answer = message(name, json.dumps(arguments, ensure_ascii=False))
                    if not has_index:
                        del answer["tool_calls"][0]["index"]
                    result, original = self.parse(mode, answer)
                    expected = arguments if mode in {"memory_only", "vote_only"} else {"action": name, "arguments": arguments}
                    self.assertEqual(result["content"], expected)
                    native = result["native_message"]
                    self.assertEqual(set(native), {"role", "content", "tool_calls"})
                    self.assertEqual(set(native["tool_calls"][0]), {"id", "type", "function"})
                    self.assertEqual(native["tool_calls"][0]["id"], answer["tool_calls"][0]["id"])
                    self.assertEqual(native["tool_calls"][0]["function"], answer["tool_calls"][0]["function"])
                    self.assertEqual(result["raw"], original)

    def test_negative_boolean_nonzero_and_unknown_metadata_remain_invalid(self):
        invalid = []
        for value in (-1, 1, True, False, "0", 0.0, None, {}, []):
            invalid.append(message("record_log", json.dumps(self.helper.log()), index=value))
        extra = message("record_log", json.dumps(self.helper.log()))
        extra["tool_calls"][0]["unexpected"] = "OFFLINE_FIXTURE"
        invalid.append(extra)
        nested = message("record_log", json.dumps(self.helper.log()))
        nested["tool_calls"][0]["function"]["index"] = 0
        invalid.append(nested)
        duplicate = message("record_log", json.dumps(self.helper.log()))
        duplicate["tool_calls"].append(deepcopy(duplicate["tool_calls"][0]))
        invalid.append(duplicate)
        malformed = message("record_log", '{"private_note":"unfinished')
        invalid.append(malformed)
        duplicated_argument = message("record_log", '{"private_note":"a","private_note":"b","reflection":"c"}')
        invalid.append(duplicated_argument)
        for index, answer in enumerate(invalid):
            with self.subTest(case=index):
                with self.assertRaises(ResponseValidationError):
                    self.parse("memory_only", answer)

    def test_default_native_fixture_includes_index_zero_without_format_retries(self):
        generate = {"action": "generate_image", "prompt": "OFFLINE_FIXTURE scene.", "reference": "none"}
        cases = [("memory_only", "A", [self.helper.log()]),
                 ("memory_only", "B", [self.helper.log()]),
                 ("memory_only", "C", [self.helper.log()]),
                 ("vote_only", "C", [{"decision": "P1", "decoded_target": "unknown"}]),
                 ("image_vote", "A", [{"action": "submit_ballot", "decision": "P1", "decoded_target": "unknown", "image_disposition": "none"}]),
                 ("image_vote", "B", [{"action": "abstain", "reason": "OFFLINE_FIXTURE", "image_disposition": "none"}]),
                 ("image_only", "C", [generate, {"action": "deliver_image"}])]
        for index, (mode, role, actions) in enumerate(cases):
            with self.subTest(mode=mode, role=role):
                backend = self.helper.backend(actions)
                result = backend.run_actor(self.helper.request(mode, role, "standardindex" + str(index)))
                self.assertEqual(result["status"], "valid")
                self.assertEqual(len(self.helper.chats()), len(actions))
                for row in result["action_log"]:
                    self.assertNotIn("validation_error", row)
                    response = self.helper.ledger.lookup(row["receipt_id"])["response"]
                    self.assertEqual(response["choices"][0]["message"]["tool_calls"][0]["index"], 0)

    def test_parser_to_history_to_next_payload_and_wire_preserves_exact_arguments(self):
        prompt = 'OFFLINE_FIXTURE "quoted" scene é with exact spacing.\nNo scientific meaning.'
        actions = [{"action": "generate_image", "prompt": prompt, "reference": "none"},
                   {"action": "inspect_full_image", "reference": "latest_draft"},
                   {"action": "submit_ballot", "decision": "P1", "decoded_target": "unknown", "image_disposition": "latest_draft"}]
        backend = self.helper.backend(actions)
        request = self.helper.request(suffix="nativeindexwire")
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        chats = self.helper.chats()
        self.assertEqual(len(chats), 3)
        raw = self.helper.ledger.lookup(result["action_log"][0]["receipt_id"])["response"]["choices"][0]["message"]
        canonical = deepcopy(raw)
        del canonical["tool_calls"][0]["index"]
        assistant = next(item for item in chats[1]["messages"] if item.get("role") == "assistant")
        self.assertEqual(assistant, canonical)
        self.assertEqual(json.loads(assistant["tool_calls"][0]["function"]["arguments"])["prompt"], prompt)
        self.assertEqual(raw["tool_calls"][0]["index"], 0)
        evidence = backend.evidence_dir
        for step, chat in enumerate(chats, 1):
            archive = json.loads((evidence / "transport_requests" /
                (hashlib.sha256((request["request_id"] + ".step" + str(step)).encode()).hexdigest() + ".json")).read_bytes())
            logical = deepcopy(archive["logical_payload"])
            logical["messages"] = hydrate_messages(evidence, logical["messages"])
            before = deepcopy(logical)
            tokens, image_count = adapter.TriadGuardedProvider._input_estimate(logical["messages"],
                {"tools": logical["tools"], "tool_choice": logical["tool_choice"]})
            self.assertGreater(tokens, 0)
            self.assertEqual(logical, before)
            self.assertEqual(adapter.codec.verify_wire(evidence, "/chat/completions", logical, archive["mapping"]), chat)
            self.assertLess(archive["mapping"]["wire_body_utf8_bytes"], archive["mapping"]["max_bytes"])
            for item in chat["messages"]:
                for call in item.get("tool_calls", []):
                    self.assertEqual(set(call), {"id", "type", "function"})

    def test_canonical_history_budget_counts_original_arguments_and_rejects_raw_metadata(self):
        result, original = self.parse("image_vote", message("generate_image", '{"prompt":"é","reference":"none"}'))
        canonical = result["native_message"]
        identifier = canonical["tool_calls"][0]["id"]
        history = [{"role": "system", "content": "OFFLINE_FIXTURE only."}, canonical,
                   {"role": "tool", "tool_call_id": identifier, "content": "OFFLINE_FIXTURE result."}]
        before = deepcopy(history)
        base, _ = adapter.TriadGuardedProvider._input_estimate(history, {})
        longer = deepcopy(history)
        longer[1]["tool_calls"][0]["function"]["arguments"] += "x" * 1000
        larger, _ = adapter.TriadGuardedProvider._input_estimate(longer, {})
        self.assertEqual(larger - base, 1000)
        self.assertEqual(history, before)
        raw_history = deepcopy(history)
        raw_history[1] = {key: original["choices"][0]["message"][key] for key in ("role", "content", "tool_calls")}
        with self.assertRaises(ProviderError):
            adapter.TriadGuardedProvider._input_estimate(raw_history, {})
        oversized = deepcopy(history)
        oversized[-1]["content"] = "x" * adapter.MAX_CHAT_TEXT_BYTES
        with self.assertRaises(ProviderError):
            adapter.TriadGuardedProvider._input_estimate(oversized, {})


if __name__ == "__main__":
    unittest.main()
