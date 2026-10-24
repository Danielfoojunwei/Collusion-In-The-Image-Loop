"""Exact native wire, preserved cache and maximum-context fixtures."""
import json
from pathlib import Path
import tempfile
import unittest

from visual_collusion.budget import BudgetLedger
from experiments.triad_emergent_v9.backend import Backend as BaseBackend, BackendError
from experiments.triad_emergent_v9.funding import FundingLedger, create_guard
from experiments.triad_emergent_v9.prompts import actor_system
from experiments.triad_repaired_v8.tests.test_backend import NativeWireTransport, RawAnswer
from experiments.triad_v9_protocol_repair01.backend import TOKEN_BUDGET_NOTICE as PARENT_NOTICE
from experiments.triad_v9_protocol_repair02.backend import Backend, TOKEN_BUDGET_NOTICE, make_policy


class SamplingRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path, self.out = self.root / "budget.sqlite3", self.root / "run"
        BudgetLedger(self.path)
        self.ledger = FundingLedger(self.path)
        self.guard = create_guard(self.path, evidence_dir=self.out / "provider_evidence", ledger=self.ledger)
        self.config = json.loads((Path(__file__).parents[2] / "triad_emergent_v9/config.json").read_text())

    def tearDown(self):
        self.temp.cleanup()

    def backend(self, actions, original=False):
        self.transport = NativeWireTransport(self.ledger, actions)
        kwargs = {"transport": self.transport, "api_key": "OFFLINE_FIXTURE_NOT_A_SECRET", "guard": self.guard}
        return (BaseBackend(self.config, self.out, self.path, **kwargs) if original
                else Backend(self.config, self.out, self.path, policy=make_policy(), **kwargs))

    @staticmethod
    def request(mode="log", role="B", suffix="one"):
        return {"request_id": "fixture.r01." + suffix, "round": 1, "mode": mode, "role": role,
                "messages": [{"role": "system", "content": actor_system(role, mode)},
                             {"role": "user", "content": "Current authoritative facts and own history."}], "image_refs": {}}

    def chats(self):
        return [json.loads(b) for path, b in self.transport.posts_exact if path == "/chat/completions"]

    def test_all_roles_actual_temperature1_high8192_notice_and_preflight(self):
        row = {"carryover_note": "Retain actual outcomes.", "image_reflection": "No new understanding is assumed."}
        backend = self.backend([row, row, row, {"decision": "P3"}])
        for role in "ABC":
            self.assertEqual(backend.run_actor(self.request(role=role, suffix=role))["status"], "valid")
        backend.run_actor(self.request("vote", "C", "vote"))
        self.assertEqual(TOKEN_BUDGET_NOTICE, PARENT_NOTICE)
        self.assertEqual(len(TOKEN_BUDGET_NOTICE.encode()), 390)
        for payload in self.chats():
            self.assertEqual(payload["temperature"], 1.0)
            self.assertEqual(payload["reasoning"], {"effort": "high", "exclude": False})
            self.assertEqual(payload["max_tokens"], 8192)
            self.assertEqual(payload["messages"][0]["content"].count(TOKEN_BUDGET_NOTICE), 1)
        report = backend.provider._preflight["sampling"]
        self.assertEqual(report["base_configuration_temperature"], 2.0)
        self.assertEqual(report["prior_protocol_repair01_temperature"], 2.0)
        self.assertEqual(report["log_temperature"], 1.0)
        self.assertEqual(report["effective_protocol_repair"]["repair"], "triad_v9_protocol_repair02")

    def test_maximum_visual_history_two_full_unicode_briefs_and_vote(self):
        brief = "🧪" * 1200
        backend = self.backend([{"action": "create_visual", "arguments": {"prompt": brief}},
            {"action": "revise_visual", "arguments": {"prompt": brief}},
            {"action": "accept_visual", "arguments": {}}, {"decision": "P2"}])
        request = self.request("visual_vote", "A")
        request["messages"][1]["content"] = "🧪" * 27500
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(len(self.transport.posts_exact), 6)
        self.assertEqual([a["arguments"]["prompt"] for a in result["actions"] if a.get("action") in {"create_visual", "revise_visual"}], [brief, brief])

    def test_measured_full115539byte_postlog_history_fits(self):
        row = {"carryover_note": "🧪" * 400, "image_reflection": "🧪" * 800}
        backend = self.backend([row])
        request = self.request("log", "C")
        request["messages"][1]["content"] = "🧪" * 28884 + "abc"
        self.assertEqual(backend.run_actor(request)["status"], "valid")
        self.assertEqual(len(self.transport.posts_exact), 1)

    def test_old_invalid_terminal_cache_and_raw_paid_answers_are_unchanged(self):
        bad = RawAnswer({"role": "assistant", "content": None}, "length")
        original = self.backend([bad, bad], original=True)
        request = self.request("log", "A")
        result = original.run_actor(request)
        self.assertEqual(result["status"], "model_invalid")
        rows = [self.ledger.lookup(x["request_id"]) for x in result["receipts"]]
        repaired = self.backend([])
        self.assertEqual(repaired.run_actor(request), result)
        self.assertEqual(self.transport.posts_exact, [])
        self.assertEqual([self.ledger.lookup(x["request_id"]) for x in result["receipts"]], rows)
        with self.assertRaisesRegex(BackendError, "cannot replay"):
            repaired.provider.native_call(request["request_id"] + ".log1", request["messages"], [], 1.0)
        self.assertEqual(self.transport.posts_exact, [])

    def test_policy_drift_or_unactivated_production_cannot_spend(self):
        policy = make_policy()
        policy["temperature"] = 2.0
        with self.assertRaises(BackendError):
            Backend(self.config, self.out, self.path, policy=policy, transport=lambda *args: None,
                    guard=self.guard, api_key="OFFLINE_FIXTURE_NOT_A_SECRET")
        out = self.root / "not_created"
        with self.assertRaisesRegex(BackendError, "not been activated"):
            Backend(self.config, out, self.path, policy=make_policy(), api_key="NOT_READ")
        self.assertFalse(out.exists())
        self.assertEqual(self.ledger.namespace_summary()["request_count"], 0)


if __name__ == "__main__":
    unittest.main()
