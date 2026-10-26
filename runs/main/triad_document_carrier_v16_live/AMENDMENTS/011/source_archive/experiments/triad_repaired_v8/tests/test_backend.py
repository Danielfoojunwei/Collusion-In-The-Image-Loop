"""Prospective coached adapter with simulated POSTs; no credentials/network used."""
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from email.message import Message
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

from visual_collusion.budget import BudgetLedger, PendingPaidRequest
from visual_collusion.providers import ProviderError
from experiments.pressure_interview_v1.planning import load_config
from experiments.pressure_interview_v1.tests.test_live_provider import ScriptedWireTransport
from experiments.triad_repaired_v8.backend import (
    TriadBackend, TriadGuardedProvider, NAMESPACE, MODEL, MAX_OUTPUT_TOKENS,
    MAX_CHAT_TEXT_BYTES, PUBLIC_TEXT_PATTERN, image_action_schema, legal_image_tools,
    effective_system_prompt, parse_object, repair, build_reliable_image_payload,
    validate_direct, validate_image_action, direct_schema, PRIVATE_LOG_TEMPERATURE,
)
from experiments.triad_repaired_v8.funding import FundingLedger, create_guard, Known429, RateLimitExhausted
from experiments.pressure_interview_v1.contracts import ContractError


@dataclass
class RawAnswer:
    message: dict
    finish_reason: str = "tool_calls"


class NativeWireTransport(ScriptedWireTransport):
    def __init__(self, ledger, actions):
        super().__init__(ledger, actions)
        self.envelope_overrides = {}
        self.transient_failures = []
        for row in self.models["data"]:
            row["supported_parameters"].extend(["temperature", "tools", "tool_choice"])
        for group in self.chat_endpoints.values():
            for endpoint in group["data"]["endpoints"]:
                endpoint["supported_parameters"].extend(["temperature", "tools", "tool_choice"])

    def __call__(self, method, path, body):
        if method == "GET":
            return super().__call__(method, path, body)
        action = self.actions[0] if path == "/chat/completions" and self.actions else None
        if isinstance(action, RawAnswer):
            self.actions[0] = "unused raw native content"
        saved_failure = self.failure
        if self.transient_failures:
            self.failure = self.transient_failures.pop(0)
        try:
            result = super().__call__(method, path, body)
        finally:
            self.failure = saved_failure
        if path != "/chat/completions":
            return result
        payload = json.loads(body)
        if isinstance(action, RawAnswer):
            result["choices"] = [{"index": 0, "finish_reason": action.finish_reason, "message": deepcopy(action.message)}]
        elif payload.get("tools"):
            arguments = result["choices"][0]["message"]["content"]
            if payload["tool_choice"] == "required":
                if isinstance(action, dict) and "action" in action:
                    function_name = action["action"]
                    arguments = json.dumps(action["arguments"], ensure_ascii=False)
                else:
                    function_name = payload["tools"][0]["function"]["name"]
            else:
                function_name = payload["tool_choice"]["function"]["name"]
            result["choices"] = [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "index": 0, "id": "fixture-native-" + str(len(self.posts_exact)), "type": "function",
                    "function": {"name": function_name, "arguments": arguments}}]}}]
        result.update(deepcopy(self.envelope_overrides))
        return result


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        BudgetLedger(self.root / "budget.sqlite3")
        self.ledger = FundingLedger(self.root / "budget.sqlite3")
        self.config = load_config()
        self.config["models"]["C"] = MODEL
        self.config["routes"] = {"A_and_B_and_C": "open-inference/fp4", "image": "alibaba", "fallbacks": False}
        self.config["budget"]["namespace"] = NAMESPACE
        self.config["sampling"] = {"temperature": 1.3, "private_logging_temperature": 0.3}
        self.config["reasoning"] = {"effort": "high"}
        self.config["max_output_tokens"] = MAX_OUTPUT_TOKENS
        self.config["log_format_attempts"] = 2
        self.config["vote_format_attempts"] = 2
        self.config["context"]["max_text_bytes"] = MAX_CHAT_TEXT_BYTES
        self.delays = []

    def tearDown(self):
        self.temp.cleanup()

    def backend(self, actions, *, preflight=True):
        out = self.root / "run"
        self.guard = create_guard(self.ledger.path, NAMESPACE, out / "provider_evidence",
                                  ledger=self.ledger, sleeper=self.delays.append)
        wrapped = [self.wire_action(value) for value in actions]
        self.transport = NativeWireTransport(self.ledger, wrapped)
        return TriadBackend(self.config, out, self.ledger.path, guard=self.guard,
            api_key="FIXTURE_ONLY_NOT_A_CREDENTIAL", transport=self.transport, preflight=preflight)

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
    def log():
        return {"private_note": 'Keep "quoted" text and unicode é.',
                "reflection": "Actual pixels show the requested scene; recipient effect remains unknown."}

    @staticmethod
    def native_message(arguments=None, **changes):
        value = {"role": "assistant", "content": None, "tool_calls": [{
            "index": 0, "id": "native-original-id", "type": "function", "function": {
                "name": "record_log", "arguments": json.dumps(BackendTests.log()) if arguments is None else arguments}}]}
        value.update(changes)
        return value

    @staticmethod
    def native_vote_message(arguments=None, **changes):
        value = BackendTests.native_message(
            json.dumps({"decision": "P1", "decoded_target": "unknown"}) if arguments is None else arguments,
            **changes)
        value["tool_calls"][0]["function"]["name"] = "record_vote"
        return value

    def chats(self):
        return [json.loads(body) for path, body in self.transport.posts_exact if path == "/chat/completions"]

    def test_A_and_B_images_votes_no_public_text_exact_pixels_and_cached_resume(self):
        backend = self.backend([item for _ in range(2) for item in [
            {"action": "generate_image", "prompt": "A freely composed landscape.", "reference": "none"},
            {"action": "submit_ballot", "decision": "P4", "decoded_target": "P4", "image_disposition": "latest_draft"}]])
        for role in "AB":
            request = self.request("image_vote", role, role)
            result = backend.run_actor(request)
            self.assertEqual(result["status"], "valid")
            self.assertEqual(result["output"]["decision"], "P4")
            self.assertEqual(result["output"]["decoded_target"], "P4")
            self.assertIsNone(result["output"]["rationale"])
            self.assertIsNone(result["output"]["message"])
            self.assertEqual(Path(result["output"]["image"]["path"]).read_bytes(), self.transport.image_bytes)
            self.assertEqual(backend.run_actor(request), result)
        self.assertEqual(len(self.transport.posts_exact), 6)
        self.assertNotIn("FORBIDDEN_METADATA_CANARY", repr(self.transport.posts_exact))

    def test_C_image_only_delivery_has_empty_arguments_and_no_log(self):
        backend = self.backend([
            {"action": "generate_image", "prompt": "An abstract sculpture.", "reference": "none"},
            {"action": "deliver_image"}])
        result = backend.run_actor(self.request("image_only", "C"))
        self.assertEqual(result["status"], "valid")
        self.assertIsNotNone(result["output"]["image"])
        self.assertIsNone(result["output"]["decision"])
        self.assertIsNone(result["output"]["decoded_target"])
        self.assertIsNone(result["output"]["rationale"])
        self.assertEqual(result["output"]["private_note"], "")
        self.assertEqual(result["output"]["reflection"], "")
        delivery = next(t["function"] for t in legal_image_tools("image_only", refs={"latest_draft": b"draft"})
                        if t["function"]["name"] == "deliver_image")
        self.assertEqual(delivery["parameters"]["properties"], {})

    def test_all_three_roles_native_tool_preserves_full_strings_and_actual_sampling(self):
        record = self.log()
        record["reflection"] = "é" * 4000
        backend = self.backend([record] * 3)
        for role in "ABC":
            result = backend.run_actor(self.request("memory_only", role, role))
            self.assertEqual(result["status"], "valid")
            for key in record:
                self.assertEqual(result["output"][key], record[key])
            self.assertTrue(result["receipts"][0]["request_id"].endswith(".attempt1"))
            minimum = Decimal(8192 * 2) * repair.REPAIRED_PROFILE["completion_ceiling"]
            self.assertGreater(Decimal(str(result["receipts"][0]["reserved_usd"])), minimum)
        for payload in self.chats():
            self.assertNotIn("response_format", payload)
            self.assertNotIn("parallel_tool_calls", payload)
            self.assertEqual(payload["tool_choice"], {"type": "function", "function": {"name": "record_log"}})
            self.assertTrue(payload["tools"][0]["function"]["strict"])
            self.assertEqual(set(payload["tools"][0]["function"]["parameters"]["properties"]), {"private_note", "reflection"})
            self.assertEqual(payload["temperature"], PRIVATE_LOG_TEMPERATURE)
            self.assertEqual(payload["reasoning"], {"effort": "high", "exclude": False})
            self.assertEqual(payload["max_tokens"], 8192)
            self.assertEqual(payload["provider"]["only"], ["open-inference/fp4"])
            self.assertFalse(payload["provider"]["allow_fallbacks"])
            self.assertTrue(payload["provider"]["require_parameters"])

    def test_log_second_attempt_is_error_only_and_first_raw_call_is_retained(self):
        bad = self.native_message('{"private_note":"missing reflection"}')
        backend = self.backend([RawAnswer(bad), self.log()])
        request = self.request("memory_only", "A")
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        self.assertIn("validation_error", result["action_log"][0])
        first, second = self.chats()
        self.assertEqual(second["messages"][:-1], first["messages"])
        self.assertEqual(second["messages"][-1]["role"], "user")
        self.assertIn("Technical output-format error:", second["messages"][-1]["content"])
        self.assertEqual([row["role"] for row in second["messages"]], ["system", "user", "user"])
        raw = self.ledger.lookup(result["receipts"][0]["request_id"])["response"]["choices"][0]
        self.assertEqual(raw["message"], bad)
        self.assertEqual(raw["finish_reason"], "tool_calls")
        self.assertEqual(backend.run_actor(request), result)
        self.assertEqual(len(self.transport.posts_exact), 2)

    def test_bad_native_shapes_have_exactly_two_attempts_and_no_coercion(self):
        wrong = self.native_message()
        wrong["tool_calls"][0]["function"]["name"] = "wrong_function"
        missing = self.native_message()
        del missing["tool_calls"][0]["id"]
        multiple = self.native_message()
        multiple["tool_calls"].append(deepcopy(multiple["tool_calls"][0]))
        cases = [RawAnswer(wrong), RawAnswer(missing), RawAnswer(multiple),
            RawAnswer({"role": "assistant", "content": json.dumps(self.log())}),
            RawAnswer(self.native_message(refusal="Cannot record")),
            RawAnswer(self.native_message(), "length"),
            RawAnswer(self.native_message('{"private_note":"one","private_note":"two","reflection":"three"}')),
            RawAnswer(self.native_message('{"private_note":"one","reflection":NaN}')),
            RawAnswer(self.native_message('{"private_note":"one","reflection":1e999}')),
            RawAnswer(self.native_message('```json\n' + json.dumps(self.log()) + '\n```')),
            RawAnswer(self.native_message('{"private_note":"one","reflection":{}}')),
            RawAnswer(self.native_message('{"private_note":"one","reflection":"two","extra":0}'))]
        backend = self.backend([answer for answer in cases for _ in range(2)])
        for index in range(len(cases)):
            with self.subTest(index=index):
                result = backend.run_actor(self.request("memory_only", "B", "bad" + str(index)))
                self.assertEqual(result["status"], "invalid_model_output")
                self.assertIsNone(result["output"])
                self.assertEqual(len(result["action_log"]), 2)
        self.assertEqual(len(self.transport.posts_exact), len(cases) * 2)

    def test_valid_native_call_can_use_stop_finish(self):
        backend = self.backend([RawAnswer(self.native_message(), "stop")])
        self.assertEqual(backend.run_actor(self.request("memory_only", "C"))["status"], "valid")

    def test_valid_votes_are_final_and_never_semantically_corrected(self):
        answers = [{"decision": value, "decoded_target": "unknown"} for value in ["P1", "P2", "P3", "P4", "reject_all"]]
        backend = self.backend(answers)
        for index, answer in enumerate(answers):
            result = backend.run_actor(self.request("vote_only", "C", "vote" + str(index)))
            self.assertEqual(result["status"], "valid")
            self.assertEqual(len(result["action_log"]), 1)
            self.assertEqual(result["output"]["decision"], answer["decision"])
            self.assertEqual(result["output"]["decoded_target"], "unknown")
            self.assertIsNone(result["output"]["rationale"])
        self.assertEqual(len(self.transport.posts_exact), len(answers))

    def test_native_vote_invalid_arguments_have_only_two_format_attempts(self):
        invalid = [
            '{"deicon":"P1","decoded_target":"P1"}',
            '{"decision":"P4","decoded_target":"P2"}}',
            '{"decision":"P4","decision":"P1","decoded_target":"P2"}',
            '{"decision":"P4","decoded_target":"P2"} {}',
            {"decision": "P4", "decoded_target": "reject_all"},
            {"decision": "P4", "decoded_target": "P4", "rationale": "unrequested"},
            {"decision": "P4"},
            {"decision": "P4", "decoded_target": None}]
        backend = self.backend([answer for answer in invalid for _ in range(2)])
        for index in range(len(invalid)):
            result = backend.run_actor(self.request("vote_only", "C", "invalid" + str(index)))
            self.assertEqual(result["status"], "invalid_model_output")
            self.assertIsNone(result["output"])
            self.assertEqual(len(result["action_log"]), 2)
        self.assertEqual(len(self.transport.posts_exact), len(invalid) * 2)

    def test_binding_and_free_metadata_reject_unavailable_parameters_and_output_budget(self):
        backend = self.backend([], preflight=False)
        for owner in (next(row for row in self.transport.models["data"] if row["id"] == MODEL),
                      self.transport.chat_endpoints[MODEL]["data"]["endpoints"][0]):
            for parameter in ("temperature", "tools", "tool_choice"):
                owner["supported_parameters"].remove(parameter)
                with self.assertRaises(ContractError):
                    backend.preflight()
                owner["supported_parameters"].append(parameter)
        endpoint = self.transport.chat_endpoints[MODEL]["data"]["endpoints"][0]
        endpoint["max_completion_tokens"] = 4096
        with self.assertRaises(ContractError):
            backend.preflight()
        for key, value in [("max_output_tokens", 4096), ("log_format_attempts", 3), ("vote_format_attempts", 3)]:
            config = deepcopy(self.config)
            config[key] = value
            with self.assertRaises(ContractError):
                backend.verify_binding(config)
        self.assertFalse(self.transport.posts_exact)

    def test_image_action_errors_consume_existing_allowance_and_two_images_max(self):
        generate = {"action": "generate_image", "prompt": "A diagram.", "reference": "none"}
        backend = self.backend([generate, generate, generate,
            {"action": "submit_ballot", "decision": "P4", "decoded_target": "P4", "image_disposition": "latest_draft"}])
        result = backend.run_actor(self.request())
        self.assertEqual(result["status"], "valid")
        self.assertIn("validation_error", result["action_log"][2])
        self.assertNotIn("generate_image", {t["function"]["name"] for t in self.chats()[2]["tools"]})
        self.assertEqual(len([path for path, _ in self.transport.posts_exact if path == "/images"]), 2)
        self.assertEqual(len(result["action_log"]), 4)

    def test_bad_image_json_consumes_action_without_syntax_recovery(self):
        backend = self.backend(["not json",
            {"action": "generate_image", "prompt": "A diagram.", "reference": "none"},
            {"action": "submit_ballot", "decision": "P4", "decoded_target": "P4", "image_disposition": "latest_draft"}])
        result = backend.run_actor(self.request())
        self.assertEqual(result["status"], "valid")
        self.assertIsNone(result["action_log"][0]["output"])
        self.assertIn("validation_error", result["action_log"][0])
        self.assertEqual(len(self.transport.posts_exact), 4)

    def test_transport_loss_never_gets_a_log_format_retry(self):
        backend = self.backend([])
        self.transport.failure = urllib.error.URLError(TimeoutError())
        with self.assertRaises(PendingPaidRequest):
            backend.run_actor(self.request("memory_only", "A"))
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_identity_or_byok_mismatch_never_gets_a_log_format_retry(self):
        backend = self.backend([self.log()])
        self.transport.envelope_overrides = {"model": "wrong-model"}
        with self.assertRaises(ProviderError):
            backend.run_actor(self.request("memory_only", "A"))
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_observed429_retries_exact_bytes_with_distinct_unknown_reserve(self):
        backend = self.backend([self.log()])
        self.transport.transient_failures = [Known429(retry_after_seconds=6), None]
        result = backend.run_actor(self.request("memory_only", "A"))
        self.assertEqual(result["status"], "valid")
        self.assertEqual(self.transport.posts_exact[0], self.transport.posts_exact[1])
        self.assertTrue(result["receipts"][0]["request_id"].endswith(".step1.attempt2"))
        self.assertEqual(len(result["action_log"]), 1)
        self.assertGreater(self.ledger.summary()["reserved_unresolved_usd"], 0)
        self.assertEqual(len(self.delays), 1)
        self.assertEqual(backend.run_actor(self.request("memory_only", "A")), result)
        self.assertEqual(len(self.transport.posts_exact), 2)
        actual = [json.loads(path.read_text()) for path in (backend.evidence_dir / "transport_requests").glob("*.json")
                  if "attempt_specific_binding" in path.read_text()]
        self.assertEqual({item["request_id"] for item in actual}, {
            NAMESPACE + ":test.r01.one.step1.attempt1", NAMESPACE + ":test.r01.one.step1.attempt2"})

    def test_three_observed429s_exhaust_transport_allowance(self):
        backend = self.backend([self.log()])
        self.transport.failure = Known429()
        with self.assertRaises(RateLimitExhausted):
            backend.run_actor(self.request("memory_only", "A"))
        self.assertEqual(len(self.transport.posts_exact), 3)
        self.assertEqual(len(self.delays), 3)  # 30s + 60s split into <=30s chunks

    def test_native_http429_safe_headers_but_no_error_body_are_archived(self):
        backend = self.backend([self.log()])
        headers = Message()
        headers["Retry-After"] = "7"
        headers["x-request-id"] = "safe-fixture-id"
        error = urllib.error.HTTPError("https://fixture.invalid", 429, "rate limit", headers,
                                       io.BytesIO(b"FORBIDDEN_ERROR_BODY_CANARY"))
        self.transport.transient_failures = [error, None]
        self.assertEqual(backend.run_actor(self.request("memory_only", "C"))["status"], "valid")
        signal = json.loads(next((backend.evidence_dir / "http_status_signals").glob("*.json")).read_text())
        self.assertEqual(signal["provider_request_id"], "safe-fixture-id")
        self.assertNotIn("FORBIDDEN_ERROR_BODY_CANARY", "".join(path.read_text() for path in backend.evidence_dir.rglob("*.json")))

    def test_unsupported_retry_after_halts_without_retry(self):
        backend = self.backend([self.log()])
        headers = Message()
        headers["Retry-After"] = "121"
        self.transport.failure = urllib.error.HTTPError("https://fixture.invalid", 429, "limit", headers, None)
        with self.assertRaises(PendingPaidRequest):
            backend.run_actor(self.request("memory_only", "B"))
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_non429_halts_without_retry(self):
        backend = self.backend([self.log()])
        self.transport.failure = urllib.error.HTTPError("https://fixture.invalid", 500, "error", None, None)
        with self.assertRaises(PendingPaidRequest):
            backend.run_actor(self.request("memory_only", "B"))
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_actual_effective_prompt_matches_archived_prompt(self):
        backend = self.backend([{"decision": "P4", "decoded_target": "P4"}])
        request = self.request("vote_only", "C")
        backend.run_actor(request)
        self.assertEqual(self.chats()[0]["messages"][0]["content"], effective_system_prompt(request))
        record = json.loads(next((backend.evidence_dir / "effective_system_prompts").glob("*.json")).read_text())
        self.assertEqual(record["system_prompt"], effective_system_prompt(request))

    def test_strict_parser_rejects_duplicates_nonfinite_arrays_and_prose(self):
        for text in ['{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}', '[]', '{} {}', 'prefix {}']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_object(text)

    def test_enum_ballots_need_no_rationale_and_preserve_independent_readouts(self):
        backend = self.backend([
            {"action": "generate_image", "prompt": "A technical image.", "reference": "none"},
            {"action": "submit_ballot", "decision": "P1", "decoded_target": "P3", "image_disposition": "latest_draft"},
            {"decision": "reject_all", "decoded_target": "unknown"}])
        image_result = backend.run_actor(self.request("image_vote", "B", "B"))
        vote_result = backend.run_actor(self.request("vote_only", "C", "C"))
        for result, decision, decoded in [(image_result, "P1", "P3"), (vote_result, "reject_all", "unknown")]:
            self.assertEqual(result["status"], "valid")
            self.assertEqual(result["output"]["decision"], decision)
            self.assertEqual(result["output"]["decoded_target"], decoded)
            self.assertIsNone(result["output"]["rationale"])
        chats = self.chats()
        self.assertNotIn("response_format", chats[0])
        delivery_schema = next(t["function"]["parameters"] for t in chats[0]["tools"]
                               if t["function"]["name"] == "submit_ballot")
        self.assertEqual(delivery_schema["properties"]["image_disposition"]["enum"], ["none"])
        self.assertNotIn("response_format", chats[-1])
        self.assertEqual(chats[-1]["tool_choice"], {"type": "function", "function": {"name": "record_vote"}})
        vote_schema = chats[-1]["tools"][0]["function"]["parameters"]
        for schema, keys in [(delivery_schema, {"decision", "decoded_target", "image_disposition"}),
                             (vote_schema, {"decision", "decoded_target"})]:
            self.assertEqual(set(schema["properties"]), keys)
            self.assertEqual(set(schema["required"]), keys)
            self.assertFalse(schema["additionalProperties"])
            self.assertNotIn("maxLength", schema["properties"]["decoded_target"])
        self.assertEqual(len(self.transport.posts_exact), 4)

    def test_legacy_rationale_is_preserved_as_bounded_invalid_action_not_repaired(self):
        legacy = {"action": "submit_ballot", "arguments": {
            "decision": "P3", "decoded_target": "P2", "image_disposition": "latest_draft", "rationale": "x" * 941}}
        current = {"action": "submit_ballot", "decision": "P3", "decoded_target": "P2", "image_disposition": "latest_draft"}
        backend = self.backend([
            {"action": "generate_image", "prompt": "A current image.", "reference": "none"},
            legacy, current])
        result = backend.run_actor(self.request())
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["action_log"][1]["output"], legacy)
        self.assertIn("validation_error", result["action_log"][1])
        self.assertEqual(len(result["action_log"]), 3)
        self.assertEqual((result["output"]["decision"], result["output"]["decoded_target"]), ("P3", "P2"))
        self.assertIsNone(result["output"]["rationale"])
        self.assertEqual(len(self.transport.posts_exact), 4)
        self.assertEqual(backend.run_actor(self.request()), result)
        self.assertEqual(len(self.transport.posts_exact), 4)

    def test_ballot_enum_domains_and_nonballot_text_boundaries(self):
        for decision in ["P1", "P2", "P3", "P4", "reject_all"]:
            for decoded in ["P1", "P2", "P3", "P4", "unknown"]:
                value = {"decision": decision, "decoded_target": decoded}
                self.assertEqual(validate_direct("vote_only", value), value)
        for field in ["decision", "decoded_target"]:
            for invalid in ["", " P1", "P1 ", "p1", "🧪" * 10000, None, False, [], {}]:
                value = {"decision": "P1", "decoded_target": "P1", field: invalid}
                with self.subTest(field=field, invalid=type(invalid).__name__), self.assertRaises(ContractError):
                    validate_direct("vote_only", value)
        self.assertEqual(validate_image_action("image_only", {"action": "decline", "reason": "🧪" * 800}, 1, 0, {}, {})["reason"], "🧪" * 800)
        with self.assertRaises(ContractError):
            validate_image_action("image_only", {"action": "decline", "reason": "🧪" * 801}, 1, 0, {}, {})
        handle = "🧪" * 120
        self.assertEqual(validate_image_action("image_only", {"action": "retrieve_memory", "handle": handle}, 1, 0, {}, {handle: {}})["handle"], handle)
        with self.assertRaises(ContractError):
            validate_image_action("image_only", {"action": "retrieve_memory", "handle": handle + "🧪"}, 1, 0, {}, {handle + "🧪": {}})

    def test_normal_control_schema_client_match_private_controls_stay_exact(self):
        import re
        from experiments.triad_repaired_v8.contracts import PUBLIC_TEXT_PATTERN as CONTRACT_PATTERN
        self.assertEqual(PUBLIC_TEXT_PATTERN, CONTRACT_PATTERN)
        for ordinal in range(32):
            value = "value" + chr(ordinal)
            allowed = chr(ordinal) in "\t\n\r"
            self.assertEqual(re.fullmatch(PUBLIC_TEXT_PATTERN, value) is not None, allowed)
            if allowed:
                self.assertEqual(build_reliable_image_payload(value)["prompt"], value)
            else:
                with self.assertRaises(ProviderError):
                    build_reliable_image_payload(value)
        for malformed in ["", "\ud800"]:
            with self.assertRaises(ProviderError):
                build_reliable_image_payload(malformed)
        private = {"private_note": "\x00\b\r", "reflection": "\x01\x0b\x1f"}
        backend = self.backend([private])
        result = backend.run_actor(self.request("memory_only", "C"))
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["private_note"], private["private_note"])
        self.assertEqual(result["output"]["reflection"], private["reflection"])

    def test3000_multibyte_character_prompt_reaches_exact_image_wire(self):
        from visual_collusion.providers import build_image_payload, MAX_IMAGE_PROMPT_BYTES
        prompt = "🧪" * 3000
        self.assertEqual(len(prompt.encode()), 12000)
        backend = self.backend([
            {"action": "generate_image", "prompt": prompt, "reference": "none"},
            {"action": "submit_ballot", "decision": "P1", "decoded_target": "P4", "image_disposition": "latest_draft"}])
        result = backend.run_actor(self.request())
        self.assertEqual(result["status"], "valid")
        image_wire = json.loads(next(body for path, body in self.transport.posts_exact if path == "/images"))
        self.assertEqual(image_wire["prompt"], prompt)
        self.assertEqual(image_wire["provider"], {"only": ["alibaba"], "allow_fallbacks": False})
        self.assertEqual((image_wire["resolution"], image_wire["aspect_ratio"], image_wire["n"]), ("1K", "1:1", 1))
        self.assertEqual(result["receipts"][1]["reserved_usd"], 0.028)
        with self.assertRaises(ProviderError):
            build_reliable_image_payload(prompt + "🧪")
        with self.assertRaises(ContractError):
            validate_image_action("image_vote", {"action": "generate_image", "prompt": prompt + "🧪", "reference": "none"}, 1, 0, {"none": None}, {})
        self.assertEqual(MAX_IMAGE_PROMPT_BYTES, 4096)
        with self.assertRaises(ProviderError):
            build_image_payload(prompt)  # The frozen builder/global caps remain unchanged.
        reference_payload = build_reliable_image_payload(prompt, input_reference=self.transport.image_bytes)
        self.assertEqual(reference_payload["prompt"], prompt)
        self.assertEqual(len(reference_payload["input_references"]), 1)
        import base64
        reference_url = reference_payload["input_references"][0]["image_url"]["url"]
        self.assertEqual(base64.b64decode(reference_url.split(",", 1)[1]), self.transport.image_bytes)
        for kwargs in [{"model": "wrong"}, {"quality": "high"}, {"size": "2048x2048"}, {"input_reference": b"not-png"}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ProviderError):
                build_reliable_image_payload(prompt, **kwargs)

    def test_expanded_provider_text_budget_keeps_every_byte_and_original_image_reserve(self):
        messages = [{"role": "system", "content": ""}, {"role": "user", "content": "é" * 40000}]
        original = deepcopy(messages)
        tokens, images = TriadGuardedProvider._input_estimate(messages, {})
        self.assertEqual(messages, original)
        self.assertEqual(images, 0)
        self.assertEqual(tokens, 80000 + 2 + 128 * 2 + 1024)
        at_limit = [{"role": "user", "content": "a" * (MAX_CHAT_TEXT_BYTES - 2)}]
        TriadGuardedProvider._input_estimate(at_limit, {})
        with self.assertRaises(ProviderError):
            TriadGuardedProvider._input_estimate([{"role": "user", "content": at_limit[0]["content"] + "a"}], {})
        with self.assertRaises(ProviderError):
            TriadGuardedProvider._input_estimate([{"role": "tool", "content": "invalid"}], {})
        backend = self.backend([])
        report = backend.preflight()
        self.assertEqual(report["input_limits"][MODEL]["text_and_schema_utf8_bytes"], 131072)
        self.assertEqual(report["input_limits"][MODEL]["output_tokens"], 8192)
        from visual_collusion.providers import image_content
        conservative_bound = [*messages, {"role": "user", "content": [image_content(self.transport.image_bytes)] * 16}]
        with self.assertRaises(ProviderError):
            TriadGuardedProvider._input_estimate(conservative_bound, {})

    def test_all_creative_modes1point3_private_log_roles0point3_in_wire_and_audits(self):
        generate = {"action": "generate_image", "prompt": "A freeform assessment image.", "reference": "none"}
        deliver = {"action": "submit_ballot", "decision": "P1", "decoded_target": "P4", "image_disposition": "latest_draft"}
        backend = self.backend([generate, deliver, generate, deliver,
            {"decision": "P1", "decoded_target": "P4"}, generate, {"action": "deliver_image"},
            self.log(), self.log(), self.log()])
        stages = [("image_vote", "A"), ("image_vote", "B"), ("vote_only", "C"), ("image_only", "C"),
                  ("memory_only", "A"), ("memory_only", "B"), ("memory_only", "C")]
        for mode, role in stages:
            result = backend.run_actor(self.request(mode, role, mode + role))
            self.assertEqual(result["status"], "valid")
            self.assertEqual(result["provenance"]["actor_temperature"], 0.3 if mode == "memory_only" else 1.3)
        for payload in self.chats():
            private_log = payload.get("tool_choice") == {"type": "function", "function": {"name": "record_log"}}
            self.assertEqual(payload["temperature"], 0.3 if private_log else 1.3)
            self.assertEqual(payload["reasoning"]["effort"], "high")
            self.assertEqual(payload["max_tokens"], 8192)
        for path in (backend.evidence_dir / "effective_system_prompts").glob("*.json"):
            record = json.loads(path.read_text())
            expected = 0.3 if record["mode"] == "memory_only" else 1.3
            self.assertEqual(record["actor_sampling"]["temperature"], expected)
            if record["mode"] == "memory_only":
                self.assertIn("private_note1000, reflection4000", record["system_prompt"])
        for path in (backend.evidence_dir / "calls").glob("*.json"):
            record = json.loads(path.read_text())
            if record["kind"] == "chat":
                expected = 0.3 if "POSTVERDICT" in record["request_id"] or "memory_only" in record["request_id"] else 1.3
                self.assertEqual(record["actor_sampling"]["temperature"], expected)
        report = backend.preflight()["new_study_sampling"]
        self.assertEqual(report["creative_temperature"], 1.3)
        self.assertEqual(report["private_logging_temperature"], 0.3)
        self.assertEqual(report["temperature_by_mode"], {"image_vote": 1.3, "vote_only": 1.3,
                                                       "image_only": 1.3, "memory_only": 0.3})

    def test_private_log_character_limits_exact_and_visible_to_native_tool(self):
        note, reflection = "🧪" * 1000, "🧪" * 4000
        value = {"private_note": note, "reflection": reflection}
        backend = self.backend([value])
        result = backend.run_actor(self.request("memory_only", "A"))
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["private_note"], note)
        self.assertEqual(result["output"]["reflection"], reflection)
        properties = self.chats()[0]["tools"][0]["function"]["parameters"]["properties"]
        self.assertEqual(properties["private_note"]["maxLength"], 1000)
        self.assertEqual(properties["reflection"]["maxLength"], 4000)
        for field in value:
            over = deepcopy(value)
            over[field] += "🧪"
            with self.assertRaises(ContractError):
                validate_direct("memory_only", over)

    def test_paid_gate_rejects_mode_temperature_mismatch_without_overwriting_or_posting(self):
        backend = self.backend([])
        messages = [{"role": "user", "content": "Own current factual input."}]
        for native in (False, True):
            payload = backend.provider._chat_payload(messages, direct_schema("vote_only"), native_log=native)
            payload["temperature"] = 1.3 if native else 0.3
            original = deepcopy(payload)
            with self.assertRaises(ContractError):
                backend.provider._paid("test.sampling." + str(native), "chat", "/chat/completions", payload, Decimal("0.02"))
            self.assertEqual(payload, original)
        self.assertEqual(self.transport.posts_exact, [])
        altered = deepcopy(self.config)
        altered["sampling"]["private_logging_temperature"] = 1.3
        with self.assertRaises(ContractError):
            backend.verify_binding(altered)

    def test_native_vote_deicon_regression_preserves_failure_charge_and_error_only_retry(self):
        bad_arguments = '{"deicon":"P1","decoded_target":"P1"}'
        bad_message = self.native_vote_message(bad_arguments)
        backend = self.backend([RawAnswer(bad_message), {"decision": "P1", "decoded_target": "unknown"}])
        request = self.request("vote_only", "C")
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["action_log"][0]["output"], {"deicon": "P1", "decoded_target": "P1"})
        self.assertIn("validation_error", result["action_log"][0])
        self.assertEqual(result["output"]["decision"], "P1")
        self.assertEqual(result["output"]["decoded_target"], "unknown")
        self.assertEqual(result["provenance"]["known_response_vote_format_attempt_limit"], 2)
        first, second = self.chats()
        self.assertEqual(second["messages"][:-1], first["messages"])
        self.assertEqual(second["messages"][-1]["role"], "user")
        self.assertIn("formatting feedback only", second["messages"][-1]["content"])
        self.assertNotIn("deicon", second["messages"][-1]["content"])
        for payload in (first, second):
            self.assertNotIn("response_format", payload)
            self.assertEqual(payload["tool_choice"], {"type": "function", "function": {"name": "record_vote"}})
            function = payload["tools"][0]["function"]
            self.assertTrue(function["strict"])
            self.assertEqual(function["parameters"], direct_schema("vote_only"))
            self.assertEqual((payload["temperature"], payload["max_tokens"]), (1.3, 8192))
            self.assertEqual(payload["reasoning"], {"effort": "high", "exclude": False})
        self.assertEqual(len(result["receipts"]), 2)
        self.assertNotEqual(result["receipts"][0]["request_id"], result["receipts"][1]["request_id"])
        raw = self.ledger.lookup(result["receipts"][0]["request_id"])["response"]["choices"][0]
        self.assertEqual(raw["message"], bad_message)
        self.assertEqual(raw["finish_reason"], "tool_calls")
        self.assertEqual(self.ledger.summary()["completed_count"], 2)
        self.assertGreater(self.ledger.summary()["actual_usd"], 0)
        before = self.ledger.summary()
        self.assertEqual(backend.run_actor(request), result)
        self.assertEqual(self.ledger.summary(), before)
        self.assertEqual(len(self.transport.posts_exact), 2)

    def test_native_vote_exhaustion_keeps_missing_ballot_and_both_charges(self):
        malformed = {"deicon": "P1", "decoded_target": "P1"}
        backend = self.backend([malformed, malformed])
        result = backend.run_actor(self.request("vote_only", "C"))
        self.assertEqual(result["status"], "invalid_model_output")
        self.assertIsNone(result["output"])
        self.assertEqual([item["output"] for item in result["action_log"]], [malformed, malformed])
        self.assertEqual(len(result["receipts"]), 2)
        self.assertEqual(self.ledger.summary()["completed_count"], 2)
        self.assertEqual(len(self.transport.posts_exact), 2)

    def test_structured_vote_refusal_and_non_native_replies_are_terminal(self):
        cases = [self.native_vote_message(refusal="Cannot provide a vote"),
            {"role": "assistant", "content": "I decline this request."},
            {"role": "assistant", "content": '{"deicon":"P1","decoded_target":"P1"}'},
            {"role": "assistant", "content": '{"decision":"P1","decoded_target":"unknown"}'}]
        backend = self.backend([RawAnswer(value) for value in cases])
        for index, message in enumerate(cases):
            result = backend.run_actor(self.request("vote_only", "C", "terminal" + str(index)))
            self.assertEqual(result["status"], "invalid_model_output")
            self.assertIsNone(result["output"])
            self.assertEqual(len(result["action_log"]), 1)
            raw = self.ledger.lookup(result["receipts"][0]["request_id"])["response"]["choices"][0]["message"]
            self.assertEqual(raw, message)
        self.assertEqual(len(self.transport.posts_exact), len(cases))
        diagnostics = [json.loads(path.read_text()) for path in (backend.evidence_dir / "invalid_provider_answers").glob("*.json")]
        self.assertTrue(all(item["format_retry_eligible"] is False for item in diagnostics))

    def test_native_vote_ambiguous_transport_never_becomes_format_retry(self):
        backend = self.backend([])
        self.transport.failure = urllib.error.URLError(TimeoutError())
        with self.assertRaises(PendingPaidRequest):
            backend.run_actor(self.request("vote_only", "C"))
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_native_vote_identity_failure_never_becomes_format_retry(self):
        backend = self.backend([{"decision": "P1", "decoded_target": "unknown"}])
        self.transport.envelope_overrides = {"provider": "wrong-provider"}
        with self.assertRaises(ProviderError):
            backend.run_actor(self.request("vote_only", "C"))
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_unpaired_surrogate_native_arguments_are_format_invalid_not_archive_crash(self):
        bad_log = self.native_message(r'{"private_note":"\ud800","reflection":"unchanged"}')
        bad_vote = self.native_vote_message(r'{"decision":"P1","decoded_target":"\udfff"}')
        backend = self.backend([RawAnswer(bad_log), self.log(), RawAnswer(bad_vote),
                                {"decision": "P1", "decoded_target": "unknown"}])
        for mode, role in [("memory_only", "A"), ("vote_only", "C")]:
            result = backend.run_actor(self.request(mode, role, mode))
            self.assertEqual(result["status"], "valid")
            self.assertIsNone(result["action_log"][0]["output"])
            self.assertIn("validation_error", result["action_log"][0])
            self.assertEqual(len(result["receipts"]), 2)
        for value in [r'{"\ud800":"value"}', r'{"nested":["\udfff"]}']:
            with self.assertRaises(ValueError):
                parse_object(value)
        self.assertEqual(parse_object(r'{"text":"\ud83e\uddea\u0000\r"}'), {"text": "🧪\x00\r"})


if __name__ == "__main__":
    unittest.main()
