"""Bounded, explicitly authorized HTTP429 retry accounting for the new study.

Each physical POST has its own reservation and attempt ID. Failed429 POSTs keep
their full unknown-charge reserves under immutable evidence-bound seals. Only
the two new namespaces are eligible; other errors never authorize a retry.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard, _wire_bytes
from experiments.triad_reliable_v4 import funding as previous_reliable_funding
from scripts import triad_max_transport_v1 as previous_transport
from visual_collusion.budget import BudgetError, PendingPaidRequest, canonical, request_digest, _units


ROOT = Path(__file__).resolve().parents[2]
MAIN_NAMESPACE = "triad_repaired_v6"
CALIBRATION_NAMESPACE = "reliable_v4_calibration"
NAMESPACES = {MAIN_NAMESPACE, CALIBRATION_NAMESPACE}
CALIBRATION_CAP_UNITS = 2_000_000_000
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 120
BACKOFF_SECONDS = (30, 60)
TABLE = "reliable_v4_http429_seals"
PREVIOUS_HELPER_SHA256 = "6f43bcc5865535fb5672cc5ef0f22c1faeef02304ad9a5cd80b587b35da963cc"
PREVIOUS_RELIABLE_FUNDING_SHA256 = "264e64e9871206e5295b272cf15f1d57914d091fca4a421201c7e4e01e14de94"
AUTHORIZATION = {
    "revision": "triad_repaired_v6_http429",
    "scope": "New calibration and restarted main study only; at most three predeclared physical attempts for HTTP429 with no model response.",
    "namespaces": sorted(NAMESPACES), "max_attempts": MAX_ATTEMPTS,
    "exact_inputs": "All attempts preserve the exact same frozen wire bytes, model, route, schema and sampling. Each has a distinct physical request ID and reserve.",
    "retry_after_max_seconds": MAX_RETRY_AFTER_SECONDS, "backoff_seconds": list(BACKOFF_SECONDS),
    "preserved": "Original aggregate $40 inference/$50 OpenRouter cash caps, Codex excluded, all prior costs and unknown reserves including the aborted A2.",
    "unknown_charge": "Every429 failed POST keeps its entire reservation; no cost or receipt is fabricated and unknown never becomes zero.",
    "not_retried": "Other HTTP statuses, timeouts, transport/account/budget/integrity failures, model semantic answers and reviewer outcomes.",
    "calibration_subcap_usd": 2,
}
POLICY_SHA256 = hashlib.sha256(canonical(AUTHORIZATION).encode()).hexdigest()
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,100}")
_SHA = re.compile(r"[a-f0-9]{64}")
_PROVIDER_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}")


def _require(value, message):
    if not value:
        raise BudgetError(message)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _safe_file(path):
    path = Path(path).absolute()
    _require(not any(item.is_symlink() for item in (path, *path.parents)) and path.is_file(), "Required funding evidence is missing or a symlink")
    return path


def _file_sha(path):
    return _sha(_safe_file(path).read_bytes())


def verify_dependencies():
    _require(_file_sha(ROOT / "scripts/triad_max_transport_v1.py") == PREVIOUS_HELPER_SHA256, "Prior immutable transport helper changed")
    _require(_file_sha(ROOT / "experiments/triad_reliable_v4/funding.py") == PREVIOUS_RELIABLE_FUNDING_SHA256,
             "Prior immutable reliable funding verifier changed")
    previous_transport.verify_dependencies()


def _save_bytes_once(path, raw):
    path = Path(path).absolute()
    _require(not any(item.is_symlink() for item in (path, *path.parents)), "Funding evidence cannot follow a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            import os
            os.fsync(stream.fileno())
    except FileExistsError:
        _require(path.read_bytes() == raw, "Immutable funding evidence changed")
    return path


def _save_json_once(path, value):
    return _save_bytes_once(path, (canonical(value) + "\n").encode())


class Known429(Exception):
    """Safe native HTTP429 observation; contains no provider body or headers."""

    def __init__(self, retry_after_seconds=None, provider_request_id=None):
        if retry_after_seconds is not None:
            if (type(retry_after_seconds) not in {int, float} or not math.isfinite(retry_after_seconds)
                    or not 0 <= retry_after_seconds <= MAX_RETRY_AFTER_SECONDS):
                raise BudgetError("Retry-After exceeds the supported safe retry interval")
            retry_after_seconds = float(retry_after_seconds)
        self.retry_after_seconds = retry_after_seconds
        self.provider_request_id = (provider_request_id if isinstance(provider_request_id, str)
                                    and _PROVIDER_ID.fullmatch(provider_request_id) else None)
        super().__init__("Observed HTTP429 without a model response")


class RateLimitExhausted(PendingPaidRequest):
    def __init__(self, attempt_trace):
        self.attempt_trace = deepcopy(attempt_trace)
        super().__init__("All three predeclared HTTP429 attempts exhausted; failed POST costs remain reserved")


def _eligible_429(row):
    return (row is not None and isinstance(row["request_id"], str)
            and row["request_id"].split(":", 1)[0] in NAMESPACES
            and re.search(r"\.attempt[123]$", row["request_id"]) is not None
            and row["status"] == "uncertain" and row["reason"] == previous_transport.GUARD_FAILURE_REASON
            and type(row["reserved_units"]) is int and row["reserved_units"] > 0
            and all(row[key] is None for key in ("actual_units", "response_json", "receipt_json", "result_hash", "finished")))


def _429_evidence(row, diagnostic_path):
    _require(_eligible_429(row), "Only a new-study response-less429 attempt can be sealed")
    diagnostic_path = _safe_file(diagnostic_path)
    prefix = _sha(row["request_id"].encode())
    _require(diagnostic_path.name == prefix + ".429.json", "429 diagnostic filename differs from its attempt")
    diagnostic = json.loads(diagnostic_path.read_text())
    required = {"request_id", "request_hash", "wire_body_sha256", "wire_body_bytes", "endpoint", "http_status", "category", "response_observed", "retry_after_seconds", "provider_request_id", "observed_at_unix"}
    _require(isinstance(diagnostic, dict) and set(diagnostic) == required, "429 diagnostic schema differs")
    _require(diagnostic["category"] == "http_429_no_model_response" and diagnostic["http_status"] == 429
             and type(diagnostic["http_status"]) is int and diagnostic["response_observed"] is False, "429 evidence does not establish the authorized retry condition")
    Known429(diagnostic["retry_after_seconds"], diagnostic["provider_request_id"])
    _require(diagnostic["provider_request_id"] is None or _PROVIDER_ID.fullmatch(diagnostic["provider_request_id"]), "Unsafe provider request identifier")
    _require(type(diagnostic["observed_at_unix"]) in {int, float} and math.isfinite(diagnostic["observed_at_unix"]) and diagnostic["observed_at_unix"] > 0, "429 observation time is invalid")
    binding_path = _safe_file(diagnostic_path.with_name(prefix + ".binding.json"))
    binding = json.loads(binding_path.read_text())
    binding_keys = {"request_id", "request_hash", "kind", "requested_model", "endpoint", "wire_body_sha256", "wire_body_bytes", "wire_path", "logical_request_id", "attempt", "reserve_units"}
    _require(isinstance(binding, dict) and set(binding) == binding_keys, "429 exact wire binding schema differs")
    endpoint = "/chat/completions" if row["kind"] == "chat" else "/images" if row["kind"] == "image" else None
    expected = {"request_id": row["request_id"], "request_hash": row["request_hash"], "kind": row["kind"], "requested_model": row["model"], "endpoint": endpoint}
    _require(endpoint is not None and all(binding.get(key) == value for key, value in expected.items()), "429 binding differs from its reserved request")
    _require(binding["reserve_units"] == row["reserved_units"]
             and type(binding["attempt"]) is int and binding["attempt"] in {1, 2, 3}
             and binding["logical_request_id"] + f".attempt{binding['attempt']}" == row["request_id"], "429 reserve or attempt identity changed")
    _require(all(diagnostic[key] == binding[key] for key in ("request_id", "request_hash", "wire_body_sha256", "wire_body_bytes", "endpoint")), "429 diagnostic and exact wire binding differ")
    wire_path = _safe_file(binding.get("wire_path", ""))
    raw = wire_path.read_bytes()
    _require(_sha(raw) == binding.get("wire_body_sha256") and len(raw) == binding.get("wire_body_bytes"), "429 exact original wire bytes changed")
    _wire_bytes(raw, row["model"])
    _require(request_digest({"endpoint": endpoint, "wire_body_sha256": _sha(raw)}) == row["request_hash"], "429 exact wire digest differs from its reservation")
    return {"diagnostic_path": str(diagnostic_path), "diagnostic_sha256": _file_sha(diagnostic_path),
            "binding_path": str(binding_path), "binding_sha256": _file_sha(binding_path),
            "wire_path": str(wire_path), "wire_sha256": _sha(raw), "wire_bytes": len(raw)}


def _verified_429_seal(db, row):
    if not _eligible_429(row):
        return None
    found = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
    if found is None:
        return None
    stored = db.execute(f"SELECT * FROM {TABLE} WHERE request_id=?", (row["request_id"],)).fetchone()
    if stored is None:
        return None
    seal = dict(stored)
    checksum = seal.pop("record_sha256")
    try:
        verify_dependencies()
        _require(seal["policy_sha256"] == POLICY_SHA256 and seal["snapshot_json"] == canonical(dict(row))
                 and _sha(canonical(seal).encode()) == checksum, "429 immutable seal changed")
        for name in ("diagnostic", "binding"):
            _require(_file_sha(seal[name + "_path"]) == seal[name + "_sha256"], "429 retained evidence changed")
        _require(_file_sha(seal["wire_path"]) == seal["wire_sha256"] and _safe_file(seal["wire_path"]).stat().st_size == seal["wire_bytes"], "429 original wire bytes changed")
    except (BudgetError, ValueError, TypeError, KeyError, OSError):
        return None
    return {**seal, "record_sha256": checksum}


class FundingLedger(previous_transport.TransportLedger):
    """Original capped ledger plus new immutable429 seals and calibration cap."""

    def __init__(self, path, cap_usd=40):
        verify_dependencies()
        super().__init__(path, cap_usd)
        with self._connect() as db:
            db.executescript(f"""
                CREATE TABLE IF NOT EXISTS {TABLE} (
                    request_id TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL,
                    diagnostic_path TEXT NOT NULL, diagnostic_sha256 TEXT NOT NULL,
                    binding_path TEXT NOT NULL, binding_sha256 TEXT NOT NULL,
                    wire_path TEXT NOT NULL, wire_sha256 TEXT NOT NULL, wire_bytes INTEGER NOT NULL,
                    policy_sha256 TEXT NOT NULL, recorded_at REAL NOT NULL, record_sha256 TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS {TABLE}_no_update BEFORE UPDATE ON {TABLE}
                BEGIN SELECT RAISE(ABORT, 'Reliable study429 seals are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS {TABLE}_no_delete BEFORE DELETE ON {TABLE}
                BEGIN SELECT RAISE(ABORT, 'Reliable study429 seals are immutable'); END;
            """)

    @staticmethod
    def _sealed(db, row):
        verify_dependencies()
        # An older valid unknown-charge seal remains valid under its original
        # namespace and policy. Verify that immutable evidence with the exact
        # frozen prior verifier; do not rewrite it under this revision's policy
        # or release its still-unknown reservation.
        return (previous_transport.TransportLedger._sealed(db, row)
                or previous_reliable_funding._verified_429_seal(db, row) is not None
                or _verified_429_seal(db, row) is not None)

    def reserve(self, request_id, kind, model, request_hash, reserve_usd):
        verify_dependencies()
        _require(isinstance(request_id, str), "Paid request identity must be text")
        if not request_id.startswith(CALIBRATION_NAMESPACE + ":"):
            return super().reserve(request_id, kind, model, request_hash, reserve_usd)
        _require(isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,180}", request_id), "Invalid calibration request identity")
        _require(kind in {"chat", "image"} and isinstance(model, str) and isinstance(request_hash, str) and _SHA.fullmatch(request_hash), "Invalid calibration paid intent")
        amount = _units(reserve_usd)
        _require(amount > 0, "Calibration requires a positive conservative reservation")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if old is not None:
                _require(old["request_hash"] == request_hash and old["kind"] == kind and old["model"] == model, "Calibration ID reused with different paid intent")
                if old["status"] == "completed":
                    return self._record(old)
                raise PendingPaidRequest("Calibration attempt is already reserved; no duplicate POST")
            self._ready(db)
            subset = db.execute("SELECT COALESCE(SUM(COALESCE(actual_units,reserved_units)),0) FROM requests WHERE request_id LIKE ?", (CALIBRATION_NAMESPACE + ":%",)).fetchone()[0]
            _require(subset + amount <= CALIBRATION_CAP_UNITS, "The fixed $2 calibration subcap cannot cover the next attempt")
            used = db.execute("SELECT COALESCE(SUM(COALESCE(actual_units,reserved_units)),0) FROM requests").fetchone()[0]
            _require(used + amount <= self.cap_units, "Original aggregate cap cannot cover calibration")
            db.execute("INSERT INTO requests(request_id,kind,model,request_hash,reserved_units,status,created) VALUES (?,?,?,?,?,'pending',?)", (request_id, kind, model, request_hash, amount, time.time()))
            return self._record(db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone())

    def seal_known_429(self, request_id, diagnostic_path):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            evidence = _429_evidence(row, diagnostic_path)
            old = db.execute(f"SELECT * FROM {TABLE} WHERE request_id=?", (request_id,)).fetchone()
            if old is not None:
                seal = _verified_429_seal(db, row)
                _require(seal is not None and all(seal[key] == value for key, value in evidence.items()), "Existing429 seal binds different evidence")
                return seal
            seal = {"request_id": request_id, "snapshot_json": canonical(dict(row)), **evidence,
                    "policy_sha256": POLICY_SHA256, "recorded_at": time.time()}
            seal["record_sha256"] = _sha(canonical(seal).encode())
            columns = list(seal)
            db.execute(f"INSERT INTO {TABLE} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(seal[key] for key in columns))
            return seal

    def verified_429_loss(self, request_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            seal = _verified_429_seal(db, row)
            _require(seal is not None, "No verified429 attempt seal exists")
            return seal


class Bounded429Funding:
    """Drop-in guard facade; retries only a safe native429 exception."""

    def __init__(self, guard, evidence_dir, *, sleeper=time.sleep, on_attempt=None):
        _require(guard.namespace in NAMESPACES and isinstance(guard.ledger, FundingLedger), "Retry funding requires its new namespace and original capped FundingLedger")
        self.guard, self.evidence_dir = guard, Path(evidence_dir).absolute()
        self.sleeper, self.on_attempt = sleeper, on_attempt
        _require(callable(sleeper) and (on_attempt is None or callable(on_attempt)), "Funding hooks must be callable")

    def __getattr__(self, name):
        return getattr(self.guard, name)

    def _wait(self, delay):
        # Even a maximum Retry-After never creates one >30-second blocking sleep.
        remaining = float(delay)
        while remaining > 0:
            chunk = min(30.0, remaining)
            self.sleeper(chunk)
            remaining -= chunk

    def _attempt_paths(self, physical_id):
        root = self.evidence_dir / "retry_funding" / "attempts"
        stem = _sha(physical_id.encode())
        return root / (stem + ".binding.json"), root / (stem + ".429.json")

    def execute(self, local_request_id, kind, model, wire_payload, reserve_usd, post):
        verify_dependencies()
        _require(isinstance(local_request_id, str) and _ID.fullmatch(local_request_id), "Logical retry identity is invalid or too long")
        _require(callable(post), "A guarded native POST callback is required")
        body = _wire_bytes(wire_payload, model)
        wire_sha, reserve_units = _sha(body), _units(reserve_usd)
        _require(reserve_units > 0, "Every attempt requires its full conservative reservation")
        logical_id = self.namespace + ":" + local_request_id
        descriptor = {"logical_request_id": logical_id, "kind": kind, "model": model,
                      "wire_body_sha256": wire_sha, "wire_body_bytes": len(body),
                      "reserve_units_per_attempt": reserve_units, "max_attempts": MAX_ATTEMPTS,
                      "policy_sha256": POLICY_SHA256}
        root = self.evidence_dir / "retry_funding"
        logical_stem = _sha(logical_id.encode())
        _save_json_once(root / "logical_requests" / (logical_stem + ".json"), descriptor)
        wire_path = _save_bytes_once(root / "wire_inputs" / (wire_sha + ".json"), body)
        terminal_path = root / "logical_results" / (logical_stem + ".json")
        trace = []
        for number in range(1, MAX_ATTEMPTS + 1):
            attempt_id = local_request_id + f".attempt{number}"
            physical_id = self.guard._request_id(attempt_id)
            binding_path, diagnostic_path = self._attempt_paths(physical_id)
            old = self.ledger.lookup(physical_id)
            if old is not None:
                endpoint = "/chat/completions" if kind == "chat" else "/images"
                expected_hash = request_digest({"endpoint": endpoint, "wire_body_sha256": wire_sha})
                self.guard._matching(old, kind, model, expected_hash)
                _require(_units(old["reserved_usd"]) == reserve_units, "Resumed attempt reservation differs from its frozen allowance")
            if old is not None and old["status"] != "completed":
                _require(diagnostic_path.exists(), "Unresolved attempt has no verified429 diagnostic; no retry")
                seal = self.ledger.seal_known_429(physical_id, diagnostic_path)
                diagnostic = json.loads(_safe_file(diagnostic_path).read_text())
                item = {"attempt": number, "request_id": physical_id, "status": "http429_unknown_charge",
                        "reserved_usd": reserve_units / 1e9, "actual_cost_usd": None,
                        "diagnostic_path": str(diagnostic_path), "seal_record_sha256": seal["record_sha256"],
                        "wire_body_sha256": wire_sha}
                trace.append(item)
                if number < MAX_ATTEMPTS:
                    next_id = self.guard._request_id(local_request_id + f".attempt{number + 1}")
                    if self.ledger.lookup(next_id) is None:
                        self._wait(max(BACKOFF_SECONDS[number - 1], diagnostic["retry_after_seconds"] or 0))
                    continue
                _save_json_once(terminal_path, {"status": "exhausted429", "logical_request_id": logical_id, "attempt_trace": trace})
                raise RateLimitExhausted(trace)

            rate_limit = None

            def observed_post(exact_body, binding):
                nonlocal rate_limit
                _require(exact_body == body and binding["wire_body_sha256"] == wire_sha, "Retry changed the exact original wire bytes")
                _save_json_once(binding_path, {**binding, "wire_path": str(wire_path),
                                             "logical_request_id": logical_id, "attempt": number,
                                             "reserve_units": reserve_units})
                if self.on_attempt is not None:
                    self.on_attempt(exact_body, deepcopy(binding))
                try:
                    return post(exact_body, binding)
                except Known429 as error:
                    rate_limit = error
                    _save_json_once(diagnostic_path, {
                        **{key: binding[key] for key in ("request_id", "request_hash", "wire_body_sha256", "wire_body_bytes", "endpoint")},
                        "category": "http_429_no_model_response", "http_status": 429, "response_observed": False,
                        "retry_after_seconds": error.retry_after_seconds, "provider_request_id": error.provider_request_id,
                        "observed_at_unix": time.time()})
                    raise

            try:
                result = self.guard.execute(attempt_id, kind, model, body, reserve_usd, observed_post)
            except PendingPaidRequest:
                if rate_limit is None:
                    raise
                seal = self.ledger.seal_known_429(physical_id, diagnostic_path)
                trace.append({"attempt": number, "request_id": physical_id, "status": "http429_unknown_charge",
                              "reserved_usd": reserve_units / 1e9, "actual_cost_usd": None,
                              "diagnostic_path": str(diagnostic_path), "seal_record_sha256": seal["record_sha256"],
                              "wire_body_sha256": wire_sha})
                if number == MAX_ATTEMPTS:
                    _save_json_once(terminal_path, {"status": "exhausted429", "logical_request_id": logical_id, "attempt_trace": trace})
                    raise RateLimitExhausted(trace) from None
                self._wait(max(BACKOFF_SECONDS[number - 1], rate_limit.retry_after_seconds or 0))
                continue
            trace.append({"attempt": number, "request_id": physical_id, "status": "completed",
                          "reserved_usd": reserve_units / 1e9, "actual_cost_usd": result["receipt"]["cost_usd"],
                          "wire_body_sha256": wire_sha})
            _save_json_once(terminal_path, {"status": "completed", "logical_request_id": logical_id, "attempt_trace": trace})
            return {**result, "attempt_trace": deepcopy(trace), "logical_request_id": logical_id}
        raise AssertionError("Bounded retry loop exceeded its fixed allowance")


def create_guard(aggregate_path, namespace, evidence_dir, *, ledger=None, sleeper=time.sleep, on_attempt=None):
    _require(namespace in NAMESPACES, "Only the new study and calibration namespaces may use bounded429 retries")
    ledger = ledger if ledger is not None else FundingLedger(aggregate_path, 40)
    guard = SharedBudgetGuard(aggregate_path, namespace, ledger=ledger)
    return Bounded429Funding(guard, evidence_dir, sleeper=sleeper, on_attempt=on_attempt)
