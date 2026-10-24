"""Temporary authored ZIP fixtures only; no live study, credentials or network."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SPEC = importlib.util.spec_from_file_location("_writeup_augment", Path(__file__).with_name("augment_export.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AugmentExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.here = self.root / "research_plans/pressure_interview_v1_writeup"
        self.study = self.root / "runs/main/pressure_interview_live"
        self.patches = [patch.object(MODULE, "ROOT", self.root), patch.object(MODULE, "HERE", self.here),
                        patch.object(MODULE, "STUDY", self.study)]
        for item in self.patches:
            item.start()
        self.write("src/visual_collusion/__init__.py", "# Authored software fixture; not model evidence.\n")
        self.write("experiments/pressure_interview_v1/__init__.py", "# Authored software fixture; not model evidence.\n")
        self.write_json("runs/main/freeze.json", {"source_files": {"__init__.py": MODULE.digest_file(self.root / "src/visual_collusion/__init__.py")}})
        self.write_json("runs/main/pressure_interview_live/freeze.json", {"evidence_origin": "live",
            "fixture_for_testing_only": True, "source_files": {"__init__.py": MODULE.digest_file(self.root / "experiments/pressure_interview_v1/__init__.py")}})
        for name in ("config.json", "plan.json"):
            self.write_json("runs/main/pressure_interview_live/" + name, {"fixture_for_testing_only": True})
        self.write_json("runs/main/pressure_interview_live/analysis/report.json", {
            "evidence_origin": "live", "revision": MODULE.REVISION, "verification": {"status": "verified"},
            "run_status": "complete_with_invalids", "fixture_for_testing_only": True})
        for name in MODULE.RUNTIME_SCRIPTS:
            self.write("scripts/" + name, "# Inert authored fixture.\n")
        for name in MODULE.DOCS:
            self.write("docs/" + name, "Authored software fixture.\n")
        for name in ("pyproject.toml", "requirements.txt", "README.md", "AGENTS.md", "CODEX_CLOUD_HANDOFF.md"):
            self.write(name, "Authored software fixture.\n")
        self.write_json("configs/canonical.json", {"fixture_for_testing_only": True})
        self.write_json("runs/main/execution_notes/official_template_read.json", {"fixture_for_testing_only": True})
        for name in ("render_paper.py", "augment_export.py", "test_augment_export.py", "test_render_monitoring.py", "README.md", "REQUIREMENTS.json", "PAPER_SCAFFOLD.md"):
            self.write("research_plans/pressure_interview_v1_writeup/" + name, "Authored software fixture.\n")
        self.source = self.root / "core.zip"
        with zipfile.ZipFile(self.source, "w") as archive:
            for path in self.study.rglob("*"):
                if path.is_file():
                    archive.write(path, str(path.relative_to(self.study)))
        self.destination = self.root / "complete.zip"

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)

    def write_json(self, name, value):
        self.write(name, json.dumps(value, sort_keys=True) + "\n")

    def test_source_copy_hashes_and_original_are_retained(self):
        before = MODULE.digest_file(self.source)
        result = MODULE.augment(self.source, self.destination, study=self.study)
        self.assertEqual(result["new_paid_calls"], 0)
        self.assertEqual(MODULE.digest_file(self.source), before)
        with zipfile.ZipFile(self.destination) as archive:
            manifest = json.loads(archive.read("reproduction_sources_manifest.json"))
            self.assertEqual(manifest["input_core_archive_sha256"], before)
            for name, expected in manifest["files"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
            self.assertNotIn(".env", archive.namelist())

    def test_changed_frozen_source_and_running_study_are_rejected(self):
        self.write("src/visual_collusion/__init__.py", "# Changed fixture.\n")
        with self.assertRaisesRegex(ValueError, "Frozen reproduction source changed"):
            MODULE.augment(self.source, self.destination, study=self.study)
        self.assertFalse(self.destination.exists())
        self.write_json("runs/main/pressure_interview_live/analysis/report.json", {
            "evidence_origin": "live", "revision": MODULE.REVISION, "verification": {"status": "verified"},
            "run_status": "awaiting_codex", "fixture_for_testing_only": True})
        with self.assertRaisesRegex(ValueError, "not in a documented terminal"):
            MODULE.augment(self.source, self.destination, study=self.study)

    def test_current_core_report_binding_is_required(self):
        self.write_json("runs/main/pressure_interview_live/analysis/report.json", {
            "evidence_origin": "live", "revision": MODULE.REVISION, "verification": {"status": "verified"},
            "run_status": "halted", "fixture_for_testing_only": True})
        with self.assertRaisesRegex(ValueError, "does not match"):
            MODULE.augment(self.source, self.destination, study=self.study)
        self.assertFalse(self.destination.exists())

    def test_stale_manuscript_is_not_exported(self):
        self.write_json("runs/main/pressure_interview_live/analysis/manuscript_source_manifest.json", {
            "source_hashes": {"analysis/report.json": "0" * 64},
            "fixture_for_testing_only": True})
        with self.assertRaisesRegex(ValueError, "Frozen reproduction source changed"):
            MODULE.augment(self.source, self.destination, study=self.study)
        self.assertFalse(self.destination.exists())

    def test_named_amendment_sources_and_validation_are_bound(self):
        self.write("scripts/fixture_amendment.py", "# Inert authored amendment fixture.\n")
        self.write_json("research_plans/fixture_amendment_validation/VALIDATION.json", {"fixture_for_testing_only": True})
        self.write_json("runs/main/pressure_interview_live/execution_notes/png_xmp_v2_amendment.json", {
            "source_files": {"scripts/fixture_amendment.py": MODULE.digest_file(self.root / "scripts/fixture_amendment.py")},
            "validation": {"path": "research_plans/fixture_amendment_validation/VALIDATION.json",
                           "sha256": MODULE.digest_file(self.root / "research_plans/fixture_amendment_validation/VALIDATION.json")}})
        MODULE.augment(self.source, self.destination, study=self.study)
        with zipfile.ZipFile(self.destination) as archive:
            self.assertIn("reproduction_workspace/scripts/fixture_amendment.py", archive.namelist())
            self.assertIn("reproduction_workspace/research_plans/fixture_amendment_validation/VALIDATION.json", archive.namelist())
            self.assertIn("execution_notes/png_xmp_v2_amendment.json", archive.namelist())
        self.write("scripts/fixture_amendment.py", "# Changed fixture.\n")
        with self.assertRaisesRegex(ValueError, "Frozen reproduction source changed"):
            MODULE.augment(self.source, self.destination, study=self.study)

    def test_v3_single_test_hash_schema_is_supported(self):
        self.write("scripts/pressure_invalid_analysis_v3.py", "# Inert authored v3 fixture.\n")
        self.write("tests/test_pressure_invalid_analysis_v3.py", "# Inert authored test fixture.\n")
        self.write_json("research_plans/fixture_v3_validation/VALIDATION.json", {
            "test_source_sha256": MODULE.digest_file(self.root / "tests/test_pressure_invalid_analysis_v3.py")})
        self.write_json("runs/main/pressure_interview_live/execution_notes/invalid_backend_analysis_v3.json", {
            "version": "invalid_backend_analysis_v3",
            "source_files": {"scripts/pressure_invalid_analysis_v3.py": MODULE.digest_file(self.root / "scripts/pressure_invalid_analysis_v3.py")},
            "validation": {"path": "research_plans/fixture_v3_validation/VALIDATION.json",
                           "sha256": MODULE.digest_file(self.root / "research_plans/fixture_v3_validation/VALIDATION.json")}})
        MODULE.augment(self.source, self.destination, study=self.study)
        with zipfile.ZipFile(self.destination) as archive:
            self.assertIn("reproduction_workspace/tests/test_pressure_invalid_analysis_v3.py", archive.namelist())


if __name__ == "__main__":
    unittest.main()
