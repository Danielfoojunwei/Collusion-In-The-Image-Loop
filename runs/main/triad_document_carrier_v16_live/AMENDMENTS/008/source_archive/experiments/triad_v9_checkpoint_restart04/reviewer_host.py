"""Attempt-bound bridge to the unchanged actual-first-Codex-answer helper.

The original bridge is part of the preserved parent source closure. Its exact
first-answer, image and provenance validation is reused inside each isolated
attempt workspace. This wrapper rejects stale or cross-attempt job routing.
It performs no model or provider request.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

from experiments.triad_emergent_v9 import reviewer_host as original
from experiments.triad_emergent_v9.coordinator import DEFAULT_OUT, read_json


class ReviewerBridgeTechnicalFault(OSError):
    def __init__(self, proof_path):
        self.proof_path = str(proof_path)
        super().__init__("Actual reviewer bridge infrastructure failure; immutable proof retained")


def record_technical_fault(out, error, operation, *, host_evidence_path=None):
    """Record a real caught file exception, or hash-bound host launch failure.

    No reviewer text, inspection limitation or judgment is a fault category.
    A host-context failure requires its separately saved actual tool evidence.
    """
    from .attempts import AttemptStore
    from .faults import safe_class
    out = original._absolute(out)
    store = AttemptStore(out)
    store.verify()
    context, job = store.current(), read_json(out / "run_status.json")
    original._require(context is not None and job.get("status") == "awaiting_codex"
                      and job.get("job_id") == f"triad_emergent_v9.{context['attempt_id']}.REVIEWER",
                      "A reviewer technical fault must bind the actual pending attempt job")
    original._require(operation in {"prepare", "envelope", "fresh_context_host_failure"},
                      "Unrecognized reviewer infrastructure operation")
    evidence = {}
    if operation == "fresh_context_host_failure":
        original._require(host_evidence_path is not None, "Actual saved host-tool failure evidence is required")
        original._require(error in {"AgentCapacityError", "ToolExecutionError", "TimeoutError", "ConnectionError", "OSError"},
                          "Only an actual host infrastructure exception qualifies")
        path = original._file(host_evidence_path, out)
        evidence[str(path)] = original.file_digest(path)
    else:
        original._require(isinstance(error, OSError), "Only a caught operating-system file exception qualifies")
    for key in ("packet_path", "instructions_path", "schema_path"):
        path = original._absolute(job[key])
        if path.exists():
            evidence[str(original._file(path, context["working_root"]))] = original.file_digest(path)
    folder = Path(context["working_root"]) / f"reviewer_host/r{context['round']:02d}"
    for name in ("launch.json", "authored_output.json", "first_final.txt", "envelope.json"):
        path = folder / name
        if path.exists():
            evidence[str(original._file(path, context["working_root"]))] = original.file_digest(path)
    name = error if isinstance(error, str) else safe_class(error)
    original._require(isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name),
                      "A safe actual host exception class is required")
    body = {"technical": True, "fault_class": "reviewer_bridge_infrastructure", "exception_class": name,
            "operation": operation, "context": context, "job_id": job["job_id"],
            "packet_sha256": job["packet_sha256"], "helper_sha256": original.file_digest(__file__),
            "evidence_files": evidence, "host_evidence_path": str(host_evidence_path) if host_evidence_path else None}
    path = out / "checkpoints_restart04/reviewer_faults" / (original.digest(body) + ".json")
    original._save_once(path, body)
    return path


def _attempt_job(out, *, archived_root=None, round_number=None):
    out = original._absolute(out)
    if archived_root is None:
        from .attempts import AttemptStore
        store = AttemptStore(out)
        store.verify()
        operations = out / "checkpoints_restart04/operations"
        original._require(not any(not path.with_name(path.name.replace(".started.json", ".finished.json")).exists()
                                  for path in operations.glob("*.started.json")),
                          "An interrupted producer operation must be recovered before starting a reviewer")
        context = store.current()
        original._require(context is not None, "No active technical attempt has a reviewer job")
        root = original._absolute(context["working_root"])
        status = read_json(out / "run_status.json")
        _, job = original.queued_job(root)
        original._require(status == job, "Global and attempt reviewer status differ")
        number = context["round"]
        identity = context["attempt_id"]
    else:
        root, number = original._absolute(archived_root), round_number
        original._require(root.is_relative_to(out) and root != out,
                          "An attempted review must remain inside its original run")
        context = read_json(root / "attempt.json")
        identity = context["attempt_id"]
        original._require(context["round"] == number and context["working_root"] == str(root),
                          "Historical reviewer attempt identity differs")
        request = read_json(root / f"episode/rounds/r{number:02d}/REVIEWER.request.json")
        _, job = original._checked_job(root, request["job"])
    original._require(root == out / "attempts" / f"r{number:02d}" / f"a{context['attempt']:02d}",
                      "Reviewer workspace is not the exact attempt subtree")
    original._require(job["round"] == number and
                      job["job_id"] == f"triad_emergent_v9.{identity}.REVIEWER",
                      "Reviewer job is not qualified by the exact round attempt")
    return root, job


def prepare(out, agent):
    try:
        root, _ = _attempt_job(out)
        return original.prepare(root, agent)
    except OSError as error:
        raise ReviewerBridgeTechnicalFault(record_technical_fault(out, error, "prepare")) from None


def envelope(out, agent, first_final):
    try:
        root, _ = _attempt_job(out)
        return original.envelope(root, agent, first_final)
    except OSError as error:
        raise ReviewerBridgeTechnicalFault(record_technical_fault(out, error, "envelope")) from None


def validate_envelope(out, response):
    root, _ = _attempt_job(out)
    return original.validate_envelope(root, response)


def validate_archived_envelope(out, round_number, *, working_root=None):
    if working_root is None:
        from .attempts import AttemptStore
        store = AttemptStore(out)
        store.verify()
        if round_number <= store.legacy_rounds:
            return original.validate_archived_envelope(out, round_number)
        selector = store.committed_selector(round_number)
        original._require(selector is not None, "No whole-round commit selects this review")
        working_root = selector["working_root"]
    root, _ = _attempt_job(out, archived_root=working_root, round_number=round_number)
    return original.validate_archived_envelope(root, round_number)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "envelope"])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--first-final", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.out, args.agent)
        else:
            original._require(args.first_final is not None, "Actual first-final capture required")
            result = envelope(args.out, args.agent, args.first_final)
    except ReviewerBridgeTechnicalFault as error:
        print(original.canonical({"status": "reviewer_technical_fault", "fault_record_path": error.proof_path}), flush=True)
        return 1
    print(original.canonical(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
