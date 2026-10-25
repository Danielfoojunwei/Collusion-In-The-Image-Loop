"""Temporary-fixture funding checks; these never open the scientific ledger."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

from budget_guard import SharedBudgetGuard, readonly_budget_snapshot, MAX_WIRE_BYTES
from run_lossless_webp_v5 import V5Ledger
from visual_collusion.budget import BudgetError, BudgetLedger, PendingPaidRequest


class BudgetGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "original_aggregate.sqlite3"
        self.ledger = V5Ledger(self.path)
        self.guard = SharedBudgetGuard(self.path, ledger=self.ledger)
        self.payload = {"model": "fixed-model", "messages": [{"role": "user", "content": "fixture"}]}
        self.posts = []

    def post(self, body, binding):
        self.posts.append((body, binding))
        # The reservation is durable before the callback can send anything.
        self.assertEqual(self.ledger.lookup(binding["request_id"])["status"], "pending")
        receipt = {key: binding[key] for key in ("request_id", "request_hash", "kind", "requested_model")}
        receipt["cost_usd"] = 0.01
        return {"response": {"actual": "fixture-response"}, "receipt": receipt}

    def execute(self, request_id="pair1:round1:A:step1", **kw):
        args = {"kind": "chat", "model": "fixed-model", "wire_payload": self.payload,
                "reserve_usd": 0.02, "post": self.post}
        args.update(kw)
        return self.guard.execute(request_id, **args)

    def row(self, request_id):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone())

    def test_old_charges_and_sealed_unknowns_count_and_are_preserved(self):
        self.ledger.reserve("old:paid", "chat", "old-model", "a" * 64, 0.4)
        self.ledger.complete("old:paid", 0.3, {"old": True}, {"cost_usd": 0.3})
        self.ledger.reserve("old:unknown", "chat", "old-model", "b" * 64, 0.2)
        self.ledger.uncertain("old:unknown", "POST interrupted or failed; billing outcome unresolved")
        self.ledger.seal_transport_loss("old:unknown")
        before = self.row("old:unknown")
        self.execute()
        self.assertEqual(before, self.row("old:unknown"))
        snapshot = readonly_budget_snapshot(self.path)
        self.assertAlmostEqual(snapshot["actual_usd"], 0.31)
        self.assertAlmostEqual(snapshot["reserved_unresolved_usd"], 0.2)
        self.assertAlmostEqual(snapshot["committed_usd"], 0.51)
        self.assertEqual(snapshot["cap_usd"], 40)

    def test_shared_cap_blocks_before_post_and_does_not_reset_for_namespace(self):
        self.ledger.reserve("old:large", "chat", "old-model", "c" * 64, 39.99)
        self.ledger.complete("old:large", 39.99, {}, {})
        with self.assertRaises(BudgetError):
            self.execute()
        self.assertEqual(self.posts, [])
        other = SharedBudgetGuard(self.path, "another_revision", ledger=self.ledger)
        with self.assertRaises(BudgetError):
            other.execute("new", "chat", "fixed-model", self.payload, 0.02, self.post)
        self.assertEqual(readonly_budget_snapshot(self.path)["request_count"], 1)

    def test_completed_exact_wire_is_cached_but_changed_wire_blocks(self):
        first = self.execute()
        second = self.execute()
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(self.posts), 1)
        with self.assertRaises(BudgetError):
            self.execute(wire_payload={"model": "fixed-model", "messages": []})
        self.assertEqual(len(self.posts), 1)

    def test_transport_failure_retains_unknown_and_prevents_any_new_post(self):
        def fail(body, binding):
            self.posts.append((body, binding))
            raise OSError("not copied to logs")
        with self.assertRaises(PendingPaidRequest):
            self.execute(post=fail)
        row = self.ledger.lookup("pressure_interview_v1:pair1:round1:A:step1")
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(row["reserved_usd"], 0.02)
        for request_id in ("pair1:round1:A:step1", "pair2:round1:A:step1"):
            with self.assertRaises(PendingPaidRequest):
                self.execute(request_id)
        self.assertEqual(len(self.posts), 1)

    def test_missing_or_mismatched_receipt_is_not_treated_as_free_output(self):
        for index, receipt in enumerate((None, {"cost_usd": 0}, {"cost_usd": float("nan")})):
            path = Path(self.tmp.name) / ("receipt" + str(index) + ".sqlite3")
            ledger = V5Ledger(path)
            guard = SharedBudgetGuard(path, ledger=ledger)
            with self.assertRaises(PendingPaidRequest):
                guard.execute("new", "chat", "fixed-model", self.payload, 0.02,
                              lambda body, binding: {"response": {}, "receipt": receipt})
            self.assertEqual(readonly_budget_snapshot(path)["reserved_unresolved_usd"], 0.02)
            if receipt is None or receipt == {"cost_usd": 0}:
                self.assertEqual(ledger.lookup("pressure_interview_v1:new")["response"], {})

    def test_wire_boundary_and_invalid_models_are_blocked_before_reservation(self):
        for value in (
            b" " * MAX_WIRE_BYTES,
            {"model": "other"},
            b'{"model":"fixed-model","model":"other"}',
            b'{"model":"fixed-model","value":NaN}',
        ):
            with self.assertRaises(BudgetError):
                self.execute(wire_payload=value)
        self.assertEqual(self.posts, [])
        self.assertEqual(readonly_budget_snapshot(self.path)["request_count"], 0)

    def test_callback_receives_exact_bytes_not_a_reserialized_dictionary(self):
        body = b'{ "model": "fixed-model", "messages": [] }'
        self.execute(wire_payload=body)
        self.assertEqual(self.posts[0][0], body)
        self.assertEqual(self.posts[0][1]["wire_body_sha256"], hashlib.sha256(body).hexdigest())

    def test_valid_body_one_byte_below_wire_boundary_is_the_exact_sent_body(self):
        prefix = b'{"model":"fixed-model","padding":"'
        suffix = b'"}'
        body = prefix + b"x" * (MAX_WIRE_BYTES - 1 - len(prefix) - len(suffix)) + suffix
        self.execute(wire_payload=body)
        self.assertEqual(len(self.posts[0][0]), MAX_WIRE_BYTES - 1)
        self.assertEqual(self.posts[0][0], body)

    def test_completed_receipt_recovers_after_postcommit_crash_without_repost(self):
        original = self.ledger.complete
        def crash_after_commit(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("simulated crash after durable aggregate completion")
        self.ledger.complete = crash_after_commit
        result = self.execute()
        self.assertTrue(result["recovered_after_completion"])
        self.assertEqual(len(self.posts), 1)
        self.assertTrue(self.execute()["cached"])
        self.assertEqual(len(self.posts), 1)

    def test_pending_intent_survives_precommit_crash_and_never_reposts(self):
        original = self.ledger.complete
        def crash_before_commit(*args, **kwargs):
            raise OSError("simulated completion failure")
        self.ledger.complete = crash_before_commit
        with self.assertRaises(PendingPaidRequest):
            self.execute()
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(readonly_budget_snapshot(self.path)["reserved_unresolved_usd"], 0.02)
        pending = self.ledger.lookup("pressure_interview_v1:pair1:round1:A:step1")
        self.assertEqual(pending["response"], {"actual": "fixture-response"})
        self.ledger.complete = original
        recovered = self.execute()
        self.assertTrue(recovered["recovered_after_completion"])
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(readonly_budget_snapshot(self.path)["actual_usd"], 0.01)

    def test_concurrent_completion_between_lookup_and_reserve_does_not_repost(self):
        reserve = self.ledger.reserve
        def completed_during_reserve(request_id, kind, model, request_hash, amount):
            reserve(request_id, kind, model, request_hash, amount)
            return self.ledger.complete(request_id, 0.01, {"other_worker": True}, {
                "cost_usd": 0.01, "request_id": request_id, "kind": kind,
                "requested_model": model, "request_hash": request_hash,
            })
        self.ledger.reserve = completed_during_reserve
        result = self.execute()
        self.assertTrue(result["cached"])
        self.assertEqual(result["response"], {"other_worker": True})
        self.assertEqual(self.posts, [])

    def test_charge_above_reserve_remains_recorded_and_halts_future_spend(self):
        def larger_charge(body, binding):
            result = self.post(body, binding)
            result["receipt"]["cost_usd"] = 0.03
            return result
        self.execute(post=larger_charge)
        snapshot = readonly_budget_snapshot(self.path)
        self.assertEqual(snapshot["actual_usd"], 0.03)
        self.assertIsNotNone(snapshot["halt_reason"])
        with self.assertRaises(BudgetError):
            self.execute("next")
        self.assertEqual(len(self.posts), 1)

    def test_readonly_snapshot_does_not_modify_ledger_or_create_a_missing_one(self):
        before = self.path.read_bytes()
        readonly_budget_snapshot(self.path)
        self.assertEqual(before, self.path.read_bytes())
        missing = Path(self.tmp.name) / "must_not_exist.sqlite3"
        with self.assertRaises(BudgetError):
            readonly_budget_snapshot(missing)
        self.assertFalse(missing.exists())
        with self.assertRaises(BudgetError):
            SharedBudgetGuard(missing)
        self.assertFalse(missing.exists())

    def test_guard_does_not_accept_a_fresh_or_different_ledger_as_aggregate(self):
        another = BudgetLedger(Path(self.tmp.name) / "another.sqlite3")
        with self.assertRaises(BudgetError):
            SharedBudgetGuard(self.path, ledger=another)
        with self.assertRaises(BudgetError):
            SharedBudgetGuard(self.path)
        with self.assertRaises(BudgetError):
            SharedBudgetGuard(self.path, namespace="old:namespace", ledger=self.ledger)


if __name__ == "__main__":
    unittest.main()
