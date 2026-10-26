"""Narrow, immutable continuation authority for new response-less transport losses.

Every lost request keeps its original uncertain row and complete reservation.
A seal makes only its dependent trial missing; it never authorizes repeating
that request. Unclassified failures, costs, account errors and interrupts stop
the study. This module performs no network work.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
import time

from visual_collusion.budget import BudgetError, PendingPaidRequest, canonical, request_digest

from .budget_guard import (
    SharedBudgetGuard, DEFAULT_NAMESPACE, DEFAULT_AGGREGATE_PATH,
    _ROOT, _FROZEN_V5_SHA256, _wire_bytes, readonly_budget_snapshot,
)


CALLBACK_UNCERTAIN_REASON = "Paid callback failed or lacked a conclusive matching receipt; no retry"
LOSS_TABLE = "terminal_transport_losses_pressure_interview_v1"
AUTHORIZATION = {
    "user_instruction": "Proceed and execute the newest main run and finish all runs. Ensure that before you run the entire experiment pipeline and workflow works.",
    "prior_independent_continuation": "proceed with the entire thing now",
    "scope": "Continue independent predeclared cases or alternative continuations after a safely observed response-less transport loss in the pressure_interview_v1 request namespace. Its dependent turn remains terminal missing, never a retried request.",
    "namespace": DEFAULT_NAMESPACE,
    "eligible_causes": "Recorded HTTP 408/429/5xx or allowlisted network/JSON decoding failures with exact request/wire binding and a response-less uncertain row.",
    "blocked_causes": "Other HTTP statuses, authentication/credit/account/model/price/context/budget errors, missing or mismatched costs, partial provider response evidence, cancellation/interrupts and unclassified/programmer errors.",
    "preserved": "All original request columns and unknown-charge reservations, the original aggregate $40 inference cap and $50 OpenRouter cash ceiling, fixed model routes and schedule, and zero paid retries. Codex remains excluded as instructed.",
    "proof": "Immutable request-row snapshot plus the exact hashed, saved safe diagnostic. No diagnostic provider body, credential, error message or unrestricted header is accepted.",
}
POLICY_SHA256 = request_digest(AUTHORIZATION)


class TerminalMissingRequest(PendingPaidRequest):
    """The exact request is sealed missing and must never be POSTed again."""


def _legacy_v5_class():
    script = _ROOT / "scripts" / "run_lossless_webp_v5.py"
    if script.is_symlink() or hashlib.sha256(script.read_bytes()).hexdigest() != _FROZEN_V5_SHA256:
        raise BudgetError("Frozen aggregate continuation controller differs")
    scripts = str(script.parent)
    added = scripts not in sys.path
    if added:
        sys.path.insert(0, scripts)
    try:
        module = importlib.import_module("run_lossless_webp_v5")
        if Path(module.__file__).resolve() != script.resolve():
            raise BudgetError("Aggregate continuation controller resolves elsewhere")
        return module.V5Ledger
    finally:
        if added:
            sys.path.remove(scripts)


_LegacyV5Ledger = _legacy_v5_class()


def _hex(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _eligible(row, namespace):
    return (row is not None and row["request_id"].startswith(namespace + ":")
            and row["status"] == "uncertain" and row["reason"] == CALLBACK_UNCERTAIN_REASON
            and row["actual_units"] is None and all(row[key] is None for key in
                ("response_json", "receipt_json", "result_hash")))


def _read_record(path, expected_sha):
    path = Path(path)
    if not path.is_absolute() or not _hex(expected_sha):
        raise BudgetError("A saved diagnostic path and SHA-256 are required")
    try:
        if path.name != path.resolve().name or any(part.is_symlink() for part in (path, *path.parents)):
            raise BudgetError("Diagnostic evidence cannot use symlinks")
        if not path.is_file() or path.stat().st_size > 16_384:
            raise BudgetError("Saved diagnostic is missing or over its safe bound")
        raw = path.read_bytes()
    except (OSError, RuntimeError):
        raise BudgetError("Saved diagnostic cannot be read safely") from None
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise BudgetError("Saved diagnostic changed")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate diagnostic key")
            result[key] = value
        return result
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        canonical(result)
    except (UnicodeError, ValueError, TypeError):
        raise BudgetError("Saved diagnostic must be one finite safe JSON object") from None
    return result


def _validate_diagnostic(diagnostic, row, namespace):
    if not isinstance(diagnostic, dict):
        raise BudgetError("A safely recorded transport diagnostic is required")
    required = {"request_id", "request_hash", "wire_body_sha256", "category",
                "exception_class", "response_observed", "record_path", "record_sha256"}
    optional = {"http_status", "reason_class", "endpoint", "wire_body_bytes", "local_request_id"}
    if not required <= set(diagnostic) or set(diagnostic) - required - optional:
        raise BudgetError("Diagnostic fields differ from the safe allowlist")
    result = deepcopy(diagnostic)
    core = {key: value for key, value in result.items() if key not in {"record_path", "record_sha256"}}
    if _read_record(result["record_path"], result["record_sha256"]) != core:
        raise BudgetError("Diagnostic does not match its saved observation")
    if not _eligible(row, namespace):
        raise PendingPaidRequest("Only an exact new-namespace response-less uncertain row may be sealed")
    if result["request_id"] != row["request_id"] or result["request_hash"] != row["request_hash"]:
        raise BudgetError("Diagnostic belongs to another request")
    endpoint = "/chat/completions" if row["kind"] == "chat" else "/images"
    if row["kind"] not in {"chat", "image"} or not _hex(result["wire_body_sha256"]):
        raise BudgetError("Diagnostic wire identity is invalid")
    if request_digest({"endpoint": endpoint, "wire_body_sha256": result["wire_body_sha256"]}) != row["request_hash"]:
        raise BudgetError("Diagnostic wire bytes do not bind the reserved request")
    if "endpoint" in result and result["endpoint"] != endpoint:
        raise BudgetError("Diagnostic endpoint differs")
    if "wire_body_bytes" in result and (type(result["wire_body_bytes"]) is not int or not 0 < result["wire_body_bytes"] < 10 * 1024 * 1024):
        raise BudgetError("Diagnostic body bound is invalid")
    if "local_request_id" in result and result["request_id"] != namespace + ":" + result["local_request_id"]:
        raise BudgetError("Diagnostic local request identity differs")
    if result["response_observed"] is not False:
        raise PendingPaidRequest("A provider response or missing cost cannot be sealed as no response")
    category, name = result["category"], result["exception_class"]
    if category == "http_error":
        status = result.get("http_status")
        eligible = (name == "HTTPError" and type(status) is int
                    and (status in (408, 429) or 500 <= status <= 599))
    elif category == "transport_failure":
        network_errors = {"TimeoutError", "ConnectionError", "ConnectionResetError",
                          "ConnectionRefusedError", "BrokenPipeError", "OSError", "gaierror"}
        eligible = name in network_errors
        if name == "URLError":
            eligible = result.get("reason_class") in network_errors
        eligible = eligible and result.get("http_status") is None
    elif category == "decoding_failure":
        eligible = name in {"JSONDecodeError", "UnicodeDecodeError"} and result.get("http_status") is None
    else:
        eligible = False
    if not eligible:
        raise PendingPaidRequest("This safely observed error is not a permitted terminal transport loss")
    return result


class LiveLossLedger(_LegacyV5Ledger):
    """Keep old exact seals and add only this revision's new immutable authority."""
    def __init__(self, path, cap_usd=40, *, namespace=DEFAULT_NAMESPACE):
        if namespace != DEFAULT_NAMESPACE:
            raise BudgetError("This loss policy is bound to its frozen experiment namespace")
        self.live_loss_namespace = namespace
        super().__init__(path, cap_usd)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS terminal_transport_losses_pressure_interview_v1 (
                    request_id TEXT PRIMARY KEY, namespace TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, diagnostic_json TEXT NOT NULL,
                    diagnostic_sha256 TEXT NOT NULL, policy_sha256 TEXT NOT NULL,
                    recorded_at REAL NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS pressure_interview_losses_no_update
                BEFORE UPDATE ON terminal_transport_losses_pressure_interview_v1
                BEGIN SELECT RAISE(ABORT, 'Live terminal transport losses are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS pressure_interview_losses_no_delete
                BEFORE DELETE ON terminal_transport_losses_pressure_interview_v1
                BEGIN SELECT RAISE(ABORT, 'Live terminal transport losses are immutable'); END;
            """)

    def _sealed_live(self, db, row):
        seal = db.execute("SELECT * FROM " + LOSS_TABLE + " WHERE request_id=?", (row["request_id"],)).fetchone()
        if (seal is None or seal["namespace"] != self.live_loss_namespace
                or seal["policy_sha256"] != POLICY_SHA256 or not _eligible(row, self.live_loss_namespace)
                or seal["snapshot_json"] != canonical(dict(row))):
            return False
        try:
            diagnostic = json.loads(seal["diagnostic_json"])
            if request_digest(diagnostic) != seal["diagnostic_sha256"]:
                return False
            _validate_diagnostic(diagnostic, row, self.live_loss_namespace)
        except (ValueError, TypeError, BudgetError):
            return False
        return True

    def _sealed(self, db, row):
        # A new namespace cannot borrow a broader prior policy's seal. Old
        # request namespaces retain their exact, original legacy authority.
        if row["request_id"].startswith(self.live_loss_namespace + ":"):
            return self._sealed_live(db, row)
        return _LegacyV5Ledger._sealed(db, row)

    def seal_transport_loss(self, request_id):
        if request_id.startswith(self.live_loss_namespace + ":"):
            raise BudgetError("New study losses require the separately bound live diagnostic policy")
        return super().seal_transport_loss(request_id)

    def is_live_terminal_loss(self, request_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            return row is not None and self._sealed_live(db, row)

    def seal_live_transport_loss(self, request_id, diagnostic):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            safe = _validate_diagnostic(diagnostic, row, self.live_loss_namespace)
            old = db.execute("SELECT * FROM " + LOSS_TABLE + " WHERE request_id=?", (request_id,)).fetchone()
            if old is not None:
                if not self._sealed_live(db, row) or old["diagnostic_json"] != canonical(safe):
                    raise BudgetError("The immutable loss seal differs from the observed request")
                return dict(old)
            db.execute("INSERT INTO " + LOSS_TABLE + " VALUES (?,?,?,?,?,?,?)",
                       (request_id, self.live_loss_namespace, canonical(dict(row)), canonical(safe),
                        request_digest(safe), POLICY_SHA256, time.time()))
            return dict(db.execute("SELECT * FROM " + LOSS_TABLE + " WHERE request_id=?", (request_id,)).fetchone())


class TransportContinuationGuard(SharedBudgetGuard):
    """Same capped funding gate with narrow separately recorded missingness."""
    def __init__(self, aggregate_path, namespace=DEFAULT_NAMESPACE, *, ledger=None):
        path = Path(aggregate_path).resolve()
        readonly_budget_snapshot(aggregate_path)
        if namespace != DEFAULT_NAMESPACE:
            raise BudgetError("This continuation authority is bound to pressure_interview_v1")
        if ledger is None:
            if path != DEFAULT_AGGREGATE_PATH.resolve():
                raise BudgetError("Production funding must use the original aggregate ledger")
            ledger = LiveLossLedger(path, namespace=namespace)
        if not isinstance(ledger, LiveLossLedger) or ledger.live_loss_namespace != namespace:
            raise BudgetError("A matching live-loss ledger is required; old loss authority is not expanded implicitly")
        super().__init__(aggregate_path, namespace, ledger=ledger)

    def seal_transport_loss(self, local_request_id, diagnostic):
        return self.ledger.seal_live_transport_loss(self._request_id(local_request_id), diagnostic)

    def execute(self, local_request_id, kind, model, wire_payload, reserve_usd, post):
        global_id = self._request_id(local_request_id)
        row = self.ledger.lookup(global_id)
        if row is not None and self.ledger.is_live_terminal_loss(global_id):
            body = _wire_bytes(wire_payload, model)
            endpoint = "/chat/completions" if kind == "chat" else "/images"
            expected = request_digest({"endpoint": endpoint, "wire_body_sha256": hashlib.sha256(body).hexdigest()})
            self._matching(row, kind, model, expected)
            raise TerminalMissingRequest("Exact request is terminal missing; no retry; unknown reservation retained: " + global_id)
        return super().execute(local_request_id, kind, model, wire_payload, reserve_usd, post)

