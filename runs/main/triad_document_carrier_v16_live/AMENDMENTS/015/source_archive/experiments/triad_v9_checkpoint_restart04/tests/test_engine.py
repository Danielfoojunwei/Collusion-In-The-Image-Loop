"""Offline selector/route/behavioral classification tests for restart04."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from experiments.triad_emergent_v9 import engine as original
from experiments.triad_emergent_v9.tests.test_engine import provenance, reviewer_answer
from experiments.triad_v9_checkpoint_restart04.attempts import AttemptIntegrityError
from experiments.triad_v9_checkpoint_restart04.engine import Engine
from .test_attempts import TECHNICAL, TransactionFixture


class EngineTests(TransactionFixture):
    def test_no_active_attempt_can_dispatch_or_read_legacy_failed_round(self):
        self.setup_store()
        with self.assertRaises(AttemptIntegrityError):
            self.engine.actor_request()
        self.assertEqual(self.engine.next_stage()["round"], 1)
        self.assertIsNone(self.engine.outcome(1, "A_IMAGE_AND_VOTE"))

    def test_attempt_review_jobs_reject_another_attempt_without_changing_first_provenance(self):
        self.setup_store()
        self.begin(1)
        self.advance_to("REVIEWER")
        first = self.engine.export_reviewer_packet()
        self.store.abandon_attempt(TECHNICAL)
        self.begin(1)
        self.advance_to("REVIEWER")
        second = self.engine.export_reviewer_packet()
        self.assertNotEqual(first["job_id"], second["job_id"])
        self.assertNotEqual(first["packet_path"], second["packet_path"])
        answer = reviewer_answer(original.read_json(second["packet_path"]))
        observed_provenance = provenance(second)
        expected = deepcopy(observed_provenance)
        with self.assertRaises(AttemptIntegrityError):
            self.engine.ingest_review(answer, observed_provenance, job_id=first["job_id"])
        result = self.engine.ingest_review(answer, observed_provenance, job_id=second["job_id"])
        self.assertEqual(result["provenance"], expected)
        self.assertEqual(result["reviewer_job_id"], second["job_id"])
        self.assertEqual(result["attempt_id"], "r01.a02")

    def test_twelve_round_fixture_with_early_and_late_restarts_and_chronological_routes(self):
        self.setup_store()
        completed, requests = [], []
        for number in range(1, 13):
            self.begin(number, tag=f"FINAL_R{number}")
            if number in {2, 11}:
                self.advance_to("POSTVERDICT_C_LOG" if number == 11 else "B_IMAGE_AND_VOTE")
                self.store.abandon_attempt({**TECHNICAL, "round": number})
                self.begin(number, tag=f"FINAL_R{number}")
            first_request = self.engine.actor_request()
            if number > 1:
                self.assertEqual(first_request["image_refs"]["previous_C"]["sha256"],
                                 self.engine.image_for(number - 1, "C")["sha256"])
                view = json.loads(first_request["messages"][1]["content"])
                self.assertEqual([item["round"] for item in view["own_logs"]], list(range(1, number)))
                self.assertEqual([item["round"] for item in view["public_history"]], list(range(1, number)))
            self.finish(self.engine, number)
            requests.extend(self.backend.requests)
            selector = self.engine.complete_round_checkpoint()
            completed.append(selector)
            self.assertEqual(self.store.committed_rounds(), number)
            self.assertEqual(selector["round"], number)
        self.assertIsNone(self.engine.next_stage())
        self.assertEqual(self.engine.verify()["committed_rounds"], 12)
        self.assertEqual(len({row["round"] for row in completed}), 12)
        self.assertEqual(len(self.store.inventory()), 14)
        self.assertEqual(self.store.verify()["abandoned_attempts"], 2)
        for request in requests:
            if request["mode"] == "vote":
                self.assertEqual(set(request["image_refs"]), {"current_A", "current_B"})
            if request["mode"] == "visual_return":
                self.assertEqual(set(request["image_refs"]), {"current_B"})
            if request["role"] == "B" and request["mode"] == "visual_vote":
                self.assertEqual(set(request["image_refs"]), {"current_A"})

    def test_valid_wrong_choice_refusal_bad_native_arguments_and_invalid_review_are_terminal(self):
        self.setup_store()

        def behavior(request, result):
            if request["stage"] == "A_IMAGE_AND_VOTE":
                result["output"]["decision"] = self.plan["rounds"][0]["private_preference"]
                result["output"]["image"] = None
                result["component_status"]["image"] = "declined"
                result["actions"].append({"validation_error": "Unsupported native argument", "arguments": {
                    "extra": True, "path": "/must-not-read-model-authored-path", "sha256": "0" * 64}})
                result["status"] = "partial"
            elif request["stage"] == "B_IMAGE_AND_VOTE":
                result["output"]["image"] = None
                result["output"]["decision"] = None
                result["component_status"]["image"] = "declined"
                result["component_status"]["decision"] = "refused"
                result["status"] = "refused"
            elif request["stage"] == "POSTVERDICT_B_LOG":
                result["output"]["carryover_note"] = ""
                result["output"]["image_reflection"] = ""
                result["component_status"]["log"] = "model_invalid"
                result["status"] = "model_invalid"

        self.begin(1, policy=behavior)
        self.finish(self.engine, 1, invalid_review=True)
        self.engine.complete_round_checkpoint()
        self.assertEqual(self.engine.outcome(1, "A_IMAGE_AND_VOTE")["status"], "partial")
        self.assertEqual(self.engine.outcome(1, "B_IMAGE_AND_VOTE")["status"], "refused")
        self.assertEqual(self.engine.outcome(1, "REVIEWER")["status"], "model_invalid")
        self.assertEqual(self.engine.outcome(1, "POSTVERDICT_B_LOG")["status"], "model_invalid")
        self.assertEqual(len(self.store.inventory()), 1)
        self.assertEqual(self.store.verify()["abandoned_attempts"], 0)

    def test_selected_committed_stages_stay_readable_after_reconstruction(self):
        self.setup_store()
        context = self.begin(1)
        self.finish(self.engine, 1)
        selector = self.engine.complete_round_checkpoint()
        reconstructed = Engine(self.out, self.config, self.plan, store=self.store)
        self.assertEqual(reconstructed._path(1, "A_IMAGE_AND_VOTE"),
                         Path(context["working_root"]) / "episode/rounds/r01/A_IMAGE_AND_VOTE.outcome.json")
        self.assertEqual(reconstructed._verify_chain(), selector["chain_head"])
        self.assertEqual(reconstructed.summary()["complete_rounds"], 1)
        self.assertEqual(reconstructed.summary()["working_terminal_stages"], 0)

    def test_corrupt_working_outcome_or_cross_attempt_record_holds_before_next_actor(self):
        self.setup_store()
        self.begin(1)
        self.engine.run_next_actor()
        path = self.engine._path(1, "A_IMAGE_AND_VOTE")
        record = original.read_json(path)
        record["attempt_id"] = "r01.a02"
        record["record_hash"] = original.digest({key: value for key, value in record.items() if key != "record_hash"})
        path.write_text(original.canonical(record) + "\n")
        calls = len(self.backend.requests)
        with self.assertRaises(AttemptIntegrityError):
            self.engine.run_next_actor()
        self.assertEqual(len(self.backend.requests), calls)

    def test_corrupt_shared_memory_pixels_hold_before_next_actor(self):
        self.setup_store()
        self.begin(1)
        self.finish(self.engine, 1)
        self.engine.complete_round_checkpoint()
        self.begin(2)
        image = self.engine.image_for(1, "C")
        Path(image["path"]).write_bytes(b"offline corrupt raster fixture")
        with self.assertRaises(AttemptIntegrityError):
            self.engine.run_next_actor()
        self.assertEqual(self.backend.requests, [])

    def test_another_attempts_valid_raster_cannot_become_current_delivery(self):
        self.setup_store()
        self.begin(1, color="red")
        self.engine.run_next_actor()
        prior = self.engine.outcome(1, "A_IMAGE_AND_VOTE")["actions"][0]["generated_image"]
        self.store.abandon_attempt(TECHNICAL)
        self.begin(1, color="blue")
        request = self.engine.actor_request()
        result = self.backend.run_actor(request)
        result["output"]["image"] = deepcopy(prior)
        result["actions"][0]["generated_image"] = deepcopy(prior)
        with self.assertRaises(AttemptIntegrityError):
            self.engine.validate_actor_result(request, result)


if __name__ == "__main__":
    unittest.main()
