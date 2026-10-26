"""Actual funding primitives with temporary ledgers and simulated POSTs only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from visual_collusion.budget import BudgetLedger, BudgetError, PendingPaidRequest, canonical
from experiments.triad_repaired_v8 import funding as prior
from experiments.triad_emergent_v9 import funding


MODEL = "z-ai/glm-5.3-flash"
BODY = b'{"model":"z-ai/glm-5.3-flash","messages":[{"role":"user","content":"OFFLINE FIXTURE"}]}'


class FundingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "budget.sqlite3"
        BudgetLedger(self.path)
        self.ledger = funding.FundingLedger(self.path)
        self.posts = []

    def tearDown(self):
        self.temp.cleanup()

    def guard(self, *, ledger=None, on_attempt=None):
        return funding.create_guard(self.path, evidence_dir=self.root / "evidence",
                                    ledger=ledger or self.ledger, on_attempt=on_attempt)

    def paid(self, body, binding, cost=.001):
        self.posts.append((body, deepcopy(binding)))
        response = {"id": "OFFLINE_RESPONSE", "usage": {"cost": cost},
                    "choices": [{"message": {"content": "ACTUAL_SIMULATED_SEMANTIC_ANSWER"}}]}
        return {"response": response, "receipt": {**binding, "response_id": response["id"],
                "usage": response["usage"], "cost_usd": cost}}

    def rows(self, ledger=None):
        with (ledger or self.ledger)._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY request_id")]

    def test_exact_completed_response_cached_once_and_original_reserve_fixed(self):
        hooks = []
        guard = self.guard(on_attempt=lambda body, binding: hooks.append((body, binding)))
        first = guard.execute("main.r01.A.visual1", "chat", MODEL, BODY, .01, self.paid)
        cached = guard.execute("main.r01.A.visual1", "chat", MODEL, BODY, .01,
                               lambda *_: self.fail("A cached response must never POST"))
        self.assertEqual(first["response"], cached["response"])
        self.assertTrue(cached["cached"])
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(hooks), 1)
        self.assertEqual(self.posts[0][0], BODY)
        self.assertEqual(self.posts[0][1]["request_id"], funding.NAMESPACE + ":main.r01.A.visual1")
        for body, reserve in [(BODY + b" ", .01), (BODY, .02)]:
            with self.assertRaises(BudgetError):
                guard.execute("main.r01.A.visual1", "chat", MODEL, body, reserve, self.paid)
        self.assertEqual(len(self.posts), 1)

    def test_namespace_cap_is_allocation_and_counts_actual_not_released_cash(self):
        guard = self.guard()
        guard.execute("first", "chat", MODEL, BODY, 24, lambda b, x: self.paid(b, x, 24))
        self.assertEqual(self.ledger.summary()["cap_usd"], 40)
        self.assertEqual(self.ledger.namespace_summary()["committed_usd"], 24)
        with self.assertRaisesRegex(BudgetError, "namespace subcap"):
            guard.execute("over", "chat", MODEL, BODY, .000000001, self.paid)
        self.assertEqual(len(self.posts), 1)

    def test_original_aggregate_cap_still_controls_other_namespace_spending(self):
        old = prior.FundingLedger(self.path)
        old.reserve("old:paid", "chat", MODEL, "a" * 64, 39)
        old.complete("old:paid", 39, {}, {})
        with self.assertRaisesRegex(BudgetError, "Original aggregate cap"):
            self.guard().execute("global_over", "chat", MODEL, BODY, 2, self.paid)
        self.assertEqual(self.posts, [])
        self.assertEqual(self.ledger.namespace_summary()["request_count"], 0)

    def test_429_timeout_and_missing_receipt_never_retry_or_zero_unknown_cost(self):
        for label in ["429", "timeout", "receipt"]:
            with self.subTest(label=label):
                path = self.root / label / "budget.sqlite3"
                BudgetLedger(path)
                ledger = funding.FundingLedger(path)
                guard = funding.create_guard(path, ledger=ledger)
                calls = []
                def fail(body, binding):
                    calls.append(binding)
                    if label == "429":
                        raise funding.Known429(0, "OFFLINE_429")
                    if label == "timeout":
                        raise TimeoutError("OFFLINE_TIMEOUT")
                    return {"response": {"choices": []}, "receipt": dict(binding)}
                with self.assertRaises(PendingPaidRequest):
                    guard.execute("uncertain", "chat", MODEL, BODY, .01, fail)
                with self.assertRaises(PendingPaidRequest):
                    guard.execute("uncertain", "chat", MODEL, BODY, .01, fail)
                with self.assertRaises(PendingPaidRequest):
                    guard.execute("different", "chat", MODEL, BODY, .01, fail)
                self.assertEqual(len(calls), 1)
                self.assertEqual(ledger.namespace_summary()["reserved_unresolved_usd"], .01)
                self.assertIsNone(self.rows(ledger)[0]["actual_units"])
                for method in [ledger.seal_known_429, ledger.seal_transport_loss,
                               ledger.seal_existing_response_less_loss]:
                    with self.assertRaises(BudgetError):
                        method(funding.NAMESPACE + ":uncertain")

    def test_semantic_refusal_is_a_completed_observation_not_a_retry_trigger(self):
        response = self.guard().execute("refusal", "chat", MODEL, BODY, .01, self.paid)
        self.assertEqual(response["response"]["choices"][0]["message"]["content"],
                         "ACTUAL_SIMULATED_SEMANTIC_ANSWER")
        self.assertEqual(len(self.posts), 1)

    def test_concurrent_guards_cannot_cross_subcap_or_duplicate_a_post(self):
        barrier = threading.Barrier(2)
        posted = []
        def worker(number):
            ledger = funding.FundingLedger(self.path)
            guard = self.guard(ledger=ledger)
            barrier.wait()
            def post(body, binding):
                posted.append(binding["request_id"])
                return self.paid(body, binding, 13)
            try:
                guard.execute("concurrent" + str(number), "chat", MODEL, BODY, 13, post)
                return "completed"
            except BudgetError:
                return "blocked"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        self.assertEqual(sorted(results), ["blocked", "completed"])
        self.assertEqual(len(posted), 1)
        self.assertEqual(self.ledger.namespace_summary()["committed_usd"], 13)

    def test_old_sealed_429_is_preserved_and_tampering_still_blocks(self):
        prior_ledger = prior.FundingLedger(self.path)
        old_guard = prior.create_guard(self.path, prior.MAIN_NAMESPACE, self.root / "old",
                                       ledger=prior_ledger, sleeper=lambda _: None)
        def old_post(body, binding):
            if binding["request_id"].endswith("attempt1"):
                raise prior.Known429(0, "OFFLINE_PRIOR_429")
            return self.paid(body, binding)
        result = old_guard.execute("old", "chat", MODEL, BODY, .01, old_post)
        old_rows = self.rows()
        identifier = result["attempt_trace"][0]["request_id"]
        seal = prior_ledger.verified_429_loss(identifier)
        self.ledger.assert_ready()
        self.guard().execute("fresh", "chat", MODEL, BODY, .01, self.paid)
        before = {row["request_id"]: row for row in old_rows}
        after = {row["request_id"]: row for row in self.rows()}
        self.assertTrue(all(after[key] == value for key, value in before.items()))
        self.assertEqual(self.ledger.summary()["reserved_unresolved_usd"], .01)
        path = Path(seal["diagnostic_path"])
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()
        path.write_bytes(original)
        self.ledger.assert_ready()

    def test_wrong_namespace_model_cap_or_changed_verifier_is_blocked(self):
        with self.assertRaises(BudgetError):
            funding.create_guard(self.path, namespace="wrong", ledger=self.ledger)
        with self.assertRaises(BudgetError):
            self.guard().execute("badmodel", "chat", "other/model", BODY, .01, self.paid)
        with self.assertRaises(BudgetError):
            funding.FundingLedger(self.path, 24)
        with patch.object(funding, "PARENT_FUNDING_SHA256", "f" * 64):
            with self.assertRaises(BudgetError):
                self.guard().execute("changed", "chat", MODEL, BODY, .01, self.paid)
        self.assertEqual(self.posts, [])

    def test_missing_ledger_and_uninjected_alternate_production_path_are_blocked(self):
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(BudgetError):
            funding.create_guard(missing)
        self.assertFalse(missing.exists())
        with self.assertRaises(BudgetError):
            funding.create_guard(self.path)

    def test_namespace_cap_binding_tamper_blocks_before_any_post(self):
        guard = self.guard()
        with self.ledger._connect() as db:
            db.execute("UPDATE settings SET value=? WHERE key=?",
                       ("25000000000", funding.CAP_SETTING))
        with self.assertRaisesRegex(BudgetError, "subcap binding changed"):
            guard.execute("tampered_cap", "chat", MODEL, BODY, .01, self.paid)
        with self.assertRaisesRegex(BudgetError, "subcap changed"):
            funding.FundingLedger(self.path)
        self.assertEqual(self.posts, [])


if __name__ == "__main__":
    unittest.main()
