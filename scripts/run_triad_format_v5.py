"""External memory-wrapper recovery for the existing first twelve-round run.

All existing inputs, outputs and reviewer packets remain immutable. Recovered
round-two A/B notes enter only their own future role context, starting after the
already issued round-three ballots. The separate reviewer never gets the notes.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_triad_format_v4 as frozen_v4
from experiments.triad_consensus_v2 import coordinator as original
from experiments.triad_consensus_v2.cli import command as original_command
from experiments.triad_consensus_v2.engine import TriadEngine


V5_ADAPTER = ROOT / "scripts/triad_response_format_v5.py"
V5_AMENDMENT = "execution_notes/triad_response_format_v5_amendment.json"
V4_RUNNER_SHA256 = "e59a5de50994769bf6ddf29426266cfbe551fa59a034be90011fc2dff8b9933a"
V4_ADAPTER_SHA256 = "ae312da63aae7b1aef74bf97abad6cf612dc81ed828a541be1685b47392ef92a"
R3_PACKET_SHA256 = "ac63453758b32c29a713615eeb44a4b2633effa93dc964724246d990611eca92"
PAUSED_REQUESTS = 31
_require = frozen_v4._require
_safe_file = frozen_v4.gate_v3._safe_file


def _v5_module():
    module = importlib.import_module("triad_response_format_v5")
    _require(Path(module.__file__).resolve() == V5_ADAPTER.resolve(), "V5 backend resolves to a different module")
    return module


def verified_memory_supplement(ledger, path, role):
    _require(role in {"A", "B"}, "Only A and B round-two own notes have a recovery allowance")
    identifier = f"triad_consensus_v2:case_05.r02.POSTVERDICT_{role}_LOG.step1"
    adapter = _v5_module()
    row = frozen_v4._adapter_module().completed_row(ledger, identifier)
    recovered = adapter.recover_completed_memory_response(row)
    supplied = original.read_json(path)
    for key in ("request_id", "original_result_hash", "parsed_original_value", "unwrapped_original_value",
                "context_value", "schema_flags", "syntax_flags", "original_text_sha256"):
        _require(supplied.get(key) == recovered[key], "Memory supplement differs from the exact same-role raw response")
    _require(recovered["request_id"] == identifier, "Memory supplement belongs to a different role or round")
    _require(set(recovered["context_value"]) == {"private_note"} and isinstance(recovered["context_value"]["private_note"], str), "Recovered memory has an undeclared field")
    _require(supplied.get("semantic_fields_inferred_or_changed") is False, "Memory text cannot be inferred or replaced")
    return recovered


class OwnLogRecoveryEngine(TriadEngine):
    """Role-private prospective view; original event records are never edited."""
    def __init__(self, directory, config, case, *, recovery_loader):
        self._recovery_loader = recovery_loader
        super().__init__(directory, config, case)

    def _own_logs(self, number, role):
        logs = deepcopy(super()._own_logs(number, role))
        if number < 3 or role not in {"A", "B"}:
            return logs
        replacement = None
        for index, entry in enumerate(logs):
            if entry.get("round") == 2 and entry.get("carryover_log_unavailable") is True:
                replacement = self._recovery_loader(role)
                if replacement is not None:
                    logs[index] = {"round": 2, "private_carryover_log": replacement["private_note"],
                                   "technical_recovery": deepcopy(replacement["technical_recovery"])}
                break
        return logs


def preserved_scientific_files(out):
    """Hash all existing immutable actor/reviewer evidence before activation."""
    names = {"cases", "stage_cache", "provider_evidence", "prompt_audit", "reviewer_jobs",
             "reviewer_dispatches", "reviewer_observations", "reviewer_addenda"}
    result = {}
    for name in sorted(names):
        directory = Path(out) / name
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                path = _safe_file(path, out)
                result[str(path.relative_to(out))] = original.file_digest(path)
    return result


class MemoryAmendedCoordinator(frozen_v4.FormatAmendedCoordinator):
    def _memory_amendment_path(self):
        return self.out / V5_AMENDMENT

    def _engine(self, case):
        if case["case_id"] not in self._engines:
            config = deepcopy(self.config)
            prompts = original.load_prompts(case["condition"])
            config.update({"prompts": prompts, "reviewer_prompt": prompts["reviewer"],
                           "evidence_origin": self.evidence_origin})
            self._engines[case["case_id"]] = OwnLogRecoveryEngine(
                self.out / "cases" / case["case_id"], config, case,
                recovery_loader=self._recovery_for)
        return self._engines[case["case_id"]]

    def _recovery_for(self, role):
        if role not in {"A", "B"} or not self._memory_amendment_path().exists():
            return None
        self.verify_memory_amendment()
        note = original.read_json(self._memory_amendment_path())
        binding = note["recovered_own_notes"][role]
        recovered = verified_memory_supplement(ROOT / "runs/main/budget.sqlite3", binding["path"], role)
        return {"private_note": recovered["context_value"]["private_note"],
                "technical_recovery": {"revision": "triad_response_format_v5", "source_round": 2,
                    "own_role": role, "supplement_sha256": binding["sha256"],
                    "learned_after_round_3_ballots": True,
                    "old_stage_and_previous_inputs_preserved": True}}

    def prepare_memory_amendment(self, A_supplement, B_supplement):
        if self._memory_amendment_path().exists():
            return self.verify_memory_amendment()
        self.verify_format_amendment()
        _require(original.file_digest(frozen_v4.__file__) == V4_RUNNER_SHA256, "Frozen v4 runner changed")
        _require(original.file_digest(frozen_v4.ADAPTER_PATH) == V4_ADAPTER_SHA256, "Frozen v4 adapter changed")
        engine = self._engine(self.selected_cases[0])
        target = engine.next_stage()
        _require(target and target["round"] == 3 and target["stage"] == "REVIEWER", "V5 activation must preserve the current round-three reviewer pause")
        _require(engine.outcome(3, "REVIEWER") is None, "Round-three reviewer was already imported")
        job = engine.export_reviewer_packet()
        _require(job["packet_sha256"] == R3_PACKET_SHA256, "Original round-three reviewer packet differs")
        dispatch = engine.directory / "rounds/r03/REVIEWER.dispatch.json"
        if dispatch.exists():
            declared = original.read_json(dispatch)
            _require(declared.get("packet_sha256") == job["packet_sha256"] and declared.get("request_id") == job["job_id"]
                     and isinstance(declared.get("reviewer_agent"), str) and declared["reviewer_agent"], "Existing reviewer dispatch differs from its original queued packet")
        ledger = ROOT / "runs/main/budget.sqlite3"
        snapshot = frozen_v4.completed_snapshot(ledger)
        _require(len(snapshot) == PAUSED_REQUESTS, "V5 activation requires the original thirty-one completed requests")
        recovered_bindings = {}
        for role, supplied in (("A", A_supplement), ("B", B_supplement)):
            outcome = engine.outcome(2, f"POSTVERDICT_{role}_LOG")
            _require(outcome and outcome["status"] == "invalid_model_output" and outcome["output"] is None,
                     "Only the original unavailable round-two own notes may be supplemented")
            path = _safe_file(supplied, self.out)
            recovered = verified_memory_supplement(ledger, path, role)
            recovered_bindings[role] = {"path": str(path), "sha256": original.file_digest(path),
                "request_id": recovered["request_id"], "original_result_hash": recovered["original_result_hash"],
                "note_field": "context_value.private_note", "only_visible_to_same_role": True}
        note = {"revision": "triad_response_format_v5", "scope": "Exact memory-only schema wrapper and prospective own-note view",
            "runner_sha256": original.file_digest(__file__), "adapter_sha256": original.file_digest(_safe_file(V5_ADAPTER, ROOT)),
            "frozen_v4_runner_sha256": V4_RUNNER_SHA256, "frozen_v4_adapter_sha256": V4_ADAPTER_SHA256,
            "v4_amendment_sha256": original.file_digest(self._format_amendment_path()),
            "original_freeze_sha256": original.file_digest(self.out / "freeze.json"),
            "completed_requests_before_v5_activation": snapshot,
            "preserved_scientific_files": preserved_scientific_files(self.out),
            "recovered_own_notes": recovered_bindings,
            "activation": {"case_id": "case_05", "round": 3, "stage": "REVIEWER", "round_3_ballots_already_issued": True,
                "queued_packet_sha256": job["packet_sha256"], "reviewer_dispatch_preserved": dispatch.exists(),
                "first_new_actor_views": "Post-round-three-verdict own logs and later scheduled actor contexts"},
            "allowed_changes": ["Use FormatV5Backend for exact memory_only properties.private_note wrappers", "Replace only the own-role round-two carryover_log_unavailable view with verified recovered text plus chronology metadata"],
            "forbidden": ["Rewrite any historical outcome, observed response, input or reviewer packet", "Put A's note in B/C/reviewer inputs or B's note in A/C/reviewer inputs", "Change any decision, peer message, image, model, route, prompt or budget", "Requery an actor, repeat a charged call, or start another case"],
            "no_POST_during_amendment": True}
        original.save_once(self._memory_amendment_path(), note)
        return self.verify_memory_amendment()

    def verify_memory_amendment(self):
        self.verify_format_amendment()
        note = original.read_json(_safe_file(self._memory_amendment_path(), self.out))
        _require(note["runner_sha256"] == original.file_digest(__file__), "V5 runner changed after activation")
        _require(note["adapter_sha256"] == original.file_digest(_safe_file(V5_ADAPTER, ROOT)), "V5 adapter changed after activation")
        _require(note["frozen_v4_runner_sha256"] == original.file_digest(frozen_v4.__file__), "Frozen v4 runner changed")
        _require(note["frozen_v4_adapter_sha256"] == original.file_digest(frozen_v4.ADAPTER_PATH), "Frozen v4 adapter changed")
        _require(note["v4_amendment_sha256"] == original.file_digest(self._format_amendment_path()), "V4 amendment changed")
        _require(note["original_freeze_sha256"] == original.file_digest(self.out / "freeze.json"), "Original frozen core changed")
        current = frozen_v4.completed_snapshot(ROOT / "runs/main/budget.sqlite3")
        for identifier, expected in note["completed_requests_before_v5_activation"].items():
            _require(current.get(identifier) == expected, "A pre-v5 paid observation changed")
        for relative, expected in note["preserved_scientific_files"].items():
            _require(original.file_digest(_safe_file(self.out / relative, self.out)) == expected, "A historical actor input/output or reviewer packet changed")
        _require(set(note["recovered_own_notes"]) == {"A", "B"}, "Both same-role recovery bindings are required")
        for role, binding in note["recovered_own_notes"].items():
            _require(role in {"A", "B"} and original.file_digest(_safe_file(binding["path"], self.out)) == binding["sha256"], "An own-note supplement changed")
            recovered = verified_memory_supplement(ROOT / "runs/main/budget.sqlite3", binding["path"], role)
            _require(binding["request_id"] == recovered["request_id"] and binding["original_result_hash"] == recovered["original_result_hash"], "Own-note source identity changed")
        return {"status": "memory_amendment_verified", "amendment_sha256": original.file_digest(self._memory_amendment_path()),
                "pre_activation_requests_preserved": len(note["completed_requests_before_v5_activation"]),
                "current_completed_requests": len(current), "same_role_only": True,
                "old_round_2_outcomes_and_round_3_inputs_unchanged": True, "no_POST": True}

    def _backend(self):
        self.verify_memory_amendment()
        adapter = _v5_module()
        if self.backend is None:
            self.backend = adapter.FormatV5Backend(self.config, self.out, ROOT / self.config["budget"]["aggregate_ledger"])
        _require(isinstance(self.backend, adapter.FormatV5Backend), "An older backend cannot bypass V5 memory handling")
        return self.backend

    def verify(self):
        result = super().verify()
        result["memory_wrapper_amendment"] = self.verify_memory_amendment()
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "verify", "serve", "advance", "status", "budget", "report", "export"])
    parser.add_argument("--A-supplement", type=Path)
    parser.add_argument("--B-supplement", type=Path)
    args = parser.parse_args(argv)
    coordinator = MemoryAmendedCoordinator()
    if args.command == "prepare":
        _require(args.A_supplement is not None and args.B_supplement is not None, "Both separately verified own-note supplements are required")
        print(original.canonical(coordinator.prepare_memory_amendment(args.A_supplement, args.B_supplement)), flush=True)
        return 0
    if args.command == "serve":
        coordinator.verify_memory_amendment()
        print(original.canonical({"status": "ready", "revision": "triad_response_format_v5", "case_limit": 1}), flush=True)
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
    print(original.canonical(original_command(coordinator, {"command": args.command})), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
