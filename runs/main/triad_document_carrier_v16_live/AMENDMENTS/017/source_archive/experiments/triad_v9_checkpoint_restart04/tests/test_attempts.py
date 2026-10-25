"""Offline whole-round transaction fixtures; no scientific/provider results."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.triad_emergent_v9 import engine as original
from experiments.triad_emergent_v9.design import make_plan
from experiments.triad_emergent_v9.tests.test_engine import (
    ScriptedBackend, actual_png, config, provenance, reviewer_answer,
)
from experiments.triad_v9_checkpoint_restart04 import attempts
from experiments.triad_v9_checkpoint_restart04.attempts import (
    AttemptIntegrityError, AttemptLimitReached, AttemptStore,
)
from experiments.triad_v9_checkpoint_restart04.engine import Engine


TECHNICAL = {"technical": True, "fault_class": "offline_injected_transport_loss"}


class TaggedBackend(ScriptedBackend):
    def __init__(self, out, plan, tag="successful", color="white", policy=None):
        super().__init__(out, plan, policy=policy)
        self.tag, self.color = tag, color

    def run_actor(self, request):
        result = super().run_actor(request)
        image = result["output"]["image"]
        if image:
            image = actual_png(image["path"], self.color)
            result["output"]["image"] = image
            result["actions"][0]["generated_image"] = deepcopy(image)
            result["actions"][0]["arguments"]["prompt"] = f"Offline image fixture {self.tag}."
        if request["mode"] == "log" and result["component_status"]["log"] == "valid":
            result["output"]["carryover_note"] = f"{self.tag} {request['role']} round{request['round']}."
        return result


class TransactionFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name) / "experiment"
        self.plan = make_plan(seed=20261025, condition="induced_shared_private")
        self.config = config()

    def tearDown(self):
        self.temp.cleanup()

    def setup_store(self, legacy_rounds=0):
        old_backend = TaggedBackend(self.out, self.plan, tag="COMMITTED_LEGACY")
        old = original.Engine(self.out, self.config, self.plan, backend=old_backend)
        for number in range(1, legacy_rounds + 1):
            self.finish(old, number, legacy=True)
        self.store = AttemptStore(self.out, legacy_rounds=legacy_rounds)
        vector = {f"episode/rounds/r{number:02d}/{stage}.outcome.json":
                  original.file_digest(old._path(number, stage))
                  for number in range(1, legacy_rounds + 1) for stage in original.STAGES}
        self.store.initialize(old._verify_chain(), vector)
        self.engine = Engine(self.out, self.config, self.plan, store=self.store)
        return old

    def begin(self, number, *, tag="SUCCESSFUL_ATTEMPT", color="blue", policy=None):
        context = self.store.begin_attempt(number)
        self.backend = TaggedBackend(context["working_root"], self.plan, tag=tag, color=color, policy=policy)
        self.engine = Engine(self.out, self.config, self.plan, backend=self.backend, store=self.store)
        return context

    def review(self, engine, *, legacy=False, tag="VALID_OFFLINE_REVIEW", invalid=False):
        job = engine.export_reviewer_packet()
        answer = reviewer_answer(original.read_json(job["packet_path"]))
        answer["feedback"] = tag
        if invalid:
            answer["task_assessment"]["correct_decision"] = "reject_all"
            # Always invalid, including tasks whose correct choice rejects all.
            answer["collusion_assessment"]["confidence"] = "bad"
        keywords = {} if legacy else {"job_id": job["job_id"]}
        return engine.ingest_review(answer, provenance(job), raw_answer=original.canonical(answer), **keywords)

    def finish(self, engine, number, *, legacy=False, review_tag="VALID_OFFLINE_REVIEW", invalid_review=False):
        while (target := engine.next_stage()) and target["round"] == number:
            if target["stage"] == "REVIEWER":
                self.review(engine, legacy=legacy, tag=review_tag, invalid=invalid_review)
            else:
                engine.run_next_actor()

    def advance_to(self, stage, *, review_tag="FAILED_ATTEMPT_REVIEW"):
        while self.engine.next_stage()["stage"] != stage:
            if self.engine.next_stage()["stage"] == "REVIEWER":
                self.review(self.engine, tag=review_tag)
            else:
                self.engine.run_next_actor()


class AttemptStoreTests(TransactionFixture):
    def test_each_stage_fault_leaves_checkpoint_and_retry_inputs_unchanged(self):
        for fault_stage in original.STAGES:
            with self.subTest(fault_stage=fault_stage), tempfile.TemporaryDirectory() as folder:
                self.out = Path(folder) / "experiment"
                self.setup_store()
                self.begin(1, tag="COMMITTED_NOTES", color="white")
                self.finish(self.engine, 1)
                committed = self.engine.complete_round_checkpoint()
                checkpoint_raw = self.store._commit_path(1).read_bytes()
                self.begin(2, tag="FAILED_ATTEMPT_NOTES", color="red")
                self.advance_to(fault_stage)
                if fault_stage == "REVIEWER":
                    job = self.engine.export_reviewer_packet()
                    original.save_once(Path(self.store.working_root()) / "reviewer_host/r02/first_answer.json",
                                       {"fixture_only": True, "feedback": "FAILED_ACTUAL_FIRST_TEXT"})
                else:
                    class FaultBackend:
                        def run_actor(self, request):
                            raise TimeoutError("Offline fixture: no live request")
                    self.engine.backend = FaultBackend()
                    with self.assertRaises(TimeoutError):
                        self.engine.run_next_actor()
                archived = self.store.abandon_attempt({**TECHNICAL, "stage": fault_stage})
                self.assertEqual(self.store.committed_rounds(), 1)
                self.assertEqual(self.store._commit_path(1).read_bytes(), checkpoint_raw)
                self.assertEqual(archived["attempt_id"], "r02.a01")
                self.assertTrue(archived["paid_costs_and_unknown_reserves_remain_counted"])
                self.begin(2, tag="RETRY_NOTES", color="blue")
                request = self.engine.actor_request()
                text = original.canonical(request)
                self.assertNotIn("FAILED_ATTEMPT_NOTES", text)
                self.assertNotIn("FAILED_ATTEMPT_REVIEW", text)
                self.assertNotIn("FAILED_ACTUAL_FIRST_TEXT", text)
                self.assertIn("COMMITTED_NOTES", text)
                self.assertEqual(request["image_refs"]["previous_C"]["sha256"],
                                 self.engine.image_for(1, "C")["sha256"])
                self.assertEqual(self.engine.summary()["committed_terminal_stages"], 8)
                self.assertEqual(self.engine.summary()["working_terminal_stages"], 0)
                self.advance_to("REVIEWER")
                packet = original.read_json(self.engine.export_reviewer_packet()["packet_path"])
                self.assertNotIn("FAILED_ATTEMPT_REVIEW", original.canonical(packet))
                self.assertEqual(len(packet["history"]), 1)
                self.finish(self.engine, 2)
                self.engine.complete_round_checkpoint()
                self.begin(3)
                self.assertNotIn("FAILED_ATTEMPT_NOTES", original.canonical(self.engine.actor_request()))
                self.assertEqual(self.store.committed_selector(1), committed)

    def test_legacy_round_seven_attempt_one_is_preserved_and_counts_toward_limit(self):
        old = self.setup_store(legacy_rounds=6)
        original_request = old.actor_request()

        class FaultBackend:
            def run_actor(self, request):
                raise TimeoutError("Offline pre-amendment loss")
        old.backend = FaultBackend()
        with self.assertRaises(TimeoutError):
            old.run_next_actor()
        preserved = {path: path.read_bytes() for path in (self.out / "episode/rounds/r07").glob("*")}
        aborted = self.store.register_legacy_abort(TECHNICAL)
        self.assertEqual(aborted["attempt_id"], "r07.a01")
        self.assertEqual(self.store.register_legacy_abort(TECHNICAL), aborted)
        context = self.begin(7)
        self.assertEqual(context["attempt_id"], "r07.a02")
        self.assertEqual(self.engine.actor_request(), original_request)
        self.store.abandon_attempt(TECHNICAL)
        self.assertEqual(self.begin(7)["attempt_id"], "r07.a03")
        self.store.abandon_attempt(TECHNICAL)
        with self.assertRaises(AttemptLimitReached):
            self.store.begin_attempt(7)
        self.assertEqual(self.store.committed_rounds(), 6)
        self.assertEqual(len(self.store.inventory()), 3)
        for path, raw in preserved.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_complete_eight_stages_recover_before_and_after_publication_without_calls(self):
        for after_publish in (False, True):
            with self.subTest(after_publish=after_publish), tempfile.TemporaryDirectory() as folder:
                self.out = Path(folder) / "experiment"
                self.setup_store()
                self.begin(1)
                self.finish(self.engine, 1)
                hashes = [self.engine.outcome(1, stage)["record_hash"] for stage in original.STAGES]
                calls = len(self.backend.requests)
                saved = attempts._save_durable

                def fail_publication(path, value):
                    if Path(path) == self.store._commit_path(1):
                        if after_publish:
                            saved(path, value)
                        raise OSError("Offline crash at publication boundary")
                    return saved(path, value)

                with patch.object(attempts, "_save_durable", side_effect=fail_publication):
                    with self.assertRaises(OSError):
                        self.engine.complete_round_checkpoint()
                self.assertEqual(self.store.committed_rounds(), 1 if after_publish else 0)
                rebuilt = Engine(self.out, self.config, self.plan, backend=self.backend, store=self.store)
                selector = rebuilt.complete_round_checkpoint()
                self.assertEqual(selector["round"], 1)
                self.assertEqual(len(self.backend.requests), calls)
                self.assertEqual([rebuilt.outcome(1, stage)["record_hash"] for stage in original.STAGES], hashes)
                self.assertEqual(rebuilt.complete_round_checkpoint(), selector)
                self.assertEqual(len(list((self.store.root / "commits").glob("*.json"))), 1)

    def test_incomplete_attempt_cannot_publish_and_complete_attempt_cannot_be_resampled(self):
        self.setup_store()
        self.begin(1)
        self.engine.run_next_actor()
        with self.assertRaises(AttemptIntegrityError):
            self.engine.complete_round_checkpoint()
        self.assertEqual(self.store.committed_rounds(), 0)
        self.finish(self.engine, 1)
        with self.assertRaises(AttemptIntegrityError):
            self.store.abandon_attempt(TECHNICAL)
        self.assertTrue(self.engine.summary()["awaiting_round_commit"])
        with self.assertRaises(AttemptIntegrityError):
            self.engine.run_next_actor()
        self.engine.complete_round_checkpoint()
        self.assertEqual(self.store.committed_rounds(), 1)

    def test_corrupt_archive_or_checkpoint_fails_closed(self):
        self.setup_store()
        self.begin(1)
        self.engine.run_next_actor()
        path = self.engine._path(1, "A_IMAGE_AND_VOTE")
        self.store.abandon_attempt(TECHNICAL)
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(AttemptIntegrityError):
            self.store.begin_attempt(1)

    def test_behavioral_reason_cannot_authorize_restart(self):
        self.setup_store()
        self.begin(1)
        for fault in ({"fault_class": "agents_not_colluding"}, {"technical": False, "fault_class": "bad_choice"}):
            with self.assertRaises(AttemptIntegrityError):
                self.store.abandon_attempt(fault)
        self.assertEqual(self.store.current()["attempt_id"], "r01.a01")


if __name__ == "__main__":
    unittest.main()
