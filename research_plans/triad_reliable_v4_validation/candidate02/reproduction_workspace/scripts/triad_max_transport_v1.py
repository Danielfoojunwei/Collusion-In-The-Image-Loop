"""Seal observed transient HTTP losses without retrying or releasing reserves.

This host-only amendment recognizes the existing continuation authority for
response-less HTTP408/429/5xx failures. It never changes a request row, invents a
receipt, or treats an unknown charge as zero. All evidence and seals are local;
this module contains no network implementation.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

from visual_collusion.budget import BudgetError, PendingPaidRequest, _units, canonical, request_digest


ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "triad_max_pressure_v3"
TABLE = "terminal_transport_losses_triad_max_v1"
GUARD_FAILURE_REASON = "Paid callback failed or lacked a conclusive matching receipt; no retry"
DEPENDENCIES = {
    "run_lossless_webp_v5.py": "b192e775157b96428f8492ed4231290d51522f80f2b5bff0e5b7abd767c5bfd8",
    "continue_repaired_execution.py": "4fbcd6782bb8302ed79a49029b28dfe3c6b0dd90aad9d9334f4d49ecae830765",
    "run_continue_independent.py": "a0536046171e74b27762f43c5ddf60908125ab6c276a813bf7eef0fdc5e5818f",
    "pressure_png_xmp_v2.py": "e5c2945662efde48a3e651e30fd9c51934c668dec0daa2d9af2e3b35bfeeab9f",
    "lossless_wire_codec.py": "d1ecf05b90f34ae99952d09771539253f7a181706a5d7ad6d75098d3dbd15247",
}
AUTHORIZATION = {
    "revision": "triad_max_transport_v1",
    "inherited_authority": "scripts/continue_repaired_execution.py AUTHORIZATION.future_losses: transient HTTP408/429/5xx may become snapshot-bound terminal missing turns without retry.",
    "scope": "Current triad_max_pressure_v3 actor calls only; observed HTTPError, no provider response or receipt, no actual charge known.",
    "preserved": "Every original request field including uncertain status/reason/full reservation; all completed receipts; original $40 inference/$50 OpenRouter cash caps.",
    "required": "Exact diagnostic, reconstructed wire binding and content hashes; positive unknown-charge reserve; immutable append-only seal.",
    "excluded": "Pending calls, other namespaces, nontransient HTTP, authentication/account/budget errors, observed response/receipt, missing cost, unclassified transport and model refusals.",
    "result": "Missing actor stage; no replacement query; unknown cost is not zero and no scientific negative is inferred.",
}
POLICY_SHA256 = hashlib.sha256(canonical(AUTHORIZATION).encode()).hexdigest()
_ID = re.compile(r"triad_max_pressure_v3:case_05_max\.r(?:0[1-9]|1[0-2])\.(?:A_IMAGE_AND_VOTE|B_IMAGE_AND_VOTE|C_VOTE|POSTVERDICT_A_LOG|POSTVERDICT_B_LOG|POSTVERDICT_C_IMAGE_LOG)\.(?:step[1-4]|image[12])")
_SHA = re.compile(r"[0-9a-f]{64}")


def _require(value, message):
    if not value:
        raise BudgetError(message)


def _safe_file(path):
    path = Path(path).absolute()
    _require(not any(item.is_symlink() for item in (path, *path.parents)), "Transport evidence cannot follow symlinks")
    _require(path.is_file(), "Required transport evidence file is missing")
    return path


def _file_sha(path):
    return hashlib.sha256(_safe_file(path).read_bytes()).hexdigest()


def verify_dependencies():
    for name, expected in DEPENDENCIES.items():
        _require(_file_sha(ROOT / "scripts" / name) == expected, "Frozen transport dependency changed")


def _legacy_module():
    verify_dependencies()
    directory = str(ROOT / "scripts")
    if directory not in sys.path:
        sys.path.insert(0, directory)
    module = importlib.import_module("run_lossless_webp_v5")
    _require(Path(module.__file__).resolve() == (ROOT / "scripts/run_lossless_webp_v5.py").resolve(), "Frozen ledger resolves elsewhere")
    return module


_legacy = _legacy_module()


def _codec():
    verify_dependencies()
    path = ROOT / "scripts/pressure_png_xmp_v2.py"
    spec = importlib.util.spec_from_file_location("_triad_max_terminal_loss_codec", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _eligible_row(row):
    return (row is not None and isinstance(row["request_id"], str)
            and _ID.fullmatch(row["request_id"]) is not None
            and row["status"] == "uncertain" and row["reason"] == GUARD_FAILURE_REASON
            and type(row["reserved_units"]) is int and row["reserved_units"] > 0
            and all(row[field] is None for field in ("actual_units", "response_json", "receipt_json", "result_hash", "finished")))


def _hydrate_pixels(value, evidence_root, files):
    if isinstance(value, list):
        return [_hydrate_pixels(item, evidence_root, files) for item in value]
    if isinstance(value, dict):
        result = {key: _hydrate_pixels(item, evidence_root, files) for key, item in value.items()}
        info = result.get("image_url")
        if isinstance(info, dict) and isinstance(info.get("url"), str) and info["url"].startswith("blob:sha256:"):
            sha = info["url"].removeprefix("blob:sha256:")
            _require(_SHA.fullmatch(sha) is not None, "Archived source image digest is invalid")
            path = _safe_file(evidence_root / "blobs" / (sha + ".png"))
            raw = path.read_bytes()
            _require(hashlib.sha256(raw).hexdigest() == sha, "Archived source image changed")
            files[str(path)] = sha
            info["url"] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        return result
    return deepcopy(value)


def _validate_binding(row, diagnostic_path):
    """Reconstruct the already-dispatched bytes; never prepare new wire files."""
    _require(_eligible_row(row), "Only an exact response-less uncertain triad request may be sealed")
    diagnostic_path = _safe_file(diagnostic_path)
    _require(diagnostic_path.parent.name == "transport_diagnostics", "Expected the retained transport diagnostic directory")
    _require(diagnostic_path.name == hashlib.sha256(row["request_id"].encode()).hexdigest() + ".json", "Diagnostic filename belongs to a different paid request")
    diagnostic = json.loads(diagnostic_path.read_text())
    keys = {"category", "endpoint", "exception_class", "http_status", "request_hash", "request_id", "response_observed", "wire_body_bytes", "wire_body_sha256"}
    _require(isinstance(diagnostic, dict) and set(diagnostic) == keys, "Transient HTTP diagnostic fields differ")
    status = diagnostic["http_status"]
    _require(diagnostic["category"] == "http_error" and diagnostic["exception_class"] == "HTTPError"
             and diagnostic["response_observed"] is False and type(status) is int
             and (status in {408, 429} or 500 <= status <= 599), "Only observed response-less transient HTTP failures are eligible")
    _require(diagnostic["request_id"] == row["request_id"] and diagnostic["request_hash"] == row["request_hash"], "HTTP diagnostic request binding differs")
    endpoint = "/chat/completions" if row["kind"] == "chat" else "/images" if row["kind"] == "image" else None
    _require(endpoint is not None and diagnostic["endpoint"] == endpoint, "HTTP diagnostic paid endpoint differs")
    _require((row["kind"] == "image") == (re.search(r"\.image[12]$", row["request_id"]) is not None), "Paid kind differs from actor action identity")
    sha, count = diagnostic["wire_body_sha256"], diagnostic["wire_body_bytes"]
    _require(isinstance(sha, str) and _SHA.fullmatch(sha) and type(count) is int and 0 < count < 10485760, "HTTP diagnostic wire identity is invalid")
    _require(request_digest({"endpoint": endpoint, "wire_body_sha256": sha}) == row["request_hash"], "HTTP diagnostic wire digest differs from the reserved request")
    evidence_root = diagnostic_path.parent.parent
    local_id = row["request_id"].removeprefix(NAMESPACE + ":")
    binding_path = _safe_file(evidence_root / "transport_requests" / (hashlib.sha256(local_id.encode()).hexdigest() + ".json"))
    binding = json.loads(binding_path.read_text())
    _require(isinstance(binding, dict) and set(binding) == {"endpoint", "request_id", "logical_payload", "mapping", "logical_images_content_addressed"}, "Archived transport binding fields differ")
    _require(binding["endpoint"] == endpoint and binding["request_id"] == local_id and binding["logical_images_content_addressed"] is True, "Archived transport binding identity differs")
    mapping = binding["mapping"]
    _require(isinstance(mapping, dict) and mapping.get("request_id") == local_id
             and mapping.get("wire_body_sha256") == sha and mapping.get("wire_body_utf8_bytes") == count,
             "HTTP diagnostic does not match the archived wire mapping")
    _require(isinstance(binding["logical_payload"], dict) and binding["logical_payload"].get("model") == row["model"], "Archived paid model differs")
    expected_model = "z-ai/glm-5.3-flash" if row["kind"] == "chat" else "qwen/qwen-image-2.1-turbo"
    _require(row["model"] == expected_model, "Transient loss belongs to a different model")
    files = {}
    logical = _hydrate_pixels(binding["logical_payload"], evidence_root, files)
    try:
        wire = _codec().verify_wire(evidence_root, endpoint, logical, mapping)
    except (ValueError, TypeError, KeyError, OSError) as error:
        raise BudgetError("Archived exact wire reconstruction failed") from error
    body = canonical(wire).encode()
    _require(len(body) == count and hashlib.sha256(body).hexdigest() == sha, "Reconstructed dispatched wire bytes differ")
    for image in mapping.get("images", []):
        image_sha = image.get("wire_sha256")
        _require(isinstance(image_sha, str) and _SHA.fullmatch(image_sha), "Mapped wire image digest is invalid")
        path = _safe_file(evidence_root / "transport_blobs" / (image_sha + ".webp"))
        _require(image.get("wire_blob") == "transport_blobs/" + image_sha + ".webp" and _file_sha(path) == image_sha, "Mapped wire image changed")
        files[str(path)] = image_sha
    return {"diagnostic_path": str(diagnostic_path), "diagnostic_sha256": _file_sha(diagnostic_path),
            "diagnostic_json": canonical(diagnostic), "binding_path": str(binding_path),
            "binding_sha256": _file_sha(binding_path), "binding_json": canonical(binding),
            "evidence_files_json": canonical(files)}


def _new_seal(db, row):
    """Return a fully bound existing seal, or None; never write on a read path."""
    if not _eligible_row(row):
        return None
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone() is None:
        return None
    found = db.execute(f"SELECT * FROM {TABLE} WHERE request_id=?", (row["request_id"],)).fetchone()
    if found is None:
        return None
    seal = dict(found)
    digest_value = seal.pop("record_sha256")
    try:
        verify_dependencies()
        _require(hashlib.sha256(canonical(seal).encode()).hexdigest() == digest_value, "Immutable transport seal checksum differs")
        _require(seal["policy_sha256"] == POLICY_SHA256 and seal["snapshot_json"] == canonical(dict(row)), "Sealed original request snapshot differs")
        _require(_file_sha(seal["diagnostic_path"]) == seal["diagnostic_sha256"] and canonical(json.loads(Path(seal["diagnostic_path"]).read_text())) == seal["diagnostic_json"], "Retained HTTP diagnostic changed")
        _require(_file_sha(seal["binding_path"]) == seal["binding_sha256"] and canonical(json.loads(Path(seal["binding_path"]).read_text())) == seal["binding_json"], "Retained wire binding changed")
        for path, sha in json.loads(seal["evidence_files_json"]).items():
            _require(_file_sha(path) == sha, "Retained source or wire pixels changed")
    except (BudgetError, ValueError, TypeError, KeyError, OSError):
        return None
    return {**seal, "record_sha256": digest_value}


class TransportLedger(_legacy.V5Ledger):
    """The original aggregate with one additional immutable loss-seal table."""

    def __init__(self, path, cap_usd=40):
        _safe_file(path)
        _require(_units(cap_usd) == 40_000_000_000, "The unchanged original $40 cap is required")
        verify_dependencies()
        super().__init__(path, cap_usd)
        with self._connect() as db:
            db.executescript(f"""
                CREATE TABLE IF NOT EXISTS {TABLE} (
                    request_id TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL,
                    diagnostic_path TEXT NOT NULL, diagnostic_sha256 TEXT NOT NULL, diagnostic_json TEXT NOT NULL,
                    binding_path TEXT NOT NULL, binding_sha256 TEXT NOT NULL, binding_json TEXT NOT NULL,
                    evidence_files_json TEXT NOT NULL, policy_sha256 TEXT NOT NULL,
                    recorded_at REAL NOT NULL, record_sha256 TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS {TABLE}_no_update BEFORE UPDATE ON {TABLE}
                BEGIN SELECT RAISE(ABORT, 'Triad HTTP loss seals are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS {TABLE}_no_delete BEFORE DELETE ON {TABLE}
                BEGIN SELECT RAISE(ABORT, 'Triad HTTP loss seals are immutable'); END;
            """)

    @staticmethod
    def _sealed(db, row):
        verify_dependencies()
        return _legacy.V5Ledger._sealed(db, row) or _new_seal(db, row) is not None

    def seal_existing_response_less_loss(self, request_id, diagnostic_path):
        verify_dependencies()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            evidence = _validate_binding(row, diagnostic_path)
            existing = db.execute(f"SELECT * FROM {TABLE} WHERE request_id=?", (request_id,)).fetchone()
            if existing is not None:
                _require(_new_seal(db, row) is not None, "Existing immutable transport seal is invalid")
                _require(all(existing[key] == value for key, value in evidence.items()), "Existing seal binds different evidence")
                return _public_seal(row, dict(existing))
            seal = {"request_id": request_id, "snapshot_json": canonical(dict(row)), **evidence,
                    "policy_sha256": POLICY_SHA256, "recorded_at": time.time()}
            seal["record_sha256"] = hashlib.sha256(canonical(seal).encode()).hexdigest()
            columns = list(seal)
            db.execute(f"INSERT INTO {TABLE} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(seal[key] for key in columns))
            return _public_seal(row, seal)


def _public_seal(row, seal):
    return {"request_id": row["request_id"], "request_hash": row["request_hash"],
            "status": "terminal_transport_missing", "original_status": row["status"],
            "reserved_units": row["reserved_units"], "reserved_usd": row["reserved_units"] / 1e9,
            "actual_cost_usd": None, "unknown_charge_retained": True, "request_row_unchanged": True,
            "no_retry": True, "seal_table": TABLE, "policy_sha256": POLICY_SHA256,
            "record_sha256": seal["record_sha256"], "diagnostic_path": seal["diagnostic_path"],
            "diagnostic_sha256": seal["diagnostic_sha256"], "binding_path": seal["binding_path"],
            "binding_sha256": seal["binding_sha256"]}


def _readonly_db(path):
    path = _safe_file(path)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN")
    cap = db.execute("SELECT value FROM settings WHERE key='cap_units'").fetchone()
    if not cap or int(cap[0]) != 40_000_000_000:
        db.close()
        raise BudgetError("Original aggregate cap changed")
    return db


def verified_terminal_loss(path, request_id):
    """Read-only proof for this amendment; legacy seals use their legacy APIs."""
    verify_dependencies()
    db = _readonly_db(path)
    try:
        row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        seal = _new_seal(db, row)
        _require(seal is not None, "No valid immutable triad HTTP terminal-loss seal exists")
        return _public_seal(row, seal)
    finally:
        db.close()


def completed_snapshot_allowing_sealed_losses(path):
    """Original completed-only snapshot shape; verified unknowns stay excluded."""
    verify_dependencies()
    db = _readonly_db(path)
    try:
        halt = db.execute("SELECT value FROM settings WHERE key='halt_reason'").fetchone()
        _require(halt is None, "Aggregate has a blocking halt reason")
        for row in db.execute("SELECT * FROM requests WHERE status IN ('pending','uncertain')"):
            _require(row["status"] != "pending" and TransportLedger._sealed(db, row), "Unsealed unresolved paid work cannot be omitted")
        result = {}
        for row in db.execute("SELECT * FROM requests WHERE request_id LIKE ? ORDER BY request_id", (NAMESPACE + ":%",)):
            if row["status"] != "completed":
                _require(_new_seal(db, row) is not None, "Unresolved namespace request is not a verified transient loss")
                continue
            _require(isinstance(row["response_json"], str) and isinstance(row["receipt_json"], str), "Completed paid evidence is missing")
            checksum = hashlib.sha256((row["response_json"] + "\n" + row["receipt_json"]).encode()).hexdigest()
            _require(checksum == row["result_hash"], "Original paid response/receipt checksum changed")
            receipt, response = json.loads(row["receipt_json"]), json.loads(row["response_json"])
            _require(receipt.get("request_id") == row["request_id"] and receipt.get("request_hash") == row["request_hash"], "Paid request binding changed")
            _require(receipt.get("requested_model") == row["model"] and receipt.get("kind") == row["kind"], "Paid model/kind binding changed")
            _require(_units(receipt.get("cost_usd")) == row["actual_units"], "Original paid amount changed")
            _require(response.get("id") == receipt.get("response_id") and response.get("usage") == receipt.get("usage"), "Raw paid response identity/usage changed")
            result[row["request_id"]] = {"result_hash": checksum, "request_hash": row["request_hash"],
                "actual_units": row["actual_units"], "model": row["model"], "kind": row["kind"]}
        return result
    finally:
        db.close()
