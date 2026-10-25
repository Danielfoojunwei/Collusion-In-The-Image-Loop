"""A proven no-POST stage is distinct from any paid/uncertain attempt."""
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import recover_repaired_v7_prepaid as recovery
from experiments.triad_repaired_v7.coordinator import save_once, digest, ContractError


class PrepaidRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.out = self.root / "run"
        self.ledger = self.root / "budget.sqlite3"
        with sqlite3.connect(self.ledger) as db:
            db.execute("CREATE TABLE requests(request_id TEXT PRIMARY KEY,status TEXT)")
        self.case = {"case_id": "fixture"}
        self.request = {"request_id": "fixture.r09.A_IMAGE_AND_VOTE", "request_hash": "f" * 64, "image_refs": {}}
        self.identifier = digest({"case": "fixture", "request_id": self.request["request_id"]})
        self.stage = self.out / "cases/fixture/rounds/r09"
        save_once(self.stage / "A_IMAGE_AND_VOTE.request.json", self.request)
        save_once(self.stage / "A_IMAGE_AND_VOTE.dispatch.json", {"request_hash": self.request["request_hash"]})
        self.claim = self.out / "stage_cache" / (self.identifier + ".claim.json")
        save_once(self.claim, {"request_id": self.request["request_id"], "request_hash": self.request["request_hash"],
                  "case_id": "fixture", "round": 9, "stage": "A_IMAGE_AND_VOTE"})
        save_once(self.out / "analysis/UNPAID_A9_PREVIEW.json", self.request)
        save_once(self.out / "analysis/FIRST_ADVANCE_PREPAID_FAILURE.json", {
            "new_v7_paid_rows_count": 0, "experimental_response": False, "actor_request_hash": self.request["request_hash"]})
        save_once(self.out / "analysis/prepaid_repair_01/freeze.before.json", {"fixture": "old freeze"})
        save_once(self.out / "freeze.json", {"fixture": "new freeze"})
        self.engine = SimpleNamespace(directory=self.out / "cases/fixture",
            actor_request=lambda: self.request, outcome=lambda *args: None)
        self.coordinator = SimpleNamespace(out=self.out, config={"budget": {"aggregate_ledger": str(self.ledger)}},
            _verify_freeze=Mock(), _require_workflow_gate=Mock(),
            _pending=lambda: (self.case, self.engine, {"round": 9, "stage": "A_IMAGE_AND_VOTE"}))

    def tearDown(self):
        self.temporary.cleanup()

    def check(self):
        with patch.object(recovery, "DEFAULT_OUT", self.out), patch.object(recovery.TriadBackend, "_references", return_value={}) as refs:
            result = recovery.checked_request(self.coordinator)
            refs.assert_called_once_with(self.request)
            return result

    def test_exact_no_post_claim_remains_and_uses_original_cache_path(self):
        original = self.claim.read_bytes()
        result = self.check()
        self.assertEqual(result[3], self.request)
        self.assertEqual(result[4], self.out / "stage_cache" / (self.identifier + ".response.json"))
        self.assertEqual(self.claim.read_bytes(), original)
        self.assertEqual(result[5]["paid_v7_rows_before_operation"], 0)

    def test_any_completed_uncertain_or_pending_row_blocks_recovery(self):
        for status in ("completed", "uncertain", "pending"):
            with sqlite3.connect(self.ledger) as db:
                db.execute("DELETE FROM requests")
                db.execute("INSERT INTO requests VALUES(?,?)", ("triad_repaired_v7:any", status))
            with self.assertRaisesRegex(ContractError, "cannot redispatch"):
                self.check()

    def test_altered_claim_is_not_cleared_or_replaced(self):
        self.claim.write_text("{}")
        with self.assertRaisesRegex(ContractError, "claim changed"):
            self.check()
        self.assertEqual(self.claim.read_text(), "{}")

    def test_changed_preview_cannot_authorize_different_inputs(self):
        self.request["request_hash"] = "e" * 64
        with self.assertRaisesRegex(ContractError, "request changed"):
            self.check()
