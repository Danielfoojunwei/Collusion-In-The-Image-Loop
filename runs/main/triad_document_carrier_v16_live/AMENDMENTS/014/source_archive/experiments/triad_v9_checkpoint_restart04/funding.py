"""Attempt-qualified one-POST funding and immutable abandoned-charge evidence."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import sqlite3

from experiments.triad_emergent_v9 import funding as base
from experiments.triad_emergent_v9.coordinator import file_sha, read_json, save_once
from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard, _wire_bytes
from experiments.pressure_interview_v1.live_provider import _save_once
from visual_collusion.budget import BudgetError, PendingPaidRequest, canonical, request_digest, _units
from .faults import safe_class

REPAIR = "triad_v9_checkpoint_restart04"
POLICY = {"repair": REPAIR, "namespace": base.NAMESPACE, "max_attempts": 3,
    "unknown_reserves_retained": True, "old_intents_reposted": False}
POLICY_SHA256 = hashlib.sha256(canonical(POLICY).encode()).hexdigest()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def process_start_ticks(pid):
    if type(pid) is not int or pid <= 1:
        raise BudgetError("An actual producer PID is required")
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def _owns_producer_lock(out):
    path = _safe(Path(out) / "producer.lock", root=out)
    if not path.is_file():
        return False
    stat = path.stat()
    for line in Path("/proc/locks").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 6 and fields[1:4] == ["FLOCK", "ADVISORY", "WRITE"] and fields[4] == str(os.getpid()):
            major, minor, inode = fields[5].split(":")
            if (int(major, 16), int(minor, 16), int(inode)) == (os.major(stat.st_dev), os.minor(stat.st_dev), stat.st_ino):
                return True
    return False


def _safe(path, *, root=None):
    path = Path(path).absolute()
    if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)) or (root is not None and not path.is_relative_to(Path(root).absolute())):
        raise BudgetError("Funding evidence path escapes its non-symlink run")
    return path


def validate_context(global_out, context, *, allow_legacy=False):
    out = _safe(global_out)
    if not isinstance(context, dict):
        raise BudgetError("An explicit funded attempt context is required")
    result = deepcopy(context)
    number, attempt, identifier = result.get("round"), result.get("attempt"), result.get("attempt_id")
    if type(number) is not int or not 1 <= number <= 12 or type(attempt) is not int or not 1 <= attempt <= 3:
        raise BudgetError("Round and technical-attempt bounds changed")
    legacy = result.get("legacy") is True
    shadow = result.get("kind") == "shadow" or isinstance(identifier, str) and identifier.startswith("shadows.")
    expected_id = f"shadows.a{attempt:02d}" if shadow else f"r{number:02d}.a{attempt:02d}"
    expected_root = out / "shadow_attempts" / f"a{attempt:02d}" if shadow else out / "attempts" / f"r{number:02d}" / f"a{attempt:02d}"
    if legacy:
        if not allow_legacy or shadow or number != 7 or attempt != 1:
            raise BudgetError("Only the authenticated original R7 legacy attempt is supported")
        expected_root = out
    if (identifier != expected_id or shadow and number != 12 or Path(result.get("working_root", "")).absolute() != expected_root
            or not isinstance(result.get("prior_checkpoint_sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", result["prior_checkpoint_sha256"]) is None):
        raise BudgetError("Attempt identity, work root or prior checkpoint changed")
    _safe(expected_root, root=out)
    return result


def _rows(path):
    path = _safe(path)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return {r["request_id"]: dict(r) for r in db.execute("SELECT * FROM requests")}


def _prefix(context):
    return base.NAMESPACE + ":" + ("" if context.get("legacy") else context["attempt_id"] + ":")


def attempt_rows(path, context):
    prefix = _prefix(context)
    if context.get("legacy"):
        prefix += "episode.r07."
    return {key: row for key, row in _rows(path).items() if key.startswith(prefix)}


def _checked_marker(global_out, context, path, expected):
    path = _safe(path, root=global_out)
    if file_sha(path) != expected:
        raise BudgetError("Abandoned attempt marker changed")
    marker = read_json(path)
    body = {k: v for k, v in marker.items() if k != "record_hash"}
    if (marker.get("record_hash") != _sha(canonical(body).encode()) or marker.get("status") != "abandoned_technical"
            or marker.get("paid_costs_and_unknown_reserves_remain_counted") is not True
            or marker.get("excluded_from_future_actor_and_reviewer_memory") is not True
            or marker.get("fault", {}).get("technical") is not True
            or not isinstance(marker.get("fault", {}).get("fault_class"), str)
            or any(marker.get(k) != context.get(k) for k in ("round", "attempt", "attempt_id", "working_root", "prior_checkpoint_sha256", "legacy"))):
        raise BudgetError("An exact immutable technical abandonment marker is required")
    for relative, digest in marker.get("evidence_files", {}).items():
        if Path(relative).is_absolute() or ".." in Path(relative).parts or file_sha(_safe(Path(global_out) / relative, root=global_out)) != digest:
            raise BudgetError("Abandoned attempt evidence changed")
    return marker


def _legacy_binding(global_out, context, rows, proof_path):
    path = _safe(proof_path, root=global_out)
    record = read_json(path)
    raw = record.get("failed_request")
    proof_info = record.get("producer_exit_proof", {})
    proof_path = _safe(proof_info.get("path", ""), root=global_out)
    if file_sha(proof_path) != proof_info.get("sha256"):
        raise BudgetError("Legacy producer exit proof changed")
    proof = read_json(proof_path)
    if (record.get("context") != context or raw is None or rows != {raw.get("request_id"): raw}
            or proof.get("failed_request") != raw or proof.get("active_producer") is not False
            or proof.get("observed_exit_code") != 0 or proof.get("observed_final_host_status") != "quitting"
            or record.get("parent_freeze_sha256") != proof.get("freeze_sha256")
            or raw.get("request_id") != "triad_emergent_v9:episode.r07.A_IMAGE_AND_VOTE.visual1"
            or raw.get("status") != "uncertain" or raw.get("actual_units") is not None
            or type(raw.get("reserved_units")) is not int or raw["reserved_units"] <= 0
            or any(raw.get(k) is not None for k in ("response_json", "receipt_json", "result_hash", "finished"))):
        raise BudgetError("The original closed R7 first-slot loss is not authenticated")
    pid = proof.get("producer_pid")
    if pid is not None and (type(pid) is not int or pid <= 1 or Path(f"/proc/{pid}").exists()):
        raise BudgetError("The original producer must have exited")
    for relative, expected in record.get("bound_files", {}).items():
        if Path(relative).is_absolute() or ".." in Path(relative).parts or file_sha(_safe(Path(global_out) / relative, root=global_out)) != expected:
            raise BudgetError("Original legacy request evidence changed")
    return {"path": str(path), "sha256": file_sha(path)}


def _pending_proof(out, context, rows, proof_path, *, sealing):
    path = _safe(proof_path, root=out)
    proof = read_json(path)
    old_pid, old_start = proof.get("old_producer_pid"), proof.get("old_process_start_ticks")
    marker_path = _safe(proof.get("operation_marker_path", ""), root=out)
    if file_sha(marker_path) != proof.get("operation_marker_sha256"):
        raise BudgetError("The original operation-start proof changed")
    marker = read_json(marker_path)
    body = {k: v for k, v in marker.items() if k != "record_hash"}
    if (marker.get("record_hash") != _sha(canonical(body).encode())
            or marker.get("producer_pid") != old_pid or str(marker.get("producer_process_start_ticks")) != str(old_start)
            or proof.get("exclusive_producer_lock_reacquired") is not True or old_start is None
            or process_start_ticks(old_pid) == str(old_start)):
        raise BudgetError("The exact paid callback's former producer has not conclusively stopped")
    if sealing and (proof.get("recovered_by_pid") != os.getpid()
            or str(proof.get("recovered_process_start_ticks")) != process_start_ticks(os.getpid())
            or not _owns_producer_lock(out)):
        raise BudgetError("Pending recovery requires this producer's reacquired exclusive lock")
    owners = {}
    for row in rows.values():
        if row["status"] != "pending":
            continue
        if context.get("legacy") or row["actual_units"] is not None or row["reserved_units"] <= 0 or any(row[k] is not None for k in ("response_json", "receipt_json", "result_hash", "finished")):
            raise BudgetError("Only a new attempt's fully reserved response-less pending callback is eligible")
        owner_path = _safe(Path(context["working_root"]) / "provider_evidence/paid_callback_owners" / (_sha(row["request_id"].encode()) + ".json"), root=out)
        owner = read_json(owner_path)
        owner_body = {k: v for k, v in owner.items() if k != "record_hash"}
        binding = owner.get("binding", {})
        if (owner.get("record_hash") != _sha(canonical(owner_body).encode()) or owner.get("context") != context
                or owner.get("producer_pid") != old_pid or str(owner.get("producer_process_start_ticks")) != str(old_start)
                or owner.get("reserved_units") != row["reserved_units"]
                or any(binding.get(k) != row[v] for k, v in (("request_id", "request_id"), ("request_hash", "request_hash"), ("kind", "kind"), ("requested_model", "model")))):
            raise BudgetError("Pending row does not match the immutable actual paid callback owner")
        owners[str(owner_path.relative_to(out))] = file_sha(owner_path)
    return {"path": str(path), "sha256": file_sha(path), "paid_callback_owner_files": owners,
        "exclusive_lock_verified_at_seal": True}


def seal_abandoned_attempt(global_out, context, fault, *, aggregate_path,
                           abandonment_path, expected_abandonment_sha256, legacy_proof_path=None,
                           pending_crash_proof_path=None):
    out = _safe(global_out)
    context = validate_context(out, context, allow_legacy=True)
    marker = _checked_marker(out, context, abandonment_path, expected_abandonment_sha256)
    if marker["fault"] != fault or fault.get("technical") is not True:
        raise BudgetError("The declared fault differs from the abandoned attempt")
    rows = attempt_rows(aggregate_path, context)
    path = out / "analysis/restart04/abandoned_charges" / (context["attempt_id"] + ".json")
    if path.exists():
        old = read_json(path)
        if old.get("rows") != rows or any(not _verified_authority(out, path, row) for row in rows.values() if row["status"] != "completed"):
            raise BudgetError("Existing abandoned-charge authority changed")
        return {"path": str(path), "sha256": file_sha(path), "unknown_count": sum(r["status"] != "completed" for r in rows.values()), "rows_unchanged": True}
    pending = None
    if any(row["status"] == "pending" for row in rows.values()):
        proof_path = pending_crash_proof_path or fault.get("pending_crash_proof_path")
        if proof_path is None:
            raise PendingPaidRequest("A pending callback requires exact dead-producer and exclusive-lock proof")
        pending = _pending_proof(out, context, rows, proof_path, sealing=True)
    if any(row["status"] not in {"completed", "uncertain", "pending"} or row["status"] in {"uncertain", "pending"} and
           (row["actual_units"] is not None or row["reserved_units"] <= 0) for row in rows.values()):
        raise BudgetError("Abandoned charges must preserve their original conclusive or fully reserved unknown state")
    legacy = _legacy_binding(out, context, rows, legacy_proof_path) if context.get("legacy") else None
    result = {"repair": REPAIR, "policy_sha256": POLICY_SHA256, "context": context,
        "abandonment_path": str(_safe(abandonment_path, root=out)), "abandonment_sha256": expected_abandonment_sha256,
        "rows": rows, "legacy_proof": legacy, "pending_crash_proof": pending, "raw_rows_and_full_unknown_reserves_retained": True}
    result["record_hash"] = _sha(canonical(result).encode())
    save_once(path, result)
    return {"path": str(path), "sha256": file_sha(path), "unknown_count": sum(r["status"] != "completed" for r in rows.values()), "rows_unchanged": True}


def _verified_authority(out, path, row):
    path = _safe(path, root=out)
    record = read_json(path)
    body = {k: v for k, v in record.items() if k != "record_hash"}
    if (record.get("record_hash") != _sha(canonical(body).encode()) or record.get("repair") != REPAIR
            or record.get("policy_sha256") != POLICY_SHA256 or record.get("raw_rows_and_full_unknown_reserves_retained") is not True
            or record.get("rows", {}).get(row["request_id"]) != dict(row)):
        return False
    context = validate_context(out, record["context"], allow_legacy=True)
    if path.name != context["attempt_id"] + ".json":
        return False
    _checked_marker(out, context, record["abandonment_path"], record["abandonment_sha256"])
    pending = record.get("pending_crash_proof")
    if row["status"] == "pending":
        if not isinstance(pending, dict) or pending.get("exclusive_lock_verified_at_seal") is not True or file_sha(_safe(pending["path"], root=out)) != pending["sha256"]:
            return False
        verified = _pending_proof(out, context, record["rows"], pending["path"], sealing=False)
        if verified != pending:
            return False
    if context.get("legacy"):
        legacy = record.get("legacy_proof", {})
        if file_sha(_safe(legacy.get("path", ""), root=out)) != legacy.get("sha256"):
            return False
        _legacy_binding(out, context, record["rows"], legacy["path"])
    return True


class FundingLedger(base.FundingLedger):
    def __init__(self, path, cap_usd=40, *, global_out):
        self.global_out = _safe(global_out)
        super().__init__(path, cap_usd)

    def _sealed(self, db, row):
        if base.FundingLedger._sealed(db, row):
            return True
        if row["status"] not in {"uncertain", "pending"}:
            return False
        folder = self.global_out / "analysis/restart04/abandoned_charges"
        return any(_verified_authority(self.global_out, path, row) for path in folder.glob("*.json"))

    def _ready(self, connection):
        halt = connection.execute("SELECT value FROM settings WHERE key='halt_reason'").fetchone()
        if halt:
            raise BudgetError(halt[0])
        for row in connection.execute("SELECT * FROM requests WHERE status IN ('pending','uncertain')"):
            if not self._sealed(connection, row):
                raise PendingPaidRequest("An unsealed unresolved paid intent blocks further paid work")


class AttemptGuard(SharedBudgetGuard):
    def __init__(self, path, *, ledger, context):
        self.context = context
        super().__init__(path, base.NAMESPACE, ledger=ledger)

    def _request_id(self, local_id):
        if not isinstance(local_id, str) or len(self.context["attempt_id"] + ":" + local_id) > 110:
            raise BudgetError("Attempt-qualified request ID exceeds the original bound")
        return super()._request_id(self.context["attempt_id"] + ":" + local_id)


class AttemptFunding(base.SingleAttemptFunding):
    def execute(self, local_request_id, kind, model, wire_payload, reserve_usd, post):
        check_active_context(self.ledger.global_out, self.guard.context)
        physical = self.guard._request_id(local_request_id)
        if self.ledger.lookup(physical) is None:
            body = _wire_bytes(wire_payload, model)
            endpoint = "/chat/completions" if kind == "chat" else "/images"
            binding = {"request_id": physical, "request_hash": request_digest({"endpoint": endpoint, "wire_body_sha256": _sha(body)}),
                "kind": kind, "requested_model": model, "endpoint": endpoint, "wire_body_sha256": _sha(body), "wire_body_bytes": len(body)}
            owner = {"binding": binding, "context": self.guard.context, "producer_pid": os.getpid(),
                "producer_process_start_ticks": process_start_ticks(os.getpid()), "reserved_units": _units(reserve_usd)}
            owner["record_hash"] = _sha(canonical(owner).encode())
            _save_once(self.evidence_dir / "paid_callback_owners" / (_sha(physical.encode()) + ".json"), owner)
        def diagnosed_post(body, binding):
            try:
                return post(body, binding)
            except BaseException as error:
                if self.evidence_dir is not None:
                    _save_once(self.evidence_dir / "callback_diagnostics" / (_sha(binding["request_id"].encode()) + ".json"),
                        {**binding, "category": "paid_callback_exception", "exception_class": safe_class(error),
                         "callback_observation_returned": False, "provider_response_observed": "unknown"})
                raise
        return super().execute(local_request_id, kind, model, wire_payload, reserve_usd, diagnosed_post)


def check_active_context(global_out, context):
    context = validate_context(global_out, context)
    if context.get("kind") == "shadow" or context["attempt_id"].startswith("shadows."):
        path = _safe(Path(context["working_root"]) / "attempt.json", root=global_out)
        record = read_json(path)
        body = {k: v for k, v in record.items() if k != "record_hash"}
        if (record.get("record_hash") != _sha(canonical(body).encode())
                or any(record.get(k) != context.get(k) for k in context)
                or (path.parent / "abandonment.json").exists()):
            raise BudgetError("The shadow workflow attempt is not active")
    else:
        from .attempts import AttemptStore
        store = AttemptStore(global_out)
        store.verify()
        if store.current() != context:
            raise BudgetError("Paid dispatch is not bound to the active checkpoint attempt")
    return context


def create_guard(aggregate_path, *, global_out, attempt_context, evidence_dir, ledger=None):
    path = _safe(aggregate_path)
    context = validate_context(global_out, attempt_context)
    if ledger is None:
        if path.resolve() != base.DEFAULT_AGGREGATE_PATH.resolve():
            raise BudgetError("Production restart04 requires the unchanged original aggregate")
        ledger = FundingLedger(path, global_out=global_out)
    if not isinstance(ledger, FundingLedger) or ledger.global_out != Path(global_out).absolute():
        raise BudgetError("An exact original capped restart ledger is required")
    return AttemptFunding(AttemptGuard(path, ledger=ledger, context=context), evidence_dir)


def preflight_guard(aggregate_path, *, global_out, ledger=None):
    path = _safe(aggregate_path)
    if ledger is None:
        if path.resolve() != base.DEFAULT_AGGREGATE_PATH.resolve():
            raise BudgetError("Preflight requires the unchanged original aggregate")
        ledger = FundingLedger(path, global_out=global_out)
    return base.SingleAttemptFunding(SharedBudgetGuard(path, base.NAMESPACE, ledger=ledger))
