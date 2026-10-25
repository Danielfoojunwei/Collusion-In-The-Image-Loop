"""Prospective and immutable-prior retry accounting; temporary ledgers, no network."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from visual_collusion.budget import BudgetLedger, BudgetError, PendingPaidRequest, canonical, request_digest
from experiments.triad_coached_v5 import funding
from experiments.triad_reliable_v4 import funding as prior_funding


class FundingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.ledger = self.new_ledger("main")
        self.delays = []
        self.posts = []
        self.body = b'{ "model":"z-ai/glm-5.3-flash", "messages":[{"role":"user","content":"FIXTURE"}] }'

    def tearDown(self):
        self.temporary.cleanup()

    def new_ledger(self, name):
        path = self.root / name / "budget.sqlite3"
        BudgetLedger(path)
        return funding.FundingLedger(path)

    def guard(self, namespace=funding.MAIN_NAMESPACE, *, ledger=None, name="provider", on_attempt=None):
        ledger = ledger or self.ledger
        return funding.create_guard(ledger.path, namespace, self.root / name, ledger=ledger,
                                    sleeper=self.delays.append, on_attempt=on_attempt)

    def paid(self, body, binding, cost=0.001):
        self.posts.append((body, deepcopy(binding)))
        usage = {"cost": cost}
        response = {"id": "fixture-response", "usage": usage, "choices": [{"message": {"content": "SEMANTIC_ANSWER_UNCHANGED"}}]}
        return {"response": response, "receipt": {**binding, "response_id": response["id"], "usage": usage, "cost_usd": cost}}

    def rows(self, ledger=None):
        with (ledger or self.ledger)._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY created,request_id")]

    def test_429_then_success_exact_bytes_distinct_ids_unknown_reserve_and_cached_restart(self):
        attempted = []
        guard = self.guard(on_attempt=lambda body, binding: attempted.append((body, binding["request_id"])))
        def post(body, binding):
            if binding["request_id"].endswith("attempt1"):
                self.posts.append((body, deepcopy(binding)))
                raise funding.Known429(65, "safe-request-429")
            return self.paid(body, binding)
        result = guard.execute("case.r01.A.step1", "chat", "z-ai/glm-5.3-flash", self.body, 0.01, post)
        self.assertEqual([item[0] for item in self.posts], [self.body, self.body])
        self.assertEqual([item[1]["request_id"] for item in self.posts], [funding.MAIN_NAMESPACE + ":case.r01.A.step1.attempt1", funding.MAIN_NAMESPACE + ":case.r01.A.step1.attempt2"])
        self.assertEqual(len(attempted), 2)
        self.assertEqual(self.delays, [30.0, 30.0, 5.0])
        self.assertEqual([item["status"] for item in result["attempt_trace"]], ["http429_unknown_charge", "completed"])
        rows = self.rows()
        self.assertEqual([row["status"] for row in rows], ["uncertain", "completed"])
        self.assertIsNone(rows[0]["actual_units"])
        self.assertIsNone(rows[0]["response_json"])
        self.assertEqual(rows[0]["reserved_units"], 10_000_000)
        self.assertEqual(self.ledger.summary()["reserved_unresolved_usd"], 0.01)
        self.assertEqual(self.ledger.summary()["actual_usd"], 0.001)
        self.ledger.assert_ready()
        prior_posts, prior_delays = len(self.posts), len(self.delays)
        restarted = self.guard()
        cached = restarted.execute("case.r01.A.step1", "chat", "z-ai/glm-5.3-flash", self.body, 0.01, lambda *_: self.fail("Cached logical call must never POST"))
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["attempt_trace"], result["attempt_trace"])
        self.assertEqual((len(self.posts), len(self.delays)), (prior_posts, prior_delays))
        self.assertEqual(self.rows(), rows)

    def test_exact_three_429_attempt_exhaustion_preserves_all_reserves_and_never_fourth(self):
        guard = self.guard()
        def post(body, binding):
            self.posts.append((body, binding))
            raise funding.Known429()
        with self.assertRaises(funding.RateLimitExhausted) as failure:
            guard.execute("exhaust", "chat", "z-ai/glm-5.3-flash", self.body, 0.01, post)
        self.assertEqual(len(self.posts), 3)
        self.assertEqual(len(failure.exception.attempt_trace), 3)
        self.assertEqual(self.delays, [30.0, 30.0, 30.0])
        self.assertEqual(self.ledger.summary()["reserved_unresolved_usd"], 0.03)
        self.assertEqual(self.ledger.summary()["actual_usd"], 0)
        self.assertTrue(all(row["actual_units"] is None for row in self.rows()))
        with self.assertRaises(funding.RateLimitExhausted):
            guard.execute("exhaust", "chat", "z-ai/glm-5.3-flash", self.body, 0.01, lambda *_: self.fail("An exhausted logical call cannot POST again"))
        self.assertEqual(len(self.posts), 3)

    def test_non429_http_timeouts_and_account_failures_do_not_retry(self):
        for label, error in [(str(status), urllib.error.HTTPError("https://fixture.invalid", status, "SAFE", {}, None)) for status in (402, 403, 408, 500, 503)] + [("timeout", TimeoutError("fixture timeout")), ("integrity", BudgetError("fixture integrity"))]:
            with self.subTest(label=label):
                ledger = self.new_ledger(label)
                guard = self.guard(ledger=ledger, name=label)
                calls = []
                def post(body, binding):
                    calls.append(binding["request_id"])
                    raise error
                with self.assertRaises(PendingPaidRequest):
                    guard.execute("nonretry", "chat", "z-ai/glm-5.3-flash", self.body, 0.01, post)
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(self.rows(ledger)), 1)
                with self.assertRaises(PendingPaidRequest):
                    ledger.assert_ready()

    def test_semantic_answer_or_missing_cost_is_not_retried(self):
        guard = self.guard()
        result = guard.execute("semantic", "chat", "z-ai/glm-5.3-flash", self.body, 0.01, self.paid)
        self.assertEqual(result["response"]["choices"][0]["message"]["content"], "SEMANTIC_ANSWER_UNCHANGED")
        self.assertEqual(len(self.posts), 1)
        calls = []
        def missing_cost(body, binding):
            calls.append(binding)
            return {"response": {}, "receipt": {**binding}}
        with self.assertRaises(PendingPaidRequest):
            guard.execute("missingcost", "chat", "z-ai/glm-5.3-flash", self.body, 0.01, missing_cost)
        self.assertEqual(len(calls), 1)

    def test_changed_input_or_reserve_cannot_reuse_existing_logical_call(self):
        guard = self.guard()
        guard.execute("fixed", "chat", "z-ai/glm-5.3-flash", self.body, 0.01, self.paid)
        for body, reserve in ((self.body + b" ", 0.01), (self.body, 0.02)):
            with self.subTest(body=body, reserve=reserve):
                with self.assertRaises(BudgetError):
                    guard.execute("fixed", "chat", "z-ai/glm-5.3-flash", body, reserve, self.paid)
        self.assertEqual(len(self.posts), 1)

    def test_calibration_subcap_counts_unknown_reserves_atomically_before_each_post(self):
        guard = self.guard(funding.CALIBRATION_NAMESPACE)
        def post(body, binding):
            self.posts.append((body, binding))
            raise funding.Known429()
        with self.assertRaisesRegex(BudgetError, "calibration subcap"):
            guard.execute("calibration", "chat", "z-ai/glm-5.3-flash", self.body, 0.8, post)
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.ledger.summary()["reserved_unresolved_usd"], 1.6)
        self.assertFalse(any(row["request_id"].endswith("attempt3") for row in self.rows()))

    def test_calibration_cap_includes_completed_actual_and_global_cap_still_applies(self):
        calibration = self.guard(funding.CALIBRATION_NAMESPACE)
        calibration.execute("paid", "chat", "z-ai/glm-5.3-flash", self.body, 1.5, lambda body, binding: self.paid(body, binding, 1.5))
        with self.assertRaisesRegex(BudgetError, "calibration subcap"):
            calibration.execute("over", "chat", "z-ai/glm-5.3-flash", self.body, 0.6, self.paid)
        self.assertEqual(len(self.posts), 1)
        main = self.guard()
        with self.assertRaises(BudgetError):
            main.execute("globalover", "chat", "z-ai/glm-5.3-flash", self.body, 39, self.paid)
        self.assertEqual(len(self.posts), 1)

    def test_seals_immutable_and_diagnostic_wire_or_row_tamper_blocks_readiness(self):
        guard = self.guard()
        def first429(body, binding):
            if binding["request_id"].endswith("attempt1"):
                raise funding.Known429(0, "fixture429")
            return self.paid(body, binding)
        result = guard.execute("tamper", "chat", "z-ai/glm-5.3-flash", self.body, 0.01, first429)
        seal = self.ledger.verified_429_loss(result["attempt_trace"][0]["request_id"])
        with self.ledger._connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(f"DELETE FROM {funding.TABLE}")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(f"UPDATE {funding.TABLE} SET wire_sha256='wrong'")
        for key in ("diagnostic_path", "binding_path", "wire_path"):
            path = Path(seal[key])
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.assertRaises(PendingPaidRequest):
                self.ledger.assert_ready()
            path.write_bytes(original)
            self.ledger.assert_ready()
        with self.ledger._connect() as db:
            db.execute("UPDATE requests SET reserved_units=1 WHERE request_id=?", (result["attempt_trace"][0]["request_id"],))
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()

    def test_safe_retry_after_and_provider_identifier_only(self):
        for invalid in (-1, 121, float("nan"), True, "60"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(BudgetError):
                    funding.Known429(invalid)
        safe = funding.Known429(120, "Bearer private credential text")
        self.assertIsNone(safe.provider_request_id)
        self.assertNotIn("credential", str(safe))
        guard = self.guard()
        def post(body, binding):
            if binding["request_id"].endswith("attempt1"):
                raise safe
            return self.paid(body, binding)
        result = guard.execute("safe", "chat", "z-ai/glm-5.3-flash", self.body, 0.01, post)
        self.assertEqual(self.delays, [30.0] * 4)
        diagnostic = json.loads(Path(result["attempt_trace"][0]["diagnostic_path"]).read_text())
        self.assertEqual(diagnostic["retry_after_seconds"], 120)
        self.assertIsNone(diagnostic["provider_request_id"])
        self.assertNotIn("credential", canonical(diagnostic))

    def test_legacy_thirteen_losses_and_aborted_A2_keep_all_unknown_costs_and_allow_new_study(self):
        legacy = funding.previous_transport._legacy
        for number in range(13):
            identifier = f"legacy_fixture:loss{number}"
            self.ledger.reserve(identifier, "chat", "legacy-fixture-model", "a" * 64, 0.001)
            self.ledger.uncertain(identifier, legacy.LOSS_REASON)
            self.ledger.seal_transport_loss(identifier)
        identifier = "triad_max_pressure_v3:case_05_max.r02.A_IMAGE_AND_VOTE.step1"
        local = identifier.split(":", 1)[1]
        wire, mapping = funding.previous_transport._codec().prepare_wire(self.root, local, "/chat/completions", json.loads(self.body))
        raw = canonical(wire).encode()
        wire_sha = hashlib.sha256(raw).hexdigest()
        request_hash = request_digest({"endpoint": "/chat/completions", "wire_body_sha256": wire_sha})
        self.ledger.reserve(identifier, "chat", "z-ai/glm-5.3-flash", request_hash, 0.0127784)
        self.ledger.uncertain(identifier, funding.previous_transport.GUARD_FAILURE_REASON)
        evidence = self.root / "old_provider"
        diagnostic_path = evidence / "transport_diagnostics" / (hashlib.sha256(identifier.encode()).hexdigest() + ".json")
        diagnostic_path.parent.mkdir(parents=True)
        diagnostic_path.write_text(canonical({"category": "http_error", "endpoint": "/chat/completions", "exception_class": "HTTPError", "http_status": 429,
            "request_hash": request_hash, "request_id": identifier, "response_observed": False, "wire_body_bytes": len(raw), "wire_body_sha256": wire_sha}))
        binding = evidence / "transport_requests" / (hashlib.sha256(local.encode()).hexdigest() + ".json")
        binding.parent.mkdir(parents=True)
        binding.write_text(canonical({"endpoint": "/chat/completions", "request_id": local, "logical_payload": json.loads(self.body), "mapping": mapping, "logical_images_content_addressed": True}))
        original = self.rows()[-1]
        self.ledger.seal_existing_response_less_loss(identifier, diagnostic_path)
        self.assertEqual(self.rows()[-1], original)
        self.ledger.assert_ready()
        self.assertEqual(self.ledger.summary()["reserved_unresolved_usd"], 0.0257784)
        self.guard().execute("fresh", "chat", "z-ai/glm-5.3-flash", self.body, 0.01, self.paid)
        self.assertEqual(self.ledger.summary()["reserved_unresolved_usd"], 0.0257784)
        self.assertEqual(len(self.posts), 1)

    def test_wrong_namespace_or_changed_prior_helper_source_is_blocked(self):
        with self.assertRaises(BudgetError):
            self.guard("triad_max_pressure_v3")
        with patch.object(funding, "PREVIOUS_HELPER_SHA256", "f" * 64):
            with self.assertRaises(BudgetError):
                self.guard().execute("source", "chat", "z-ai/glm-5.3-flash", self.body, 0.01, self.paid)
        self.assertEqual(self.posts, [])

    def prior_sealed_fixture(self, name, namespace):
        """Create an actual v4-format immutable seal with only simulated POSTs."""
        path = self.root / name / "budget.sqlite3"
        BudgetLedger(path)
        ledger = prior_funding.FundingLedger(path)
        guard = prior_funding.create_guard(path, namespace, self.root / (name + "_evidence"),
            ledger=ledger, sleeper=self.delays.append)
        def post(body, binding):
            if binding["request_id"].endswith("attempt1"):
                raise prior_funding.Known429(0, "OFFLINE_FIXTURE_PRIOR_429")
            return self.paid(body, binding)
        result = guard.execute("OFFLINE_FIXTURE_PRIOR", "chat", "z-ai/glm-5.3-flash",
                               self.body, .01, post)
        return ledger, result["attempt_trace"][0]["request_id"]

    def test_valid_prior_v4_seals_allow_new_funding_without_rewriting_unknowns(self):
        for label, namespace in [("prior_main", prior_funding.MAIN_NAMESPACE),
                                 ("prior_calibration", prior_funding.CALIBRATION_NAMESPACE)]:
            with self.subTest(namespace=namespace):
                prior, identifier = self.prior_sealed_fixture(label, namespace)
                before_rows = self.rows(prior)
                with prior._connect() as db:
                    original_seals = [dict(row) for row in db.execute(f"SELECT * FROM {prior_funding.TABLE}")]
                current = funding.FundingLedger(prior.path)
                current.assert_ready()
                self.assertEqual(self.rows(current), before_rows)
                self.assertEqual(current.lookup(identifier)["reserved_usd"], .01)
                self.assertIsNone(current.lookup(identifier)["actual_usd"])
                self.assertEqual(current.summary()["reserved_unresolved_usd"], .01)
                guard = self.guard(ledger=current, name=label + "_new")
                guard.execute("OFFLINE_FIXTURE_NEW", "chat", "z-ai/glm-5.3-flash", self.body, .01, self.paid)
                self.assertEqual(self.rows(current)[:len(before_rows)], before_rows)
                with current._connect() as db:
                    self.assertEqual([dict(row) for row in db.execute(f"SELECT * FROM {funding.TABLE}")], original_seals)
                self.assertEqual(current.summary()["reserved_unresolved_usd"], .01)
                self.assertEqual(current.summary()["cap_usd"], 40)
                if namespace == funding.CALIBRATION_NAMESPACE:
                    with self.assertRaisesRegex(BudgetError, "calibration subcap"):
                        self.guard(namespace, ledger=current, name=label + "_over").execute(
                            "OFFLINE_FIXTURE_OVER", "chat", "z-ai/glm-5.3-flash", self.body, 1.99, self.paid)

    def test_prior_seal_evidence_tamper_still_blocks_current_readiness(self):
        prior, identifier = self.prior_sealed_fixture("prior_tamper", prior_funding.MAIN_NAMESPACE)
        seal = prior.verified_429_loss(identifier)
        current = funding.FundingLedger(prior.path)
        current.assert_ready()
        for field in ("diagnostic_path", "binding_path", "wire_path"):
            path = Path(seal[field])
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.assertRaises(PendingPaidRequest):
                current.assert_ready()
            path.write_bytes(original)
            current.assert_ready()
        self.assertIsNone(current.lookup(identifier)["actual_usd"])
        self.assertEqual(current.summary()["reserved_unresolved_usd"], .01)
        with patch.object(funding, "PREVIOUS_RELIABLE_FUNDING_SHA256", "f" * 64):
            with self.assertRaisesRegex(BudgetError, "reliable funding verifier changed"):
                current.assert_ready()

    def test_unsealed_prior_v4_unknown_is_not_accepted_by_compatibility(self):
        identifier = prior_funding.MAIN_NAMESPACE + ":OFFLINE_UNSEALED.step1.attempt1"
        self.ledger.reserve(identifier, "chat", "z-ai/glm-5.3-flash", "a" * 64, .01)
        self.ledger.uncertain(identifier, prior_funding.previous_transport.GUARD_FAILURE_REASON)
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()
        self.assertEqual(self.ledger.summary()["reserved_unresolved_usd"], .01)


if __name__ == "__main__":
    unittest.main()
