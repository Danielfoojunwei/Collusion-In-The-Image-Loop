"""Sealing tests use temporary runs and injected accounting, never live writes."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from experiments.triad_emergent_v9 import coordinator as base
from experiments.triad_emergent_v9.design import make_plan
from experiments.triad_emergent_v9.engine import Engine
from experiments.triad_emergent_v9.tests.test_engine import ScriptedBackend
from experiments.triad_v9_protocol_repair01 import coordinator as repair


class RepairSealTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "run"
        self.gate = self.root / "repair_gate.json"
        self.accounting = {"pending_count": 0, "halt_reason": None, "fixture_only": True}
        self.rows = {"fixture_prior_row": "a" * 64}
        self.patches = [patch.object(base, "budget", side_effect=lambda: deepcopy(self.accounting)),
                        patch.object(base, "ledger_rows", side_effect=lambda: deepcopy(self.rows)),
                        patch.object(repair, "GATE", self.gate)]
        for item in self.patches:
            item.start()
        self.host = base.Coordinator(self.out)
        self.host.init()
        config = base.read_json(self.out / "config.json")
        plan = base.read_json(self.out / "plan.json")
        engine = Engine(self.out, config, plan, ScriptedBackend(self.out, plan))
        for _ in range(3):
            engine.run_next_actor()
        self.expected = base.file_sha(self.out / "freeze.json")
        self.gate.write_text(base.canonical({"status": "passed", "source_files": repair.source_manifest(),
            "original_workflow_gate_sha256": base.file_sha(base.HERE / "WORKFLOW_GATE.json"),
            "config_source_sha256": base.file_sha(base.HERE / "config.json")}))
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        self.assertEqual(process.wait(), 0)
        self.exit_path = self.root / "exit.json"
        self.exit_path.write_text(base.canonical({"producer_pid": process.pid, "exit_code": 0}))

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def seal(self, *, activate=False, temperature=2.0):
        return repair.seal(self.out, expected_initial_sha256=self.expected,
            producer_exit_path=self.exit_path, temperature=temperature,
            activation_phrase=repair.ACTIVATION_PHRASE if activate else None)

    def test_dry_run_has_no_active_freeze_or_scientific_effect(self):
        before = {str(p): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        result = self.seal()
        self.assertEqual(result["status"], "prospective_repair_ready_not_activated")
        self.assertEqual(base.file_sha(self.out / "freeze.json"), self.expected)
        self.assertFalse((self.out / "analysis/repair01").exists())
        for path, raw in before.items():
            self.assertEqual(Path(path).read_bytes(), raw)

    def test_activation_preserves_base_inputs_data_rows_sources_and_frontier(self):
        old_config = (self.out / "config.json").read_bytes()
        old_engine = (self.out / "episode/engine_manifest.json").read_bytes()
        old_freeze = (self.out / "freeze.json").read_bytes()
        old_episode = {str(p): p.read_bytes() for p in (self.out / "episode").rglob("*") if p.is_file()}
        result = self.seal(activate=True, temperature=1.0)
        self.assertEqual(result["status"], "repair_activated")
        self.assertEqual(result["frontier"]["stage"], "REVIEWER")
        self.assertEqual((self.out / "analysis/repair01/ORIGINAL_FREEZE.json").read_bytes(), old_freeze)
        self.assertEqual((self.out / "config.json").read_bytes(), old_config)
        self.assertEqual((self.out / "episode/engine_manifest.json").read_bytes(), old_engine)
        for path, raw in old_episode.items():
            self.assertEqual(Path(path).read_bytes(), raw)
        frozen = repair.verify_repair(self.out)
        self.assertEqual(frozen["protocol_repair01"]["policy"]["temperature"], 1.0)
        self.assertEqual(base.read_json(self.out / "config.json")["sampling"]["planning_temperature"], 2.0)
        host = repair.Coordinator(self.out)
        self.assertEqual(host._engine().summary()["terminal_stages"], 3)
        self.assertEqual(host.verify()["prior_ledger_rows_unchanged"], 1)
        with self.assertRaises(ValueError):
            self.host.verify_freeze()

    def test_pending_reservation_halt_live_producer_or_bad_gate_blocks_activation(self):
        for state in ({"pending_count": 1, "halt_reason": None},
                      {"pending_count": 0, "halt_reason": "fixture hold"}):
            self.accounting.update(state)
            with self.assertRaises(ValueError):
                self.seal(activate=True)
        self.accounting.update(pending_count=0, halt_reason=None)
        self.exit_path.write_text(base.canonical({"producer_pid": __import__("os").getpid(), "exit_code": 0}))
        with self.assertRaises(ValueError):
            self.seal(activate=True)
        self.exit_path.write_text(base.canonical({"producer_session": 123,
            "observed_exit_code": 0, "observed_final_host_status": "quitting", "active_producer": False}))
        gate = base.read_json(self.gate)
        gate["source_files"]["fake_changed.py"] = "0" * 64
        self.gate.write_text(base.canonical(gate))
        with self.assertRaises(ValueError):
            self.seal(activate=True)
        self.assertEqual(base.file_sha(self.out / "freeze.json"), self.expected)

    def test_unfinished_dispatch_cannot_be_reset_by_repair(self):
        path = self.out / "episode/rounds/r01/POSTVERDICT_A_LOG.dispatch.json"
        path.write_text('{"fixture_only":true}')
        with self.assertRaisesRegex(ValueError, "unfinished dispatched"):
            self.seal(activate=True)
        self.assertFalse((self.out / "analysis/repair01/AMENDMENT.json").exists())

    def test_committed_scientific_or_original_paid_record_tamper_is_detected(self):
        self.seal(activate=True)
        row = self.rows["fixture_prior_row"]
        self.rows["fixture_prior_row"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "paid request"):
            repair.verify_repair(self.out)
        self.rows["fixture_prior_row"] = row
        outcome = self.out / "episode/rounds/r01/A_IMAGE_AND_VOTE.outcome.json"
        outcome.write_bytes(outcome.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "scientific observation"):
            repair.verify_repair(self.out)


if __name__ == "__main__":
    unittest.main()
