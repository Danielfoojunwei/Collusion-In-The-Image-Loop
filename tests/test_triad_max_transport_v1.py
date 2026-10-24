"""Offline transient-loss evidence tests; temporary ledgers, no network calls."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from visual_collusion.archive import store_pixels
from visual_collusion.budget import BudgetLedger, BudgetError, PendingPaidRequest, canonical, request_digest
from visual_collusion.providers import image_content
from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard
from scripts import triad_max_transport_v1 as amendment


class TransportLossTests(unittest.TestCase):
    identifier = "triad_max_pressure_v3:case_05_max.r02.A_IMAGE_AND_VOTE.step1"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "budget.sqlite3"
        BudgetLedger(self.path)
        self.ledger = amendment.TransportLedger(self.path)
        self.evidence = self.root / "provider_evidence"

    def tearDown(self):
        self.temporary.cleanup()

    def raw_row(self, identifier=None):
        with self.ledger._connect() as db:
            return dict(db.execute("SELECT * FROM requests WHERE request_id=?", (identifier or self.identifier,)).fetchone())

    def change_row(self, field, value):
        with self.ledger._connect() as db:
            db.execute(f"UPDATE requests SET {field}=? WHERE request_id=?", (value, self.identifier))

    def fixture_loss(self, status=429, *, identifier=None, with_image=False):
        identifier = identifier or self.identifier
        local = identifier.split(":", 1)[1]
        payload = {"model": "z-ai/glm-5.3-flash", "messages": [{"role": "user", "content": "Explicit offline fixture."}],
                   "temperature": 1.3, "reasoning": {"effort": "high"}}
        archived = deepcopy(payload)
        self.source_path = None
        if with_image:
            output = io.BytesIO()
            Image.new("RGB", (8, 8), "green").save(output, format="PNG")
            raw = output.getvalue()
            payload["messages"][0]["content"] = [image_content(raw)]
            archived = deepcopy(payload)
            blob = store_pixels(self.evidence, raw)
            self.source_path = self.evidence / "blobs" / (blob.removeprefix("blob:sha256:") + ".png")
            archived["messages"][0]["content"][0]["image_url"]["url"] = blob
        wire, mapping = amendment._codec().prepare_wire(self.evidence, local, "/chat/completions", payload)
        wire_raw = canonical(wire).encode()
        self.last_wire_raw = wire_raw
        sha = hashlib.sha256(wire_raw).hexdigest()
        request_hash = request_digest({"endpoint": "/chat/completions", "wire_body_sha256": sha})
        self.ledger.reserve(identifier, "chat", payload["model"], request_hash, 0.0127784)
        self.ledger.uncertain(identifier, amendment.GUARD_FAILURE_REASON)
        diagnostic = {"category": "http_error", "endpoint": "/chat/completions", "exception_class": "HTTPError", "http_status": status,
                      "request_hash": request_hash, "request_id": identifier, "response_observed": False,
                      "wire_body_bytes": len(wire_raw), "wire_body_sha256": sha}
        diagnostic_path = self.evidence / "transport_diagnostics" / (hashlib.sha256(identifier.encode()).hexdigest() + ".json")
        diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        diagnostic_path.write_text(canonical(diagnostic) + "\n")
        self.binding_path = self.evidence / "transport_requests" / (hashlib.sha256(local.encode()).hexdigest() + ".json")
        self.binding_path.parent.mkdir(parents=True, exist_ok=True)
        self.binding_path.write_text(canonical({"endpoint": "/chat/completions", "request_id": local, "logical_payload": archived,
                                                 "mapping": mapping, "logical_images_content_addressed": True}) + "\n")
        return diagnostic_path

    def test_observed_429_seal_preserves_row_reserve_and_blocks_same_request_retry(self):
        diagnostic = self.fixture_loss(with_image=True)
        original = self.raw_row()
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()
        seal = self.ledger.seal_existing_response_less_loss(self.identifier, diagnostic)
        self.assertEqual(self.raw_row(), original)
        self.assertEqual(seal["reserved_units"], 12778400)
        self.assertIsNone(seal["actual_cost_usd"])
        self.assertTrue(seal["unknown_charge_retained"])
        self.ledger.assert_ready()
        self.assertEqual(self.ledger.summary()["reserved_unresolved_usd"], 0.0127784)
        self.assertEqual(amendment.completed_snapshot_allowing_sealed_losses(self.path), {})
        self.assertEqual(amendment.verified_terminal_loss(self.path, self.identifier), seal)
        self.assertEqual(self.ledger.seal_existing_response_less_loss(self.identifier, diagnostic), seal)
        guard = SharedBudgetGuard(self.path, amendment.NAMESPACE, ledger=self.ledger)
        callback = lambda *_: self.fail("A sealed lost paid request must never POST again")
        with self.assertRaises(PendingPaidRequest):
            guard.execute(self.identifier.split(":", 1)[1], "chat", "z-ai/glm-5.3-flash",
                          self.last_wire_raw, 0.0127784, callback)
        self.assertEqual(self.raw_row(), original)

    def test_other_recognized_transient_http_statuses(self):
        for number, status in enumerate((408, 500, 503, 599), 1):
            identifier = f"triad_max_pressure_v3:case_05_max.r03.A_IMAGE_AND_VOTE.step{number}"
            diagnostic = self.fixture_loss(status, identifier=identifier)
            self.ledger.seal_existing_response_less_loss(identifier, diagnostic)
            self.ledger.assert_ready()

    def test_nontransient_and_account_statuses_are_blocking(self):
        diagnostic = self.fixture_loss()
        original = json.loads(diagnostic.read_text())
        for status in (400, 401, 402, 403, 404, 409, 499, 600):
            with self.subTest(status=status):
                diagnostic.write_text(canonical({**original, "http_status": status}))
                with self.assertRaises(BudgetError):
                    self.ledger.seal_existing_response_less_loss(self.identifier, diagnostic)
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()

    def test_observed_response_unclassified_error_or_binding_mismatch_rejected(self):
        diagnostic = self.fixture_loss(503)
        original = json.loads(diagnostic.read_text())
        for changes in ({"response_observed": True}, {"category": "transport_failure"}, {"exception_class": "ValueError"},
                        {"request_hash": "f" * 64}, {"wire_body_sha256": "f" * 64}, {"wire_body_bytes": 10},
                        {"endpoint": "/images"}, {"http_status": True}):
            with self.subTest(changes=changes):
                diagnostic.write_text(canonical({**original, **changes}))
                with self.assertRaises(BudgetError):
                    self.ledger.seal_existing_response_less_loss(self.identifier, diagnostic)

    def test_pending_or_any_partial_response_or_known_amount_is_not_sealable(self):
        diagnostic = self.fixture_loss()
        original = self.raw_row()
        fields = {"status": "pending", "actual_units": 0, "response_json": "{}", "receipt_json": "{}",
                  "result_hash": "f" * 64, "finished": 1.0, "reserved_units": 0, "reason": "unclassified failure"}
        for field, value in fields.items():
            with self.subTest(field=field):
                self.change_row(field, value)
                with self.assertRaises(BudgetError):
                    self.ledger.seal_existing_response_less_loss(self.identifier, diagnostic)
                self.change_row(field, original[field])

    def test_other_namespace_rejected(self):
        identifier = "other_study:case_05_max.r02.A_IMAGE_AND_VOTE.step1"
        diagnostic = self.fixture_loss(identifier=identifier)
        with self.assertRaises(BudgetError):
            self.ledger.seal_existing_response_less_loss(identifier, diagnostic)

    def test_seals_are_immutable_and_row_tampering_invalidates_readiness(self):
        diagnostic = self.fixture_loss()
        self.ledger.seal_existing_response_less_loss(self.identifier, diagnostic)
        with self.ledger._connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(f"UPDATE {amendment.TABLE} SET policy_sha256='wrong'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(f"DELETE FROM {amendment.TABLE}")
        self.change_row("reserved_units", 1)
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()
        with self.assertRaises(BudgetError):
            amendment.verified_terminal_loss(self.path, self.identifier)

    def test_diagnostic_binding_source_and_dependency_tampering_fail_closed(self):
        diagnostic = self.fixture_loss(with_image=True)
        self.ledger.seal_existing_response_less_loss(self.identifier, diagnostic)
        for path in (diagnostic, self.binding_path, self.source_path):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                with self.assertRaises(PendingPaidRequest):
                    self.ledger.assert_ready()
                with self.assertRaises(BudgetError):
                    amendment.completed_snapshot_allowing_sealed_losses(self.path)
                path.write_bytes(original)
                self.ledger.assert_ready()
        with patch.dict(amendment.DEPENDENCIES, {"run_lossless_webp_v5.py": "f" * 64}):
            with self.assertRaises(BudgetError):
                self.ledger.assert_ready()

    def test_completed_snapshot_verifies_receipts_and_does_not_hide_another_pending_call(self):
        identifier = "triad_max_pressure_v3:case_05_max.r01.C_VOTE.step1"
        request_hash = "a" * 64
        self.ledger.reserve(identifier, "chat", "z-ai/glm-5.3-flash", request_hash, 0.01)
        usage = {"cost": 0.001}
        response = {"id": "fixture-complete", "usage": usage}
        receipt = {"request_id": identifier, "request_hash": request_hash, "requested_model": "z-ai/glm-5.3-flash",
                   "kind": "chat", "cost_usd": 0.001, "response_id": "fixture-complete", "usage": usage}
        self.ledger.complete(identifier, 0.001, response, receipt)
        diagnostic = self.fixture_loss()
        self.ledger.seal_existing_response_less_loss(self.identifier, diagnostic)
        snapshot = amendment.completed_snapshot_allowing_sealed_losses(self.path)
        self.assertEqual(set(snapshot), {identifier})
        self.assertEqual(snapshot[identifier]["actual_units"], 1000000)
        self.ledger.reserve("triad_max_pressure_v3:case_05_max.r02.B_IMAGE_AND_VOTE.step1", "chat", "z-ai/glm-5.3-flash", "b" * 64, 0.01)
        with self.assertRaises(BudgetError):
            amendment.completed_snapshot_allowing_sealed_losses(self.path)

    def test_completed_receipt_integrity_tampering_is_not_omitted(self):
        identifier = "triad_max_pressure_v3:case_05_max.r01.C_VOTE.step1"
        self.ledger.reserve(identifier, "chat", "z-ai/glm-5.3-flash", "a" * 64, 0.01)
        usage = {"cost": 0.001}
        self.ledger.complete(identifier, 0.001, {"id": "complete", "usage": usage},
            {"request_id": identifier, "request_hash": "a" * 64, "requested_model": "z-ai/glm-5.3-flash",
             "kind": "chat", "cost_usd": 0.001, "response_id": "complete", "usage": usage})
        with self.ledger._connect() as db:
            db.execute("UPDATE requests SET response_json='{}' WHERE request_id=?", (identifier,))
        with self.assertRaises(BudgetError):
            amendment.completed_snapshot_allowing_sealed_losses(self.path)

    def test_legacy_snapshot_bound_losses_remain_allowed(self):
        identifier = "old_fixture:response_less"
        self.ledger.reserve(identifier, "chat", "legacy-fixture-model", "c" * 64, 0.01)
        self.ledger.uncertain(identifier, amendment._legacy.LOSS_REASON)
        self.ledger.seal_transport_loss(identifier)
        self.ledger.assert_ready()
        self.assertEqual(amendment.completed_snapshot_allowing_sealed_losses(self.path), {})
        self.assertEqual(self.ledger.summary()["reserved_unresolved_usd"], 0.01)


if __name__ == "__main__":
    unittest.main()
