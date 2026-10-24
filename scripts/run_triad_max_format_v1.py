"""Offline post-stage format recovery and prospective continuation of the same run.

All original outcomes, role inputs, reviewer reports, pixels and receipts remain
immutable. Only existing B1 private fields and C1's literal delivery are exposed
in future allowlisted contexts; no replacement actor call is made.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.triad_max_pressure_v3 import coordinator as original
from experiments.triad_max_pressure_v3.cli import command as original_command
from experiments.triad_max_pressure_v3.contracts import ContractError
from experiments.triad_max_pressure_v3.engine import TriadEngine, _validate_png
from visual_collusion.budget import _units
from scripts import triad_response_format_v4 as frozen_parser

ADAPTER_PATH = ROOT / "scripts/triad_max_format_v1.py"
AMENDMENT_PATH = "execution_notes/triad_max_format_v1_amendment.json"
OPERATIONAL_GATE = "execution_notes/triad_max_format_v1_operational_gate.json"
SUPPLEMENTS = {role: f"execution_notes/format_v1/round01_{role}_technical_supplement.json" for role in "BC"}
NAMESPACE = "triad_max_pressure_v3"
CASE_ID = "case_05_max"
C_IMAGE_SHA256 = "f96ec4855e1dabfa251066e544317a50b92a6179945fc4c412357e5d31bd2d6f"
EXPECTED_PAUSED_REQUESTS = 16


def _require(value, message):
    if not value:
        raise ContractError(message)


def _safe_file(path, base=None):
    path = Path(path).absolute()
    _require(not any(part.is_symlink() for part in (path, *path.parents)), "Evidence must not follow a symlink")
    _require(path.is_file(), "Required evidence file is missing")
    if base is not None:
        _require(path.is_relative_to(Path(base).absolute()), "Evidence escaped its declared directory")
    return path


def _adapter_module():
    adapter = importlib.import_module("scripts.triad_max_format_v1")
    _require(Path(adapter.__file__).resolve() == ADAPTER_PATH.resolve(), "Format helper resolves to a different module")
    return adapter


def completed_snapshot(ledger):
    """Verify all namespace receipts, retaining older reservations elsewhere."""
    path = _safe_file(ledger)
    result = {}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        cap = db.execute("SELECT value FROM settings WHERE key='cap_units'").fetchone()
        _require(cap and int(cap[0]) == 40_000_000_000, "Original aggregate cap changed")
        rows = db.execute("SELECT * FROM requests WHERE request_id LIKE ? ORDER BY request_id", (NAMESPACE + ":%",)).fetchall()
        for row in rows:
            _require(row["status"] == "completed", "Format activation cannot bypass unresolved paid work")
            checksum = hashlib.sha256((row["response_json"] + "\n" + row["receipt_json"]).encode()).hexdigest()
            _require(checksum == row["result_hash"], "Original paid response/receipt checksum changed")
            receipt, response = json.loads(row["receipt_json"]), json.loads(row["response_json"])
            _require(receipt.get("request_id") == row["request_id"] and receipt.get("request_hash") == row["request_hash"], "Paid request binding changed")
            _require(receipt.get("requested_model") == row["model"] and receipt.get("kind") == row["kind"], "Paid model/kind binding changed")
            _require(_units(receipt.get("cost_usd")) == row["actual_units"], "Original paid amount changed")
            _require(response.get("id") == receipt.get("response_id") and response.get("usage") == receipt.get("usage"), "Raw paid response identity/usage changed")
            result[row["request_id"]] = {"result_hash": checksum, "request_hash": row["request_hash"],
                "actual_units": row["actual_units"], "model": row["model"], "kind": row["kind"]}
    return result


def preserved_files(out):
    """Hash every current immutable file; exclude mutable status and analysis."""
    out = Path(out)
    ignored = {"run.lock", "run_status.json", AMENDMENT_PATH, OPERATIONAL_GATE}
    return {str(path.relative_to(out)): original.file_digest(_safe_file(path, out))
            for path in sorted(out.rglob("*")) if path.is_file()
            and str(path.relative_to(out)) not in ignored and "analysis" not in path.relative_to(out).parts
            and not path.name.endswith(("-wal", "-shm"))}


def recovered_poststage(ledger, role):
    _require(role in "BC" and len(role) == 1, "Only original B/C post-stages have supplements")
    suffix = "POSTVERDICT_B_LOG.step1" if role == "B" else "POSTVERDICT_C_IMAGE_LOG.step4"
    mode = "memory_only" if role == "B" else "image_memory"
    identifier = f"{NAMESPACE}:{CASE_ID}.r01.{suffix}"
    return _adapter_module().recover_completed_poststage(frozen_parser.completed_row(ledger, identifier), mode)


def prove_original_C_image(out, snapshot):
    """Bind the explicit delivery to its sole actual latest generated draft."""
    outcome = original.read_json(Path(out) / f"cases/{CASE_ID}/rounds/r01/POSTVERDICT_C_IMAGE_LOG.outcome.json")
    _require(outcome["status"] == "invalid_model_output" and outcome["output"] is None, "Original C failure must remain unavailable")
    trace = outcome.get("action_log", [])
    generations = [entry for entry in trace if entry.get("generated_image")]
    _require(len(generations) == 1, "C recovery requires exactly the observed sole current draft")
    generated = generations[0]
    _require(generated.get("step") == 1 and generated.get("generation_attempt") == 1, "C latest draft has an unexpected generation history")
    _require(generated.get("output", {}).get("action") == "generate_image", "C draft is not bound to an actual generation action")
    _require(not any(entry.get("step", 0) > 1 and (entry.get("output") or {}).get("action") == "generate_image" for entry in trace), "C has a later generation that could change its selected draft")
    _require(any(entry.get("step") == 4 and entry.get("receipt_id") == f"{NAMESPACE}:{CASE_ID}.r01.POSTVERDICT_C_IMAGE_LOG.step4" for entry in trace), "Original terminal C action is missing")
    image = generated["generated_image"]
    _require(image.get("sha256") == C_IMAGE_SHA256, "C draft differs from the original archived PNG")
    path = _safe_file(image["path"], out)
    _validate_png(path, C_IMAGE_SHA256)
    receipt_id = generated.get("generated_image_receipt_id")
    _require(receipt_id == f"{NAMESPACE}:{CASE_ID}.r01.POSTVERDICT_C_IMAGE_LOG.image1", "C image receipt binding changed")
    _require(receipt_id in snapshot and snapshot[receipt_id]["kind"] == "image", "C image lacks a verified original paid receipt")
    return {"path": str(path), "sha256": C_IMAGE_SHA256, "original_image_receipt_id": receipt_id,
            "sole_latest_generated_draft": True, "original_failed_stage_preserved": True}


class ProspectiveRecoveryEngine(TriadEngine):
    def __init__(self, directory, config, case, *, recovery_loader, amendment_checker):
        self._recovery_loader = recovery_loader
        self._amendment_checker = amendment_checker
        super().__init__(directory, config, case)

    def _verify_manifest(self):
        super()._verify_manifest()
        self._amendment_checker()

    def _valid_output(self, number, stage):
        output = super()._valid_output(number, stage)
        if output is None and number == 1 and stage == "POSTVERDICT_C_IMAGE_LOG":
            return deepcopy(self._recovery_loader("C")["output"])
        return output

    def _own_logs(self, number, role):
        logs = deepcopy(super()._own_logs(number, role))
        if number < 2 or role not in {"B", "C"}:
            return logs
        for index, record in enumerate(logs):
            if record.get("round") == 1 and record.get("carryover_log_unavailable") is True:
                recovered = self._recovery_loader(role)
                value = recovered["output"]
                logs[index] = {"round": 1, "private_carryover_log": value["private_note"],
                    "own_image_review": deepcopy(value["image_review"]), "technical_recovery": deepcopy(recovered["technical_recovery"])}
                break
        return logs

    def _reviewer_payload(self, number, image_copies):
        packet = super()._reviewer_payload(number, image_copies)
        if number == 2:
            packet["technical_image_availability_note"] = {
                "speaker": "C", "source_round": 1, "received_by_A_in_round": 2,
                "image_sha256": C_IMAGE_SHA256,
                "note": "The original post-round-1 C stage was format-invalid. Its literal existing deliver action was recovered locally before round 2, without another model call or changing pixels. Original outcomes and reviewer 1 remain unchanged."}
        return packet


class FormatAmendedCoordinator(original.TriadCoordinator):
    def _amendment_path(self):
        return self.out / AMENDMENT_PATH

    def _ledger_path(self):
        ledger = (ROOT / self.config["budget"]["aggregate_ledger"]).absolute()
        if self.evidence_origin == "live":
            _require(ledger == ROOT / "runs/main/budget.sqlite3", "Must retain the original aggregate ledger")
        return ledger

    def _plain_engine(self, case):
        config = deepcopy(self.config)
        prompts = original.load_prompts(case["condition"])
        config.update({"prompts": prompts, "reviewer_prompt": prompts["reviewer"], "evidence_origin": self.evidence_origin})
        return TriadEngine(self.out / "cases" / case["case_id"], config, case)

    def _engine(self, case):
        if case["case_id"] not in self._engines:
            config = deepcopy(self.config)
            prompts = original.load_prompts(case["condition"])
            config.update({"prompts": prompts, "reviewer_prompt": prompts["reviewer"], "evidence_origin": self.evidence_origin})
            self._engines[case["case_id"]] = ProspectiveRecoveryEngine(
                self.out / "cases" / case["case_id"], config, case,
                recovery_loader=self._recovery_for, amendment_checker=self.verify_format_amendment)
        return self._engines[case["case_id"]]

    def _recovery_for(self, role):
        self.verify_format_amendment(require_operational_gate=False)
        _require(role in {"B", "C"}, "Private recovery is confined to B/C's own existing fields")
        return original.read_json(self.out / SUPPLEMENTS[role])

    def prepare_format_amendment(self):
        if self._amendment_path().exists():
            return self.verify_format_amendment()
        self._verify_freeze()
        self._require_workflow_gate()
        engine = self._plain_engine(self.selected_cases[0])
        target = engine.next_stage()
        _require(target and target["round"] == 2 and target["stage"] == "A_IMAGE_AND_VOTE", "Recovery must bind the pause before any round-2 actor input")
        _require(not list((engine.directory / "rounds/r02").glob("*")), "A round-2 request already exists")
        snapshot = completed_snapshot(self._ledger_path())
        _require(len(snapshot) == EXPECTED_PAUSED_REQUESTS and all(".r01." in key for key in snapshot), "The original sixteen completed round-1 calls must be preserved")
        for stage in ("POSTVERDICT_A_LOG", "POSTVERDICT_B_LOG", "POSTVERDICT_C_IMAGE_LOG"):
            _require(engine.outcome(1, stage)["status"] == "invalid_model_output", "Original three post-stage failures changed")
        image = prove_original_C_image(self.out, snapshot)
        bindings = {}
        for role in "BC":
            recovered = recovered_poststage(self._ledger_path(), role)
            value = recovered["context_value"] if role == "B" else recovered["context_value"]["arguments"]
            output = deepcopy(value)
            if role == "C":
                output["image"] = {"path": image["path"], "sha256": image["sha256"]}
            supplement = {"revision": "triad_max_format_v1", "own_role": role,
                "original_paid_recovery": recovered, "output": output,
                "technical_recovery": {"revision": "triad_max_format_v1", "source_round": 1,
                    "own_role": role, "learned_before_round_2_inputs": True,
                    "original_invalid_outcome_preserved": True, "no_new_POST": True}}
            path = self.out / SUPPLEMENTS[role]
            original.save_once(path, supplement)
            bindings[role] = {"path": str(path), "sha256": original.file_digest(path),
                             "request_id": recovered["request_id"], "original_result_hash": recovered["original_result_hash"]}
        runtime_snapshots = {}
        for name, source in (("runner", Path(__file__).absolute()), ("helper", ADAPTER_PATH)):
            destination = self.out / "execution_notes/format_v1/runtime_code" / source.name
            original.copy_exact_once(source, destination)
            runtime_snapshots[name] = {"path": str(destination), "sha256": original.file_digest(source)}
        note = {"revision": "triad_max_format_v1", "runner_sha256": original.file_digest(__file__),
            "helper_sha256": original.file_digest(_safe_file(ADAPTER_PATH)),
            "runtime_source_snapshots": runtime_snapshots,
            "original_freeze_sha256": original.file_digest(self.out / "freeze.json"),
            "original_source_files": original.source_manifest(), "ledger_path": str(self._ledger_path()),
            "completed_requests_before_activation": snapshot, "preserved_pre_activation_files": preserved_files(self.out),
            "supplements": bindings, "C_image_binding": image,
            "activation": {"round": 2, "stage": "A_IMAGE_AND_VOTE", "round_2_inputs_not_issued": True},
            "allowed": ["Exact private generated_role_assessment key alias with existing text preserved", "Unique complete terminal C image_memory delivery after brace-free prose", "Future exact terminal memory_only object after brace-free prose", "Prospective same-role B/C carryover and observed C1 raster to A2/reviewer2"],
            "unchanged": ["Every old request, response, outcome, original gate and reviewer report", "All frozen prompts/models/routes/sampling/reasoning/action allowances", "All ballots, generation prompts/pixels and aggregate budget"],
            "A1_private_note": "Still unavailable; no note inferred from partial assessment", "no_POST_during_preparation": True}
        original.save_once(self._amendment_path(), note)
        self.verify_format_amendment(require_operational_gate=False)
        # A read-only preview binds actual prospective routing before execution.
        config = deepcopy(engine.config)
        preview = ProspectiveRecoveryEngine(engine.directory, config, engine.case,
            recovery_loader=self._recovery_for,
            amendment_checker=lambda: self.verify_format_amendment(require_operational_gate=False))
        request = preview.actor_request()
        view = json.loads(request["messages"][1]["content"])
        own_b = preview._own_logs(2, "B")
        own_c = preview._own_logs(2, "C")
        all_a_images = {value["sha256"] for value in request["image_refs"].values()}
        check = {"exact_C1_pixels_in_next_A": C_IMAGE_SHA256 in all_a_images,
                 "C1_marker_available": view["incoming_C_image"]["available"] is True,
                 "A1_note_stays_unavailable": any(x.get("carryover_log_unavailable") is True for x in view["own_carryover_logs"]),
                 "A_view_has_no_B_C_private_logs": all(x.get("carryover_log_unavailable") is True for x in view["own_carryover_logs"]),
                 "B1_only_B_own_log_recovered": own_b[0]["technical_recovery"]["own_role"] == "B",
                 "C1_only_C_own_log_recovered": own_c[0]["technical_recovery"]["own_role"] == "C",
                 "full_actual_review1_preserved": view["previous_reviewer_feedback"]["reviewer_report"] == engine.outcome(1, "REVIEWER")["output"],
                 "original_three_invalid_outcomes_preserved": all(engine.outcome(1, s)["status"] == "invalid_model_output" for s in ("POSTVERDICT_A_LOG", "POSTVERDICT_B_LOG", "POSTVERDICT_C_IMAGE_LOG")),
                 "same_aggregate_budget": self._ledger_path() == Path(note["ledger_path"])}
        _require(all(check.values()), "Prospective routing gate failed")
        original.save_once(self.out / OPERATIONAL_GATE, {"status": "passed", "checks": check,
            "amendment_sha256": original.file_digest(self._amendment_path()), "prospective_A2_request_hash": request["request_hash"],
            "no_POST": True, "historical_gate_unchanged": True})
        self._engines.clear()
        return self.verify_format_amendment()

    def verify_format_amendment(self, *, require_operational_gate=True):
        note = original.read_json(_safe_file(self._amendment_path(), self.out))
        _require(note["runner_sha256"] == original.file_digest(__file__), "Format runner changed after activation")
        _require(note["helper_sha256"] == original.file_digest(_safe_file(ADAPTER_PATH)), "Format helper changed after activation")
        for name, source in (("runner", Path(__file__).absolute()), ("helper", ADAPTER_PATH)):
            binding = note["runtime_source_snapshots"][name]
            _require(original.file_digest(_safe_file(binding["path"], self.out)) == binding["sha256"] == original.file_digest(source), "Runtime reproduction snapshot changed")
        _require(note["original_freeze_sha256"] == original.file_digest(self.out / "freeze.json"), "Original freeze changed")
        _require(note["original_source_files"] == original.source_manifest(), "A frozen source changed")
        _require(Path(note["ledger_path"]) == self._ledger_path(), "Original aggregate changed")
        snapshot = completed_snapshot(self._ledger_path())
        for key, binding in note["completed_requests_before_activation"].items():
            _require(snapshot.get(key) == binding, "A pre-activation paid response changed")
        for relative, sha in note["preserved_pre_activation_files"].items():
            _require(original.file_digest(_safe_file(self.out / relative, self.out)) == sha, "An immutable pre-activation file changed")
        _require(prove_original_C_image(self.out, snapshot) == note["C_image_binding"], "Original C image proof changed")
        for role in "BC":
            binding = note["supplements"][role]
            path = _safe_file(binding["path"], self.out)
            _require(original.file_digest(path) == binding["sha256"], "A private supplement changed")
            supplied = original.read_json(path)
            recovered = recovered_poststage(self._ledger_path(), role)
            _require(supplied["original_paid_recovery"] == recovered, "Supplement differs from original literal paid fields")
            value = recovered["context_value"] if role == "B" else recovered["context_value"]["arguments"]
            expected = deepcopy(value)
            if role == "C":
                expected["image"] = {k: note["C_image_binding"][k] for k in ("path", "sha256")}
            _require(supplied["output"] == expected and supplied["own_role"] == role, "A supplement changed role or semantic content")
        if require_operational_gate:
            gate = original.read_json(_safe_file(self.out / OPERATIONAL_GATE, self.out))
            _require(gate["status"] == "passed" and all(gate["checks"].values()) and gate["no_POST"] is True
                and gate["amendment_sha256"] == original.file_digest(self._amendment_path()), "Operational recovery gate changed or failed")
        return {"status": "max_format_amendment_verified", "pre_activation_requests_preserved": len(note["completed_requests_before_activation"]),
            "current_completed_requests": len(snapshot), "C1_image_sha256": C_IMAGE_SHA256,
            "original_three_invalid_stages_preserved": True, "A1_note_still_missing": True, "no_POST": True}

    def _backend(self):
        self.verify_format_amendment()
        adapter = _adapter_module()
        if self.backend is None:
            self.backend = adapter.FormatV1Backend(self.config, self.out, self._ledger_path())
        _require(isinstance(self.backend, adapter.FormatV1Backend), "An older backend cannot bypass the declared format amendment")
        return self.backend

    def verify(self):
        result = super().verify()
        result["max_format_amendment"] = self.verify_format_amendment()
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "serve", "advance", "status", "budget", "verify", "report", "export", "dispatch", "ingest"])
    parser.add_argument("--response", type=Path)
    parser.add_argument("--job-id")
    parser.add_argument("--reviewer-agent")
    parser.add_argument("--target", type=Path)
    args = parser.parse_args(argv)
    coordinator = FormatAmendedCoordinator()
    if args.command == "prepare":
        print(original.canonical(coordinator.prepare_format_amendment()), flush=True)
        return 0
    coordinator.verify_format_amendment()
    if args.command == "serve":
        print(original.canonical({"status": "ready", "revision": "triad_max_format_v1", "same_run": True}), flush=True)
        for line in sys.stdin:
            try:
                value = json.loads(line)
                result = original_command(coordinator, value)
            except Exception as error:
                print(original.canonical({"status": "host_error", "exception_class": type(error).__name__, "reason": "Inspect retained evidence; no automatic paid retry"}), flush=True)
                continue
            print(original.canonical(result), flush=True)
            if value.get("command") in {"quit", "exit"}:
                return 0
        return 0
    value = {"command": args.command}
    for field, argument in (("response_path", args.response), ("job_id", args.job_id), ("reviewer_agent", args.reviewer_agent), ("target", args.target)):
        if argument is not None:
            value[field] = str(argument)
    print(original.canonical(original_command(coordinator, value)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
