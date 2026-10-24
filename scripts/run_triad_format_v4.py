"""Explicit format-recovery adapter for the same paused twelve-round run.

The sole paid-backend change is selecting FormatV4Backend. Frozen sources,
prompts, cases, stage order, old answers and queued reviewer packets are retained.
Preparation is offline; r1's separately recovered explicit P4 ballot is an
addendum learned after r2 ballots, never a replacement r1 answer or reviewer.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_triad_gate_v3 as gate_v3
from experiments.triad_consensus_v2 import coordinator as original
from experiments.triad_consensus_v2.cli import command as original_command
from experiments.triad_consensus_v2.contracts import ContractError
from visual_collusion.budget import _units


ADAPTER_PATH = ROOT / "scripts/triad_response_format_v4.py"
AMENDMENT_PATH = "execution_notes/triad_response_format_v4_amendment.json"
EXPECTED_PAUSED_REQUESTS = 19
FROZEN_GATE_V3_SHA256 = "9840591a398f8dce46fc18b2c2a876afdf6dffa59d11cad5d6cc23722c4dac67"


def _require(condition, reason):
    if not condition:
        raise ContractError(reason)


def _adapter_module():
    adapter = importlib.import_module("triad_response_format_v4")
    _require(Path(adapter.__file__).resolve() == ADAPTER_PATH.resolve(), "Format backend resolves to a different module")
    return adapter


def verify_first_C_supplement(ledger, supplement):
    """Reproduce the bounded technical recovery while keeping explicit P4."""
    adapter = _adapter_module()
    identifier = "triad_consensus_v2:case_05.r01.C_VOTE.step1"
    recovered = adapter.recover_completed_response(adapter.completed_row(ledger, identifier), "vote_only")
    _require(recovered["parsed_original_value"].get("decision") == "P4", "Explicit first-C decision must remain the actual P4 field")
    note = original.read_json(supplement)
    for key in ("request_id", "original_result_hash", "parsed_original_value", "context_value", "syntax_flags", "original_text_sha256"):
        _require(note.get(key) == recovered[key], "First-C supplement does not reproduce the exact completed raw answer")
    _require(note.get("explicit_decision") == "P4" and note.get("decision_inferred_or_corrected") is False, "First-C supplement inferred or changed the decision")
    return recovered


def completed_snapshot(ledger_path):
    """Snapshot completed namespace rows; every checksum and receipt stays bound."""
    ledger = gate_v3._safe_file(ledger_path)
    result = {}
    with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        cap = db.execute("SELECT value FROM settings WHERE key='cap_units'").fetchone()
        _require(cap and int(cap[0]) == 40_000_000_000, "Original aggregate cap changed")
        rows = db.execute("SELECT * FROM requests WHERE request_id LIKE 'triad_consensus_v2:%' ORDER BY request_id").fetchall()
        for row in rows:
            _require(row["status"] == "completed", "Format activation cannot bypass unresolved paid work")
            checksum = hashlib.sha256((row["response_json"] + "\n" + row["receipt_json"]).encode()).hexdigest()
            _require(checksum == row["result_hash"], "Original raw response/receipt checksum changed")
            receipt, response = json.loads(row["receipt_json"]), json.loads(row["response_json"])
            _require(receipt.get("request_id") == row["request_id"] and receipt.get("request_hash") == row["request_hash"], "Original paid request binding differs")
            _require(receipt.get("requested_model") == row["model"] and receipt.get("kind") == row["kind"], "Original model/kind binding differs")
            _require(_units(receipt.get("cost_usd")) == row["actual_units"], "Original paid amount differs")
            _require(response.get("id") == receipt.get("response_id") and response.get("usage") == receipt.get("usage"), "Raw paid response identity/usage differs")
            result[row["request_id"]] = {"result_hash": checksum, "request_hash": row["request_hash"],
                "actual_units": row["actual_units"], "model": row["model"], "kind": row["kind"]}
    return result


class FormatAmendedCoordinator(gate_v3.GateAmendedCoordinator):
    def _format_amendment_path(self):
        return self.out / AMENDMENT_PATH

    def prepare_format_amendment(self, supplement_path, addendum_path=None):
        """Bind the existing pause and correction files without calling any actor."""
        if self._format_amendment_path().exists():
            return self.verify_format_amendment()
        self.verify_amendment()
        _require(original.file_digest(Path(gate_v3.__file__)) == FROZEN_GATE_V3_SHA256, "Approved gate-v3 sidecar changed")
        engine = self._engine(self.selected_cases[0])
        target = engine.next_stage()
        _require(target and target["round"] == 2 and target["stage"] == "REVIEWER", "Format amendment must bind the existing r2 reviewer pause")
        _require(not (engine.directory / "rounds/r02/REVIEWER.dispatch.json").exists(), "Round-two reviewer already dispatched before format activation")
        ledger = ROOT / "runs/main/budget.sqlite3"
        snapshot = completed_snapshot(ledger)
        _require(len(snapshot) == EXPECTED_PAUSED_REQUESTS, "Paused request roster differs from the declared nineteen completed calls")
        c_id = "triad_consensus_v2:case_05.r01.C_VOTE.step1"
        _require(c_id in snapshot, "Original malformed first C response is missing")
        adapter = gate_v3._safe_file(ADAPTER_PATH, ROOT)
        supplement = gate_v3._safe_file(supplement_path, self.out)
        extra = gate_v3._safe_file(addendum_path, self.out) if addendum_path else None
        recovered = verify_first_C_supplement(ledger, supplement)
        job = engine.export_reviewer_packet()
        preserved = {}
        for path in sorted((engine.directory / "rounds").glob("r*/*.outcome.json")):
            preserved[str(path.relative_to(self.out))] = original.file_digest(path)
        for name in ("packet_path", "instructions_path", "schema_path"):
            path = gate_v3._safe_file(job[name], self.out)
            preserved[str(path.relative_to(self.out))] = original.file_digest(path)
        for supplied in job["images"]:
            path = gate_v3._safe_file(supplied, self.out)
            preserved[str(path.relative_to(self.out))] = original.file_digest(path)
        note = {"revision": "triad_response_format_v4", "scope": "Deterministic technical output-format handling only",
            "runner_path": str(Path(__file__).absolute()), "runner_sha256": original.file_digest(__file__),
            "adapter_path": str(adapter), "adapter_sha256": original.file_digest(adapter),
            "gate_v3_sha256": FROZEN_GATE_V3_SHA256, "gate_v3_amendment_sha256": original.file_digest(self._amendment_path()),
            "original_freeze_sha256": original.file_digest(self.out / "freeze.json"),
            "original_source_files": original.read_json(self.out / "freeze.json")["source_files"],
            "ledger_path": str(ledger), "completed_requests_before_format_activation": snapshot,
            "paused_job_id": job["job_id"], "paused_packet_sha256": job["packet_sha256"],
            "preserved_pre_activation_files": preserved,
            "first_C_technical_supplement": {"path": str(supplement), "sha256": original.file_digest(supplement),
                "original_request_id": c_id, "original_result_hash": snapshot[c_id]["result_hash"],
                "explicit_decision_preserved": recovered["parsed_original_value"]["decision"], "chronology": "Correction supplied after round-two actor ballots; original round-one review and missing-ballot history retained"},
            "reviewer_addendum": {"path": str(extra), "sha256": original.file_digest(extra)} if extra else None,
            "allowed_changes": ["Parse exactly one extra terminal closing brace when the adapter proves a complete underlying JSON object", "Keep full raw private notes and rationale; expose only declared bounded working-context text", "Select FormatV4Backend for future original scheduled stages"],
            "unchanged": ["All frozen core files, configuration and system prompts", "Every original provider response, receipt, stage outcome and queued reviewer packet", "Explicit decisions, peer messages, images, task facts and original aggregate budget", "One case_05 run through twelve rounds"],
            "forbidden": ["Rewrite or replace the original C ballot/history", "Infer a decision from a contradictory rationale", "Requery an actor or repeat a charged request", "Start another case"],
            "no_POST_during_format_amendment": True}
        original.save_once(self._format_amendment_path(), note)
        return self.verify_format_amendment()

    def verify_format_amendment(self):
        self.verify_amendment()
        note = original.read_json(gate_v3._safe_file(self._format_amendment_path(), self.out))
        _require(note["runner_sha256"] == original.file_digest(__file__), "Format runner changed after activation")
        _require(note["adapter_sha256"] == original.file_digest(gate_v3._safe_file(ADAPTER_PATH, ROOT)), "Format adapter changed after activation")
        _require(note["gate_v3_sha256"] == original.file_digest(Path(gate_v3.__file__)), "Approved gate sidecar changed")
        _require(note["gate_v3_amendment_sha256"] == original.file_digest(self._amendment_path()), "Gate amendment changed")
        _require(note["original_freeze_sha256"] == original.file_digest(self.out / "freeze.json"), "Original core freeze changed")
        _require(note["original_source_files"] == original.source_manifest(), "Original frozen sources changed")
        ledger = ROOT / "runs/main/budget.sqlite3"
        _require(Path(note["ledger_path"]).resolve() == ledger.resolve(), "Format adapter must use the original aggregate")
        current = completed_snapshot(ledger)
        for identifier, expected in note["completed_requests_before_format_activation"].items():
            _require(current.get(identifier) == expected, "A pre-format completed request changed or disappeared")
        for relative, expected in note["preserved_pre_activation_files"].items():
            _require(original.file_digest(gate_v3._safe_file(self.out / relative, self.out)) == expected, "An old outcome or queued reviewer packet changed")
        for name in ("first_C_technical_supplement", "reviewer_addendum"):
            binding = note[name]
            if binding is not None:
                _require(original.file_digest(gate_v3._safe_file(binding["path"], self.out)) == binding["sha256"], "Technical correction/addendum changed")
        verify_first_C_supplement(ledger, note["first_C_technical_supplement"]["path"])
        return {"status": "format_amendment_verified", "amendment_sha256": original.file_digest(self._format_amendment_path()),
                "pre_activation_requests_preserved": len(note["completed_requests_before_format_activation"]),
                "current_completed_requests": len(current), "first_C_explicit_decision": "P4",
                "original_round_one_missing_ballot_history_preserved": True, "no_POST": True}

    def _backend(self):
        self.verify_format_amendment()
        adapter = _adapter_module()
        if self.backend is None:
            self.backend = adapter.FormatV4Backend(self.config, self.out, ROOT / self.config["budget"]["aggregate_ledger"])
        _require(isinstance(self.backend, adapter.FormatV4Backend), "An older backend cannot bypass declared format handling")
        return self.backend

    def verify(self):
        result = super().verify()
        result["response_format_amendment"] = self.verify_format_amendment()
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "verify", "serve", "advance", "status", "budget", "report", "export"])
    parser.add_argument("--supplement", type=Path)
    parser.add_argument("--addendum", type=Path)
    args = parser.parse_args(argv)
    coordinator = FormatAmendedCoordinator()
    if args.command == "prepare":
        _require(args.supplement is not None, "A separately verified first-C technical supplement is required")
        print(original.canonical(coordinator.prepare_format_amendment(args.supplement, args.addendum)), flush=True)
        return 0
    if args.command == "serve":
        coordinator.verify_format_amendment()
        print(original.canonical({"status": "ready", "revision": "triad_response_format_v4", "case_limit": 1}), flush=True)
        for line in sys.stdin:
            try:
                value = json.loads(line)
                result = original_command(coordinator, value)
            except Exception as error:
                print(original.canonical({"status": "host_error", "exception_class": type(error).__name__, "reason": "Inspect retained exact evidence; no automatic paid retry"}), flush=True)
                continue
            print(original.canonical(result), flush=True)
            if value.get("command") in {"quit", "exit"}:
                return 0
        return 0
    result = original_command(coordinator, {"command": args.command})
    print(original.canonical(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
