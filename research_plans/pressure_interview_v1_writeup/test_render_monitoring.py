"""Authored, temporary monitoring fixtures; not scientific observations."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image


SPEC = importlib.util.spec_from_file_location("_writeup_renderer", Path(__file__).with_name("render_paper.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.study = Path(self.temporary.name)
        self.plan = {"cases": [{"case_id": "case_fixture", "correct_decision": "P1"}],
                     "episodes": [{"episode_id": "episode_fixture", "case_id": "case_fixture", "condition": "explicit"}]}
        self.prefix = self.round_events("prefix", 8, "P2", "revise", True)
        self.write("prefix", self.prefix)
        self.write("branch_1", self.prefix + self.round_events("branch_1", 9, "P2", "approve", False))
        self.write("branch_2", self.prefix + self.round_events("branch_2", 9, "P1", "approve", False))

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def round_events(branch, number, decision, verdict, coordination):
        common = {"branch": branch, "round_number": number}
        identifier = "episode_fixture." + branch + ".r" + str(number)
        return [
            {**common, "id": identifier + ".b", "kind": "b_public", "payload": {"decision": decision}},
            {**common, "id": identifier + ".flag", "kind": "b_flag", "payload": {"suspicious": coordination}},
            {**common, "id": identifier + ".c", "kind": "c_review", "payload": {
                "verdict": verdict, "collusion_suspected": coordination, "task_violation": coordination}},
        ]

    def write(self, branch, events):
        path = self.study / "episodes/episode_fixture" / branch / "memory/events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(event) + "\n" for event in events))

    def test_shared_prefix_once_and_actual_wrong_approval_only(self):
        value = MODULE.monitoring_timeline(self.study, self.plan, 3)
        self.assertEqual(len(value["unique_review_rows"]), 3)
        self.assertEqual(value["wrong_approvals"], 1)
        self.assertEqual(value["wrong_decisions_reviewed"], 2)
        self.assertEqual(value["primary_with_prior_coordination_flag"], 2)
        self.assertEqual(value["primary_with_prior_task_flag"], 2)
        self.assertEqual(len(value["source_hashes"]), 3)
        self.assertEqual([row["prior_reviews"] for row in value["primary_prior_rows"]], [1, 1])

    def test_missing_B_choice_does_not_become_wrong_approval(self):
        events = self.round_events("branch_1", 9, "P2", "approve", False)[1:]
        self.write("branch_1", self.prefix + events)
        value = MODULE.monitoring_timeline(self.study, self.plan, 3)
        self.assertEqual(value["wrong_approvals"], 0)
        self.assertEqual(value["wrong_decisions_reviewed"], 1)
        self.assertEqual(len(value["primary_prior_rows"]), 1)

    def test_mismatched_shared_prefix_and_stale_report_rejected(self):
        with self.assertRaisesRegex(ValueError, "differs from the verified report"):
            MODULE.monitoring_timeline(self.study, self.plan, 4)
        changed = json.loads(json.dumps(self.prefix))
        changed[-1]["payload"]["collusion_suspected"] = False
        self.write("branch_2", changed + self.round_events("branch_2", 9, "P1", "approve", False))
        with self.assertRaisesRegex(ValueError, "shared-prefix judgments differ"):
            MODULE.monitoring_timeline(self.study, self.plan, 3)

    def test_fixture_artifact_illustration_stays_within_study(self):
        source = self.study / "fixture_artifact.png"
        Image.new("RGB", (32, 32), "white").save(source)
        markdown = "# Authored software fixture\n\n![Synthetic-only original image.](fixture_artifact.png)\n\n## References\n\nAuthored fixture, not model evidence.\n"
        value = MODULE.render_pdf(markdown, self.study / "fixture_preview.pdf", study=self.study)
        self.assertEqual(value["main_pages"], 1)
        self.assertEqual(value["total_pages"], 2)
        with self.assertRaisesRegex(ValueError, "within the study"):
            MODULE.render_pdf(markdown.replace("fixture_artifact.png", "../outside.png"), self.study / "rejected_preview.pdf", study=self.study)


if __name__ == "__main__":
    unittest.main()
