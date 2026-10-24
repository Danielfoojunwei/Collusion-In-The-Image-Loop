"""External, narrowly scoped post-stage format amendment for maximum-pressure v3.

Frozen actor inputs, prompts, images, allowances and prior outcomes are unchanged.
Only an exact private assessment-key spelling and one unambiguous terminal C
JSON delivery may be recovered. Raw paid observations and base evidence remain.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import threading

from experiments.pressure_interview_v1.contracts import ContractError
from experiments.pressure_interview_v1.live_provider import _save_once
from experiments.triad_max_pressure_v3.backend import TriadBackend, NAMESPACE, validate_direct
from experiments.triad_max_pressure_v3.contracts import IMAGE_REVIEW_KEYS, validate_image_review
from scripts import triad_response_format_v4 as v4

FormatRepairError = v4.FormatRepairError
FormatEvidenceError = v4.FormatEvidenceError
_ORIGINAL_PARSE = v4.parse_response_object
_PARSER_LOCK = threading.RLock()
_ALIAS = "generated_role_assessment"
_CANONICAL = "generator_role_assessment"
_MARKER = "\n[Technical excerpt; full original note archived.]"
REVISION = "triad_max_format_v1"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse_poststage_object(text, mode):
    """Retain original parser behavior; add only brace-free C delivery prose."""
    try:
        return _ORIGINAL_PARSE(text)
    except FormatRepairError as original_error:
        if mode not in {"memory_only", "image_memory"} or not isinstance(text, str):
            raise
        first = text.find("{")
        prefix = text[:first] if first >= 0 else ""
        if first <= 0 or not prefix.strip() or "{" in prefix or "}" in prefix:
            raise original_error
        body = text[first:].strip()
        decoder = json.JSONDecoder(object_pairs_hook=v4._unique, parse_float=v4._finite_float,
            parse_constant=lambda _: (_ for _ in ()).throw(FormatRepairError("Nonfinite JSON is not recoverable")))
        try:
            value, end = decoder.raw_decode(body)
        except (ValueError, TypeError) as error:
            raise FormatRepairError(str(error)) from None
        if body[end:].strip() or not isinstance(value, dict):
            raise FormatRepairError("Prose recovery requires one complete terminal private post-stage object")
        if mode == "image_memory":
            if (set(value) != {"action", "arguments"} or value.get("action") != "deliver"
                    or not isinstance(value.get("arguments"), dict)
                    or set(value["arguments"]) != {"private_note", "image_review"}):
                raise FormatRepairError("Prose recovery requires exactly one complete terminal image_memory deliver object")
            flag = "brace_free_prose_prefix_before_terminal_image_memory_delivery"
        else:
            if set(value) != {"private_note", "image_review"}:
                raise FormatRepairError("Prose recovery requires a complete terminal private_note and image_review object")
            flag = "brace_free_prose_prefix_before_terminal_memory_only_object"
        return {"value": value,
                "syntax_flags": [flag],
                "original_text": text, "original_text_sha256": _sha(text.encode()),
                "original_text_utf8_bytes": len(text.encode()),
                "prefix_diagnostic": {"exact_prefix": prefix, "prefix_utf8_bytes": len(prefix.encode()),
                                      "prefix_sha256": _sha(prefix.encode()), "prefix_contains_braces": False}}


@contextmanager
def _poststage_parser(mode):
    """Patch only the pure parser while the frozen receipt verifier executes."""
    with _PARSER_LOCK:
        previous = v4.parse_response_object
        if previous is not _ORIGINAL_PARSE:
            raise FormatEvidenceError("The frozen parser is unexpectedly patched")
        try:
            v4.parse_response_object = lambda text: parse_poststage_object(text, mode)
            yield
        finally:
            v4.parse_response_object = previous


def _excerpt_review(review, parents):
    changes = []
    for key, value in review.items():
        if not isinstance(value, str):
            continue
        try:
            raw = value.encode("utf-8")
        except UnicodeError:
            raise FormatRepairError("Private assessment is not valid UTF-8") from None
        if len(raw) <= 500:
            continue
        if any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise FormatRepairError("Invalid control characters cannot be hidden by an excerpt")
        excerpt = raw[:500-len(_MARKER.encode())].decode("utf-8", errors="ignore") + _MARKER
        review[key] = excerpt
        changes.append({"field_path": [*parents, "image_review", key],
            "normalization": "bounded_UTF8_prefix_with_explicit_excerpt_marker",
            "context_limit_utf8_bytes": 500, "full_original_text": value,
            "full_original_utf8_bytes": len(raw), "full_original_sha256": _sha(raw),
            "context_excerpt": excerpt, "context_excerpt_utf8_bytes": len(excerpt.encode()),
            "context_excerpt_sha256": _sha(excerpt.encode())})
    return changes


def recover_completed_poststage(row, mode):
    """Recover literal completed post-stage values, with exact source integrity."""
    if mode not in {"memory_only", "image_memory"}:
        raise FormatRepairError("This amendment cannot recover voting or task-image stages")
    suffix = (r"POSTVERDICT_[AB]_LOG\.step1" if mode == "memory_only" else
              r"POSTVERDICT_C_IMAGE_LOG\.step[1-4]")
    if re.fullmatch(re.escape(NAMESPACE) + r":[A-Za-z0-9_-]+\.r[0-9]{2}\." + suffix,
                    row.get("request_id", "")) is None:
        raise FormatEvidenceError("Post-stage request identity does not match its mode")
    with _poststage_parser(mode):
        record = v4.recover_completed_response(row, mode)
    value = deepcopy(record["context_value"])
    schema_flags, alias_changes = [], []
    if mode == "memory_only":
        if isinstance(value, dict) and set(value) in ({"properties"}, {"properties", "property"}):
            wrapped = value.get("properties")
            if (not isinstance(wrapped, dict) or set(wrapped) != {"private_note", "image_review"}
                    or ("property" in value and value["property"] is not True)):
                raise FormatRepairError("Not an exact existing memory wrapper with both required values")
            value = deepcopy(wrapped)
            schema_flags.append("exact_memory_properties_wrapper_with_all_required_fields")
        if not isinstance(value, dict) or set(value) != {"private_note", "image_review"}:
            raise FormatRepairError("A complete private_note and image_review must already exist")
        target, parents = value, []
    else:
        if (not isinstance(value, dict) or set(value) != {"action", "arguments"}
                or value.get("action") != "deliver" or not isinstance(value.get("arguments"), dict)
                or set(value["arguments"]) != {"private_note", "image_review"}):
            raise FormatRepairError("Only exact image_memory delivery is eligible")
        target, parents = value["arguments"], ["arguments"]
    review = target.get("image_review")
    if not isinstance(review, dict):
        raise FormatRepairError("An existing image_review object is required")
    expected_alias = (set(IMAGE_REVIEW_KEYS) - {_CANONICAL}) | {_ALIAS}
    if _ALIAS in review:
        if set(review) != expected_alias:
            raise FormatRepairError("The private alias requires exactly seven keys with no canonical duplicate or extras")
        # The sole allowed key alias preserves the exact string bytes. Content
        # excerpts, when necessary, are separately marked with the full original.
        review[_CANONICAL] = review.pop(_ALIAS)
        schema_flags.append("exact_private_image_review_generated_to_generator_key_alias")
        alias_changes.append({"field_path": [*parents, "image_review"],
                              "original_key": _ALIAS, "context_key": _CANONICAL,
                              "value_bytes_preserved_by_alias": True})
    if set(review) != set(IMAGE_REVIEW_KEYS):
        raise FormatRepairError("Private image_review has missing or unknown fields")
    normalized = v4.normalize_private_text_fields(value, mode)
    value = normalized["value"]
    target = value if mode == "memory_only" else value["arguments"]
    record["private_text_normalizations"].extend(normalized["private_text_normalizations"])
    record["private_text_normalizations"].extend(_excerpt_review(target["image_review"], parents))
    try:
        validate_image_review(target["image_review"])
        if mode == "memory_only":
            validate_direct(mode, value)
        else:
            # The image, chosen draft, round and delivery action are checked by
            # the unchanged engine/backend. This pure helper validates no image.
            if not isinstance(target["private_note"], str) or not target["private_note"].strip() or len(target["private_note"].encode()) > 2048:
                raise ValueError("Complete private note must fit its marked context allowance")
            if any(ord(char) < 32 and char not in "\n\t" for char in target["private_note"]):
                raise ValueError("Unsupported private note control character")
    except ValueError as error:
        raise FormatRepairError("Recovered original post-stage values still fail validation: " + str(error)) from None
    record.update({"amendment": REVISION, "revision": REVISION,
                   "schema_flags": schema_flags, "alias_changes": alias_changes,
                   "context_value": value, "decision_inferred_or_corrected": False,
                   "private_note_inferred_or_completed": False, "image_prompt_or_pixels_changed": False,
                   "new_POST_or_repair_query": False})
    return record


class FormatV1Backend(TriadBackend):
    """Same paid calls and allowances, with new post-stage-only format views."""
    def _call(self, local_id, kind, **kwargs):
        result = super()._call(local_id, kind, **kwargs)
        if kind != "chat" or self._active_mode not in {"memory_only", "image_memory"}:
            return result
        try:
            record = recover_completed_poststage(
                v4.completed_row(self.guard.path, self.guard._request_id(local_id)), self._active_mode)
        except FormatRepairError:
            return result  # Original validator retains any still-invalid answer.
        new_rule_used = (bool(record["alias_changes"]) or
            any(flag.startswith("brace_free_prose_prefix_before_terminal_") for flag in record["syntax_flags"]))
        if not new_rule_used:
            return result
        path = self.evidence_dir / "format_v1/normalizations" / (_sha(local_id.encode()) + ".json")
        record["original_backend_evidence_path"] = result["evidence_path"]
        _save_once(path, record)
        normalized = dict(result)
        normalized.pop("invalid_provider_answer", None)
        normalized.pop("reason", None)
        normalized.update({"content": deepcopy(record["context_value"]), "evidence_path": str(path),
                           "technical_max_format_v1": True})
        return normalized
