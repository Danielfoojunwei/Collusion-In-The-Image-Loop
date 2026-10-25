"""Temporary offline fixtures for the new namespace's loss continuation policy."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from experiments.pressure_interview_v1.budget_guard import readonly_budget_snapshot
from experiments.pressure_interview_v1.live_loss_policy import (
    AUTHORIZATION, POLICY_SHA256, LOSS_TABLE, LiveLossLedger,
    TransportContinuationGuard, TerminalMissingRequest,
)
from visual_collusion.budget import BudgetError, PendingPaidRequest, canonical


class LiveLossPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "aggregate.sqlite3"
        self.ledger = LiveLossLedger(self.path)
        self.guard = TransportContinuationGuard(self.path, ledger=self.ledger)
        self.payload = {"model": "fixed-model", "messages": []}
        self.posts = []
        self.binding = None

    def row(self, request_id="pressure_interview_v1:lost"):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone())

    def fail(self, body, binding):
        self.posts.append(binding["request_id"])
        self.binding = binding
        raise TimeoutError("Secret/error text must not be logged")

    def execute(self, local_id="lost", post=None, **kwargs):
        return self.guard.execute(local_id, "chat", "fixed-model", kwargs.get("payload", self.payload),
                                  0.02, post or self.fail)

    def diagnostic(self, **changes):
        binding = self.binding
        core = {key: binding[key] for key in ("request_id", "request_hash", "wire_body_sha256", "endpoint", "wire_body_bytes")}
        core.update({"category": "transport_failure", "exception_class": "TimeoutError", "response_observed": False})
        core.update(changes)
        path = self.root / "transport_diagnostics" / (hashlib.sha256(binding["request_id"].encode()).hexdigest() + ".json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(canonical(core) + "\n")
        return {**core, "record_path": str(path), "record_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def make_loss(self):
        with self.assertRaises(PendingPaidRequest):
            self.execute()
        return self.row()

    def success(self, body, binding):
        self.posts.append(binding["request_id"])
        receipt = {key: binding[key] for key in ("request_id", "request_hash", "kind", "requested_model")}
        receipt["cost_usd"] = 0.005
        return {"response": {"ok": True}, "receipt": receipt}

    def test_exact_seal_preserves_unknown_and_permits_only_new_independent_call(self):
        before = self.make_loss()
        diagnostic = self.diagnostic()
        seal = self.guard.seal_transport_loss("lost", diagnostic)
        self.assertEqual(seal["policy_sha256"], POLICY_SHA256)
        self.assertEqual(self.row(), before)
        self.assertTrue(self.ledger.is_terminal_loss("pressure_interview_v1:lost"))
        self.execute("independent", self.success)
        snapshot = readonly_budget_snapshot(self.path)
        self.assertEqual(snapshot["reserved_unresolved_usd"], 0.02)
        self.assertEqual(snapshot["actual_usd"], 0.005)
        with self.assertRaises(TerminalMissingRequest):
            self.execute()
        self.assertEqual(self.posts, ["pressure_interview_v1:lost", "pressure_interview_v1:independent"])

    def test_lost_request_changed_content_is_not_hidden_by_missing_seal(self):
        self.make_loss()
        self.guard.seal_transport_loss("lost", self.diagnostic())
        with self.assertRaises(BudgetError):
            self.execute(payload={"model": "fixed-model", "messages": ["changed"]})
        self.assertEqual(len(self.posts), 1)

    def test_unsealed_loss_still_blocks_new_reservations(self):
        self.make_loss()
        with self.assertRaises(PendingPaidRequest):
            self.execute("independent", self.success)
        self.assertEqual(len(self.posts), 1)

    def test_only_transient_http_statuses_are_sealable(self):
        for status in (400, 401, 402, 403, 404, 413):
            with self.subTest(status=status):
                self.make_loss() if self.binding is None else None
                with self.assertRaises(PendingPaidRequest):
                    self.guard.seal_transport_loss("lost", self.diagnostic(category="http_error", exception_class="HTTPError", http_status=status))
        self.guard.seal_transport_loss("lost", self.diagnostic(category="http_error", exception_class="HTTPError", http_status=503))
        self.ledger.assert_ready()

    def test_http_408_and_429_are_sealable_with_their_own_exact_evidence(self):
        for index, status in enumerate((408, 429)):
            local = "loss" + str(index)
            with self.assertRaises(PendingPaidRequest):
                self.execute(local)
            self.guard.seal_transport_loss(local, self.diagnostic(category="http_error", exception_class="HTTPError", http_status=status))
        self.assertEqual(readonly_budget_snapshot(self.path)["reserved_unresolved_usd"], 0.04)

    def test_unknown_url_error_interrupt_programmer_and_generic_value_errors_block(self):
        self.make_loss()
        for changes in ({"exception_class": "KeyboardInterrupt"}, {"exception_class": "RuntimeError"},
                        {"exception_class": "URLError"}, {"exception_class": "URLError", "reason_class": "str"},
                        {"category": "decoding_failure", "exception_class": "ValueError"},
                        {"category": "unclassified_url_error", "exception_class": "URLError"}):
            with self.subTest(changes=changes), self.assertRaises(PendingPaidRequest):
                self.guard.seal_transport_loss("lost", self.diagnostic(**changes))

    def test_recorded_url_reason_and_actual_json_decode_errors_are_eligible(self):
        self.make_loss()
        self.guard.seal_transport_loss("lost", self.diagnostic(exception_class="URLError", reason_class="TimeoutError"))
        with self.assertRaises(PendingPaidRequest):
            self.execute("json_loss")
        self.guard.seal_transport_loss("json_loss", self.diagnostic(category="decoding_failure", exception_class="JSONDecodeError"))
        self.ledger.assert_ready()

    def test_missing_cost_or_any_observed_response_is_never_a_transport_seal(self):
        def no_cost(body, binding):
            self.binding = binding
            return {"response": {"some": "response"}, "receipt": {}}
        with self.assertRaises(PendingPaidRequest):
            self.execute(post=no_cost)
        with self.assertRaises(PendingPaidRequest):
            self.guard.seal_transport_loss("lost", self.diagnostic())
        self.assertIsNotNone(self.row()["response_json"])

    def test_diagnostic_without_file_or_changed_or_private_fields_is_rejected(self):
        self.make_loss()
        diagnostic = self.diagnostic()
        without_proof = {k: v for k, v in diagnostic.items() if k != "record_sha256"}
        with self.assertRaises(BudgetError):
            self.guard.seal_transport_loss("lost", without_proof)
        Path(diagnostic["record_path"]).write_text("{}")
        with self.assertRaises(BudgetError):
            self.guard.seal_transport_loss("lost", diagnostic)
        with self.assertRaises(BudgetError):
            self.guard.seal_transport_loss("lost", self.diagnostic(error_body="must not be accepted"))

    def test_another_request_wire_or_namespace_cannot_receive_the_seal(self):
        self.make_loss()
        for changes in ({"request_id": "pressure_interview_v1:other"}, {"request_hash": "b" * 64},
                        {"wire_body_sha256": "c" * 64}):
            with self.subTest(changes=changes), self.assertRaises(BudgetError):
                self.guard.seal_transport_loss("lost", self.diagnostic(**changes))
        with self.assertRaises(BudgetError):
            TransportContinuationGuard(self.path, "old_study", ledger=self.ledger)

    def test_seals_are_idempotent_immutable_and_invalidated_by_evidence_tampering(self):
        self.make_loss()
        diagnostic = self.diagnostic()
        seal = self.guard.seal_transport_loss("lost", diagnostic)
        self.assertEqual(self.guard.seal_transport_loss("lost", diagnostic), seal)
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM " + LOSS_TABLE)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE " + LOSS_TABLE + " SET policy_sha256='changed'")
        Path(diagnostic["record_path"]).write_text("{}")
        self.assertFalse(self.ledger.is_live_terminal_loss("pressure_interview_v1:lost"))
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()

    def test_original_legacy_sealed_losses_still_count_without_becoming_new_authority(self):
        self.ledger.reserve("old:unknown", "chat", "old", "a" * 64, 0.3)
        self.ledger.uncertain("old:unknown", "POST interrupted or failed; billing outcome unresolved")
        self.ledger.seal_transport_loss("old:unknown")
        before = self.row("old:unknown")
        self.execute("new", self.success)
        self.assertEqual(before, self.row("old:unknown"))
        self.assertEqual(readonly_budget_snapshot(self.path)["reserved_unresolved_usd"], 0.3)

    def test_new_namespace_cannot_borrow_legacy_seal_authority(self):
        self.make_loss()
        with self.assertRaises(BudgetError):
            self.ledger.seal_transport_loss("pressure_interview_v1:lost")
        # Even an injected legacy-shaped snapshot cannot authorize the new
        # namespace; only this policy's separately validated table is eligible.
        old_policy = importlib.import_module("run_lossless_webp_v5").POLICY_SHA256
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE requests SET reason=? WHERE request_id='pressure_interview_v1:lost'",
                       ("POST interrupted or failed; billing outcome unresolved",))
        raw = self.row()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO terminal_transport_losses_v5 VALUES (?,?,?,?)",
                       ("pressure_interview_v1:lost", canonical(raw), old_policy, 0))
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()

    def test_changed_request_snapshot_still_blocks(self):
        self.make_loss()
        self.guard.seal_transport_loss("lost", self.diagnostic())
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE requests SET reserved_units=reserved_units+1 WHERE request_id='pressure_interview_v1:lost'")
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()

    def test_existing_halt_is_not_waived_by_valid_transport_seals(self):
        self.make_loss()
        self.guard.seal_transport_loss("lost", self.diagnostic())
        self.ledger.halt("Account or budget stop remains binding")
        with self.assertRaises(BudgetError):
            self.execute("independent", self.success)
        self.assertEqual(len(self.posts), 1)


if __name__ == "__main__":
    unittest.main()
