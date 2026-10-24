"""Durable reservation-before-POST accounting for one capped experiment.

Amounts are rounded UP to integer nanodollars. A reservation is an estimate,
not a provider-enforced maximum. The separate lifetime OpenRouter key cap is
required. Interrupted/ambiguous requests stop paid work and are never retried.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_CEILING

MAX_INFERENCE_USD = 40.0
MAX_CASH_USD = 50.0
_SCALE = Decimal(1_000_000_000)


class BudgetError(RuntimeError):
    pass


class PendingPaidRequest(BudgetError):
    """A request may already have been billed; reconcile outside this runner."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def request_digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _units(value):
    if isinstance(value, bool):
        raise BudgetError("A monetary amount must be a finite nonnegative number")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise BudgetError("Invalid monetary amount") from None
    if not amount.is_finite() or amount < 0 or amount > 1_000_000:
        raise BudgetError("A monetary amount must be finite, nonnegative, and bounded")
    return int((amount * _SCALE).to_integral_value(rounding=ROUND_CEILING))


def _dollars(value):
    return float(Decimal(value) / _SCALE)


class BudgetLedger:
    """One SQLite database shared by every paid request and resumed process.

    A completed exact-hash request is recoverable from its stored response.
    An existing pending/uncertain request instead blocks all new reservations.
    There is intentionally no automatic release, reconciliation, or retry API.
    """

    def __init__(self, path: Path, cap_usd=MAX_INFERENCE_USD):
        self.path = Path(path)
        self.cap_units = _units(cap_usd)
        if not 0 < self.cap_units <= _units(MAX_INFERENCE_USD):
            raise BudgetError("Inference cap must be positive and no more than $40")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise BudgetError("Budget database cannot be a symlink")
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    model TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    reserved_units INTEGER NOT NULL,
                    actual_units INTEGER,
                    status TEXT NOT NULL,
                    created REAL NOT NULL,
                    finished REAL,
                    response_json TEXT,
                    receipt_json TEXT,
                    result_hash TEXT,
                    reason TEXT
                );
            """)
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT value FROM settings WHERE key='cap_units'").fetchone()
            if old and int(old[0]) != self.cap_units:
                raise BudgetError("Existing budget cap differs; do not replace the run's ledger")
            connection.execute("INSERT OR IGNORE INTO settings VALUES ('cap_units', ?)", (str(self.cap_units),))
            connection.execute("INSERT OR IGNORE INTO settings VALUES ('schema_version','1')")
        os.chmod(self.path, 0o600)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def bind(self, key, value):
        """Bind immutable public model configuration; never a Cloud secret token."""
        if key != "model_configuration" or not isinstance(value, str):
            raise BudgetError("Invalid budget identity setting")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if row and row[0] != value:
                raise BudgetError("Frozen model/provider configuration differs; refusing to mix runs")
            connection.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, value))

    @staticmethod
    def _record(row):
        if row is None:
            return None
        result = dict(row)
        result["reserved_usd"] = _dollars(result.pop("reserved_units"))
        units = result.pop("actual_units")
        result["actual_usd"] = None if units is None else _dollars(units)
        raw_response = result.pop("response_json")
        raw_receipt = result.pop("receipt_json")
        if result["status"] == "completed":
            if raw_response is None or raw_receipt is None:
                raise BudgetError("Completed receipt is missing its durable response")
        if raw_response is not None or raw_receipt is not None:
            if raw_response is None or raw_receipt is None:
                raise BudgetError("Partial stored provider response/receipt")
            expected = hashlib.sha256((raw_response + "\n" + raw_receipt).encode()).hexdigest()
            if expected != result["result_hash"]:
                raise BudgetError("Stored provider response/receipt integrity check failed")
        try:
            result["response"] = json.loads(raw_response) if raw_response is not None else None
            result["receipt"] = json.loads(raw_receipt) if raw_receipt is not None else None
        except ValueError:
            raise BudgetError("Stored provider response is not valid JSON") from None
        return result

    def lookup(self, request_id):
        with self._connect() as connection:
            return self._record(connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone())

    @staticmethod
    def _ready(connection):
        halt = connection.execute("SELECT value FROM settings WHERE key='halt_reason'").fetchone()
        if halt:
            raise BudgetError(halt[0])
        unresolved = connection.execute("SELECT request_id FROM requests WHERE status IN ('pending','uncertain') LIMIT 1").fetchone()
        if unresolved:
            raise PendingPaidRequest("An unresolved paid request exists: " + unresolved[0] + "; no automatic POST retry or further paid work")

    def assert_ready(self):
        with self._connect() as connection:
            self._ready(connection)

    def reserve(self, request_id, kind, model, request_hash, reserve_usd):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,180}", request_id):
            raise BudgetError("Invalid stable request ID")
        if kind not in {"chat", "image"} or not isinstance(model, str):
            raise BudgetError("Invalid paid request kind/model")
        if not isinstance(request_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", request_hash):
            raise BudgetError("Invalid request hash")
        amount = _units(reserve_usd)
        if amount <= 0:
            raise BudgetError("Paid requests require a positive reservation")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["request_hash"] != request_hash or old["kind"] != kind or old["model"] != model:
                    raise BudgetError("Request ID reused with different content/model")
                if old["status"] == "completed":
                    return self._record(old)
                raise PendingPaidRequest("Request already reserved; reconcile without retrying the POST")
            self._ready(connection)
            used = connection.execute("SELECT COALESCE(SUM(COALESCE(actual_units,reserved_units)),0) FROM requests").fetchone()[0]
            if used + amount > self.cap_units:
                raise BudgetError("Local inference budget cannot cover the next conservative reservation")
            connection.execute("INSERT INTO requests(request_id,kind,model,request_hash,reserved_units,status,created) VALUES (?,?,?,?,?,'pending',?)",
                               (request_id, kind, model, request_hash, amount, time.time()))
            row = connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            return self._record(row)

    def complete(self, request_id, actual_usd, response: dict, receipt: dict):
        actual = _units(actual_usd)
        response_json, receipt_json = canonical(response), canonical(receipt)
        checksum = hashlib.sha256((response_json + "\n" + receipt_json).encode()).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row["status"] not in {"pending", "uncertain"}:
                raise BudgetError("Only an existing unresolved request can receive a cost receipt")
            connection.execute("UPDATE requests SET actual_units=?,status='completed',finished=?,response_json=?,receipt_json=?,result_hash=? WHERE request_id=?",
                               (actual, time.time(), response_json, receipt_json, checksum, request_id))
            if actual > row["reserved_units"]:
                connection.execute("INSERT OR REPLACE INTO settings VALUES ('halt_reason',?)",
                                   ("A provider charge exceeded its conservative reservation; paid work is stopped for budget review",))
        return self.lookup(request_id)

    def halt(self, reason):
        """Persist a generic stop reason; callers must never include secrets."""
        with self._connect() as connection:
            connection.execute("INSERT OR REPLACE INTO settings VALUES ('halt_reason',?)", (str(reason)[:240],))

    def uncertain(self, request_id, reason="No conclusive cost receipt", *, response=None, receipt=None):
        # Callers must pass only generic errors: provider bodies may echo secrets.
        safe_reason = str(reason)[:240]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE requests SET status='uncertain',reason=? WHERE request_id=? AND status='pending'", (safe_reason, request_id))
            if response is not None:
                response_json, receipt_json = canonical(response), canonical(receipt or {})
                checksum = hashlib.sha256((response_json + "\n" + receipt_json).encode()).hexdigest()
                connection.execute("UPDATE requests SET response_json=?,receipt_json=?,result_hash=? WHERE request_id=? AND status='uncertain'",
                                   (response_json, receipt_json, checksum, request_id))

    def summary(self):
        with self._connect() as connection:
            # Do not reread/decode every base64 image for each budget check.
            # Exact cached responses are integrity-checked when lookup restores them.
            rows = connection.execute("SELECT request_id,status,actual_units,reserved_units FROM requests ORDER BY created,request_id").fetchall()
            actual = sum(row["actual_units"] or 0 for row in rows)
            pending = sum(row["reserved_units"] for row in rows if row["actual_units"] is None)
            halt = connection.execute("SELECT value FROM settings WHERE key='halt_reason'").fetchone()
        return {
            "cap_usd": _dollars(self.cap_units), "actual_usd": _dollars(actual),
            "reserved_unresolved_usd": _dollars(pending), "committed_usd": _dollars(actual + pending),
            "remaining_usd": _dollars(max(0, self.cap_units - actual - pending)),
            "request_count": len(rows), "completed_count": sum(r["status"] == "completed" for r in rows),
            "unresolved_request_ids": [r["request_id"] for r in rows if r["status"] != "completed"],
            "halt_reason": halt[0] if halt else None,
        }
