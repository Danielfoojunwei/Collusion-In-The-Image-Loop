"""Narrow memory-note wrapper amendment layered on the frozen v4 adapter.

Only memory_only replies may unwrap exactly properties.private_note, optionally
accompanied by the literal root marker property:true. Original observations,
receipts, notes and historical stage outcomes remain immutable. Other actor
fields and modes retain their existing contracts and all calls retain v4 bounds.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from visual_collusion.budget import canonical
from experiments.pressure_interview_v1.contracts import ContractError
from experiments.pressure_interview_v1.live_provider import _save_once
from experiments.triad_consensus_v2.backend import NAMESPACE, validate_direct
from scripts import triad_response_format_v4 as v4


_V4_SHA256 = "ae312da63aae7b1aef74bf97abad6cf612dc81ed828a541be1685b47392ef92a"
_v4_path = Path(v4.__file__)
if _v4_path.is_symlink() or hashlib.sha256(_v4_path.read_bytes()).hexdigest() != _V4_SHA256:
    raise ContractError("The frozen v4 format dependency changed")

FormatRepairError = v4.FormatRepairError
FormatEvidenceError = v4.FormatEvidenceError


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def recover_memory_wrapper(value):
    """Unwrap only the two observed exact, nonrecursive memory-only shapes."""
    if not isinstance(value, dict) or set(value) not in ({"properties"}, {"properties", "property"}):
        raise FormatRepairError("Not an allowlisted memory-note schema wrapper")
    if "property" in value and value["property"] is not True:
        raise FormatRepairError("Only the literal property:true schema marker is allowlisted")
    properties = value["properties"]
    if (not isinstance(properties, dict) or set(properties) != {"private_note"}
            or not isinstance(properties["private_note"], str)):
        raise FormatRepairError("The wrapper must contain exactly one string private_note")
    flags = ["memory_only_exact_properties_private_note_wrapper"]
    if "property" in value:
        flags.append("literal_property_true_schema_marker")
    return {"value": {"private_note": properties["private_note"]}, "schema_flags": flags}


def recover_completed_memory_response(row):
    """Derive a validated memory view from a paid completed immutable answer."""
    if re.fullmatch(r"triad_consensus_v2:[A-Za-z0-9_.:-]+\.r[0-9]{2}\.POSTVERDICT_[AB]_LOG\.step1",
                    row.get("request_id", "")) is None:
        raise FormatEvidenceError("Wrapper recovery is restricted to actual A/B post-verdict memory stages")
    record = v4.recover_completed_response(row, "memory_only")
    unwrapped = recover_memory_wrapper(record["parsed_original_value"])
    normalized = v4.normalize_private_text_fields(unwrapped["value"], "memory_only")
    try:
        validate_direct("memory_only", normalized["value"])
    except ContractError as error:
        raise FormatRepairError("Unwrapped memory note still fails its unchanged contract: " + str(error)) from None
    record.update({"amendment": "triad_response_format_v5",
                   "frozen_v4_dependency_sha256": _V4_SHA256,
                   "unwrapped_original_value": unwrapped["value"],
                   "context_value": normalized["value"],
                   "schema_flags": unwrapped["schema_flags"],
                   "private_text_normalizations": normalized["private_text_normalizations"],
                   "semantic_fields_inferred_or_changed": False,
                   "wrapper_scope": "memory_only_private_note"})
    return record


def build_posthoc_memory_supplement(aggregate_path, local_id, output_path):
    """Write a supplemental note record without rewriting historical inputs."""
    request_id = local_id if local_id.startswith(NAMESPACE + ":") else NAMESPACE + ":" + local_id
    record = recover_completed_memory_response(v4.completed_row(aggregate_path, request_id))
    record.update({"record_kind": "posthoc_memory_note_technical_supplement",
                   "historical_original_invalid_outcome_preserved": True,
                   "previous_actor_inputs_not_retroactively_changed": True,
                   "unchanged_memory_only_contract_validated": True})
    _save_once(Path(output_path), record)
    return record


class FormatV5Backend(v4.FormatV4Backend):
    """Same paid workflow; unwrap only exact memory-note schema wrappers."""

    def _call(self, local_id, kind, **kwargs):
        result = super()._call(local_id, kind, **kwargs)
        if (kind != "chat" or self._format_mode != "memory_only"
                or result.get("invalid_provider_answer") or not isinstance(result.get("content"), dict)
                or "properties" not in result["content"]):
            return result
        try:
            record = recover_completed_memory_response(v4.completed_row(self.guard.path, self.guard._request_id(local_id)))
        except FormatRepairError:
            return result  # Original validator retains the still-invalid answer.
        path = self.evidence_dir / "format_v5" / "normalizations" / (_sha(local_id.encode()) + ".json")
        record["original_backend_evidence_path"] = result["evidence_path"]
        _save_once(path, record)
        item = {"request_id": local_id, "path": str(path), "sha256": _sha(path.read_bytes()),
                "schema_flags": record["schema_flags"], "syntax_flags": record["syntax_flags"],
                "private_text_fields": [n["field_path"] for n in record["private_text_normalizations"]]}
        self._format_v5_records.append(item)
        normalized = dict(result)
        normalized.update({"content": deepcopy(record["context_value"]),
                           "evidence_path": str(path), "technical_format_v5_normalization": True})
        return normalized

    def run_actor(self, request):
        if request.get("mode") != "memory_only":
            return super().run_actor(request)
        request_hash = _sha(canonical(request).encode())
        sidecar = self.evidence_dir / "format_v5" / "stage_results" / (_sha(request["request_id"].encode()) + ".json")
        if sidecar.exists():
            if sidecar.is_symlink():
                raise ContractError("Memory-wrapper result cannot be symlinked")
            saved = json.loads(sidecar.read_text())
            if saved.get("request_hash") != request_hash:
                raise ContractError("Memory-wrapper request changed")
            for item in saved["normalizations"]:
                path = Path(item["path"])
                if path.is_symlink() or _sha(path.read_bytes()) != item["sha256"]:
                    raise ContractError("Full memory-wrapper evidence changed")
            original = super().run_actor(request)
            if original != saved["original_backend_result"]:
                raise ContractError("Original bounded memory result changed")
            return saved["result"]
        self._format_v5_records = []
        try:
            original = super().run_actor(request)
            if not self._format_v5_records:
                return original
            result = deepcopy(original)
            result["provenance"]["technical_format_v5"] = {
                "normalizations": deepcopy(self._format_v5_records),
                "scope": "memory_only_exact_properties_private_note_wrapper",
                "semantic_fields_inferred_or_changed": False, "paid_retries": 0}
            for item in self._format_v5_records:
                if item["path"] not in result["evidence_paths"]:
                    result["evidence_paths"].append(item["path"])
            _save_once(sidecar, {"request_hash": request_hash, "normalizations": self._format_v5_records,
                                 "original_backend_result": original, "result": result})
            return result
        finally:
            self._format_v5_records = []
