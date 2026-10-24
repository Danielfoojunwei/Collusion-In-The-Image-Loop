"""Offline source-bound reporting fixtures, never live scientific results."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from PIL import Image

from experiments.triad_emergent_v9 import analysis
from experiments.triad_emergent_v9.design import make_plan
from experiments.triad_emergent_v9.engine import Engine, canonical, read_json
from experiments.triad_emergent_v9.tests.test_engine import (
    ScriptedBackend, config, reviewer_answer, provenance)


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "fixture"
        self.plan, self.config = make_plan(), config()
        self.backend = ScriptedBackend(self.out, self.plan)
        self.engine = Engine(self.out, self.config, self.plan, backend=self.backend)
        for name, value in [("config.json", self.config), ("plan.json", self.plan)]:
            (self.out / name).write_text(canonical(value))
        self.freeze = {"revision": "triad_emergent_v9", "source_files": {
            str(Path(analysis.__file__).relative_to(analysis.ROOT)): digest_file(analysis.__file__)},
            "config_sha256": digest_file(self.out / "config.json"),
            "plan_sha256": digest_file(self.out / "plan.json")}
        (self.out / "freeze.json").write_text(canonical(self.freeze))
        self.report()

    def tearDown(self):
        self.tmp.cleanup()

    def report(self):
        (self.out / "analysis").mkdir(exist_ok=True)
        (self.out / "analysis/report.json").write_text(canonical({"revision": "triad_emergent_v9",
            "condition": self.plan["condition"], "summary": self.engine.summary(), "rounds": self.engine.report_rows(),
            "budget": {"fixture_only": True, "actual_paid_calls": 0}}))

    def complete(self):
        while self.engine.next_stage():
            if self.engine.next_stage()["stage"] == "REVIEWER":
                job = self.engine.export_reviewer_packet()
                answer = reviewer_answer(read_json(job["packet_path"]))
                self.engine.ingest_review(answer, provenance(job), raw_answer=canonical(answer))
            else:
                self.engine.run_next_actor()
        self.report()

    def raw_manifest(self):
        return {str(p.relative_to(self.out)): digest_file(p) for p in self.out.rglob("*")
                if p.is_file() and not p.is_relative_to(self.out / "analysis/human_readable")}

    def test_default_refuses_incomplete_but_explicit_status_render_is_readonly(self):
        before = self.raw_manifest()
        with self.assertRaises(analysis.AnalysisError):
            analysis.render(self.out)
        self.assertFalse((self.out / "analysis/human_readable").exists())
        result = analysis.render(self.out, allow_incomplete=True)
        self.assertEqual(result["main_rounds_complete"], 0)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(before, self.raw_manifest())

    def test_complete_report_separates_c12_archive_shadows_and_peer_receptions(self):
        self.complete()
        before = self.raw_manifest()
        result = analysis.render(self.out)
        self.assertEqual(result["terminal_main_stages"], 96)
        self.assertEqual(before, self.raw_manifest())
        evidence = json.loads((self.out / "analysis/human_readable/EVIDENCE.json").read_text())
        self.assertEqual(evidence["main_rounds_complete"], 12)
        self.assertEqual(evidence["main_accepted_raster_slots"], 36)
        self.assertEqual(evidence["main_peer_raster_slots"], 35)
        self.assertEqual(evidence["confirmed_peer_receptions"], 47)
        self.assertEqual(evidence["main_recorded_generated_drafts"], 36)
        self.assertEqual(evidence["main_completed_image_receipts"], 0)  # scripted fixture, no actual provider receipts
        self.assertEqual(evidence["rounds"][-1]["C_return_delivery"]["status"], "archive_only")
        self.assertEqual(len(evidence["shadows"]["rows"]), 24)
        self.assertTrue(all(row["status"] == "not_reached" for row in evidence["shadows"]["rows"]))
        self.assertFalse(evidence["shadows"]["included_in_main_counts"] if "included_in_main_counts" in evidence["shadows"] else False)
        self.assertEqual(sum(len(row["post_review_input_checks"]) for row in evidence["rounds"]), 48)
        gallery = (self.out / "analysis/human_readable/GALLERY.html").read_text()
        self.assertIn('id="r12"', gallery)
        self.assertIn("no A13 recipient", gallery)
        self.assertIn("Private draft and rejected-action record", gallery)
        self.assertEqual(evidence["condition"], "induced_shared_private")
        self.assertTrue(any("not evidence of organic or unprompted" in text for text in evidence["limitations"]))
        self.assertTrue(any("charging a role does not prove" in text for text in evidence["limitations"]))

    def test_rgba_original_is_accepted_without_editing_or_normalizing(self):
        path = self.out / "rgba_original.png"
        Image.new("RGBA", (1024, 1024), (20, 30, 40, 128)).save(path, format="PNG")
        before = path.read_bytes()
        result = analysis.image(analysis.Reader(), {"path": str(path), "sha256": digest_file(path)},
                                self.out, self.out / "analysis/human_readable")
        self.assertEqual(result["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(path.read_bytes(), before)

    def test_native_envelopes_export_tools_and_system_but_never_raw_reasoning(self):
        self.complete()
        marker = "RAW_PROVIDER_REASONING_MUST_NOT_BE_EXPORTED"
        p = self.out / "provider_evidence/transport_requests/example.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tool = {"type": "function", "function": {"name": "create_visual", "parameters": {"type": "object"}}}
        p.write_text(canonical({"endpoint": "/chat/completions", "request_id": "episode.r01.A_IMAGE_AND_VOTE.visual1",
            "logical_payload": {"model": "software_fixture", "temperature": 1.0,
                "max_tokens": 8192, "reasoning": {"effort": "high", "exclude": False},
                "tools": [tool], "tool_choice": "required", "messages": [
                    {"role": "system", "content": "Exact fixture system string."},
                    {"role": "assistant", "content": "", "reasoning": marker}]}}))
        analysis.render(self.out)
        rendered = self.out / "analysis/human_readable"
        for path in rendered.iterdir():
            self.assertNotIn(marker, path.read_text())
        text = (rendered / "EXACT_SYSTEM_PROMPTS_BY_STAGE.txt").read_text()
        self.assertIn("Exact fixture system string.", text)
        self.assertIn('"tool_choice": "required"', text)
        self.assertIn('"name": "create_visual"', text)

    def test_tampered_stage_prevents_happy_derived_report(self):
        self.complete()
        path = self.engine._path(1, "A_IMAGE_AND_VOTE")
        record = json.loads(path.read_text())
        record["output"]["decision"] = "reject_all"
        path.write_text(canonical(record))
        with self.assertRaises(analysis.AnalysisError):
            analysis.render(self.out)
        self.assertFalse((self.out / "analysis/human_readable").exists())


if __name__ == "__main__":
    unittest.main()
