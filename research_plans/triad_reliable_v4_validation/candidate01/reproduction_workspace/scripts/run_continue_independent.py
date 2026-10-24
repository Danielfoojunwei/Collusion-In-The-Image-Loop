"""Host-only, explicitly authorized continuation after unavailable transport.

No paid request is retried. Unknown charges remain reserved in the original
ledger. A sealed transport loss makes its dependent study turn missing and
permits the unchanged canonical scheduler to try independent planned work.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

from run_authorized_key import AuthorizedKeyProvider
from visual_collusion import cli
from visual_collusion.budget import BudgetError, BudgetLedger, PendingPaidRequest, request_digest
from visual_collusion.common import append_event, canonical, digest, read_json, source_manifest, utc_now, write_json
from visual_collusion.providers import ProviderError


INITIAL_LOSS = "episode_896b810c5a:prefix:round1:A:step2"
LOSS_REASON = "POST interrupted or failed; billing outcome unresolved"
AUTHORIZATION = {
    "literal_user_messages": [
        "the $50 is only for the openrouter, do not count codex",
        "doesnt matter, just proceed now",
        "proceed with the entire thing now",
    ],
    "scope": "Continue independent scheduled cases after a sealed no-response transport loss; do not retry the lost request or invent its outcome or cost.",
    "waived_rule": "Global halt solely for no-response POST transport losses that are sealed as terminal missing study turns.",
    "preserved": "Original request rows, uncertain status, full unknown-charge reservations, $40 local ledger, $50 OpenRouter cash ceiling, fixed source/config/plan/model/route/prompt/schedule, and no paid retries.",
    "limits": "Aggregate key usage is not per-request reconciliation. Unknown cost remains unknown. Missing turns are not refusals, negative collusion results, or billed invalid model outputs.",
}
POLICY_SHA256 = digest(AUTHORIZATION)


def _eligible_loss(row):
    return (row is not None and row["status"] == "uncertain"
            and row["reason"] == LOSS_REASON and row["actual_units"] is None
            and row["response_json"] is None and row["receipt_json"] is None
            and row["result_hash"] is None)


class ContinuationLedger(BudgetLedger):
    """Keep every original request unchanged; allow only separately sealed losses."""

    def __init__(self, path, cap_usd=40):
        super().__init__(path, cap_usd)
        if self.cap_units != 40_000_000_000:
            raise BudgetError("Continuation requires the unchanged $40 inference cap")
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS terminal_transport_losses (
                    request_id TEXT PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    policy_sha256 TEXT NOT NULL,
                    recorded_at REAL NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS terminal_transport_losses_no_update
                BEFORE UPDATE ON terminal_transport_losses
                BEGIN SELECT RAISE(ABORT, 'Terminal transport loss records are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS terminal_transport_losses_no_delete
                BEFORE DELETE ON terminal_transport_losses
                BEGIN SELECT RAISE(ABORT, 'Terminal transport loss records are immutable'); END;
            """)

    @staticmethod
    def _sealed(connection, row):
        sealed = connection.execute(
            "SELECT snapshot_json,policy_sha256 FROM terminal_transport_losses WHERE request_id=?",
            (row["request_id"],)).fetchone()
        return (sealed is not None and sealed["policy_sha256"] == POLICY_SHA256
                and sealed["snapshot_json"] == canonical(dict(row)) and _eligible_loss(row))

    def _ready(self, connection):
        halt = connection.execute("SELECT value FROM settings WHERE key='halt_reason'").fetchone()
        if halt:
            raise BudgetError(halt[0])
        for row in connection.execute("SELECT * FROM requests WHERE status IN ('pending','uncertain')"):
            if row["status"] == "pending" or not self._sealed(connection, row):
                raise PendingPaidRequest("An unsealed unresolved paid request exists: " + row["request_id"])

    def seal_transport_loss(self, request_id):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if not _eligible_loss(row):
                raise PendingPaidRequest("Only a no-response POST transport loss can be sealed")
            if self._sealed(connection, row):
                return
            connection.execute(
                "INSERT INTO terminal_transport_losses VALUES (?,?,?,?)",
                (request_id, canonical(dict(row)), POLICY_SHA256, time.time()))

    def is_terminal_loss(self, request_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            return row is not None and self._sealed(connection, row)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class _ObservingOpener:
    """Observe only status and allowlisted IDs, preserving standard proxy/TLS."""
    def __init__(self, owner, delegate):
        self.owner, self.delegate = owner, delegate

    def _observe(self, status, headers):
        safe = {"http_status": int(status)}
        for name in ("X-Generation-Id", "X-Request-Id", "Request-Id"):
            value = headers.get(name) if headers is not None else None
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
                safe[name.lower()] = value
        self.owner._observed_http = safe

    def open(self, request, timeout):
        try:
            response = self.delegate.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            self._observe(error.code, error.headers)
            raise
        self._observe(response.status, response.headers)
        return response


class IndependentCaseProvider(AuthorizedKeyProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_paid_id = None
        self._observed_http = None
        self._last_post_failure = None
        if self._transport is None:
            delegate = self._opener or urllib.request.build_opener(_NoRedirect())
            self._opener = _ObservingOpener(self, delegate)

    def _request(self, method, path, payload=None):
        self._observed_http = None
        try:
            return super()._request(method, path, payload)
        except ProviderError as error:
            if method == "POST" and self._current_paid_id:
                message = str(error)
                match = re.fullmatch(r"API HTTP ([0-9]{3}); no automatic retry", message)
                safe_category = ("http_error" if match else
                                 "transport_or_json_decoding_failure" if message == "Transport or JSON decoding failed; no automatic retry"
                                 else "provider_error_message_omitted")
                observed = deepcopy(self._observed_http) or {}
                if match:
                    observed.setdefault("http_status", int(match.group(1)))
                self._last_post_failure = {
                    "request_id": self._current_paid_id, "endpoint": path,
                    "exception_class": type(error).__name__, "category": safe_category,
                    **observed,
                }
                append_event(self.ledger.path.parent / "execution_notes" / "transport_diagnostics.jsonl",
                             self._last_post_failure)
            raise

    def _paid(self, request_id, kind, endpoint, payload, reserve_usd):
        from visual_collusion.runtime import InvalidAgentOutput
        old = self.ledger.lookup(request_id)
        expected = request_digest({"endpoint": endpoint, "payload": payload})
        if old is not None:
            if old["request_hash"] != expected or old["model"] != payload["model"] or old["kind"] != kind:
                raise BudgetError("Stable request ID does not match the original request")
            if self.ledger.is_terminal_loss(request_id):
                raise InvalidAgentOutput("Transport response unavailable for " + request_id
                                         + "; terminal missing turn, no retry; unknown charge remains reserved")
        self._current_paid_id = request_id
        self._last_post_failure = None
        try:
            return super()._paid(request_id, kind, endpoint, payload, reserve_usd)
        except ProviderError:
            # Do not classify missing-cost responses, model/route/pricing errors,
            # budget overruns, or interrupts without an observed transport error.
            row = self.ledger.lookup(request_id)
            if (self._last_post_failure is not None and row is not None
                    and row["status"] == "uncertain" and row["reason"] == LOSS_REASON
                    and row["actual_usd"] is None and row["response"] is None and row["receipt"] is None):
                self.ledger.seal_transport_loss(request_id)
                raise InvalidAgentOutput("Transport response unavailable for " + request_id
                                         + "; terminal missing turn, no retry; unknown charge remains reserved") from None
            raise
        finally:
            self._current_paid_id = None


def prepare_authorization(out):
    """Record the policy before any network use and seal only the known loss."""
    out = Path(out)
    frozen = read_json(out / "freeze.json")
    checks = {
        "source_manifest_unchanged": source_manifest() == frozen["source_files"],
        "config_unchanged": digest(read_json(out / "config.json")) == frozen["config_sha256"],
        "plan_unchanged": digest(read_json(out / "plan.json")) == frozen["plan_sha256"],
    }
    if not all(checks.values()):
        raise BudgetError("Canonical source, config or plan changed")
    wrapper = Path(__file__).resolve()
    record = {
        "authorization": AUTHORIZATION, "policy_sha256": POLICY_SHA256,
        "recorded_at_utc": utc_now(), "wrapper_path": str(wrapper),
        "wrapper_sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
        "initial_transport_loss": INITIAL_LOSS, "preservation_checks": checks,
        "no_retry": True, "unknown_reservations_released": False,
    }
    note = out / "execution_notes" / "authorized_independent_case_continuation.json"
    if note.exists():
        prior = read_json(note)
        for key in ("authorization", "policy_sha256", "wrapper_sha256", "initial_transport_loss", "preservation_checks"):
            if prior[key] != record[key]:
                raise BudgetError("Previously recorded continuation authorization differs")
    else:
        write_json(note, record)
    ledger = ContinuationLedger(out / "budget.sqlite3", 40)
    old = ledger.lookup(INITIAL_LOSS)
    if old is not None and old["status"] == "uncertain":
        ledger.seal_transport_loss(INITIAL_LOSS)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--out" not in argv:
        raise SystemExit("Pass the existing run using --out")
    prepare_authorization(Path(argv[argv.index("--out") + 1]))
    cli.BudgetLedger = ContinuationLedger
    cli.OpenRouterProvider = IndependentCaseProvider
    return cli.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
