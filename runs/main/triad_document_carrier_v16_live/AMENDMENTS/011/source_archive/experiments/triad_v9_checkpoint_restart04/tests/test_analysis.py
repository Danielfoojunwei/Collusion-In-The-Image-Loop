"""Offline selector/report fixtures; no service calls or live ledger writes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
import zipfile

from experiments.triad_emergent_v9.design import make_plan
from experiments.triad_emergent_v9.engine import Engine as BaseEngine, canonical, read_json
from experiments.triad_emergent_v9.tests.test_engine import (
    ScriptedBackend, config, reviewer_answer, provenance,
)
from experiments.triad_v9_checkpoint_restart04 import analysis


class FixtureStore:
    legacy_rounds = 1

    def __init__(self, out):
        self.out, self.completed = out, 2
        self.working = out / "attempts/r02/a02"

    def committed_rounds(self):
        return self.completed

    def committed_selector(self, number):
        return {"working_root": str(self.working if number == 2 else self.out)} if number <= self.completed else None

    def inventory(self):
        return [{"round": 2, "attempt": 1, "attempt_id": "r02.a01", "status": "abandoned",
                 "working_root": str(self.out), "cost_unknown": True},
                {"round": 2, "attempt": 2, "attempt_id": "r02.a02", "status": "committed",
                 "working_root": str(self.working)},
                {"round": 3, "attempt": 1, "attempt_id": "r03.a01", "status": "active",
                 "working_root": str(self.out / "attempts/r03/a01")}]


class SelectedEngine(BaseEngine):
    def __init__(self, *args, store, **kwargs):
        self.store = store
        super().__init__(*args, **kwargs)

    def _path(self, number, stage, suffix="outcome"):
        if number == 2:
            return self.store.working / "episode/rounds/r02" / (stage + "." + suffix + ".json")
        return super()._path(number, stage, suffix)


class FixtureHost:
    def __init__(self, out, engine, store, cfg):
        self.out, self.engine, self.store, self.config = out, engine, store, cfg

    def _engine(self):
        return self.engine

    def _store(self):
        return self.store

    def verify(self):
        return {"status": "passed", "budget": {"fixture_only": True, "actual_paid_calls": 0}}

    def report(self):
        result = analysis.build_report(self, self.verify())
        path = self.out / "analysis/report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical(result))
        return result


class CheckpointAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "fixture"
        self.plan, self.config = make_plan(), config()
        backend = ScriptedBackend(self.out, self.plan)
        original = BaseEngine(self.out, self.config, self.plan, backend=backend)
        while original.next_stage()["round"] <= 2:
            if original.next_stage()["stage"] == "REVIEWER":
                job = original.export_reviewer_packet()
                answer = reviewer_answer(read_json(job["packet_path"]))
                original.ingest_review(answer, provenance(job), raw_answer=canonical(answer))
            else:
                original.run_next_actor()
        self.store = FixtureStore(self.out)
        destination = self.store.working / "episode/rounds/r02"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self.out / "episode/rounds/r02"), destination)
        self.engine = SelectedEngine(self.out, self.config, self.plan, backend=backend, store=self.store)
        self.host = FixtureHost(self.out, self.engine, self.store, self.config)
        for name, value in (("config.json", self.config), ("plan.json", self.plan)):
            (self.out / name).write_text(canonical(value))
        source = Path(analysis.__file__)
        self.freeze = {"revision": "triad_emergent_v9", "source_files": {
            str(source.relative_to(analysis.ROOT)): analysis.sha(source.read_bytes())},
            "config_sha256": analysis.sha((self.out / "config.json").read_bytes()),
            "plan_sha256": analysis.sha((self.out / "plan.json").read_bytes())}
        (self.out / "freeze.json").write_text(canonical(self.freeze))
        self.host.report()

    def tearDown(self):
        self.tmp.cleanup()

    def raw_manifest(self):
        return {str(p.relative_to(self.out)): analysis.sha(p.read_bytes()) for p in self.out.rglob("*")
                if p.is_file() and not p.is_relative_to(self.out / "analysis/human_readable")}

    def test_committed_selector_uses_new_attempt_and_leaves_originals_unchanged(self):
        before = self.raw_manifest()
        result = analysis.render(self.host, allow_incomplete=True)
        self.assertEqual(result["main_rounds_complete"], 2)
        self.assertEqual(result["terminal_main_stages"], 16)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(before, self.raw_manifest())
        evidence = json.loads((self.out / "analysis/human_readable/EVIDENCE.json").read_text())
        self.assertIn("/attempts/r02/a02/", next(row["source_file"] for row in evidence["exact_stage_prompts"] if row["round"] == 2))
        self.assertEqual(evidence["technical_attempts"]["status_counts"], {"abandoned": 1, "committed": 1, "active": 1})
        self.assertEqual(evidence["main_peer_raster_slots"], 6)
        self.assertEqual(evidence["confirmed_peer_receptions"], 7)
        self.assertEqual(evidence["rounds"][1]["C_return_delivery"]["status"], "not_observed")
        self.assertEqual(evidence["provider_calls_by_renderer"], 0)

    def test_uncommitted_complete_or_partial_round_never_enters_canonical_report(self):
        # Even a complete third-round working tree is not a completed checkpoint.
        while self.engine.next_stage()["round"] == 3:
            if self.engine.next_stage()["stage"] == "REVIEWER":
                job = self.engine.export_reviewer_packet()
                answer = reviewer_answer(read_json(job["packet_path"]))
                self.engine.ingest_review(answer, provenance(job), raw_answer=canonical(answer))
            else:
                self.engine.run_next_actor()
        report = self.host.report()
        self.assertEqual(len(report["rounds"]), 2)
        self.assertEqual(report["committed_rounds"], 2)
        result = analysis.render(self.host, allow_incomplete=True)
        self.assertEqual(result["terminal_main_stages"], 16)
        with self.assertRaises(analysis.AnalysisError):
            analysis.render(self.host)

    def test_funded_ids_with_attempt_prefix_use_only_selected_provider_root(self):
        marker = "PRIVATE_RAW_REASONING_MUST_REMAIN_UNEXPORTED"
        for root, label in ((self.out, "ABANDONED_SYSTEM"), (self.store.working, "SELECTED_SYSTEM")):
            path = root / "provider_evidence/transport_requests/native.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(canonical({"endpoint": "/chat/completions",
                "request_id": "r02.a02:episode.r02.A_IMAGE_AND_VOTE.visual1",
                "logical_payload": {"model": "fixture", "temperature": 1,
                    "max_tokens": 8192, "reasoning": {"effort": "high"},
                    "messages": [{"role": "system", "content": label},
                                 {"role": "assistant", "content": "", "reasoning": marker}]}}))
        self.host.report()
        analysis.render(self.host, allow_incomplete=True)
        folder = self.out / "analysis/human_readable"
        text = (folder / "EXACT_SYSTEM_PROMPTS_BY_STAGE.txt").read_text()
        self.assertIn("SELECTED_SYSTEM", text)
        self.assertNotIn("ABANDONED_SYSTEM", text)
        for path in folder.iterdir():
            self.assertNotIn(marker, path.read_text())
        evidence = json.loads((folder / "EVIDENCE.json").read_text())
        self.assertEqual(evidence["nonselected_provider_evidence_counts"]["native_calls"], 1)

    def test_tampered_selected_attempt_cannot_produce_status_report(self):
        path = self.engine._path(2, "A_IMAGE_AND_VOTE")
        original = json.loads(path.read_text())
        original["output"]["decision"] = "reject_all"
        path.write_text(canonical(original))
        with self.assertRaises(analysis.AnalysisError):
            analysis.render(self.host, allow_incomplete=True)
        self.assertFalse((self.out / "analysis/human_readable").exists())

    def test_failed_attempt_malformed_artifacts_remain_audited_without_poisoning_selected_chronology(self):
        root = self.out / "attempts/r03/a01/provider_evidence"
        (root / "transport_requests").mkdir(parents=True)
        (root / "image_calls").mkdir(parents=True)
        (root / "transport_requests/broken.json").write_bytes(b'{"incomplete":')
        (root / "image_calls/broken.json").write_bytes(b'{"not_valid_png":true}')
        analysis.render(self.host, allow_incomplete=True)
        evidence = json.loads((self.out / "analysis/human_readable/EVIDENCE.json").read_text())
        counts = evidence["nonselected_provider_evidence_counts"]
        self.assertEqual(counts["image_calls"], 1)
        self.assertEqual(counts["invalid_transport_metadata_files"], 1)
        self.assertIn(str(root / "image_calls/broken.json"), evidence["source_files_sha256"])
        self.assertEqual(evidence["main_rounds_complete"], 2)

    def test_per_attempt_costs_preserve_unknown_reserve_without_asserting_zero(self):
        path = Path(self.tmp.name) / "fixture_budget.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE requests(request_id TEXT,status TEXT,reserved_units INTEGER,actual_units INTEGER)")
            db.executemany("INSERT INTO requests VALUES(?,?,?,?)", [
                ("triad_emergent_v9:r02.a01:episode.r02.A_IMAGE_AND_VOTE.visual1", "uncertain", 17896200, None),
                ("triad_emergent_v9:r02.a02:episode.r02.A_IMAGE_AND_VOTE.visual1", "completed", 20000000, 1234567)])
        before = path.read_bytes()
        result = analysis._attempt_inventory(self.store, {"aggregate_path": str(path)})
        failed, committed = result["records"][:2]
        self.assertEqual(failed["funding"]["unknown_or_pending_reserve_usd"], .0178962)
        self.assertEqual(failed["funding"]["actual_cost_unknown_for_requests"], ["triad_emergent_v9:r02.a01:episode.r02.A_IMAGE_AND_VOTE.visual1"])
        self.assertFalse(failed["funding"]["unknown_charges_asserted_zero"])
        self.assertEqual(committed["funding"]["known_actual_usd"], .001234567)
        self.assertEqual(path.read_bytes(), before)

    def test_selected_shadow_workflow_renders_actual_endpoints_and_visual_archive_excludes_raw_responses(self):
        while self.engine.next_stage():
            if self.engine.next_stage()["stage"] == "REVIEWER":
                job = self.engine.export_reviewer_packet()
                answer = reviewer_answer(read_json(job["packet_path"]))
                self.engine.ingest_review(answer, provenance(job), raw_answer=canonical(answer))
            else:
                self.engine.run_next_actor()
        self.store.completed = 12
        root = self.out / "shadow_attempts/a02"
        files = {}
        for number in (2, 4, 6, 8, 10, 12):
            for family, role in (("Aneutral", "B"), ("Aneutral", "C"), ("Bneutral", "C"), ("Cprevneutral", "A")):
                body = {"family": family, "round": number, "role": role, "status": "unavailable", "reason": "Software fixture only"}
                value = {**body, "record_hash": analysis.sha(canonical(body).encode())}
                path = root / "shadows" / family / f"r{number:02d}" / (role + ".outcome.json")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(canonical(value))
                files[str(path.relative_to(self.out))] = analysis.sha(path.read_bytes())
        marker = "RAW_SHADOW_REASONING_NEVER_IN_PORTABLE_ARCHIVE"
        secret = root / "provider_evidence/responses/raw.json"
        secret.parent.mkdir(parents=True)
        secret.write_text(canonical({"reasoning": marker}))
        files[str(secret.relative_to(self.out))] = analysis.sha(secret.read_bytes())
        report = {"status": "complete", "planned_shadow_turns": 24, "observed_shadow_turns": 0,
                  "unavailable_shadow_turns": 24, "main_preservation_verified": True,
                  "selected_technical_attempt": "shadows.a02", "failed_technical_workflows": [{"attempt_id": "shadows.a01"}]}
        report_path = root / "PUBLISHED_REPORT.json"
        report_path.write_text(canonical(report))
        files[str(report_path.relative_to(self.out))] = analysis.sha(report_path.read_bytes())
        body = {"attempt_id": "shadows.a02", "working_root": str(root), "main_sha256": "0" * 64,
                "report_path": str(report_path), "report_sha256": analysis.sha(report_path.read_bytes()), "evidence_files": files}
        selector = {**body, "record_hash": analysis.sha(canonical(body).encode())}
        path = self.out / "shadow_checkpoints_restart04/COMMIT.json"
        path.parent.mkdir(parents=True)
        path.write_text(canonical(selector))
        (self.out / "shadows").mkdir()
        (self.out / "shadows/report.json").write_text(canonical(report))
        self.host.report()
        data = analysis.build(self.host)
        self.assertTrue(all(row["status"] == "unavailable" for row in data["shadows"]["rows"]))
        self.assertTrue(all("/shadow_attempts/a02/" in row["source_file"] for row in data["shadows"]["rows"]))
        target = Path(self.tmp.name) / "portable.zip"
        result = analysis.export_visual(self.host, target)
        self.assertFalse(result["provider_raw_reasoning_exported"])
        with zipfile.ZipFile(target) as archive:
            self.assertFalse(any("responses/raw.json" in name for name in archive.namelist()))
            for name in archive.namelist():
                if name.endswith((".txt", ".json", ".md", ".html")):
                    self.assertNotIn(marker, archive.read(name).decode())
        # A modified selected endpoint fails even if its own record hash could be recomputed.
        endpoint = root / "shadows/Aneutral/r02/B.outcome.json"
        endpoint.write_text(endpoint.read_text() + " ")
        with self.assertRaises(analysis.AnalysisError):
            analysis.build(self.host)

    def test_final_archives_reject_unfinished_checkpoint_or_controls(self):
        for function in (analysis.export, analysis.export_visual):
            with self.assertRaises(analysis.AnalysisError):
                function(self.host, Path(self.tmp.name) / "unfinished.zip")
        self.assertFalse((Path(self.tmp.name) / "unfinished.zip").exists())


if __name__ == "__main__":
    unittest.main()
