"""One funding gate for the original aggregate allowance, never a new allowance.

This module has no network implementation. A future paid adapter must send the
exact bytes passed to its ``post`` callback and return the observed receipt.
The offline CLI uses only ``readonly_budget_snapshot`` on the actual ledger.
Execution tests use temporary ledger fixtures exclusively.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import re
import sqlite3
import sys

from visual_collusion.budget import (
    BudgetError, PendingPaidRequest, canonical, request_digest, _units,
)


AGGREGATE_CAP_USD = 40
OPENROUTER_CASH_CEILING_USD = 50
MAX_WIRE_BYTES = 10 * 1024 * 1024
DEFAULT_NAMESPACE = "pressure_interview_v1"
_COMPLETION_INTERRUPTED = "Response completion interrupted; reservation retained; no retry"
_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGGREGATE_PATH = _ROOT / "runs" / "main" / "budget.sqlite3"
_FROZEN_V5_SHA256 = "b192e775157b96428f8492ed4231290d51522f80f2b5bff0e5b7abd767c5bfd8"


def readonly_budget_snapshot(aggregate_path):
    """Read an existing aggregate without running a ledger constructor or DDL.

    This returns accounting metadata only, never response/prompt/secret fields.
    Unresolved requests continue to count at their entire reservation amounts,
    including old sealed losses whose unknown final charges remain unknown.
    """
    path = Path(aggregate_path)
    if not path.is_file() or path.is_symlink():
        raise BudgetError("An existing nonsymlink aggregate ledger is required")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        settings = dict(db.execute("SELECT key,value FROM settings"))
        if int(settings.get("cap_units", -1)) != 40_000_000_000:
            raise BudgetError("The original aggregate cap must remain $40")
        rows = db.execute(
            "SELECT request_id,status,actual_units,reserved_units FROM requests "
            "ORDER BY created,request_id"
        ).fetchall()
        actual = sum(row["actual_units"] or 0 for row in rows)
        reserved = sum(row["reserved_units"] for row in rows if row["actual_units"] is None)
    return {
        "scope": "Original aggregate; all new experiment costs must enter this same ledger",
        "aggregate_path": str(path.resolve()),
        "cap_usd": AGGREGATE_CAP_USD,
        "cash_ceiling_OpenRouter_usd": OPENROUTER_CASH_CEILING_USD,
        "Codex_excluded": True,
        "actual_usd": actual / 1e9,
        "reserved_unresolved_usd": reserved / 1e9,
        "committed_usd": (actual + reserved) / 1e9,
        "remaining_usd": max(0, 40_000_000_000 - actual - reserved) / 1e9,
        "request_count": len(rows),
        "completed_count": sum(row["status"] == "completed" for row in rows),
        "pending_count": sum(row["status"] == "pending" for row in rows),
        "uncertain_count": sum(row["status"] == "uncertain" for row in rows),
        "halt_reason": settings.get("halt_reason"),
        "local_subset_totals_must_not_be_added_again": True,
        "unknown_charges_asserted_zero": False,
    }


def _existing_v5_ledger(path):
    """Use exact legacy sealed-loss readiness; do not invent new loss authority."""
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
        return module.V5Ledger(path, AGGREGATE_CAP_USD)
    finally:
        if added:
            sys.path.remove(scripts)


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _wire_bytes(value, model):
    if isinstance(value, dict):
        try:
            body = canonical(value).encode("utf-8")
        except (TypeError, ValueError):
            raise BudgetError("Wire payload must be finite JSON") from None
    elif isinstance(value, bytes):
        body = value
    else:
        raise BudgetError("Wire payload must be a JSON object or exact bytes")
    # Check before expensive decoding, reservation, or the callback.
    if len(body) >= MAX_WIRE_BYTES:
        raise BudgetError("Actual wire body must be strictly smaller than 10 MiB")
    try:
        parsed = json.loads(
            body.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
        )
    except (UnicodeError, ValueError, TypeError):
        raise BudgetError("Wire body must be one valid finite JSON object") from None
    if not isinstance(parsed, dict) or parsed.get("model") != model:
        raise BudgetError("Wire model differs from the reserved model")
    return body


class SharedBudgetGuard:
    """Reserve each new paid request in the SAME existing original ledger.

    ``post(wire_body_bytes, binding)`` must send those exact bytes, once. It
    returns ``{"response": <observed dict>, "receipt": <observed dict>}``.
    Receipt fields cost_usd/request_id/request_hash/kind/requested_model must
    match this request. This is a per-call gate, not an outer actor-turn gate:
    every actor, image and interview API call needs its own stable identifier.

    ``ledger`` injection exists for temporary offline fixtures or a verified
    adapter. It must refer to the supplied existing aggregate path. There is no
    method here to create allowances, release unknown reservations, seal a loss,
    buy credits, or fall back to another provider/model.
    """

    def __init__(self, aggregate_path, namespace=DEFAULT_NAMESPACE, *, ledger=None):
        self.path = Path(aggregate_path).resolve()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,60}", namespace or ""):
            raise BudgetError("A distinct bounded experiment namespace is required")
        self.namespace = namespace
        readonly_budget_snapshot(aggregate_path)  # Require original cap, never a new file.
        if ledger is None and self.path != DEFAULT_AGGREGATE_PATH.resolve():
            raise BudgetError("Production funding must use this workspace's original aggregate ledger")
        self.ledger = ledger if ledger is not None else _existing_v5_ledger(self.path)
        if Path(self.ledger.path).resolve() != self.path or self.ledger.cap_units != 40_000_000_000:
            raise BudgetError("Funding gate must use the existing $40 aggregate ledger")

    def _request_id(self, local_id):
        if not isinstance(local_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,110}", local_id):
            raise BudgetError("A stable bounded local request ID is required")
        return self.namespace + ":" + local_id

    @staticmethod
    def _matching(row, kind, model, request_hash):
        if any(row[key] != value for key, value in (
            ("kind", kind), ("model", model), ("request_hash", request_hash)
        )):
            raise BudgetError("Stable request ID differs from original kind/model/wire bytes")

    @staticmethod
    def _completed(row, *, cached, recovered_after_completion=False):
        return {
            "request_id": row["request_id"], "response": deepcopy(row["response"]),
            "receipt": deepcopy(row["receipt"]), "cached": cached,
            "recovered_after_completion": recovered_after_completion,
        }

    @staticmethod
    def _observation(observed, binding):
        if not isinstance(observed, dict) or not isinstance(observed.get("response"), dict):
            raise ValueError("No observed response object")
        response, receipt = observed["response"], observed.get("receipt")
        if not isinstance(receipt, dict):
            raise ValueError("No conclusive receipt")
        if any(receipt.get(key) != binding[key] for key in (
            "request_id", "request_hash", "kind", "requested_model"
        )):
            raise ValueError("Receipt does not match the exact request")
        if "cost_usd" not in receipt:
            raise ValueError("No conclusive charge")
        _units(receipt["cost_usd"])
        canonical(response)
        canonical(receipt)
        return response, receipt

    def _retain_uncertain_observation(self, request_id, reason, observed):
        evidence = {}
        if isinstance(observed, dict) and isinstance(observed.get("response"), dict):
            try:
                canonical(observed["response"])
                receipt = observed.get("receipt")
                if not isinstance(receipt, dict):
                    receipt = {}
                canonical(receipt)
                evidence = {"response": observed["response"], "receipt": receipt}
            except (ValueError, TypeError):
                pass
        self.ledger.uncertain(request_id, reason, **evidence)

    def execute(self, local_request_id, kind, model, wire_payload, reserve_usd, post):
        if kind not in {"chat", "image"} or not isinstance(model, str) or not model:
            raise BudgetError("A paid chat/image kind and model are required")
        if not callable(post):
            raise BudgetError("A guarded paid-call callback is required")
        if _units(reserve_usd) <= 0:
            raise BudgetError("A positive conservative reservation is required")
        body = _wire_bytes(wire_payload, model)  # BEFORE any reservation.
        endpoint = "/chat/completions" if kind == "chat" else "/images"
        request_id = self._request_id(local_request_id)
        wire_sha256 = hashlib.sha256(body).hexdigest()
        request_hash = request_digest({"endpoint": endpoint, "wire_body_sha256": wire_sha256})
        binding = {
            "request_id": request_id, "request_hash": request_hash, "kind": kind,
            "requested_model": model, "endpoint": endpoint,
            "wire_body_sha256": wire_sha256, "wire_body_bytes": len(body),
        }
        old = self.ledger.lookup(request_id)
        if old is not None:
            self._matching(old, kind, model, request_hash)
            if old["status"] == "uncertain" and old.get("reason") == _COMPLETION_INTERRUPTED:
                # This reason is written only after this gate has observed a
                # matching complete response/receipt. Recover those exact
                # integrity-checked bytes, never query the provider again.
                try:
                    response, receipt = self._observation(old, binding)
                except (ValueError, TypeError, BudgetError):
                    raise PendingPaidRequest("Stored completion evidence is inconclusive; no retry") from None
                completed = self.ledger.complete(request_id, receipt["cost_usd"], response, receipt)
                return self._completed(completed, cached=True, recovered_after_completion=True)
            if old["status"] != "completed":
                raise PendingPaidRequest("Existing unresolved request is not retried")
            return self._completed(old, cached=True)
        reserved = self.ledger.reserve(request_id, kind, model, request_hash, reserve_usd)
        # A concurrent process may have completed the exact request between
        # lookup and reserve. Never POST after reserve returns that completion.
        if reserved["status"] == "completed":
            self._matching(reserved, kind, model, request_hash)
            return self._completed(reserved, cached=True)
        observed = None
        try:
            observed = post(body, deepcopy(binding))
            response, receipt = self._observation(observed, binding)
        except BaseException:
            self._retain_uncertain_observation(
                request_id, "Paid callback failed or lacked a conclusive matching receipt; no retry", observed)
            raise PendingPaidRequest("Paid request outcome unresolved; reservation retained and no automatic retry") from None
        try:
            completed = self.ledger.complete(request_id, receipt["cost_usd"], response, receipt)
        except BaseException:
            # Recover only a response durably completed before a local failure.
            # A pending row remains blocking; a lookup-integrity failure also
            # remains blocking and is never interpreted as permission to POST.
            completed = self.ledger.lookup(request_id)
            if (completed is None or completed["status"] != "completed" or
                    completed["response"] != response or completed["receipt"] != receipt):
                self._retain_uncertain_observation(request_id, _COMPLETION_INTERRUPTED, observed)
                raise PendingPaidRequest("Response completion unresolved; no automatic retry") from None
            return self._completed(completed, cached=True, recovered_after_completion=True)
        return self._completed(completed, cached=False)
