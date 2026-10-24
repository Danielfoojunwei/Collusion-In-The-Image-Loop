"""Parent-snapshot, clean-frontier and preservation tests; no live output writes."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from experiments.triad_emergent_v9 import coordinator as base
from experiments.triad_emergent_v9.engine import Engine
from experiments.triad_emergent_v9.offline_gate import test_manifest
from experiments.triad_emergent_v9.reviewer_host import prepare, envelope
from experiments.triad_emergent_v9.tests.test_engine import ScriptedBackend, reviewer_answer
from experiments.triad_v9_protocol_repair01 import coordinator as parent
from experiments.triad_v9_protocol_repair01.tests import test_coordinator as parent_fixture
from experiments.triad_v9_protocol_repair02 import coordinator as repair


class SamplingSealTests(unittest.TestCase):
    def setUp(self):
        parent_fixture.RepairSealTests.setUp(self)
        parent.seal(self.out, expected_initial_sha256=self.expected,
                    producer_exit_path=self.exit_path, temperature=2.0,
                    activation_phrase=parent.ACTIVATION_PHRASE)
        host = parent.Coordinator(self.out)
        job = host.advance()
        agent = "/root/offline_sampling_repair_fixture"
        launched = prepare(self.out, agent)
        host.dispatch(job["job_id"], agent)
        raw = base.canonical(reviewer_answer(base.read_json(job["packet_path"])))
        Path(launched["authored_output_path"]).write_text(raw)
        capture = self.root / "first_fixture.txt"
        capture.write_text(raw)
        captured = envelope(self.out, agent, capture)
        host.ingest(base.read_json(captured["envelope_path"]))
        config, plan = base.read_json(self.out / "config.json"), base.read_json(self.out / "plan.json")
        def invalid_log(request, result):
            if request["mode"] == "log":
                result["status"] = "model_invalid"
                result["component_status"]["log"] = "model_invalid"
                result["output"]["carryover_note"] = result["output"]["image_reflection"] = ""
        engine = Engine(self.out, config, plan, ScriptedBackend(self.out, plan, policy=invalid_log))
        engine.run_next_actor()
        self.parent_sha = base.file_sha(self.out / "freeze.json")
        self.target = engine.next_stage()
        self.assertEqual(self.target["stage"], "POSTVERDICT_B_LOG")
        self.gate2 = self.root / "gate2.json"
        self.patches.append(patch.object(repair, "GATE", self.gate2))
        self.patches[-1].start()
        self.gate2.write_text(base.canonical({"status": "passed", "source_files": repair.source_manifest(),
            "test_files": test_manifest(), "parent_gate_sha256": base.file_sha(parent.GATE),
            "original_gate_sha256": base.file_sha(base.HERE / "WORKFLOW_GATE.json"),
            "config_source_sha256": base.file_sha(base.HERE / "config.json")}))
        self.exit2 = self.root / "exit2.json"
        self.exit_record = {"active_producer": False, "observed_exit_code": 0,
            "observed_final_host_status": "one_original_stage_complete", "producer_session": 27032,
            "next_stage": self.target, "terminal_main_stages": 5,
            "budget": {"request_count": len(self.rows), "pending_count": 0}, "fixture_only": True}
        self.exit2.write_text(base.canonical(self.exit_record))
        self.patches.append(patch.object(parent, "verify_repair", side_effect=AssertionError("Do not invoke old current-freeze verifier")))
        self.patches[-1].start()

    def tearDown(self):
        parent_fixture.RepairSealTests.tearDown(self)

    def seal(self, activate=False):
        return repair.seal(self.out, expected_parent_sha256=self.parent_sha,
            producer_exit_path=self.exit2, activation_phrase=repair.ACTIVATION_PHRASE if activate else None)

    def test_dry_run_preserves_parent_and_activation_resumes_only_b1_log(self):
        before = {str(p): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(self.seal()["status"], "repair02_ready_not_activated")
        self.assertFalse((self.out / "analysis/repair02").exists())
        result = self.seal(True)
        self.assertEqual(result["frontier"], self.target)
        self.assertEqual(result["policy"]["temperature"], 1.0)
        for name, raw in before.items():
            if name != str(self.out / "freeze.json"):
                self.assertEqual(Path(name).read_bytes(), raw)
        self.assertEqual((self.out / "analysis/repair02/PARENT_FREEZE.json").read_bytes(), before[str(self.out / "freeze.json")])
        host = repair.Coordinator(self.out)
        self.assertEqual(host.verify()["engine"]["terminal_stages"], 5)
        self.assertEqual(host._engine().next_stage(), self.target)
        self.assertEqual(base.read_json(self.out / "config.json")["sampling"]["planning_temperature"], 2.0)
        self.assertEqual(host.status()["protocol_repair02"]["policy"]["temperature"], 1.0)
        self.assertEqual(host.report()["rounds"], 1)
        report = base.read_json(self.out / "analysis/report.json")
        self.assertEqual(report["protocol_repair01"]["policy"]["temperature"], 2.0)
        self.assertEqual(report["protocol_repair02"]["policy"]["temperature"], 1.0)

    def test_old_exit_or_pending_charge_blocks_without_activation(self):
        old = dict(self.exit_record, producer_session=66007, terminal_main_stages=4,
                   next_stage={"stage": "POSTVERDICT_A_LOG"})
        self.exit2.write_text(base.canonical(old))
        with self.assertRaisesRegex(ValueError, "current producer"):
            self.seal(True)
        self.exit2.write_text(base.canonical(self.exit_record))
        self.accounting["pending_count"] = 1
        with self.assertRaisesRegex(ValueError, "reservation"):
            self.seal(True)
        self.assertFalse((self.out / "analysis/repair02/AMENDMENT.json").exists())

    def test_parent_artifacts_and_prior_paid_records_remain_protected(self):
        self.seal(True)
        repair.verify_repair(self.out)
        self.rows["fixture_prior_row"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "paid record"):
            repair.verify_repair(self.out)
        self.rows["fixture_prior_row"] = "a" * 64
        path = self.out / "analysis/repair01/AMENDMENT.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            repair.verify_repair(self.out)


if __name__ == "__main__":
    unittest.main()
