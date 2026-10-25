"""One-attempt funding for the prospective v9 study.

Every real and shadow call uses the original $40 aggregate. The $24 namespace
subcap is an allocation within that allowance. All prior uncertain reserves
remain counted, and only the frozen prior verifiers may recognize old seals.
No v9 HTTP error, including429, authorizes a second POST or a new loss seal.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re
import time

from experiments.pressure_interview_v1.budget_guard import (
    DEFAULT_AGGREGATE_PATH, SharedBudgetGuard, readonly_budget_snapshot,
)
from experiments.triad_repaired_v8 import funding as prior
from visual_collusion.budget import BudgetError, PendingPaidRequest, canonical, _units


ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "triad_emergent_v9"
MAIN_NAMESPACE = NAMESPACE
NAMESPACE_CAP_USD = 24
NAMESPACE_CAP_UNITS = 24_000_000_000
AGGREGATE_CAP_UNITS = 40_000_000_000
CAP_SETTING = NAMESPACE + "_namespace_cap_units"
MAX_ATTEMPTS = 1
PARENT_FUNDING_SHA256 = "125db8c91406d5c352ea7ce7e05864d99ae36251d83d7f3a17cd6629f577a0ce"
MODELS = {"chat": "z-ai/glm-5.3-flash", "image": "qwen/qwen-image-2.1-turbo"}
# Compatibility with the provider's safe HTTP429 classifier. It is terminal
# here; importing its exception does not import a retry policy into v9.
Known429 = prior.Known429


def _require(condition, message):
    if not condition:
        raise BudgetError(message)


def verify_dependencies():
    path = ROOT / "experiments/triad_repaired_v8/funding.py"
    _require(not any(p.is_symlink() for p in (path, *path.parents))
             and path.is_file()
             and hashlib.sha256(path.read_bytes()).hexdigest() == PARENT_FUNDING_SHA256,
             "Frozen prior funding verifier changed")
    prior.verify_dependencies()


class FundingLedger(prior.FundingLedger):
    """Prior seal compatibility plus atomic v9 and aggregate reservation caps."""

    def __init__(self, path, cap_usd=40):
        _require(_units(cap_usd) == AGGREGATE_CAP_UNITS,
                 "V9 must retain the original $40 aggregate cap")
        verify_dependencies()
        super().__init__(path, cap_usd)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT value FROM settings WHERE key=?", (CAP_SETTING,)).fetchone()
            _require(old is None or old[0] == str(NAMESPACE_CAP_UNITS),
                     "The fixed v9 $24 subcap changed")
            db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)",
                       (CAP_SETTING, str(NAMESPACE_CAP_UNITS)))

    def reserve(self, request_id, kind, model, request_hash, reserve_usd):
        verify_dependencies()
        prefix = NAMESPACE + ":"
        _require(isinstance(request_id, str) and request_id.startswith(prefix)
                 and re.fullmatch(r"[A-Za-z0-9_.:-]{1,180}", request_id),
                 "V9 funding accepts only its fixed namespace")
        _require(kind in MODELS and model == MODELS[kind],
                 "V9 paid intent differs from the pinned model")
        _require(isinstance(request_hash, str) and re.fullmatch(r"[a-f0-9]{64}", request_hash),
                 "Invalid exact-wire request hash")
        amount = _units(reserve_usd)
        _require(amount > 0, "A positive conservative reservation is required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if old is not None:
                _require(all(old[key] == value for key, value in (
                    ("kind", kind), ("model", model), ("request_hash", request_hash),
                    ("reserved_units", amount))), "V9 request identity or reservation changed")
                if old["status"] == "completed":
                    return self._record(old)
                raise PendingPaidRequest("Existing v9 reservation is unresolved; no duplicate POST")
            self._ready(db)  # Prior seals are verified; every new unresolved call blocks.
            setting = db.execute("SELECT value FROM settings WHERE key=?", (CAP_SETTING,)).fetchone()
            _require(setting is not None and setting[0] == str(NAMESPACE_CAP_UNITS),
                     "V9 namespace subcap binding changed")
            used_namespace = db.execute(
                "SELECT COALESCE(SUM(COALESCE(actual_units,reserved_units)),0) FROM requests "
                "WHERE substr(request_id,1,?)=?", (len(prefix), prefix)).fetchone()[0]
            _require(used_namespace + amount <= NAMESPACE_CAP_UNITS,
                     "The fixed v9 $24 namespace subcap cannot cover the next reservation")
            used_aggregate = db.execute(
                "SELECT COALESCE(SUM(COALESCE(actual_units,reserved_units)),0) FROM requests").fetchone()[0]
            _require(used_aggregate + amount <= self.cap_units,
                     "Original aggregate cap cannot cover the next v9 reservation")
            db.execute(
                "INSERT INTO requests(request_id,kind,model,request_hash,reserved_units,status,created) "
                "VALUES (?,?,?,?,?,'pending',?)",
                (request_id, kind, model, request_hash, amount, time.time()))
            return self._record(db.execute(
                "SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone())

    def namespace_summary(self):
        prefix = NAMESPACE + ":"
        with self._connect() as db:
            rows = db.execute(
                "SELECT status,actual_units,reserved_units FROM requests "
                "WHERE substr(request_id,1,?)=?", (len(prefix), prefix)).fetchall()
        actual = sum(row["actual_units"] or 0 for row in rows)
        unresolved = sum(row["reserved_units"] for row in rows if row["actual_units"] is None)
        return {"namespace": NAMESPACE, "cap_usd": NAMESPACE_CAP_USD,
                "actual_usd": actual / 1e9, "reserved_unresolved_usd": unresolved / 1e9,
                "committed_usd": (actual + unresolved) / 1e9, "request_count": len(rows),
                "new_allowance": False}

    def seal_known_429(self, *args, **kwargs):
        raise BudgetError("V9 errors are terminal; no new transport-loss seal is authorized")

    seal_transport_loss = seal_known_429
    seal_existing_response_less_loss = seal_known_429


class SingleAttemptFunding:
    """Exact-wire SharedBudgetGuard facade with one optional archive hook."""

    def __init__(self, guard, evidence_dir=None, *, on_attempt=None):
        _require(guard.namespace == NAMESPACE and isinstance(guard.ledger, FundingLedger),
                 "V9 requires its capped original-ledger funding adapter")
        _require(on_attempt is None or callable(on_attempt), "Attempt hook must be callable")
        self.guard = guard
        self.evidence_dir = Path(evidence_dir).absolute() if evidence_dir is not None else None
        self.on_attempt = on_attempt

    def __getattr__(self, name):
        return getattr(self.guard, name)

    def execute(self, local_request_id, kind, model, wire_payload, reserve_usd, post):
        verify_dependencies()
        _require(kind in MODELS and model == MODELS[kind], "V9 paid model differs from its pin")
        _require(callable(post), "A guarded native POST callback is required")
        old = self.ledger.lookup(self.guard._request_id(local_request_id))
        if old is not None:
            _require(_units(old["reserved_usd"]) == _units(reserve_usd),
                     "Resumed v9 reservation differs from its original exact intent")

        def observed_post(body, binding):
            if self.on_attempt is not None:
                self.on_attempt(body, deepcopy(binding))
            return post(body, binding)

        # The underlying guard retains every completed semantic answer, caches
        # exact responses and blocks ambiguous outcomes. There is no retry loop.
        return self.guard.execute(local_request_id, kind, model, wire_payload,
                                  reserve_usd, observed_post)


def create_guard(aggregate_path, namespace=NAMESPACE, evidence_dir=None, *, ledger=None, on_attempt=None):
    _require(namespace == NAMESPACE, "Only the fixed v9 namespace is authorized")
    path = Path(aggregate_path).absolute()
    _require(not any(p.is_symlink() for p in (path, *path.parents)),
             "Funding paths cannot follow symlinks")
    readonly_budget_snapshot(path)  # Require an existing $40 database before construction.
    if ledger is None:
        _require(path.resolve() == DEFAULT_AGGREGATE_PATH.resolve(),
                 "Production v9 must use the original aggregate ledger")
        ledger = FundingLedger(path, 40)
    _require(isinstance(ledger, FundingLedger), "V9 requires its verified capped ledger")
    guard = SharedBudgetGuard(path, namespace, ledger=ledger)
    return SingleAttemptFunding(guard, evidence_dir, on_attempt=on_attempt)
