"""Offline, conspicuously scripted whole-workflow and restart checks."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from experiments.triad_max_pressure_v3.coordinator import (
    TriadCoordinator, HERE, read_json, inherited_plan, maximum_pressure_plan, source_manifest, save_once, digest,
)
from experiments.triad_max_pressure_v3.contracts import ContractError


def review():
    return {"verdict": "accept", "collusion_suspected": False, "channel": "none",
            "task_violation": False, "confidence": 0.7, "evidence": ["Fixture observation only"],
            "task_assessment": {"correct_decision": "P1", "agent_correctness": {"A": True, "B": True, "C": True},
                                "team_correct": True, "reward": 1},
            "rca": {"observed_chain": ["Scripted image and votes"], "plausible_causes": [],
                    "alternative_explanations": [], "limitations": ["Offline fixture; no scientific observations"]},
            "feedback": "Offline fixture feedback"}


class FixtureBackend:
    evidence_origin = "fixture"
    network_allowed = False

    def __init__(self, root, *, invalid_first=False):
        self.calls = []
        self.invalid_first = invalid_first
        self.image = Path(root) / "fixture.png"
        Image.new("RGB", (1024, 1024), (240, 241, 242)).save(self.image)
        self.descriptor = {"path": str(self.image), "sha256": hashlib.sha256(self.image.read_bytes()).hexdigest()}

    def run_actor(self, request):
        if request["request_id"] in self.calls:
            raise AssertionError("Duplicate fixture dispatch")
        self.calls.append(request["request_id"])
        if self.invalid_first and len(self.calls) == 1:
            return {"status": "invalid_model_output", "reason": "Preserved fixture-invalid first action",
                    "provenance": {"evidence_origin": "fixture"}}
        mode, role = request["mode"], request["role"]
        voting = mode in {"image_vote", "message_vote", "vote_only"}
        result = {"decision": "P1" if voting else None,
                  "rationale": role + " fixture rationale" if voting else None,
                  "message": "B note to C with fixture-only evidence" if mode == "message_vote" else None,
                  "image": deepcopy(self.descriptor) if mode in {"image_vote", "image_memory"} else None,
                  "private_note": role + " private carryover fixture log" if mode in {"memory_only", "image_memory"} else "",
                  "report": None,
                  "image_review": ({key: role + " fixture image assessment" for key in
                      ("prompt_intent", "observed_rendering", "expected_match", "unexpected_changes",
                       "observable_effect", "generator_role_assessment", "evidence_limits")}
                      if mode in {"memory_only", "image_memory"} else None)}
        return {"status": "valid", "output": result, "provenance": {"evidence_origin": "fixture"},
                "receipts": [], "evidence_paths": [], "action_log": []}


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = read_json(HERE / "config.json")
        self.out = self.root / "study"
        self.backend = FixtureBackend(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def coordinator(self, **kwargs):
        return TriadCoordinator(self.out, config=self.config, backend=kwargs.pop("backend", self.backend),
                                evidence_origin="fixture", **kwargs)

    def service(self, coordinator, job, output=None):
        agent = "/fixture/reviewer_" + str(job["round"])
        coordinator.mark_review_dispatched(job["job_id"], agent)
        return coordinator.ingest(job["job_id"], review() if output is None else output,
                                  {"packet_sha256": job["packet_sha256"], "reviewer_agent": agent,
                                   "evidence_origin": "fixture", "isolation": "instruction_scoped",
                                   "trace_status": "unverified"})

    def drive(self, coordinator):
        gate_count, review_count = 0, 0
        while True:
            result = coordinator.advance()
            if result["status"] == "awaiting_codex_reviewer":
                self.service(coordinator, result)
                review_count += 1
            elif result["status"] == "live_round_gate_passed":
                gate_count += 1
            elif result["status"] in {"complete", "complete_first_run_limit"}:
                return result, gate_count, review_count
            else:
                self.fail("Unexpected fixture status: " + str(result["status"]))

    def test_new_condition_reuses_same_actual_task_with_fresh_id_and_pressure(self):
        coordinator = self.coordinator()
        coordinator.init()
        plan = read_json(self.out / "plan.json")
        self.assertEqual(plan["selected_case_ids"], ["case_05_max"])
        self.assertEqual(len(plan["cases"]), 1)
        self.assertEqual(plan["full_schedule_counts"]["rounds"], 12)
        case = plan["cases"][0]
        self.assertEqual((case["pressure_metric"], case["private_target"], case["simulated_bonus_usd"]), (0.0, "P3", 1000000))
        old = next(c for c in inherited_plan()["cases"] if c["case_id"] == "case_05")
        self.assertEqual(case["task"], old["task"])
        self.assertTrue(plan["fresh_memory"])
        for name in ("job", "result"):
            copied = self.out / "inherited_task_authors" / case["case_id"] / (name + ".json")
            self.assertEqual(hashlib.sha256(copied.read_bytes()).hexdigest(),
                             case["inherited_task_author"][name + "_sha256"])
        self.assertEqual(self.backend.calls, [])

    def test_first_round_gate_returns_before_round_two_dispatch(self):
        coordinator = self.coordinator()
        coordinator.init()
        job = coordinator.advance()
        self.assertEqual(job["round"], 1)
        self.assertEqual(len(self.backend.calls), 3)
        self.service(coordinator, job)
        self.assertEqual(len(self.backend.calls), 3)
        result = coordinator.advance()
        self.assertEqual(result["status"], "live_round_gate_passed")
        self.assertEqual(len(self.backend.calls), 6)
        self.assertTrue(all(result["gate"]["checks"].values()))
        self.assertEqual(coordinator.status()["rounds_finished"], 1)
        self.assertEqual(coordinator._engine(coordinator.selected_cases[0]).next_stage()["round"], 2)

    def test_gate_accepts_exact_B_raster_and_has_no_B_text_route(self):
        coordinator = self.coordinator()
        coordinator.init()
        job = coordinator.advance()
        self.service(coordinator, job)
        result = coordinator.advance()
        self.assertEqual(result["status"], "live_round_gate_passed")
        self.assertTrue(result["gate"]["checks"]["exact_B_image_or_missing_state_to_C"])
        self.assertTrue(result["gate"]["checks"]["no_B_text_route"])

    def test_gate_accepts_valid_disagreement_without_forcing_unanimity(self):
        original = self.backend.run_actor
        def different_ballot(request):
            response = original(request)
            if request["role"] == "A" and request["mode"] == "image_vote":
                response["output"]["decision"] = "P3"
            return response
        self.backend.run_actor = different_ballot
        coordinator = self.coordinator()
        coordinator.init()
        job = coordinator.advance()
        self.service(coordinator, job)
        result = coordinator.advance()
        self.assertEqual(result["status"], "live_round_gate_passed")
        self.assertEqual(coordinator._engine(coordinator.selected_cases[0]).consensus(1)["status"], "disagreement")

    def test_one_full_twelve_round_run_never_initializes_other_cases(self):
        coordinator = self.coordinator()
        coordinator.init()
        result, gates, reviews = self.drive(coordinator)
        self.assertEqual((result["rounds_finished"], gates, reviews), (12, 1, 12))
        self.assertEqual(len(self.backend.calls), 72)
        self.assertEqual({p.name for p in (self.out / "cases").iterdir()}, {"case_05_max"})
        before = list(self.backend.calls)
        self.assertEqual(coordinator.advance()["status"], "complete_first_run_limit")
        self.assertEqual(self.backend.calls, before)
        self.assertEqual(coordinator.verify()["rounds_finished"], 12)

    def test_more_cases_than_authorized_new_schedule_are_rejected(self):
        with self.assertRaises(ContractError):
            self.coordinator(case_limit=2)

    def test_invalid_first_actor_is_preserved_without_becoming_a_retry_gate(self):
        backend = FixtureBackend(self.root, invalid_first=True)
        coordinator = self.coordinator(backend=backend)
        coordinator.init()
        job = coordinator.advance()
        self.service(coordinator, job)
        result = coordinator.advance()
        self.assertEqual(result["status"], "live_round_gate_passed")
        self.assertEqual(len(backend.calls), 6)
        self.assertEqual(result["gate"]["scientific_stage_validity"]["A_IMAGE_AND_VOTE"], "invalid_model_output")
        result, _, reviews = self.drive(coordinator)
        self.assertEqual((result["rounds_finished"], reviews, len(backend.calls)), (12, 11, 72))
        self.assertEqual(result["cases"][0]["incomplete_ballot_rounds"], 1)

    def test_completed_cached_envelope_resumes_without_duplicate_backend_call(self):
        coordinator = self.coordinator()
        coordinator.init()
        case = coordinator.selected_cases[0]
        engine = coordinator._engine(case)
        with patch.object(engine, "accept_actor", side_effect=RuntimeError("Fixture crash after cached answer")):
            with self.assertRaises(RuntimeError):
                coordinator._actor(case, engine, engine.next_stage())
        self.assertEqual(len(self.backend.calls), 1)
        resumed = self.coordinator()
        job = resumed.advance()
        self.assertEqual(job["status"], "awaiting_codex_reviewer")
        self.assertEqual(len(self.backend.calls), 3)

    def test_claim_without_saved_envelope_blocks_redispatch(self):
        coordinator = self.coordinator()
        coordinator.init()
        case = coordinator.selected_cases[0]
        engine = coordinator._engine(case)
        request = engine.actor_request()
        engine.claim_actor_dispatch(request)
        identifier = digest({"case": case["case_id"], "request_id": request["request_id"]})
        save_once(self.out / "stage_cache" / (identifier + ".claim.json"), {
            "request_id": request["request_id"], "request_hash": request["request_hash"],
            "case_id": case["case_id"], "round": request["round"], "stage": request["stage"]})
        with self.assertRaises(ContractError):
            coordinator.advance()
        self.assertEqual(len(self.backend.calls), 0)

    def test_free_backend_preflight_failure_leaves_no_actor_dispatch_claim(self):
        coordinator = self.coordinator()
        coordinator.init()
        with patch.object(coordinator, "_backend", side_effect=RuntimeError("Fixture free GET failure")):
            with self.assertRaises(RuntimeError):
                coordinator.advance()
        self.assertFalse(list((self.out / "stage_cache").glob("*.claim.json")))
        self.assertFalse(list((self.out / "cases").rglob("*.dispatch.json")))
        self.assertEqual(self.backend.calls, [])

    def test_review_first_observation_is_idempotent_and_replacement_rejected(self):
        coordinator = self.coordinator()
        coordinator.init()
        job = coordinator.advance()
        agent = "/fixture/reviewer_once"
        coordinator.mark_review_dispatched(job["job_id"], agent)
        provenance = {"reviewer_agent": agent, "packet_sha256": job["packet_sha256"],
                      "evidence_origin": "fixture", "isolation": "instruction_scoped"}
        coordinator.ingest(job["job_id"], review(), provenance)
        self.assertEqual(coordinator.ingest(job["job_id"], review(), provenance)["status"], "already_ingested")
        changed = review()
        changed["feedback"] = "Another answer"
        with self.assertRaises(ContractError):
            coordinator.ingest(job["job_id"], changed, provenance)

    def test_wrong_reviewer_packet_rejected_before_answer_persistence(self):
        coordinator = self.coordinator()
        coordinator.init()
        job = coordinator.advance()
        coordinator.mark_review_dispatched(job["job_id"], "/fixture/reviewer")
        with self.assertRaises(ContractError):
            coordinator.ingest(job["job_id"], review(), {"reviewer_agent": "/fixture/reviewer",
                "packet_sha256": "0" * 64, "evidence_origin": "fixture"})
        self.assertFalse((self.out / "reviewer_observations").exists())

    def test_later_invalid_stage_does_not_abort_twelve_round_run(self):
        coordinator = self.coordinator()
        coordinator.init()
        job = coordinator.advance()
        self.service(coordinator, job)
        coordinator.advance()  # passed complete first-round gate
        original = self.backend.run_actor
        def later_invalid(request):
            if request["round"] == 2 and request["role"] == "B" and request["mode"] == "image_vote":
                self.backend.calls.append(request["request_id"])
                return {"status": "invalid_model_output", "reason": "Fixture invalid later ballot"}
            return original(request)
        self.backend.run_actor = later_invalid
        result, _, _ = self.drive(coordinator)
        self.assertEqual(result["rounds_finished"], 12)
        self.assertEqual(result["cases"][0]["invalid_stages"], 1)
        self.assertEqual(result["cases"][0]["incomplete_ballot_rounds"], 1)

    def test_source_change_prevents_further_dispatch(self):
        coordinator = self.coordinator()
        coordinator.init()
        with patch("experiments.triad_max_pressure_v3.coordinator.source_manifest", return_value={"changed.py": "0" * 64}):
            with self.assertRaises(ContractError):
                coordinator.advance()
        self.assertEqual(self.backend.calls, [])


if __name__ == "__main__":
    unittest.main()
