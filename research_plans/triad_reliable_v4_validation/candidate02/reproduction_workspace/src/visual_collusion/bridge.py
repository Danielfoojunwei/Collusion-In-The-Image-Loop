"""Public-packet bridge to the real Codex host; never impersonates Codex via an API.

A fresh context is procedural blinding, not an OS filesystem boundary. Tool
traces and their host audit are retained; absence of a trace is not certification.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .common import atomic_bytes, digest, read_json, write_json
from . import protocol


class AwaitingCodex(Exception):
    def __init__(self, job):
        self.job = job
        super().__init__("Awaiting an actual Codex response to public packet " + job["job_id"])


class InvalidCodexResponse(ValueError):
    """One actual Codex attempt ended unusably; never replace or retry it."""
    def __init__(self, failure):
        self.failure = failure
        self.job_id = failure["job_id"]
        super().__init__("Codex job " + self.job_id + " is sealed as invalid: " + failure["reason"])


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def request_job(out: Path, key: str, kind: str, public: dict, images: list[tuple[str, bytes]]):
    if kind not in {"task", "review"}:
        raise ValueError("Unknown Codex job kind")
    job_id = digest({"protocol": "4", "key": key})[:24]
    folder = Path(out) / "review_jobs" / job_id
    folder.mkdir(parents=True, exist_ok=True)
    records = []
    for i, (label, data) in enumerate(images, 1):
        relative = f"images/image_{i:02d}.png"
        dest = folder / relative
        if dest.exists() and dest.read_bytes() != data:
            raise ValueError("Saved public packet image changed")
        atomic_bytes(dest, data)
        records.append({"label": label, "path": relative, "sha256": _sha(data)})
    prompt_name = "task_author_system.txt" if kind == "task" else "codex_review_system.txt"
    schema = protocol.TASK_SCHEMA if kind == "task" else protocol.REVIEW_SCHEMA
    prompt = protocol.load_prompt(prompt_name)
    body = {"job_id": job_id, "kind": kind, "public": public, "images": records,
            "system_prompt": prompt, "output_schema": schema,
            "information_boundary": "Only this packet and its listed images; never researcher files or parent conversation."}
    packet = {**body, "packet_sha256": digest(body)}
    packet_path = folder / "packet.json"
    if packet_path.exists() and read_json(packet_path) != packet:
        raise ValueError("Public packet content changed after creation")
    write_json(packet_path, packet)
    atomic_bytes(folder / "system_prompt.txt", prompt.encode())
    write_json(folder / "schema.json", schema)
    request = {"status": "awaiting_codex", "job_id": job_id, "kind": kind,
               "packet_path": str(packet_path.resolve()),
               "instructions_path": str((folder / "system_prompt.txt").resolve()),
               "schema_path": str((folder / "schema.json").resolve()),
               "packet_sha256": packet["packet_sha256"]}
    if (folder / "failure.json").exists():
        raise InvalidCodexResponse(validate_saved_failure(folder))
    if not (folder / "response.json").exists():
        raise AwaitingCodex(request)
    response = validate_saved_response(folder)
    return response


def validate_packet(folder: Path):
    folder = Path(folder)
    if (folder / "packet.json").is_symlink():
        raise ValueError("Codex packet cannot be a symlink")
    packet = read_json(folder / "packet.json")
    body = {k: v for k, v in packet.items() if k != "packet_sha256"}
    if digest(body) != packet.get("packet_sha256"):
        raise ValueError("Codex packet hash mismatch")
    for item in packet["images"]:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts or not str(relative).startswith("images/"):
            raise ValueError("Image path escaped the packet")
        path = folder / relative
        try:
            path.resolve().relative_to(folder.resolve())
        except ValueError:
            raise ValueError("Codex packet image escaped through a directory link") from None
        if path.is_symlink() or _sha(path.read_bytes()) != item["sha256"]:
            raise ValueError("Codex packet image hash mismatch")
    instructions, schema = folder / "system_prompt.txt", folder / "schema.json"
    if instructions.is_symlink() or instructions.read_bytes() != packet["system_prompt"].encode():
        raise ValueError("Codex instructions differ from the bound packet")
    if schema.is_symlink() or read_json(schema) != packet["output_schema"]:
        raise ValueError("Codex output schema differs from the bound packet")
    return packet


def _trace_binding(packet, output):
    return {"job_id": packet["job_id"], "packet_sha256": packet["packet_sha256"],
            "output_sha256": digest(output)}


def _check_trace_binding(wrapper, packet, output):
    if not isinstance(wrapper, dict) or any(wrapper.get(key) != value for key, value in _trace_binding(packet, output).items()):
        raise ValueError("Reviewer trace does not bind this job, packet, and exact output")


def _check_origin(folder, response):
    runtime = response.get("actor_runtime")
    if runtime not in {"mock", "codex"} or response.get("evidence_origin") != ("mock" if runtime == "mock" else "live"):
        raise ValueError("Reviewer runtime and evidence origin disagree")
    freeze = Path(folder).parent.parent / "freeze.json"
    if freeze.exists() and read_json(freeze).get("evidence_origin") != response["evidence_origin"]:
        raise ValueError("Reviewer evidence origin differs from the frozen run")


def validate_saved_response(folder: Path):
    if (Path(folder) / "failure.json").exists():
        raise ValueError("A Codex job cannot contain both an accepted response and a sealed failure")
    packet = validate_packet(folder)
    response = read_json(Path(folder) / "response.json")
    if response["job_id"] != packet["job_id"] or response["packet_sha256"] != packet["packet_sha256"]:
        raise ValueError("Codex response is attached to a different packet")
    expected = response.pop("response_sha256", None)
    if digest(response) != expected:
        raise ValueError("Saved Codex response integrity mismatch")
    response["response_sha256"] = expected
    _check_origin(folder, response)
    validator = protocol.validate_task if packet["kind"] == "task" else protocol.validate_review
    validator(response["output"])
    trace = response.get("trace", {})
    if trace.get("wrapper_sha256"):
        wrapper = read_json(Path(folder) / "trace_wrapper.json")
        if digest(wrapper) != trace["wrapper_sha256"]:
            raise ValueError("Stored reviewer trace wrapper integrity mismatch")
        _check_trace_binding(wrapper, packet, response["output"])
    if trace.get("raw_sha256"):
        raw = Path(folder) / "trace.raw"
        if _sha(raw.read_bytes()) != trace["raw_sha256"]:
            raise ValueError("Stored reviewer trace hash mismatch")
    return response


def validate_saved_failure(folder: Path):
    folder = Path(folder)
    if (folder / "response.json").exists():
        raise ValueError("A Codex job cannot contain both an accepted response and a sealed failure")
    packet = validate_packet(folder)
    failure = read_json(folder / "failure.json")
    body = {key: value for key, value in failure.items() if key != "failure_sha256"}
    if digest(body) != failure.get("failure_sha256"):
        raise ValueError("Saved Codex failure integrity mismatch")
    if failure.get("job_id") != packet["job_id"] or failure.get("packet_sha256") != packet["packet_sha256"]:
        raise ValueError("Codex failure is attached to a different packet")
    _check_origin(folder, failure)
    raw = folder / "failed_response.raw"
    if raw.is_symlink() or _sha(raw.read_bytes()) != failure.get("raw_response_sha256"):
        raise ValueError("Saved failed Codex response integrity mismatch")
    return failure


def fail_job(out, job_id, reason, raw_response_path, *, actor_model,
             context_isolation, actor_runtime="codex", allow_mock=False):
    """Seal an actual unusable first answer, preserving bytes and provenance.

    This is a terminal study outcome, not authorization for a replacement model
    attempt. Product identity is host-observed metadata, not independently proven.
    """
    if not isinstance(job_id, str) or len(job_id) != 24 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("Invalid opaque job ID")
    if not isinstance(reason, str) or not reason.strip() or len(reason.encode()) > 2000:
        raise ValueError("Failed-job reason must be nonempty and at most 2000 UTF-8 bytes")
    if actor_runtime != ("mock" if allow_mock else "codex"):
        raise ValueError("Failed-job provenance must match its actual runtime")
    if not isinstance(actor_model, str) or not actor_model.strip():
        raise ValueError("Record the actual host-reported model for the failed attempt")
    if context_isolation not in {"instruction_scoped", "packet_environment"}:
        raise ValueError("Declare the actual reviewer isolation mode")
    folder = Path(out) / "review_jobs" / job_id
    packet = validate_packet(folder)
    if (folder / "response.json").exists():
        validate_saved_response(folder)
        raise ValueError("An accepted Codex response is already frozen")
    existing = validate_saved_failure(folder) if (folder / "failure.json").exists() else None
    with Path(raw_response_path).open("rb") as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Failed Codex raw response exceeds 8 MiB")
    failure = {"job_id": job_id, "packet_sha256": packet["packet_sha256"], "kind": packet["kind"],
               "reason": reason, "actor_runtime": actor_runtime, "actor_model": actor_model,
               "context_isolation": context_isolation, "raw_response_sha256": _sha(raw),
               "raw_response_bytes": len(raw), "evidence_origin": "mock" if allow_mock else "live",
               "trace": {"coverage": "unavailable", "audit_status": "unverified"}}
    _check_origin(folder, failure)
    failure["failure_sha256"] = digest(failure)
    if existing is not None:
        if existing != failure:
            raise ValueError("A different failed Codex response is already frozen")
    else:
        target = folder / "failed_response.raw"
        if target.exists() and target.read_bytes() != raw:
            raise ValueError("Different failed response bytes are already preserved")
        atomic_bytes(target, raw)
        write_json(folder / "failure.json", failure)
    return {"status": "invalid_codex_response", "job_id": job_id,
            "failure_sha256": failure["failure_sha256"]}


def _load_trace(folder, packet, envelope, response_path):
    """Validate candidate trace in memory; never mutate accepted evidence here."""
    trace = {"coverage": "unavailable", "audit_status": "unverified",
             "reason": "No host-exported trace was supplied.", "raw_sha256": None,
             "wrapper_sha256": None}
    trace_path = envelope["trace_path"]
    if trace_path is None:
        return trace, None, None
    if not isinstance(trace_path, str):
        raise ValueError("trace_path must be a path or null")
    wrapper_path = Path(trace_path)
    if not wrapper_path.is_absolute():
        wrapper_path = Path(response_path).parent / wrapper_path
    wrapper = read_json(wrapper_path)
    _check_trace_binding(wrapper, packet, envelope["output"])
    if wrapper.get("origin") != "host_export" or wrapper.get("coverage") not in {"complete", "partial", "unavailable"}:
        raise ValueError("Reviewer trace must describe actual host-exported events")
    events = wrapper.get("events", [])
    if not isinstance(events, list) or any(not isinstance(e, dict) or not isinstance(e.get("tool"), str) for e in events):
        raise ValueError("Malformed observed reviewer events")
    audit = wrapper.get("audit", {})
    if not isinstance(audit, dict) or audit.get("status") not in {"public_packet_only", "contaminated", "unverified"}:
        raise ValueError("Trace audit needs an explicit status")
    trace = {"coverage": wrapper["coverage"], "audit_status": audit["status"],
             "reason": str(audit.get("reason", "")), "raw_sha256": None,
             "wrapper_sha256": digest(wrapper), "events": events,
             **_trace_binding(packet, envelope["output"])}
    raw = None
    raw_path = wrapper.get("raw_trace_path")
    if raw_path:
        if not isinstance(raw_path, str):
            raise ValueError("Raw trace path must be a string or null")
        raw_path = Path(raw_path)
        if not raw_path.is_absolute():
            raw_path = wrapper_path.parent / raw_path
        with raw_path.open("rb") as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024 or _sha(raw) != wrapper.get("sha256"):
            raise ValueError("Raw reviewer trace is oversized or its supplied hash differs")
        trace["raw_sha256"] = _sha(raw)
    if trace["coverage"] != "complete" or raw is None:
        if trace["audit_status"] != "contaminated":
            trace["audit_status"] = "unverified"

    def mark_unverified(reason):
        if trace["audit_status"] != "contaminated":
            trace["audit_status"] = "unverified"
            trace["reason"] = reason

    packet_root = wrapper.get("packet_root")
    if packet_root is not None and (not isinstance(packet_root, str) or not Path(packet_root).is_absolute()):
        raise ValueError("packet_root must be the host-observed absolute packet directory or null")
    if envelope["context_isolation"] == "packet_environment" and packet_root is None:
        mark_unverified("Separate packet environment has no host-observed packet_root mapping.")
    root = Path(packet_root).resolve() if packet_root is not None else Path(folder).resolve()
    trace["packet_root"] = str(root)
    allowed = {"packet.json", "system_prompt.txt", "schema.json", *(item["path"] for item in packet["images"])}
    # Check exact allowlisted files, not the entire directory. A mode label
    # cannot excuse an observed outside read. Unparsed commands/missing paths
    # cannot automatically certify clean access; the original trace is retained.
    for event in events:
        event_path = event.get("path")
        if not isinstance(event_path, str) or not event_path:
            mark_unverified("An observed tool event has no auditable file path.")
            continue
        if set(event) - {"tool", "path"}:
            mark_unverified("An observed tool event contains arguments this path audit cannot interpret.")
        path = Path(event_path)
        if not path.is_absolute():
            path = root / path
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError:
            relative = None
        if relative not in allowed:
            trace["audit_status"] = "contaminated"
            trace["reason"] = "Observed access outside the exact public packet file allowlist."
    return trace, wrapper, raw


def ingest(out: Path, response_path: Path, *, allow_mock=False):
    envelope = read_json(response_path)
    required = {"job_id", "packet_sha256", "actor_runtime", "actor_model", "context_isolation", "trace_path", "output"}
    if not isinstance(envelope, dict) or set(envelope) != required:
        raise ValueError("Codex response envelope fields differ from the documented contract")
    job_id = envelope["job_id"]
    if not isinstance(job_id, str) or len(job_id) != 24 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("Invalid opaque job ID")
    folder = Path(out) / "review_jobs" / job_id
    packet = validate_packet(folder)
    if (folder / "failure.json").exists():
        validate_saved_failure(folder)
        raise ValueError("This Codex job is sealed as failed; no replacement response is permitted")
    destination = folder / "response.json"
    existing = validate_saved_response(folder) if destination.exists() else None
    if envelope["packet_sha256"] != packet["packet_sha256"]:
        raise ValueError("The response does not bind this public packet")
    if envelope["actor_runtime"] != ("mock" if allow_mock else "codex"):
        raise ValueError("Live bridge requires actual Codex; mock fixtures belong to a separate run")
    if not isinstance(envelope["actor_model"], str) or not envelope["actor_model"].strip():
        raise ValueError("Record the actual Codex model, not an invented model alias")
    if envelope["context_isolation"] not in {"instruction_scoped", "packet_environment"}:
        raise ValueError("Declare the actual reviewer isolation mode")
    validator = protocol.validate_task if packet["kind"] == "task" else protocol.validate_review
    validator(envelope["output"])
    trace, wrapper, raw = _load_trace(folder, packet, envelope, response_path)
    saved = {k: v for k, v in envelope.items() if k != "trace_path"}
    saved["trace"] = trace
    saved["evidence_origin"] = "mock" if allow_mock else "live"
    _check_origin(folder, saved)
    saved["response_sha256"] = digest(saved)
    if existing is not None:
        if existing != saved:
            raise ValueError("A reviewer response is already frozen; do not replace or rerun it")
    else:
        # Stage all comparisons before writing any evidence. A rejected import
        # must leave a prior response or orphaned partial import unchanged.
        if raw is not None and (folder / "trace.raw").exists() and (folder / "trace.raw").read_bytes() != raw:
            raise ValueError("A different raw reviewer trace is already preserved")
        if wrapper is not None and (folder / "trace_wrapper.json").exists() and read_json(folder / "trace_wrapper.json") != wrapper:
            raise ValueError("A different reviewer trace wrapper is already preserved")
        if raw is not None:
            atomic_bytes(folder / "trace.raw", raw)
        if wrapper is not None:
            write_json(folder / "trace_wrapper.json", wrapper)
        write_json(destination, saved)
    return {"status": "imported", "job_id": job_id, "trace_audit": trace["audit_status"]}
