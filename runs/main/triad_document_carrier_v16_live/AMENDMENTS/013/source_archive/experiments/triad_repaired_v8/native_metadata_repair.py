"""Two exact first-paid logs recovered from a native transport metadata error.

The original observed outcomes remain immutable and invalid. This module only
projects the first already-paid two-string logs into their owner's later memory.
The frozen literal binds the complete sidecar without a current-freeze cycle.
It performs no provider calls, ledger writes, output rewriting or text cleanup.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path

from .contracts import validate_private_log


ROOT = Path(__file__).resolve().parents[2]
BOUND_OUT = ROOT / "runs/main/triad_repaired_v8_live"
BOUND_CASE = "case_05_repaired_v6_shared_coached"
SIDECAR_FILE = "technical_log_corrections.json"
EXPECTED_SIDECAR_SHA256 = "dbdca3e9044439383c5eff8821bdc3313eb91a2ce2c06ca5a4ee08836e8b8d4d"
_CACHE = {}


class NativeMetadataRepairError(ValueError):
    """The exact forensic projection no longer matches its frozen evidence."""


def _require(value, message):
    if not value:
        raise NativeMetadataRepairError(message)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "Duplicate forensic JSON key")
        result[key] = value
    return result


def _json(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        _canonical(value).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        if isinstance(error, NativeMetadataRepairError):
            raise
        raise NativeMetadataRepairError("Invalid forensic UTF-8 JSON") from None
    _require(isinstance(value, dict), "Forensic JSON must be an object")
    return value


def _path(out, relative):
    _require(isinstance(relative, str), "Forensic path must be a string")
    rel = Path(relative)
    _require(not rel.is_absolute() and rel.parts and ".." not in rel.parts,
             "Forensic path escapes its exact output")
    path = out / rel
    _require(not any(item.is_symlink() for item in (path, *path.parents)),
             "Forensic evidence cannot use symlinks")
    _require(path.is_file(), "Forensic evidence is missing")
    return path


def _signature(path):
    _require(not any(item.is_symlink() for item in (path, *path.parents)),
             "Forensic evidence cannot use symlinks")
    st = path.stat()
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _binding(out):
    """Verify original evidence once, rechecking all file identities on reuse."""
    key = str(out), EXPECTED_SIDECAR_SHA256
    cached = _CACHE.get(key)
    if cached is not None:
        if all(_signature(path) == sig for path, sig in cached["signatures"].items()):
            return cached["entries"]
        _CACHE.pop(key, None)
    paths = {}

    def read(relative, expected=None):
        path = _path(out, relative)
        raw = path.read_bytes()
        if expected is not None:
            _require(_sha(raw) == expected, "Forensic evidence SHA-256 changed")
        paths[path] = _signature(path)
        return raw

    sidecar = _json(read(SIDECAR_FILE, EXPECTED_SIDECAR_SHA256))
    _require(sidecar.get("revision") == "native_index_metadata_repair02"
             and sidecar.get("output_root") == str(out)
             and sidecar.get("case_id") == BOUND_CASE
             and sidecar.get("original_terminal_prefix_count") == 70
             and sidecar.get("ledger_rows_at_hold") == 736,
             "Forensic sidecar scope differs from the actual stopped prefix")
    read("config.json", sidecar["config_sha256"])
    read("plan.json", sidecar["plan_sha256"])
    previous = _json(read("analysis/native_index_hold/repair02/freeze.before.json",
                          sidecar["prior_active_freeze_sha256"]))
    _require(len(previous.get("source_files", {})) == 142,
             "Forensic predecessor is not the archived142-source freeze")
    entries = sidecar.get("entries")
    _require(isinstance(entries, list) and len(entries) == 2
             and {item.get("role") for item in entries if isinstance(item, dict)} == {"A", "B"},
             "Only the two original A/B logs may be projected")
    recovered = {}
    for entry in entries:
        role = entry["role"]
        stage = "POSTVERDICT_" + role + "_LOG"
        logical = BOUND_CASE + ".r09." + stage
        physical = "triad_repaired_v8:" + logical + ".step1.attempt1"
        prefix = "cases/" + BOUND_CASE + "/rounds/r09/" + stage
        _require(entry.get("round") == 9 and entry.get("stage") == stage
                 and entry.get("function") == "record_log"
                 and entry.get("classification") == "first_paid_log_recovered_from_transport_metadata_rejection"
                 and entry.get("first_physical_request_id") == physical
                 and entry.get("excluded_second_physical_request_id") ==
                     "triad_repaired_v8:" + logical + ".step2.attempt1"
                 and entry.get("original_request_file") == prefix + ".request.json"
                 and entry.get("original_outcome_file") == prefix + ".outcome.json"
                 and entry.get("first_ledger_row_file") ==
                     "analysis/native_index_hold/repair02/ledger_first_rows/" + role + ".json",
                 "Forensic selection must be the exact first paid log")
        request = _json(read(entry["original_request_file"], entry["original_request_sha256"]))
        _require(request.get("request_id") == logical
                 and request.get("request_hash") == entry["original_request_hash"]
                 and request.get("role") == role and request.get("round") == 9
                 and request.get("stage") == stage and request.get("mode") == "memory_only",
                 "Forensic prepared log request changed")
        outcome = _json(read(entry["original_outcome_file"], entry["original_outcome_sha256"]))
        _require(outcome.get("status") == "invalid_model_output" and outcome.get("output") is None
                 and outcome.get("record_hash") == entry["original_outcome_record_hash"]
                 and _sha(_canonical({k: v for k, v in outcome.items() if k != "record_hash"}).encode())
                     == outcome["record_hash"], "Original invalid log observation changed")
        if role == "B":
            _require(outcome["record_hash"] == sidecar["original_terminal_prefix_chain_head"],
                     "Original70-stage chain head changed")
        row = _json(read(entry["first_ledger_row_file"], entry["first_ledger_row_sha256"]))
        response_text, receipt_text = row.get("response_json"), row.get("receipt_json")
        _require(row.get("request_id") == physical and row.get("status") == "completed"
                 and row.get("kind") == "chat" and row.get("model") == "z-ai/glm-5.3-flash"
                 and isinstance(response_text, str) and isinstance(receipt_text, str)
                 and _sha(response_text.encode()) == entry["response_json_sha256"]
                 and _sha(receipt_text.encode()) == entry["receipt_json_sha256"]
                 and _sha((response_text + "\n" + receipt_text).encode()) == row.get("result_hash"),
                 "First paid log row or raw response/receipt changed")
        response, receipt = _json(response_text), _json(receipt_text)
        _require(receipt.get("request_id") == physical
                 and receipt.get("request_hash") == row.get("request_hash")
                 and receipt.get("kind") == "chat"
                 and receipt.get("requested_model") == row["model"]
                 and receipt.get("response_id") == response.get("id")
                 and int((Decimal(str(receipt["cost_usd"])) * Decimal(1000000000))
                         .to_integral_value(rounding=ROUND_CEILING)) == row.get("actual_units"),
                 "First paid log receipt binding changed")
        choices = response.get("choices")
        _require(isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict),
                 "First paid log must contain one native answer")
        choice = choices[0]
        message = choice.get("message")
        _require(choice.get("finish_reason") in {"tool_calls", "stop"}
                 and isinstance(message, dict) and not message.get("refusal"),
                 "First paid log is incomplete or a refusal")
        calls = message.get("tool_calls")
        _require(isinstance(calls, list) and len(calls) == 1 and isinstance(calls[0], dict),
                 "First paid log must call exactly one function")
        call = calls[0]
        function = call.get("function")
        _require(set(call) == {"id", "type", "function", "index"}
                 and type(call["index"]) is int and call["index"] == 0
                 and call.get("type") == "function" and isinstance(call.get("id"), str) and call["id"]
                 and isinstance(function, dict) and set(function) == {"name", "arguments"}
                 and function.get("name") == "record_log" and isinstance(function.get("arguments"), str)
                 and _sha(function["arguments"].encode()) == entry["arguments_sha256"],
                 "Only the exact standard index0 metadata error is recoverable")
        projection = _json(function["arguments"])
        try:
            validate_private_log(projection)
        except ValueError:
            raise NativeMetadataRepairError("First paid native log arguments are invalid") from None
        _require(projection == entry["projection"]
                 and _sha(_canonical(projection).encode()) == entry["projection_sha256"],
                 "Forensic log projection differs from the first paid arguments")
        recovered[role] = {"projection": projection, "outcome": outcome}
    _CACHE[key] = {"entries": recovered, "signatures": paths}
    return recovered


def recovered_private_log(directory, number, role, original_outcome):
    """Return exact first log strings only in the two frozen live positions."""
    if number != 9 or role not in {"A", "B"}:
        return None
    directory = Path(directory).absolute()
    if directory != BOUND_OUT / "cases" / BOUND_CASE:
        return None
    entry = _binding(BOUND_OUT)[role]
    _require(original_outcome == entry["outcome"], "Supplied original log outcome differs from its binding")
    return deepcopy(entry["projection"])
