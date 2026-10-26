"""Actual bridge plus coordinator rollback fixtures; never live findings."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from experiments.triad_emergent_v9 import engine as original
from experiments.triad_emergent_v9.coordinator import canonical, read_json
from experiments.triad_emergent_v9.tests.test_engine import reviewer_answer
from experiments.triad_v9_checkpoint_restart04 import coordinator as module
from experiments.triad_v9_checkpoint_restart04 import reviewer_host
from experiments.triad_v9_checkpoint_restart04.faults import TechnicalRoundFault, PermanentTechnicalFault
from .test_attempts import TransactionFixture, TaggedBackend


class FixtureHost(module.Coordinator):
    def __init__(self, fixture):
        super().__init__(fixture.out)
        self.fixture = fixture
        self.backends = []
        self.failures = {}

    def verify_freeze(self):
        return {"source_files": {}, "fixture_only": True}

    def _store(self):
        return self.fixture.store

    def _backend(self):
        context = self._store().current()
        if self.backend is None:
            host = self

            class FaultBackend(TaggedBackend):
                def preflight(self):
                    return {"fixture_only": True}

                def run_actor(self, request):
                    fault = host.failures.pop((context["attempt"], request["stage"]), None)
                    if fault is not None:
                        raise fault
                    return super().run_actor(request)

            self.backend = FaultBackend(Path(context["working_root"]), self.fixture.plan,
                                        tag=context["attempt_id"], color="blue")
            self.backend.attempt_context = deepcopy(context)
            self.backends.append(self.backend)
        return self.backend


class CoordinatorTests(TransactionFixture):
    def setUp(self):
        TransactionFixture.setUp(self)
        self.setup_store(legacy_rounds=6)
        (self.out / "plan.json").write_text(canonical(self.plan))
        (self.out / "config.json").write_text(canonical(self.config))
        self.host = FixtureHost(self)
        self.seals = patch("experiments.triad_v9_checkpoint_restart04.funding.seal_abandoned_attempt", return_value={"fixture_only": True})
        self.seal_mock = self.seals.start()
        self.free = patch("experiments.triad_v9_checkpoint_restart04.backend.Backend.preflight_before_attempt",
                          return_value={"fixture_only": True})
        self.free_mock = self.free.start()

    def tearDown(self):
        self.free.stop()
        self.seals.stop()
        TransactionFixture.tearDown(self)

    def actual_review(self, job, *, raw=None, agent=None):
        agent = agent or f"/root/offline_review_{self.store.current()['attempt_id'].replace('.', '_')}"
        info = reviewer_host.prepare(self.out, agent)
        self.host.dispatch(job["job_id"], agent)
        raw = canonical(reviewer_answer(read_json(job["packet_path"]))) if raw is None else raw
        Path(info["authored_output_path"]).write_text(raw)
        capture = self.out.parent / f"capture_{self.store.current()['attempt_id']}.txt"
        capture.write_text(raw)
        result = reviewer_host.envelope(self.out, agent, capture)
        return read_json(result["envelope_path"])

    def test_actual_first_reviewer_job_flows_into_committed_round_without_provenance_rewrite(self):
        job = self.host.advance(7)
        response = self.actual_review(job)
        provenance = deepcopy(response["provenance"])
        self.host.ingest(response)
        result = self.host.advance(7)
        self.assertEqual(result["status"], "round_boundary")
        self.assertEqual(self.store.committed_rounds(), 7)
        review = self.host._engine().outcome(7, "REVIEWER")
        self.assertEqual(review["provenance"], provenance)
        self.assertEqual(review["reviewer_job_id"], job["job_id"])
        self.assertEqual(review["attempt_id"], "r07.a01")
        self.assertEqual(reviewer_host.validate_archived_envelope(self.out, 7)["status"], "passed")
        self.assertEqual(self.seal_mock.call_count, 0)

    def test_existing_incomplete_dispatch_archives_whole_round_and_restarts_qualified_attempt(self):
        self.store.begin_attempt(7)
        engine = self.host._engine()
        request = engine.actor_request()
        original.save_once(engine._path(7, "A_IMAGE_AND_VOTE", "request"), request)
        original.save_once(engine._path(7, "A_IMAGE_AND_VOTE", "dispatch"), {"fixture_crash": True})
        before = self.store._manifest()["legacy_chain_head"]
        job = self.host.advance(7)
        self.assertEqual(job["job_id"], "triad_emergent_v9.r07.a02.REVIEWER")
        inventory = self.store.inventory()
        self.assertEqual([row["status"] for row in inventory], ["abandoned_technical", "active"])
        self.assertEqual(inventory[0]["fault"]["fault_class"], "incomplete_dispatched_stage")
        self.assertEqual(self.store._manifest()["legacy_chain_head"], before)
        self.assertEqual(self.store.committed_rounds(), 6)
        self.assertEqual(self.seal_mock.call_count, 1)

    def test_typed_actor_failure_restarts_from_six_not_from_failed_working_stage(self):
        self.host.failures[(1, "B_IMAGE_AND_VOTE")] = TechnicalRoundFault("offline_transport_failure")
        job = self.host.advance(7)
        self.assertEqual(job["job_id"], "triad_emergent_v9.r07.a02.REVIEWER")
        first = read_json(self.out / "attempts/r07/a01/abandonment.json")
        self.assertIn("episode/rounds/r07/A_IMAGE_AND_VOTE.outcome.json", " ".join(first["evidence_files"]))
        retry_A = self.host.backends[-1].requests[0]
        self.assertEqual(retry_A["stage"], "A_IMAGE_AND_VOTE")
        self.assertNotIn("r07.a01", canonical(retry_A["messages"]))
        self.assertEqual(self.store.committed_rounds(), 6)

    def test_postreview_failure_archives_actual_first_answer_and_excludes_it_from_retry(self):
        job = self.host.advance(7)
        response = self.actual_review(job)
        self.host.ingest(response)
        old_root = Path(self.store.current()["working_root"])
        original_final = (old_root / "reviewer_host/r07/first_final.txt").read_bytes()
        self.host.failures[(1, "POSTVERDICT_B_LOG")] = TechnicalRoundFault("offline_after_review_failure")
        retry = self.host.advance(7)
        self.assertEqual(retry["job_id"], "triad_emergent_v9.r07.a02.REVIEWER")
        self.assertEqual((old_root / "reviewer_host/r07/first_final.txt").read_bytes(), original_final)
        packet = read_json(retry["packet_path"])
        self.assertEqual([item["round"] for item in packet["history"]], list(range(1, 7)))
        with self.assertRaises(ValueError):
            self.host.ingest(response)

    def test_invalid_actual_first_review_remains_terminal_without_technical_restart(self):
        job = self.host.advance(7)
        response = self.actual_review(job, raw="not JSON; actual first fixture answer")
        result = self.host.ingest(response)
        self.assertEqual(result["result"]["status"], "model_invalid")
        self.host.advance(7)
        self.assertEqual(self.store.committed_rounds(), 7)
        self.assertEqual(len(self.store.inventory()), 1)
        self.assertEqual(self.seal_mock.call_count, 0)

    def test_repeated_eligible_faults_stop_at_three_attempts(self):
        for attempt in range(1, 4):
            self.host.failures[(attempt, "A_IMAGE_AND_VOTE")] = TechnicalRoundFault("offline_repeat_failure")
        result = self.host.advance(7)
        self.assertEqual(result["status"], "technical_hold")
        self.assertEqual(self.store.committed_rounds(), 6)
        self.assertEqual(len(self.store.inventory()), 3)
        self.assertEqual(self.seal_mock.call_count, 3)

    def test_permanent_auth_or_schema_failure_holds_without_spending_another_attempt(self):
        self.host.failures[(1, "A_IMAGE_AND_VOTE")] = PermanentTechnicalFault("offline_auth_failure")
        result = self.host.advance(7)
        self.assertEqual(result["status"], "technical_hold")
        self.assertEqual(len(self.store.inventory()), 1)
        self.assertEqual(self.store.inventory()[0]["status"], "active")
        self.assertEqual(self.seal_mock.call_count, 0)

    def test_free_preflight_failure_consumes_no_attempt(self):
        self.free_mock.side_effect = TechnicalRoundFault("offline_metadata_outage")
        result = self.host.advance(7)
        self.assertEqual(result["status"], "technical_hold")
        self.assertEqual(self.store.inventory(), [])
        self.assertEqual(self.seal_mock.call_count, 0)

    def test_complete_saved_round_is_published_on_resume_with_zero_new_actor_calls(self):
        job = self.host.advance(7)
        self.host.ingest(self.actual_review(job))
        engine = self.host._engine()
        engine.backend = self.host._backend()
        while engine.next_stage()["round"] == 7:
            engine.run_next_actor()
        count = sum(len(backend.requests) for backend in self.host.backends)
        result = self.host.advance(7)
        self.assertEqual(result["status"], "round_boundary")
        self.assertEqual(self.store.committed_rounds(), 7)
        self.assertEqual(sum(len(backend.requests) for backend in self.host.backends), count)
        self.assertEqual(self.seal_mock.call_count, 0)

    def test_source_integrity_failure_prevents_attempt_creation(self):
        with patch.object(self.host, "verify_freeze", side_effect=ValueError("offline source drift")):
            with self.assertRaises(ValueError):
                self.host.advance(7)
        self.assertEqual(self.store.inventory(), [])
        self.free_mock.assert_not_called()

    def test_process_loss_after_published_stage_without_dangling_dispatch_restarts_round(self):
        self.store.begin_attempt(7)
        self.host._begin_operation("advance")
        engine = self.host._engine()
        engine.backend = self.host._backend()
        engine.run_next_actor()
        self.assertIsNotNone(engine.outcome(7, "A_IMAGE_AND_VOTE"))
        self.assertFalse(engine._path(7, "B_IMAGE_AND_VOTE", "dispatch").exists())
        job = self.host.advance(7)
        self.assertEqual(job["job_id"], "triad_emergent_v9.r07.a02.REVIEWER")
        self.assertEqual(self.store.inventory()[0]["fault"]["fault_class"], "interrupted_producer_operation")

    def test_marker_before_funding_seal_interruption_reseals_exact_prefix_before_restart(self):
        self.store.begin_attempt(7)
        self.seal_mock.side_effect = [OSError("offline seal interruption"), {"fixture_only": True}]
        with self.assertRaises(OSError):
            self.host._archive_fault({"technical": True, "fault_class": "offline_marker_seal_fault"})
        self.assertIsNone(self.store.current())
        job = self.host.advance(7)
        self.assertEqual(job["job_id"], "triad_emergent_v9.r07.a02.REVIEWER")
        self.assertEqual(self.seal_mock.call_count, 2)

    def test_operation_journal_hash_mismatch_holds_before_any_more_actor_calls(self):
        self.host.advance(7)
        path = next((self.out / "checkpoints_restart04/operations").glob("*.finished.json"))
        path.write_text('{"operation_file_sha256":"wrong","status":"awaiting_codex"}')
        before = sum(len(backend.requests) for backend in self.host.backends)
        with self.assertRaises(ValueError):
            self.host.advance(7)
        self.assertEqual(sum(len(backend.requests) for backend in self.host.backends), before)
        self.assertEqual(len(self.store.inventory()), 1)

    def test_reviewer_packet_storage_fault_restarts_whole_round(self):
        from experiments.triad_v9_checkpoint_restart04.engine import Engine
        export = Engine.export_reviewer_packet
        count = [0]

        def fail_once(engine):
            count[0] += 1
            if count[0] == 1:
                raise OSError("offline packet write interruption")
            return export(engine)

        with patch.object(Engine, "export_reviewer_packet", fail_once):
            job = self.host.advance(7)
        self.assertEqual(job["job_id"], "triad_emergent_v9.r07.a02.REVIEWER")
        self.assertEqual(self.store.inventory()[0]["fault"]["phase"], "reviewer_packet_export")

    def test_reviewer_outcome_publication_fault_preserves_first_answer_and_restarts_round(self):
        from experiments.triad_v9_checkpoint_restart04.engine import Engine
        job = self.host.advance(7)
        response = self.actual_review(job)
        ingest = Engine.ingest_review

        def publish_then_fail(engine, *args, **kwargs):
            ingest(engine, *args, **kwargs)
            raise OSError("offline after actual review publication")

        with patch.object(Engine, "ingest_review", publish_then_fail):
            result = self.host.ingest(response)
        self.assertEqual(result["status"], "technical_hold")
        self.assertEqual(self.store.inventory()[0]["status"], "abandoned_technical")
        self.assertTrue((self.out / "attempts/r07/a01/reviewer_host/r07/first_final.txt").is_file())
        self.assertEqual(self.host.advance(7)["job_id"], "triad_emergent_v9.r07.a02.REVIEWER")
