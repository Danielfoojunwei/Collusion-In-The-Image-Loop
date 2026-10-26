"""Native-wire continuation tests with temporary ledgers and no network."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from visual_collusion.budget import BudgetLedger
from experiments.triad_emergent_v9.backend import Backend as OriginalBackend, BackendError
from experiments.triad_emergent_v9.funding import FundingLedger, create_guard
from experiments.triad_emergent_v9.prompts import actor_system
from experiments.triad_repaired_v8.tests.test_backend import NativeWireTransport, RawAnswer
from experiments.triad_v9_protocol_repair01.backend import Backend, TOKEN_BUDGET_NOTICE, make_policy


class RepairBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "budget.sqlite3"
        BudgetLedger(self.path)
        self.ledger = FundingLedger(self.path)
        self.out = self.root / "run"
        self.config = json.loads((Path(__file__).parents[2] / "triad_emergent_v9/config.json").read_text())
        self.guard = create_guard(self.path, evidence_dir=self.out / "provider_evidence", ledger=self.ledger)

    def tearDown(self):
        self.temp.cleanup()

    def backend(self, actions, temperature=2.0, original=False):
        self.transport = NativeWireTransport(self.ledger, actions)
        kwargs = dict(transport=self.transport, api_key="OFFLINE_FIXTURE_NOT_A_SECRET", guard=self.guard)
        return (OriginalBackend(self.config, self.out, self.path, **kwargs) if original
                else Backend(self.config, self.out, self.path, policy=make_policy(temperature), **kwargs))

    @staticmethod
    def action(name, **args):
        return {"action": name, "arguments": args}

    @staticmethod
    def request(mode="visual_vote", role="A", suffix="one"):
        return {"request_id": "main.r01." + suffix, "role": role, "round": 1, "mode": mode,
                "messages": [{"role": "system", "content": actor_system(role, mode)},
                             {"role": "user", "content": "Current factual task and own history."}],
                "image_refs": {}}

    def chats(self):
        return [json.loads(body) for path, body in self.transport.posts_exact if path == "/chat/completions"]

    def test_both_selected_temperatures_preserve_high_reasoning_and_original_base_config(self):
        for temperature in (1.0, 2.0):
            with self.subTest(temperature=temperature):
                backend = self.backend([{"decision": "P2"}], temperature)
                request = self.request("vote", "C", str(temperature))
                result = backend.run_actor(request)
                self.assertEqual(result["output"]["decision"], "P2")
                payload = self.chats()[0]
                self.assertEqual(payload["temperature"], temperature)
                self.assertEqual(payload["reasoning"], {"effort": "high", "exclude": False})
                self.assertEqual(payload["max_tokens"], 8192)
                self.assertEqual(payload["messages"][0]["content"].count(TOKEN_BUDGET_NOTICE), 1)
                self.assertNotIn(TOKEN_BUDGET_NOTICE, request["messages"][0]["content"])
                self.assertEqual(backend.config["sampling"]["planning_temperature"], 2.0)
                self.assertEqual(backend.provider._preflight["sampling"]["base_configuration_temperature"], 2.0)

    def test_maximum_visual_history_two_complete_unicode_briefs_and_vote_fit(self):
        brief = "🧪" * 1200
        backend = self.backend([self.action("create_visual", prompt=brief),
            self.action("revise_visual", prompt=brief), self.action("accept_visual"), {"decision": "P1"}], 1.0)
        request = self.request()
        request["messages"][1]["content"] = "🧪" * 27500
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(len(self.transport.posts_exact), 6)
        self.assertEqual([x["arguments"]["prompt"] for x in result["actions"] if x.get("action") in {"create_visual", "revise_visual"}], [brief, brief])
        for payload in self.chats():
            self.assertEqual(payload["messages"][0]["content"].count(TOKEN_BUDGET_NOTICE), 1)
            self.assertEqual(payload["temperature"], 1.0)

    def test_original_terminal_stage_cache_is_unchanged_without_any_new_post(self):
        request = self.request("vote", "C")
        original = self.backend([{"decision": "P3"}], original=True)
        old = original.run_actor(request)
        row = self.ledger.lookup(old["receipts"][0]["request_id"])
        exact = deepcopy(row)
        backend = self.backend([], 1.0)
        self.assertEqual(backend.run_actor(request), old)
        self.assertEqual(self.transport.posts_exact, [])
        self.assertEqual(self.ledger.lookup(old["receipts"][0]["request_id"]), exact)

    def test_full_measured_legal_postlog_history_plus_notice_fits(self):
        row = {"carryover_note": "🧪" * 400, "image_reflection": "🧪" * 800}
        backend = self.backend([row], 2.0)
        request = self.request("log", "C")
        request["messages"][1]["content"] = "🧪" * 28884 + "abc"
        self.assertEqual(len(request["messages"][1]["content"].encode()), 115539)
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["output"]["image_reflection"], row["image_reflection"])
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_malformed_paid_answer_remains_exact_and_only_original_slots_continue(self):
        malformed = RawAnswer({"role": "assistant", "content": None, "tool_calls": [{
            "id": "truncated-native", "type": "function", "index": 0,
            "function": {"name": "create_visual", "arguments": '{"prompt":"unfinished'}}]}, "tool_calls")
        backend = self.backend([malformed, self.action("create_visual", prompt="Short complete image brief."),
            self.action("accept_visual"), {"decision": "P4"}], 2.0)
        result = backend.run_actor(self.request())
        self.assertEqual(result["status"], "valid")
        self.assertIn("validation_error", result["actions"][0])
        first = self.ledger.lookup(result["receipts"][0]["request_id"])["response"]
        self.assertEqual(first["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"], '{"prompt":"unfinished')
        self.assertEqual(sum(path == "/chat/completions" for path, _ in self.transport.posts_exact), 4)
        self.assertEqual(sum(path == "/images" for path, _ in self.transport.posts_exact), 1)
        self.assertEqual(len(result["actions"]), 4)

    def test_earlier_paid_native_intent_cannot_be_replayed_with_a_new_notice(self):
        request = self.request("vote", "C")
        original = self.backend([{"decision": "P2"}], original=True)
        original.run_actor(request)
        backend = self.backend([], 2.0)
        with self.assertRaisesRegex(BackendError, "cannot replay"):
            backend.provider.native_call(request["request_id"] + ".vote1", request["messages"], [], 2.0)
        self.assertEqual(self.transport.posts_exact, [])

    def test_unactivated_production_repair_fails_before_ledger_or_output_creation(self):
        before = self.ledger.namespace_summary()
        out = self.root / "never_created"
        with self.assertRaisesRegex(BackendError, "not been activated"):
            Backend(self.config, out, self.path, policy=make_policy(2.0), api_key="NOT_READ")
        self.assertFalse(out.exists())
        self.assertEqual(self.ledger.namespace_summary(), before)


if __name__ == "__main__":
    unittest.main()
