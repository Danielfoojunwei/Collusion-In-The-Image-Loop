"""Offline regressions for failure after a valid first-post-fork delivery.

These fixtures use only the scripted MockProvider and mock Codex bridge.
They exercise real persistence, evidence validation and report construction;
their successful marker transfer is not evidence about any live model.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from visual_collusion.analysis import build_report
from visual_collusion.bridge import fail_job, validate_saved_failure
from visual_collusion.budget import BudgetLedger
from visual_collusion.common import digest, read_json
from visual_collusion.mock import MockProvider, fulfill_mock_job
from visual_collusion.runtime import advance, prepare_run


CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "canonical.json"


class StopAtFirstNeutralCall(MockProvider):
    """A pre-request budget/transport-like stop after the real B delivery."""

    def __init__(self, plan, ledger):
        super().__init__(plan, ledger)
        self.stopped_request = None

    def chat(self, request_id, *args, **kwargs):
        if self.stopped_request is None and request_id.endswith(":round3:B:neutral:step1"):
            self.stopped_request = request_id
            raise RuntimeError("OFFLINE FIXTURE: neutral replay unavailable before first request")
        return super().chat(request_id, *args, **kwargs)


class RuntimeFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.out = Path(self.temporary.name) / "run"
        self.config = read_json(CONFIG_PATH)
        self.plan = prepare_run(self.config, self.out, evidence_origin="mock")
        self.ledger = BudgetLedger(self.out / "budget.sqlite3", self.config["max_inference_usd"])

    def provider(self, cls=MockProvider):
        provider = cls(self.plan, self.ledger)
        provider.auto_reviews = True
        return provider

    def test_neutral_stop_keeps_valid_original_reception_and_unknown_pair(self):
        provider = self.provider(StopAtFirstNeutralCall)

        with self.assertRaisesRegex(RuntimeError, "neutral replay unavailable"):
            advance(provider, self.config, self.out)

        self.assertIsNotNone(provider.stopped_request)
        round_key = provider.stopped_request.removesuffix(":B:neutral:step1")
        checkpoint_name = digest(round_key)[:24] + ".json"
        checkpoint = read_json(self.out / "reception_rounds" / checkpoint_name)
        self.assertEqual(checkpoint["round"], 3)
        self.assertEqual(checkpoint["condition"], "explicit")
        self.assertEqual(checkpoint["b_output"]["action"], "deliver")
        self.assertEqual(checkpoint["b_output"]["decision"], checkpoint["target"])
        self.assertEqual(checkpoint["neutral_replay"], {"status": "pending"})
        self.assertNotIn("review", checkpoint)
        self.assertFalse((self.out / "unreviewed_rounds" / checkpoint_name).exists())
        self.assertFalse((self.out / "rounds" / checkpoint_name).exists())

        # Validation/reporting must consume the saved primary outcome even
        # though no replay, Codex review, or final branch has completed.
        calls_before_report = provider.fresh_calls
        report = build_report(self.out)
        self.assertEqual(provider.fresh_calls, calls_before_report)
        self.assertEqual(report["evidence_origin"], "mock")
        self.assertEqual(report["status"], "stopped")
        stopped = read_json(self.out / "run_status.json")
        self.assertEqual(stopped["stop_category"], "RuntimeError")
        self.assertFalse(stopped["automatic_paid_retry"])
        self.assertEqual(report["live_observations"], 0)
        self.assertEqual(report["primary_receptions_planned"], 12)
        self.assertEqual(report["primary_receptions_observed"], 1)
        reception = report["primary_receptions"][0]
        self.assertEqual(reception["episode_id"], checkpoint["episode_id"])
        self.assertEqual(reception["branch"], checkpoint["branch"])
        self.assertEqual(reception["decision"], checkpoint["target"])
        self.assertIsNone(reception["neutral_decision"])
        self.assertIsNone(reception["neutral_target_match"])
        self.assertFalse(reception["review_completed"])
        pair = next(p for p in report["case_level_pairs"]
                    if p["episode_id"] == checkpoint["episode_id"])
        self.assertEqual(pair["available_branches"], 1)
        self.assertIsNone(pair["both_assigned_targets_followed"])
        self.assertIsNone(pair["target_dependence_theta"])
        self.assertIsNone(pair["intact_minus_neutral_matches"])

    def test_sealed_round3_review_failure_preserves_primary_and_other_cases(self):
        provider = self.provider()
        episodes = {e["episode_id"]: e for e in self.plan["episodes"]}
        episode = next(episodes[key] for key in self.plan["execution_order"]
                       if episodes[key]["condition"] == "explicit")
        branch = episode["branches"][0]
        round_key = f"{episode['episode_id']}:{branch['branch_id']}:round3"
        failed_job_id = digest({"protocol": "4", "key": round_key + ":review"})[:24]
        sealed_jobs = []

        def fulfill_or_seal(out, status):
            if status["job_id"] != failed_job_id:
                return fulfill_mock_job(out, status)
            packet = read_json(status["packet_path"])
            self.assertEqual(packet["kind"], "review")
            self.assertEqual(packet["public"]["rounds"][-1]["round"], 3)
            # Preserve one actually supplied, deliberately malformed fixture
            # response. This is not an invented live Codex response or retry.
            raw = Path(out) / "invalid_mock_review.raw"
            raw.write_bytes(b"OFFLINE MOCK FIRST RESPONSE: malformed reviewer JSON")
            sealed_jobs.append(status["job_id"])
            return fail_job(out, status["job_id"], "Malformed first mock review response", raw,
                            actor_model="scripted-fixture", context_isolation="instruction_scoped",
                            actor_runtime="mock", allow_mock=True)

        with patch("visual_collusion.mock.fulfill_mock_job", side_effect=fulfill_or_seal):
            status = advance(provider, self.config, self.out)
            self.assertEqual(status["status"], "complete_with_invalids")
            self.assertEqual(status["completed_trajectories"], 15)
            self.assertEqual(len(status["failures"]), 1)
            self.assertEqual(status["failures"][0]["episode_id"], episode["episode_id"])
            self.assertEqual(status["failures"][0]["branch"], branch["branch_id"])
            self.assertEqual(status["failures"][0]["round"], 3)
            self.assertEqual(sealed_jobs, [failed_job_id])

            failure_folder = self.out / "review_jobs" / failed_job_id
            failure = validate_saved_failure(failure_folder)
            self.assertEqual(failure["actor_runtime"], "mock")
            self.assertEqual(failure["evidence_origin"], "mock")
            self.assertFalse((failure_folder / "response.json").exists())

            report = build_report(self.out)
            self.assertEqual(report["primary_receptions_observed"], 12)
            self.assertEqual(report["complete_trajectories"], 15)
            self.assertEqual(report["live_observations"], 0)
            affected = next(r for r in report["primary_receptions"]
                            if r["episode_id"] == episode["episode_id"] and r["branch"] == branch["branch_id"])
            self.assertEqual(affected["decision"], branch["private_target"])
            self.assertFalse(affected["review_completed"])
            self.assertIsNotNone(affected["neutral_decision"])
            self.assertTrue(any(r["episode_id"] != episode["episode_id"]
                                for r in report["trajectory_summaries"]))

            # A resume must reuse all saved provider responses and the sealed
            # review failure rather than asking for a replacement verdict.
            requests_before_resume = provider.fresh_calls
            resumed = advance(provider, self.config, self.out)
            self.assertEqual(resumed["status"], "complete_with_invalids")
            self.assertEqual(provider.fresh_calls, requests_before_resume)
            self.assertEqual(sealed_jobs, [failed_job_id])


if __name__ == "__main__":
    unittest.main()
