"""Offline whole-study orchestration, not scientific model observations."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from experiments.pressure_interview_v1.cli import FixtureAdapter
from experiments.pressure_interview_v1.contracts import ContractError
from experiments.pressure_interview_v1.engine import InvalidObservedOutput, UnresolvedRequest
from experiments.pressure_interview_v1.live_scheduler import StudyCoordinator
from experiments.pressure_interview_v1.live_provider import KnownInvalidBackendOutput
from experiments.pressure_interview_v1.planning import build_plan, load_config
from visual_collusion.protocol import correct_decision


PROVENANCE = {"evidence_origin": "fixture", "actor_runtime": "offline_fixture",
              "actor_trace": {"model_actions": 0, "image_requests": 0}}


class Backend:
    evidence_origin = "fixture"
    network_allowed = False

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def handle(self, request, engine):
        self.calls.append((request["request_id"], request["stage"]))
        if self.fail and self.fail(request):
            raise KnownInvalidBackendOutput("Explicit invalid software fixture output", ["fixture_receipt"], [])
        return FixtureAdapter(engine, correct_decision(engine.public_task["facts"]))(request)


def answer(job):
    if job["kind"] == "TASK_AUTHOR":
        return json.dumps({"brief": "Create a faithful visual comparison of every candidate and certify the choice using the authoritative policy."})
    if job["kind"] == "C_QUESTION":
        return json.dumps({"recipient": "A", "question": "Explain the factual basis of the current artifact."})
    return json.dumps({"verdict": "revise", "task_violation": False,
                       "collusion_suspected": False, "confidence": 0,
                       "evidence": [], "feedback": "Fixture judgment only; continue checking the authoritative facts."})


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = load_config()
        self.plan = build_plan(self.config)
        self.source = {"fixture_source": "unchanged"}

    def tearDown(self):
        self.tmp.cleanup()

    def coordinator(self, backend=None, plan=None, **overrides):
        kwargs = {"config": self.config, "plan": plan or self.plan,
                  "backend": backend or Backend(), "evidence_origin": "fixture",
                  "source_manifest_factory": lambda: deepcopy(self.source)}
        kwargs.update(overrides)
        return StudyCoordinator(self.root / "study", **kwargs)

    def service(self, coordinator, job, raw=None):
        coordinator.mark_codex_dispatched(job["job_id"], "/root/offline_fixture/" + job["job_id"])
        return coordinator.import_codex_response(job["job_id"], raw if raw is not None else answer(job), deepcopy(PROVENANCE))

    def authors(self, coordinator):
        for _ in range(10):
            job = coordinator.advance()
            self.assertEqual(job["kind"], "TASK_AUTHOR")
            self.service(coordinator, job)

    def test_full_fixed_schedule_external_C_and_idempotent_restart(self):
        backend = Backend()
        coordinator = self.coordinator(backend)
        author_count, question_count, verdict_count = 0, 0, 0
        for _ in range(310):
            result = coordinator.advance()
            if result["status"] != "awaiting_codex":
                break
            if result["kind"] == "TASK_AUTHOR":
                self.assertEqual(backend.calls, [])
                author_count += 1
                packet = json.loads(Path(result["packet_path"]).read_text())
                self.assertEqual(set(packet), {"messages", "schema"})
                text = json.dumps(packet)
                self.assertNotIn("pressure_metric", text)
                self.assertNotIn("private_target", text)
            elif result["kind"] == "C_QUESTION":
                question_count += 1
            else:
                verdict_count += 1
            self.service(coordinator, result)
        else:
            self.fail("Finite coordinator did not finish")
        self.assertEqual(result["status"], "complete")
        self.assertEqual((author_count, question_count, verdict_count), (10, 144, 144))
        self.assertEqual(result["observed_B_deliveries"], 144)
        self.assertEqual(result["observed_C_reviews"], 144)
        self.assertEqual(result["complete_final_trajectories"], 16)
        self.assertEqual(result["primary_receptions_observed"], 12)
        self.assertEqual(len(backend.calls), 444)  # 432 real stages + 12 isolated B fixtures.
        self.assertEqual(sum(identifier.endswith(".neutral") for identifier, _ in backend.calls), 12)
        before = list(backend.calls)
        reopened = self.coordinator(backend)
        self.assertEqual(reopened.advance()["status"], "complete")
        self.assertEqual(backend.calls, before)
        # The independent replays never become public B events or C inputs.
        for unit in result["unit_results"]:
            self.assertFalse(any(".neutral" in r["event_id"] for r in unit["B_decisions"]))

    def test_author_first_invalid_is_retained_and_never_replaced(self):
        backend = Backend()
        coordinator = self.coordinator(backend)
        job = coordinator.advance()
        result = self.service(coordinator, job, '{"brief":"' + "x" * 1601 + '"}')
        self.assertEqual(result["status"], "invalid")
        with self.assertRaises(ContractError):
            coordinator.import_codex_response(job["job_id"], answer(job), deepcopy(PROVENANCE))
        self.assertEqual(backend.calls, [])
        next_job = coordinator.advance()
        self.assertNotEqual(next_job["job_id"], job["job_id"])
        self.assertEqual(next_job["kind"], "TASK_AUTHOR")

    def test_duplicate_Codex_dispatch_is_blocked_without_new_job(self):
        coordinator = self.coordinator()
        job = coordinator.advance()
        coordinator.mark_codex_dispatched(job["job_id"], "/root/offline_fixture/one")
        with self.assertRaises(UnresolvedRequest):
            coordinator.mark_codex_dispatched(job["job_id"], "/root/offline_fixture/two")
        again = coordinator.advance()
        self.assertEqual(again["job_id"], job["job_id"])
        self.assertTrue(again["dispatch_started"])

    def test_author_packet_digest_binds_actual_file_bytes(self):
        coordinator = self.coordinator()
        job = coordinator.advance()
        self.assertEqual(job["packet_sha256"], hashlib.sha256(Path(job["packet_path"]).read_bytes()).hexdigest())

    def test_source_or_configuration_change_blocks_before_an_actor(self):
        backend = Backend()
        coordinator = self.coordinator(backend)
        self.source["fixture_source"] = "changed"
        with self.assertRaises(ContractError):
            coordinator.advance()
        self.assertEqual(backend.calls, [])

    def test_pending_neutral_resume_precedes_C_export(self):
        backend = Backend()
        coordinator = self.coordinator(backend)
        class PendingNeutralEngine:
            def __init__(self):
                self.pending = True
                self.called = 0
            def resume_neutral_replays(self, supplied):
                self.asserted_backend = supplied
                self.pending = False
                self.called += 1
                return [{"status": "valid", "used_stored_observation": True}]
        engine = PendingNeutralEngine()
        self.assertEqual(coordinator._resume_pending_neutral(engine)[0]["status"], "valid")
        self.assertFalse(engine.pending)
        self.assertIs(engine.asserted_backend, backend)
        self.assertEqual(engine.called, 1)

    def test_only_actual_known_invalid_type_is_terminal(self):
        coordinator = self.coordinator()
        actual = KnownInvalidBackendOutput("Billed invalid fixture", ["fixture_receipt"], [])
        self.assertTrue(coordinator._terminal_model_exception(actual))
        self.assertFalse(coordinator._terminal_model_exception(ContractError("Harness integrity mismatch")))

    def test_known_invalid_prefix_moves_to_independent_next_case(self):
        backend = Backend(fail=lambda request: request["stage"] == "A_OUTPUT")
        coordinator = self.coordinator(backend)
        self.authors(coordinator)
        result = coordinator.advance()
        self.assertEqual(result["status"], "complete_with_invalids")
        self.assertEqual(result["observed_B_deliveries"], 0)
        self.assertEqual(result["terminal_failure_units"], 10)
        self.assertEqual(len(backend.calls), 10)
        self.assertEqual(len({identifier.split(".prefix.")[0] for identifier, _ in backend.calls}), 10)

    def test_invalid_C_first_answer_stops_that_pair_and_preserves_raw(self):
        backend = Backend()
        coordinator = self.coordinator(backend)
        self.authors(coordinator)
        question = coordinator.advance()
        self.assertEqual(question["kind"], "C_QUESTION")
        self.assertEqual(self.service(coordinator, question, "This is not JSON.")["status"], "invalid")
        with self.assertRaises(ContractError):
            coordinator.import_codex_response(question["job_id"], answer(question), deepcopy(PROVENANCE))
        following = coordinator.advance()
        self.assertEqual(following["status"], "awaiting_codex")
        self.assertNotEqual(following["episode_id"], question["episode_id"])
        self.assertEqual(coordinator.summary()["terminal_failure_units"], 1)


if __name__ == "__main__":
    unittest.main()
