"""Narrow response-format amendment; original triad sources/results stay frozen.

No query or semantic correction is performed here. A unique complete JSON object
may have exactly one extraneous final closing brace removed for parsing. Only
private rationale/carryover text may receive a flagged bounded context excerpt;
the complete original text, response and receipt are archived unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

from visual_collusion.budget import canonical, _units
from experiments.pressure_interview_v1.contracts import ContractError
from experiments.pressure_interview_v1.live_provider import MODEL, _save_once
from experiments.triad_consensus_v2.backend import TriadBackend, NAMESPACE, validate_direct


class FormatRepairError(ValueError):
    """The observation is outside the deliberately narrow format allowance."""


class FormatEvidenceError(ContractError):
    """Receipt/source integrity errors remain hard technical stops."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FormatRepairError("Duplicate JSON keys are not recoverable")
        result[key] = value
    return result


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise FormatRepairError("Nonfinite JSON numbers are not recoverable")
    return value


def parse_response_object(text):
    """Parse one object or exactly that object plus one terminal extra '}'.

    Braces inside strings are handled by the JSON decoder. Missing braces,
    multiple extra braces, trailing prose, additional JSON and duplicate keys
    are rejected. One complete traditional JSON code fence remains supported.
    The original text is returned verbatim for immutable evidence.
    """
    if not isinstance(text, str) or not text.strip():
        raise FormatRepairError("A nonempty observed text completion is required")
    try:
        raw_bytes = text.encode("utf-8")
    except UnicodeError:
        raise FormatRepairError("Observed completion is not valid UTF-8") from None
    body, flags = text.strip(), []
    fence = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", body)
    if fence is not None:
        body = fence.group(1).strip()
        flags.append("whole_reply_json_code_fence_existing_parser")
    decoder = json.JSONDecoder(object_pairs_hook=_unique, parse_float=_finite_float,
        parse_constant=lambda _: (_ for _ in ()).throw(FormatRepairError("Nonfinite JSON is not recoverable")))
    try:
        value, end = decoder.raw_decode(body)
    except (ValueError, TypeError) as error:
        raise FormatRepairError(str(error)) from None
    if not isinstance(value, dict):
        raise FormatRepairError("The completed reply must contain one JSON object")
    trailing = body[end:].strip()
    if trailing:
        if trailing != "}":
            raise FormatRepairError("Only one extraneous terminal closing brace is recoverable")
        flags.append("one_extraneous_terminal_closing_brace")
    return {"value": value, "syntax_flags": flags,
            "original_text": text, "original_text_sha256": _sha(raw_bytes),
            "original_text_utf8_bytes": len(raw_bytes)}


_EXCERPT_MARKER = "\n[Technical excerpt; full original note archived.]"


def normalize_private_text_fields(value, mode):
    """Derive bounded private context views without editing other fields."""
    result, changes = deepcopy(value), []
    paths = []
    if mode in {"message_vote", "vote_only"}:
        paths.append(((), "rationale", 800))
    elif mode == "memory_only":
        paths.append(((), "private_note", 2048))
    elif mode in {"image_vote", "image_memory"} and value.get("action") == "deliver":
        paths.append((("arguments",), "rationale" if mode == "image_vote" else "private_note",
                      800 if mode == "image_vote" else 2048))
    for parents, key, limit in paths:
        target = result
        for parent in parents:
            target = target.get(parent) if isinstance(target, dict) else None
        if not isinstance(target, dict) or not isinstance(target.get(key), str):
            continue  # The unchanged original validator rejects wrong types.
        text = target[key]
        try:
            encoded = text.encode("utf-8")
        except UnicodeError:
            raise FormatRepairError("Private text is not valid UTF-8") from None
        if len(encoded) <= limit:
            continue
        # Length handling must not accidentally conceal an invalid control code.
        if any(ord(char) < 32 and char not in "\n\t" for char in text):
            raise FormatRepairError("Private note with unsupported controls is not length-normalized")
        prefix = encoded[:limit-len(_EXCERPT_MARKER.encode())].decode("utf-8", errors="ignore")
        excerpt = prefix + _EXCERPT_MARKER
        target[key] = excerpt
        changes.append({"field_path": list(parents) + [key],
                        "normalization": "bounded_UTF8_prefix_with_explicit_excerpt_marker",
                        "context_limit_utf8_bytes": limit,
                        "full_original_text": text, "full_original_utf8_bytes": len(encoded),
                        "full_original_sha256": _sha(encoded),
                        "context_excerpt": excerpt, "context_excerpt_utf8_bytes": len(excerpt.encode()),
                        "context_excerpt_sha256": _sha(excerpt.encode())})
    return {"value": result, "private_text_normalizations": changes}


def completed_row(aggregate_path, request_id):
    path = Path(aggregate_path)
    if path.is_symlink() or not path.is_file():
        raise FormatEvidenceError("An existing nonsymlink aggregate ledger is required")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        raw = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
    if raw is None:
        raise FormatEvidenceError("No exact completed observation exists")
    return dict(raw)


def recover_completed_response(row, mode):
    """Read one integrity-checked completed GLM observation; never infer a vote."""
    if (row.get("status") != "completed" or row.get("kind") != "chat" or row.get("model") != MODEL
            or row.get("actual_units") is None or not isinstance(row.get("response_json"), str)
            or not isinstance(row.get("receipt_json"), str)):
        raise FormatEvidenceError("Only a completed, paid, pinned GLM chat response can be recovered")
    if _sha((row["response_json"] + "\n" + row["receipt_json"]).encode()) != row.get("result_hash"):
        raise FormatEvidenceError("Stored response/receipt integrity mismatch")
    response, receipt = json.loads(row["response_json"]), json.loads(row["receipt_json"])
    choices = response.get("choices")
    if (not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict)
            or choices[0].get("finish_reason") != "stop" or not isinstance(choices[0].get("message"), dict)):
        raise FormatRepairError("Recovery requires exactly one complete stopped answer")
    message = choices[0]["message"]
    if message.get("refusal"):
        raise FormatRepairError("A refusal is never format-recovered")
    if (response.get("model") not in {MODEL, MODEL.split("/", 1)[1]}
            or response.get("provider") not in {"OpenInference", "open-inference/fp4"}
            or receipt.get("request_id") != row.get("request_id")
            or receipt.get("request_hash") != row.get("request_hash")
            or receipt.get("requested_model") != MODEL or receipt.get("kind") != "chat"
            or receipt.get("returned_model") != response.get("model")
            or receipt.get("provider") != response.get("provider")
            or receipt.get("response_id") != response.get("id")
            or receipt.get("usage") != response.get("usage")
            or (isinstance(response.get("usage"), dict) and response["usage"].get("is_byok") is True)):
        raise FormatEvidenceError("Exact model/route/request receipt mismatch")
    try:
        cost = Decimal(str(receipt["cost_usd"]))
        if (not cost.is_finite() or _units(receipt["cost_usd"]) != row["actual_units"]
                or Decimal(str(response["usage"]["cost"])) != cost):
            raise ValueError("cost")
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise FormatEvidenceError("Completed cost evidence does not match") from None
    parsed = parse_response_object(message.get("content"))
    normalized = normalize_private_text_fields(parsed["value"], mode)
    record = {"amendment": "triad_response_format_v4", "request_id": row["request_id"],
              "mode": mode, "original_result_hash": row["result_hash"],
              "response": response, "receipt": receipt,
              "parsed_original_value": parsed["value"], "context_value": normalized["value"],
              **{k: v for k, v in parsed.items() if k != "value"},
              "private_text_normalizations": normalized["private_text_normalizations"],
              "decision_inferred_or_corrected": False,
              "new_POST_or_repair_query": False,
              "full_response_and_private_notes_visibility": "experimenter_only_unless_separately_allowlisted"}
    return record


def build_posthoc_supplement(aggregate_path, local_id, mode, output_path):
    """Create an offline addendum; existing outcomes, reviews and inputs stay put."""
    request_id = local_id if local_id.startswith(NAMESPACE + ":") else NAMESPACE + ":" + local_id
    record = recover_completed_response(completed_row(aggregate_path, request_id), mode)
    if mode not in {"vote_only", "message_vote", "memory_only"}:
        raise FormatRepairError("Historical supplement requires a complete single-call stage contract")
    try:
        validate_direct(mode, record["context_value"])
    except ContractError as error:
        raise FormatRepairError("Recovered historical fields still fail their unchanged contract: " + str(error)) from None
    record.update({"record_kind": "posthoc_technical_supplement",
                   "unchanged_direct_stage_contract_validated": True,
                   "historical_original_invalid_outcome_preserved": True,
                   "historical_original_reviewer_output_preserved": True,
                   "previous_actor_inputs_not_retroactively_changed": True,
                   "explicit_decision": record["parsed_original_value"].get("decision"),
                   "rationale_is_self_report_and_may_conflict_with_explicit_decision": True})
    _save_once(Path(output_path), record)
    return record


class FormatV4Backend(TriadBackend):
    """The original API/allowances plus disclosed local format normalization."""

    def _call(self, local_id, kind, **kwargs):
        result = super()._call(local_id, kind, **kwargs)
        if kind != "chat":
            return result
        try:
            record = recover_completed_response(completed_row(self.guard.path, self.guard._request_id(local_id)),
                                                self._format_mode)
        except FormatRepairError as error:
            if result.get("invalid_provider_answer"):
                return result
            # Duplicate/nonfinite/ambiguous output stays an observed invalid;
            # this path never asks the provider for a replacement answer.
            rejected = self.evidence_dir / "format_v4" / "rejected" / (_sha(local_id.encode()) + ".json")
            _save_once(rejected, {"request_id": local_id, "reason": str(error),
                                  "receipt": result["receipt"], "raw_response_in_original_aggregate_ledger": True})
            return {"invalid_provider_answer": True, "receipt": result["receipt"],
                    "reason": str(error), "evidence_path": str(rejected)}
        changed = ("one_extraneous_terminal_closing_brace" in record["syntax_flags"] or
                   bool(record["private_text_normalizations"]))
        if not changed:
            return result
        path = self.evidence_dir / "format_v4" / "normalizations" / (_sha(local_id.encode()) + ".json")
        record["original_backend_evidence_path"] = result["evidence_path"]
        _save_once(path, record)
        self._format_records.append({"request_id": local_id, "path": str(path), "sha256": _sha(path.read_bytes()),
                                     "syntax_flags": record["syntax_flags"],
                                     "private_text_fields": [n["field_path"] for n in record["private_text_normalizations"]]})
        normalized = dict(result)
        normalized.pop("invalid_provider_answer", None)
        normalized.pop("reason", None)
        normalized.update({"content": deepcopy(record["context_value"]),
                           "evidence_path": str(path), "technical_format_normalization": True})
        return normalized

    def run_actor(self, request):
        request_hash = _sha(canonical(request).encode())
        sidecar = self.evidence_dir / "format_v4" / "stage_results" / (_sha(request["request_id"].encode()) + ".json")
        if sidecar.exists():
            if sidecar.is_symlink():
                raise ContractError("Format sidecar cannot be symlinked")
            saved = json.loads(sidecar.read_text())
            if saved.get("request_hash") != request_hash:
                raise ContractError("Format sidecar request changed")
            for item in saved["normalizations"]:
                path = Path(item["path"])
                if path.is_symlink() or _sha(path.read_bytes()) != item["sha256"]:
                    raise ContractError("Full format-normalization evidence changed")
            # Keep the original backend's cached-raster integrity check.
            original = super().run_actor(request)
            if original != saved["original_backend_result"]:
                raise ContractError("Original bounded backend result changed")
            return saved["result"]
        self._format_mode, self._format_records = request["mode"], []
        try:
            original = super().run_actor(request)
            if not self._format_records:
                return original
            result = deepcopy(original)
            result["provenance"]["technical_format_v4"] = {
                "normalizations": deepcopy(self._format_records),
                "private_text_views_are_explicitly_marked_excerpts": True,
                "decision_inferred_or_corrected": False, "paid_retries": 0}
            for item in self._format_records:
                if item["path"] not in result["evidence_paths"]:
                    result["evidence_paths"].append(item["path"])
            _save_once(sidecar, {"request_hash": request_hash, "normalizations": self._format_records,
                                 "original_backend_result": original, "result": result})
            return result
        finally:
            self._format_mode, self._format_records = None, []
