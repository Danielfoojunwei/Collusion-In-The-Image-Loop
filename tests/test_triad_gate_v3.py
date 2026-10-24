"""Read-only saved-evidence tests plus isolated receipt/checkpoint fixtures.

No actor is called. SQLite mutations below affect temporary fixture databases
containing copies of already observed first-round records, never the aggregate.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_triad_gate_v3 as sidecar


class GateAmendmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = sidecar.GateAmendedCoordinator()
        engine = cls.live._engine(cls.live.selected_cases[0])
        cls.outcomes = {stage: engine.outcome(1, stage) for stage in sidecar.STAGES}
        original_db = ROOT / "runs/main/budget.sqlite3"
        with sqlite3.connect(original_db.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            cls.table_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='requests'").fetchone()[0]
            cls.rows = [dict(row) for row in db.execute("SELECT * FROM requests WHERE request_id LIKE 'triad_consensus_v2:case_05.r01.%'")]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.ledger = self.directory / "fixture.sqlite3"
        with sqlite3.connect(self.ledger) as db:
            db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("INSERT INTO settings VALUES ('cap_units','40000000000')")
            db.execute(self.table_sql)
            fields = list(self.rows[0])
            sql = "INSERT INTO requests (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")"
            db.executemany(sql, [[row[key] for key in fields] for row in self.rows])
        self.c_id = "triad_consensus_v2:case_05.r01.C_VOTE.step1"

    def tearDown(self):
        self.temp.cleanup()

    def proof(self, outcomes=None):
        return sidecar.verify_receipts(self.outcomes if outcomes is None else outcomes, self.ledger, origin="fixture")

    def edit_raw_C(self, change):
        with sqlite3.connect(self.ledger) as db:
            raw, receipt = db.execute("SELECT response_json,receipt_json FROM requests WHERE request_id=?", (self.c_id,)).fetchone()
            response = json.loads(raw)
            change(response)
            changed = sidecar.original.canonical(response)
            checksum = hashlib.sha256((changed + "\n" + receipt).encode()).hexdigest()
            db.execute("UPDATE requests SET response_json=?,result_hash=? WHERE request_id=?", (changed, checksum, self.c_id))

    def test_all_thirteen_retained_receipts_match_and_C_raw_reply_is_invalid(self):
        proof = self.proof()
        self.assertEqual(proof["completed_receipts"], 13)
        self.assertEqual(proof["C_paid_attempts"], 1)
        self.assertTrue(proof["C_raw_reply_replays_as_invalid"])

    def test_uncertain_paid_receipt_is_rejected(self):
        with sqlite3.connect(self.ledger) as db:
            db.execute("UPDATE requests SET status='uncertain' WHERE request_id=?", (self.c_id,))
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.proof()

    def test_raw_response_hash_tampering_is_rejected(self):
        with sqlite3.connect(self.ledger) as db:
            db.execute("UPDATE requests SET response_json='{}' WHERE request_id=?", (self.c_id,))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.proof()

    def test_wrong_model_is_rejected(self):
        with sqlite3.connect(self.ledger) as db:
            db.execute("UPDATE requests SET model='other/model' WHERE request_id=?", (self.c_id,))
        with self.assertRaisesRegex(ValueError, "model"):
            self.proof()

    def test_wrong_charged_amount_is_rejected(self):
        with sqlite3.connect(self.ledger) as db:
            db.execute("UPDATE requests SET actual_units=actual_units+1 WHERE request_id=?", (self.c_id,))
        with self.assertRaisesRegex(ValueError, "charged amount"):
            self.proof()

    def test_raw_usage_cost_mismatch_is_rejected_even_with_rehashed_response(self):
        self.edit_raw_C(lambda value: value["usage"].update({"cost": 0.99}))
        with self.assertRaisesRegex(ValueError, "usage"):
            self.proof()

    def test_unlisted_extra_C_attempt_is_rejected(self):
        with sqlite3.connect(self.ledger) as db:
            values = dict(next(row for row in self.rows if row["request_id"] == self.c_id))
            values["request_id"] = "triad_consensus_v2:case_05.r01.C_VOTE.step2"
            fields = list(values)
            db.execute("INSERT INTO requests (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")", [values[key] for key in fields])
        with self.assertRaisesRegex(ValueError, "Unlisted"):
            self.proof()

    def test_valid_C_JSON_cannot_be_labeled_malformed(self):
        self.edit_raw_C(lambda value: value["choices"][0]["message"].update({"content": '{"decision":"P1","rationale":"Valid fixture rationale"}'}))
        with self.assertRaisesRegex(ValueError, "valid C reply"):
            self.proof()

    def test_valid_fenced_C_JSON_cannot_be_labeled_malformed(self):
        text = '```json\n{"decision":"P1","rationale":"Valid fixture rationale"}\n```'
        self.edit_raw_C(lambda value: value["choices"][0]["message"].update({"content": text}))
        with self.assertRaisesRegex(ValueError, "valid C reply"):
            self.proof()

    def test_ambiguous_C_envelope_is_rejected(self):
        self.edit_raw_C(lambda value: value["choices"].append(deepcopy(value["choices"][0])))
        with self.assertRaisesRegex(ValueError, "Ambiguous C response"):
            self.proof()

    def test_missing_actor_receipt_is_rejected(self):
        outcomes = deepcopy(self.outcomes)
        outcomes["POSTVERDICT_B_LOG"]["receipts"] = []
        with self.assertRaisesRegex(ValueError, "lacks completed paid evidence"):
            self.proof(outcomes)

    def test_dotdot_escape_and_symlink_are_rejected(self):
        inside = self.directory / "inside"
        inside.mkdir()
        outside = self.directory / "outside.json"
        outside.write_text("{}")
        with self.assertRaisesRegex(ValueError, "escaped"):
            sidecar._safe_file(inside / ".." / "outside.json", inside)
        link = inside / "link.json"
        link.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            sidecar._safe_file(link, inside)

    def test_live_alternative_ledger_is_rejected_before_amendment_write(self):
        with patch.object(self.live, "_amendment_path", return_value=self.directory / "not_created.json"):
            with self.assertRaisesRegex(ValueError, "original aggregate ledger"):
                self.live.prepare_amendment(ledger_path=self.ledger)
        self.assertFalse((self.directory / "not_created.json").exists())

    def test_first_advance_checkpoint_has_zero_paid_or_parent_advance(self):
        coordinator = sidecar.GateAmendedCoordinator.__new__(sidecar.GateAmendedCoordinator)
        coordinator.out = self.directory / "checkpoint_fixture"
        coordinator.verify_amendment = Mock(return_value={"status": "amendment_verified", "no_model_call_or_POST": True})
        with patch.object(sidecar.original.TriadCoordinator, "advance", return_value={"status": "explicit_second_command"}) as parent:
            first = coordinator.advance()
            self.assertEqual(first["status"], "live_round_gate_amendment_passed")
            parent.assert_not_called()
            self.assertTrue(first["remaining_work_requires_explicit_next_advance"])
            second = coordinator.advance()
            self.assertEqual(second["status"], "explicit_second_command")
            parent.assert_called_once()

    def test_current_saved_actual_routing_and_full_feedback_proof_without_POST(self):
        target = self.live._engine(self.live.selected_cases[0]).next_stage()
        if not target or target["round"] != 2 or target["stage"] != "A_IMAGE_AND_VOTE":
            self.skipTest("Amendment establishment applies before round 2; later proof uses verify_amendment")
        old_gate = self.live.out / "live_round_gate.json"
        freeze = self.live.out / "freeze.json"
        before = (old_gate.read_bytes(), freeze.read_bytes())
        with patch.object(sidecar.original.TriadCoordinator, "_backend", side_effect=AssertionError("No paid backend may be constructed")):
            gate = sidecar.evaluate_technical_gate(self.live, self.ledger)
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(all(gate["checks"].values()))
        self.assertEqual(gate["scientific_ballot_status"], "incomplete")
        self.assertEqual((old_gate.read_bytes(), freeze.read_bytes()), before)


if __name__ == "__main__":
    unittest.main()
