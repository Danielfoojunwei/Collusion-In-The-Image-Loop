"""Offline nonretry continuation, claim integrity and unknown-reserve fixtures."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch

from scripts import run_triad_max_transport_v1 as runner
from experiments.pressure_interview_v1.budget_guard import readonly_budget_snapshot
from tests.test_triad_max_format_v1_runner import FormatRunnerTests

ROOT = Path(__file__).resolve().parents[1]
ACTUAL = ROOT / "runs/main/triad_max_pressure_live"


class TransportRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FormatRunnerTests.setUpClass()
        with sqlite3.connect((ROOT / "runs/main/budget.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            cls.loss = dict(db.execute("SELECT * FROM requests WHERE request_id=?", (runner.KNOWN_REQUEST,)).fetchone())

    def setUp(self):
        self.fixture = FormatRunnerTests("test_prepare_is_offline_and_preserves_original_files_and_three_failures")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.coordinator.prepare_format_amendment()
        engine = self.fixture.coordinator._engine(self.fixture.coordinator.selected_cases[0])
        self.request = engine.claim_actor_dispatch()
        target = engine.next_stage()
        self.identifier = runner.original.digest({"case": runner.frozen.CASE_ID, "request_id": target["request_id"]})
        claim = self.fixture.out / "stage_cache" / (self.identifier + ".claim.json")
        runner.original.save_once(claim, {"request_id": target["request_id"], "request_hash": self.request["request_hash"],
            "case_id": runner.frozen.CASE_ID, "round": 2, "stage": "A_IMAGE_AND_VOTE"})
        self.claim = claim
        self.original_dispatch = engine._path(2, "A_IMAGE_AND_VOTE", "dispatch").read_bytes()
        with sqlite3.connect(self.fixture.ledger) as db:
            columns = list(self.loss)
            db.execute("INSERT INTO requests (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")", [self.loss[key] for key in columns])
        evidence = self.fixture.out / "provider_evidence"
        for dirname in ("blobs", "transport_blobs"):
            shutil.copytree(ACTUAL / "provider_evidence" / dirname, evidence / dirname, dirs_exist_ok=True)
        local = runner.KNOWN_REQUEST.removeprefix(runner.frozen.NAMESPACE + ":")
        for dirname, name in [("transport_diagnostics", hashlib.sha256(runner.KNOWN_REQUEST.encode()).hexdigest() + ".json"),
                               ("transport_requests", hashlib.sha256(local.encode()).hexdigest() + ".json")]:
            path = evidence / dirname / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ACTUAL / "provider_evidence" / dirname / name, path)
        self.coordinator = runner.TransportAmendedCoordinator(self.fixture.out,
            config=self.fixture.coordinator.config, plan=self.fixture.coordinator.plan, evidence_origin="fixture")

    def row(self):
        with sqlite3.connect(self.fixture.ledger) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute("SELECT * FROM requests WHERE request_id=?", (runner.KNOWN_REQUEST,)).fetchone())

    def test_prepare_never_posts_preserves_claim_and_full_unknown_reservation(self):
        before = self.row()
        claim = self.claim.read_bytes()
        with patch.object(self.coordinator, "_backend", side_effect=AssertionError("Preparation must not use a backend")):
            result = self.coordinator.prepare_transport_amendment()
        self.assertEqual(self.row(), before)
        self.assertEqual(self.claim.read_bytes(), claim)
        self.assertEqual(result["original_request_rows_preserved"], 17)
        self.assertFalse(result["unknown_reservations_released"])
        engine = self.coordinator._engine(self.coordinator.selected_cases[0])
        self.assertEqual(engine._path(2, "A_IMAGE_AND_VOTE", "dispatch").read_bytes(), self.original_dispatch)
        outcome = engine.outcome(2, "A_IMAGE_AND_VOTE")
        self.assertIsNone(outcome["output"])
        self.assertEqual(outcome["receipts"], [])
        self.assertIn("TRANSPORT_UNAVAILABLE", outcome["reason"])
        self.assertFalse(outcome["provenance"]["actor_response_observed"])
        self.assertFalse(outcome["provenance"]["model_refusal"])
        self.assertEqual(engine.next_stage()["stage"], "B_IMAGE_AND_VOTE")
        view = json.loads(engine.actor_request()["messages"][1]["content"])
        self.assertFalse(view["current_A_image"]["available"])
        budget = readonly_budget_snapshot(self.fixture.ledger)
        self.assertEqual(budget["reserved_unresolved_usd"], before["reserved_units"] / 1e9)
        runner._helper().TransportLedger(self.fixture.ledger, 40).assert_ready()

    def test_preparation_is_idempotent_and_later_scheduled_actors_can_continue(self):
        self.coordinator.prepare_transport_amendment()
        original_row = self.row()
        engine = self.coordinator._engine(self.coordinator.selected_cases[0])
        old_outcome = engine._path(2, "A_IMAGE_AND_VOTE").read_bytes()
        self.coordinator.prepare_transport_amendment()
        self.assertEqual(engine._path(2, "A_IMAGE_AND_VOTE").read_bytes(), old_outcome)
        self.assertEqual(self.row(), original_row)
        self.fixture.actor(engine=engine)
        self.fixture.actor(engine=engine)
        self.assertEqual(engine.next_stage()["stage"], "REVIEWER")
        self.assertEqual(engine.consensus(2)["status"], "incomplete")
        self.assertIsNone(engine.consensus(2)["votes"]["A"])
        job = engine.export_reviewer_packet()
        packet = json.loads(Path(job["packet_path"]).read_text())
        self.assertEqual([value["speaker"] for value in packet["images"]], ["B", "C"])
        self.assertEqual(packet["infrastructure_missingness"][0]["actor"], "A")
        self.assertEqual(packet["infrastructure_missingness"][0]["http_status"], 429)
        self.assertFalse(packet["infrastructure_missingness"][0]["model_refusal"])
        self.assertIn("unverified", packet["infrastructure_missingness"][0]["input_processing"])
        self.assertNotIn("received_by_A_in_round", packet["technical_image_availability_note"])
        self.assertIn("does not prove", packet["technical_image_availability_note"]["model_processing"])

    def test_tampered_original_request_claim_blocks_missing_stage_record(self):
        self.claim.write_text("{}")
        with self.assertRaisesRegex(ValueError, "cache claim changed"):
            self.coordinator.prepare_transport_amendment()
        self.assertFalse((self.fixture.out / "cases" / runner.frozen.CASE_ID / "rounds/r02/A_IMAGE_AND_VOTE.outcome.json").exists())

    def test_nontransient_http_remains_blocking_without_stage_outcome(self):
        path = self.coordinator._loss_diagnostic(runner.KNOWN_REQUEST)
        value = json.loads(path.read_text())
        value["http_status"] = 401
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "not the observed HTTP429"):
            self.coordinator.prepare_transport_amendment()
        self.assertFalse(self.coordinator._transport_note().exists())
        self.assertIsNone(self.row()["actual_units"])

    def test_existing_format_history_and_external_sources_are_preserved(self):
        note = self.fixture.out / runner.frozen.AMENDMENT_PATH
        old_note = note.read_bytes()
        before = {str(path.relative_to(self.fixture.out)): path.read_bytes() for path in
            (self.fixture.out / "cases" / runner.frozen.CASE_ID / "rounds/r01").glob("*.json")}
        self.coordinator.prepare_transport_amendment()
        self.assertEqual(note.read_bytes(), old_note)
        for relative, raw in before.items():
            self.assertEqual((self.fixture.out / relative).read_bytes(), raw)
        transport = json.loads(self.coordinator._transport_note().read_text())
        self.assertEqual(set(transport["runtime_sources"]), {"runner", "helper"})
        self.assertTrue(transport["no_POST_during_preparation"])
        for binding in transport["runtime_sources"].values():
            self.assertEqual(runner.original.file_digest(binding["path"]), binding["sha256"])


if __name__ == "__main__":
    unittest.main()
