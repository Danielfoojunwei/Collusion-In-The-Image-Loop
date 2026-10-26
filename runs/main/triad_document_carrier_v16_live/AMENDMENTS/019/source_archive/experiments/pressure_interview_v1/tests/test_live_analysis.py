"""Descriptive analysis tests use only temporary, explicitly labeled fixtures."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from experiments.pressure_interview_v1.contracts import ContractError, canonical
from experiments.pressure_interview_v1.live_analysis import build_report, verify_study, export_study, _verify_wire_records
from experiments.pressure_interview_v1.memory import EventStore
from experiments.pressure_interview_v1.planning import build_plan, load_config, ROOT
from visual_collusion.budget import BudgetLedger
from visual_collusion.protocol import public_case


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.out = self.base / "study"
        self.out.mkdir()
        self.config = deepcopy(load_config())
        self.ledger = BudgetLedger(self.base / "aggregate.sqlite3", 40)
        self.config["budget"]["aggregate_ledger"] = str(self.ledger.path)
        self.plan = build_plan()
        write(self.out / "config.json", self.config)
        write(self.out / "plan.json", self.plan)
        write(self.out / "freeze.json", {"evidence_origin": "fixture", "source_files": {},
                                         "config_sha256": digest(self.config), "plan_sha256": digest(self.plan)})
        self.ep = next(e for e in self.plan["episodes"] if e["condition"] == "explicit")
        self.case = next(c for c in self.plan["cases"] if c["case_id"] == self.ep["case_id"])

    def tearDown(self):
        self.temp.cleanup()

    def unit(self, branch):
        folder = self.out / "episodes" / self.ep["episode_id"] / branch
        store = EventStore(folder / "memory")
        path = store.path / "artifacts/card.png"
        path.parent.mkdir()
        Image.new("RGB", (1024, 1024), "white").save(path)
        image = {"path": "artifacts/card.png", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        store.append("initial.task", "environment", "task", {"brief": "Fixture only", "facts": public_case(self.case)})
        return folder, store, image

    def decision(self, store, image, branch, candidate, number=9):
        store.append(f"pair.{branch}.r{number}.B", "B", "b_public",
                     {"message": "Explicit software fixture", "image": image, "decision": candidate},
                     round_number=number, branch=branch)

    def test_early_primary_survives_no_finalized_unit_and_missing_sibling(self):
        folder, store, image = self.unit("branch_1")
        target = self.ep["branches"][0]["private_target"]
        self.decision(store, image, "branch_1", target)
        report = build_report(self.out)
        row = next(r for r in report["main_cases"] if r["case_id"] == self.case["case_id"])
        self.assertEqual(report["counts"]["primary_receptions_observed"], 1)
        self.assertTrue(row["branches"][0]["target_match"])
        self.assertIsNone(row["branches"][1]["target_match"])
        self.assertIsNone(row["target_dependence_theta"])
        self.assertEqual(len(report["main_cases"]), 6)
        self.assertEqual(len(report["controls"]), 4)
        self.assertFalse(report["live_scientific_results"])

    def test_pair_metric_and_shared_prefix_are_not_inflated(self):
        prefix, store, image = self.unit("prefix")
        self.decision(store, image, "prefix", self.case["correct_decision"], 1)
        for branch in self.ep["branches"]:
            folder = self.out / "episodes" / self.ep["episode_id"] / branch["branch_id"]
            child = store.fork(folder / "memory")
            self.decision(child, image, branch["branch_id"], branch["private_target"])
        report = build_report(self.out)
        row = next(r for r in report["main_cases"] if r["case_id"] == self.case["case_id"])
        self.assertTrue(row["both_branches_observed"])
        self.assertEqual(row["target_dependence_theta"], 1)
        self.assertEqual(report["counts"]["B_deliveries_observed"], 3)
        self.assertIsNone(row["intact_minus_neutral_target_matches"])

    def test_tampered_memory_fails_without_repairing_it(self):
        folder, store, image = self.unit("prefix")
        self.decision(store, image, "prefix", "P1", 1)
        before = store.log_path.read_bytes().replace(b"Explicit software fixture", b"Modified software fixture")
        store.log_path.write_bytes(before)
        with self.assertRaises(ContractError):
            verify_study(self.out)
        self.assertEqual(store.log_path.read_bytes(), before)

    def test_aggregate_scope_counts_old_unknown_and_subset_once(self):
        self.ledger.reserve("old.completed", "chat", "fixture", "a" * 64, 1)
        self.ledger.complete("old.completed", 0.7, {}, {})
        self.ledger.reserve("old.unknown", "chat", "fixture", "b" * 64, 0.1)
        self.ledger.uncertain("old.unknown", "Fixture unknown cost")
        # A reserved unknown blocks further financial work; direct SQL here is
        # unnecessary. The subset is empty and the old uncertainty still counts.
        report = build_report(self.out)
        budget = report["budget"]
        self.assertEqual(budget["actual_usd"], 0.7)
        self.assertEqual(budget["reserved_unresolved_usd"], 0.1)
        self.assertEqual(budget["committed_usd"], 0.8)
        self.assertEqual(budget["new_namespace_subset"]["actual_usd"], 0)
        self.assertFalse(budget["unknown_charges_asserted_zero"])

    def test_stage_input_binding_is_checked(self):
        folder, store, image = self.unit("prefix")
        write(folder / "requests/fake.json", {"request_id": "fixture.stage", "stage": "A_OUTPUT", "request_hash": "0" * 64})
        with self.assertRaises(ContractError):
            verify_study(self.out)

    def test_scheduler_author_and_C_packets_verify_exact_saved_bytes(self):
        from experiments.pressure_interview_v1.live_scheduler import StudyCoordinator
        from experiments.pressure_interview_v1.tests.test_live_scheduler import Backend, answer, PROVENANCE
        folder = self.base / "scheduler_packets"
        coordinator = StudyCoordinator(folder, config=self.config, plan=self.plan,
            backend=Backend(), evidence_origin="fixture")
        first_packet = None
        for _ in range(12):
            job = coordinator.advance()
            self.assertEqual(job["status"], "awaiting_codex")
            first_packet = first_packet or Path(job["packet_path"])
            actor = "/root/offline_packet_fixture/" + job["job_id"]
            coordinator.mark_codex_dispatched(job["job_id"], actor)
            coordinator.import_codex_response(job["job_id"], answer(job), deepcopy(PROVENANCE))
        checks = verify_study(folder)
        self.assertEqual(checks["resolved_codex_jobs"], 12)
        self.assertEqual(checks["saved_stages"], 5)
        # Valid JSON with different bytes must still violate the exact binding.
        first_packet.write_bytes(first_packet.read_bytes() + b" ")
        with self.assertRaises(ContractError):
            verify_study(folder)

    def test_actual_wire_mapping_must_match_aggregate_intent(self):
        spec = importlib.util.spec_from_file_location("fixture_wire_codec", ROOT / "scripts/lossless_wire_codec.py")
        codec = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(codec)
        evidence = self.out / "provider_evidence"
        payload = {"model": "z-ai/glm-5.3-flash", "messages": [{"role": "user", "content": "Fixture only"}]}
        wire, mapping = codec.prepare_wire(evidence, "fixture.call", "/chat/completions", payload)
        body_hash = hashlib.sha256(canonical(wire).encode()).hexdigest()
        request_hash = digest({"endpoint": "/chat/completions", "wire_body_sha256": body_hash})
        identifier = self.config["budget"]["namespace"] + ":fixture.call"
        self.ledger.reserve(identifier, "chat", payload["model"], request_hash, 0.1)
        self.ledger.complete(identifier, 0.01, {"fixture": True},
                             {"request_id": identifier, "request_hash": request_hash,
                              "kind": "chat", "requested_model": payload["model"], "cost_usd": 0.01})
        write(evidence / "transport_requests/call.json", {"request_id": "fixture.call", "endpoint": "/chat/completions",
                                                       "logical_payload": payload, "mapping": mapping})
        result = _verify_wire_records(self.out, self.config)
        self.assertEqual(result["paid_intents"], 1)
        record = json.loads((evidence / "transport_requests/call.json").read_text())
        record["mapping"]["wire_body_sha256"] = "0" * 64
        write(evidence / "transport_requests/call.json", record)
        with self.assertRaises(Exception):
            _verify_wire_records(self.out, self.config)
        record["mapping"] = mapping
        write(evidence / "transport_requests/call.json", record)
        with sqlite3.connect(self.ledger.path) as db:
            db.execute("UPDATE requests SET response_json=? WHERE request_id=?", ('{"changed":true}', identifier))
        with self.assertRaises(ContractError):
            _verify_wire_records(self.out, self.config)

    def test_export_contains_consistent_aggregate_snapshot_and_fixture_label(self):
        path = self.base / "results.zip"
        result = export_study(self.out, path)
        self.assertEqual(result["evidence_origin"], "fixture")
        with zipfile.ZipFile(path) as z:
            self.assertIn("analysis/aggregate_budget_snapshot.sqlite3", z.namelist())
            report = json.loads(z.read("analysis/report.json"))
            self.assertFalse(report["live_scientific_results"])
            scope = json.loads(z.read("analysis/aggregate_snapshot_scope.json"))
            self.assertEqual(hashlib.sha256(z.read("analysis/aggregate_budget_snapshot.sqlite3")).hexdigest(), scope["sha256"])


if __name__ == "__main__":
    unittest.main()
