"""Unpaid host integration using temporary evidence and scripted actors only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.triad_emergent_v9 import coordinator as hostmod
from experiments.triad_emergent_v9.design import make_plan
from experiments.triad_emergent_v9.reviewer_host import prepare, envelope
from experiments.triad_emergent_v9.tests.test_engine import ScriptedBackend, reviewer_answer


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "run"
        self.package = self.root / "package"
        self.package.mkdir()
        self.prior = self.root / "prior"
        (self.prior / "analysis").mkdir(parents=True)
        (self.prior / "freeze.json").write_text('{"source_files":{}}')
        (self.prior / "analysis/FINAL_INTEGRITY.json").write_text('{"status":"passed","fixture_only":true}')
        self.config_path = self.package / "config.json"
        self.config = hostmod.read_json(hostmod.HERE / "config.json")
        self.config_path.write_text(hostmod.canonical(self.config))
        self.sources = {"fixture_only.py": "a" * 64}
        self.gate = {"status": "passed", "source_files": self.sources,
                     "config_sha256": hostmod.file_sha(self.config_path)}
        (self.package / "WORKFLOW_GATE.json").write_text(hostmod.canonical(self.gate))
        self.snapshot = {"pending_count": 0, "halt_reason": None, "cap_usd": 40,
                         "fixture_only": True}
        self.patches = [patch.object(hostmod, "HERE", self.package),
                        patch.object(hostmod, "PRIOR_OUT", self.prior),
                        patch.object(hostmod, "source_manifest", return_value=self.sources),
                        patch.object(hostmod, "budget", return_value=self.snapshot),
                        patch.object(hostmod, "ledger_rows", return_value={"prior_fixture_row": "b" * 64})]
        for item in self.patches:
            item.start()
        plan = make_plan(seed=self.config["seed"], condition=self.config["condition"])
        self.backend = ScriptedBackend(self.out, plan)
        self.host = hostmod.Coordinator(self.out, config_path=self.config_path, backend=self.backend)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def actual_fixture_response(self, job):
        # This trace-shaped fixture tests host plumbing; it is never a review result.
        agent = f"/root/offline_fixture_review_{job['round']:02d}"
        launched = prepare(self.out, agent)
        self.host.dispatch(job["job_id"], agent)
        answer = reviewer_answer(hostmod.read_json(job["packet_path"]))
        raw = hostmod.canonical(answer)
        Path(launched["authored_output_path"]).write_text(raw)
        capture = self.root / f"actual_first_fixture_{job['round']:02d}.txt"
        capture.write_text(raw)
        result = envelope(self.out, agent, capture)
        return hostmod.read_json(result["envelope_path"])

    def test_init_is_offline_idempotent_and_source_bound(self):
        result = self.host.init()
        self.assertEqual(result["host"]["status"], "initialized")
        frozen = (self.out / "freeze.json").read_bytes()
        self.host.init()
        self.assertEqual((self.out / "freeze.json").read_bytes(), frozen)
        self.assertEqual(self.backend.requests, [])
        self.sources["changed.py"] = "c" * 64
        with self.assertRaises(ValueError):
            self.host.verify_freeze()

    def test_failed_gate_blocks_even_offline_initialization(self):
        self.gate["config_sha256"] = "0" * 64
        (self.package / "WORKFLOW_GATE.json").write_text(hostmod.canonical(self.gate))
        with self.assertRaises(ValueError):
            self.host.init()
        self.assertFalse(self.out.exists())

    def test_copied_provenance_cannot_change_first_actual_review(self):
        self.host.init()
        job = self.host.advance()
        original = self.actual_fixture_response(job)
        tampered = deepcopy(original)
        tampered["output"]["feedback"] = "Changed after first capture."
        with self.assertRaises(ValueError):
            self.host.ingest(tampered)
        self.assertEqual(self.host._engine().next_stage()["stage"], "REVIEWER")
        self.host.ingest(original)
        self.assertEqual(self.host._engine().next_stage()["stage"], "POSTVERDICT_A_LOG")

    def test_full_twelve_round_host_with_all_fixed_controls(self):
        self.host.init()
        for number in range(1, 13):
            job = self.host.advance()
            self.assertEqual(job["status"], "awaiting_codex")
            self.assertEqual(job["round"], number)
            self.host.ingest(self.actual_fixture_response(job))
        self.assertEqual(self.host.advance()["status"], "main_complete")
        self.assertEqual(self.host.verify()["engine"]["terminal_stages"], 96)
        # A final export cannot silently leave out the preregistered controls.
        with self.assertRaises(ValueError):
            self.host.export(self.root / "unfinished.zip")
        main_before = {str(p): p.read_bytes() for p in (self.out / "episode").rglob("*") if p.is_file()}
        controls = self.host.shadows()
        self.assertEqual(controls["observed_shadow_turns"], 24)
        self.assertTrue(controls["main_preservation_verified"])
        self.assertEqual(main_before, {str(p): p.read_bytes() for p in (self.out / "episode").rglob("*") if p.is_file()})
        self.assertEqual(self.host.report()["rounds"], 12)
        self.assertEqual(self.host.status()["host"]["status"], "complete")
        self.assertEqual(len(self.backend.requests), 84 + 24)


if __name__ == "__main__":
    unittest.main()
