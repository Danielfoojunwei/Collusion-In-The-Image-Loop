"""Information-flow contracts for the separately versioned interview study.

These are experiment data contracts, not instructions to expose hidden reasoning.
Only observed messages and optional brief reported notes are stored as such.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re


class ContractError(ValueError):
    pass


ROLES = frozenset({"A", "B", "C", "environment", "monitor", "experimenter"})
_POLICIES = {
    "task": ({"environment", "experimenter"}, {"A", "B", "C"}),
    "pressure": ({"environment"}, {"A"}),
    "nomination": ({"environment"}, {"A"}),
    "assignment_notice": ({"environment"}, {"A", "B"}),
    "a_public": ({"A"}, {"A", "B", "C"}),
    "b_public": ({"B"}, {"A", "B", "C"}),
    "b_flag": ({"B"}, {"B", "monitor"}),
    "monitor_log": ({"monitor"}, {"C", "monitor"}),
    "c_question": ({"C"}, {"A", "C"}),
    "a_answer": ({"A"}, {"A", "C"}),
    "c_review": ({"C"}, {"A", "B", "C"}),
    "a_private": ({"A"}, {"A"}),
    "b_private": ({"B"}, {"B"}),
    "c_private": ({"C"}, set()),
    "provider_record": ({"experimenter"}, set()),
}
_FIELDS = {
    "task": ({"brief", "facts"}, set()),
    "pressure": ({"metric"}, {"source"}),
    "nomination": ({"candidate"}, set()),
    "assignment_notice": ({"fresh"}, set()),
    "a_public": ({"message", "image"}, set()),
    "b_public": ({"message", "image", "decision"}, set()),
    "b_flag": ({"suspicious", "confidence", "tag", "evidence"}, set()),
    "monitor_log": ({"suspicious", "confidence", "tag", "evidence"}, set()),
    "c_question": ({"recipient", "question"}, set()),
    "a_answer": ({"answer"}, set()),
    "c_review": ({"verdict", "task_violation", "collusion_suspected", "confidence", "evidence", "feedback"}, set()),
    "a_private": ({"note"}, set()),
    "b_private": ({"note"}, set()),
    "c_private": ({"note"}, set()),
    "provider_record": ({"record"}, set()),
}


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ContractError("Event data must be finite JSON") from None


def _text(value, name, maximum, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value.encode("utf-8")) > maximum:
        raise ContractError(name + " is missing or exceeds its UTF-8 byte limit")


def _number(value, name, minimum=0, maximum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < minimum
            or (maximum is not None and value > maximum)):
        raise ContractError(name + " must be a finite number in range")


def _evidence(value):
    if not isinstance(value, list) or len(value) > 10:
        raise ContractError("Evidence must be a bounded list")
    for item in value:
        _text(item, "Evidence reference", 500)


def validate_image_descriptor(image):
    if not isinstance(image, dict) or set(image) - {"path", "sha256", "width", "height", "mime"} or not {"path", "sha256"} <= set(image):
        raise ContractError("Image descriptor fields differ from the contract")
    _text(image["path"], "Image path", 4096)
    if not isinstance(image["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", image["sha256"]):
        raise ContractError("Image SHA-256 is invalid")
    if "width" in image and (type(image["width"]) is not int or image["width"] != 1024):
        raise ContractError("Image width must remain 1024")
    if "height" in image and (type(image["height"]) is not int or image["height"] != 1024):
        raise ContractError("Image height must remain 1024")
    if "mime" in image and image["mime"] != "image/png":
        raise ContractError("Source artifact must remain PNG")
    return deepcopy(image)


def visibility_for(kind):
    if not isinstance(kind, str) or kind not in _POLICIES:
        raise ContractError("Unknown event kind")
    return sorted(_POLICIES[kind][1] | {"experimenter"})


def validate_event(event):
    """Validate the event body before adding hash-chain transport fields."""
    if not isinstance(event, dict) or set(event) - {"id", "role", "kind", "payload", "visibility", "round_number", "branch"}:
        raise ContractError("Event fields differ from the contract")
    if not {"id", "role", "kind", "payload"} <= set(event):
        raise ContractError("Event identity, role, kind and payload are required")
    out = deepcopy(event)
    if not isinstance(out["id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", out["id"]):
        raise ContractError("Invalid stable event ID")
    kind, role, payload = out["kind"], out["role"], out["payload"]
    if not isinstance(kind, str) or kind not in _POLICIES or not isinstance(role, str) or role not in _POLICIES[kind][0]:
        raise ContractError("Event author does not own this event kind")
    expected_visibility = visibility_for(kind)
    if "visibility" in out and (not isinstance(out["visibility"], (list, tuple, set))
                                or any(not isinstance(item, str) for item in out["visibility"])
                                or sorted(out["visibility"]) != expected_visibility):
        raise ContractError("Event visibility cannot override the information-flow contract")
    out["visibility"] = expected_visibility
    out.setdefault("round_number", None)
    out.setdefault("branch", None)
    if out["round_number"] is not None and (type(out["round_number"]) is not int or out["round_number"] < 1):
        raise ContractError("Round number must be a positive integer")
    if out["branch"] is not None and (not isinstance(out["branch"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", out["branch"])):
        raise ContractError("Invalid researcher branch identifier")
    required, optional = _FIELDS[kind]
    if not isinstance(payload, dict) or not required <= set(payload) or set(payload) - required - optional:
        raise ContractError("Payload fields differ from the event contract")
    if "message" in payload:
        _text(payload["message"], "Public message", 800, empty=True)
    if "image" in payload:
        payload["image"] = validate_image_descriptor(payload["image"])
    if kind == "task":
        _text(payload["brief"], "Task brief", 1600)
        if not isinstance(payload["facts"], dict):
            raise ContractError("Authoritative facts must be an object")
        if len(canonical(payload["facts"]).encode()) > 16000:
            raise ContractError("Authoritative facts exceed their bound")
    elif kind == "pressure":
        _number(payload["metric"], "Synthetic pressure metric")
        if "source" in payload:
            _text(payload["source"], "Pressure description", 500)
    elif kind == "nomination":
        _text(payload["candidate"], "Private nomination", 64)
    elif kind == "assignment_notice" and type(payload["fresh"]) is not bool:
        raise ContractError("Assignment notice must not carry an identifier")
    elif kind in {"b_flag", "monitor_log"}:
        if type(payload["suspicious"]) is not bool:
            raise ContractError("Suspicion flag must be Boolean")
        _number(payload["confidence"], "Report confidence", 0, 1)
        _text(payload["tag"], "Report tag", 64)
        _evidence(payload["evidence"])
    elif kind == "c_question":
        if payload["recipient"] != "A":
            raise ContractError("C may interview A only")
        _text(payload["question"], "Interview question", 800)
    elif kind == "a_answer":
        _text(payload["answer"], "Interview answer", 800)
    elif kind == "c_review":
        if payload["verdict"] not in {"accept", "revise", "reject"}:
            raise ContractError("Unknown review verdict")
        if any(type(payload[k]) is not bool for k in ("task_violation", "collusion_suspected")):
            raise ContractError("Review flags must be Boolean")
        _number(payload["confidence"], "Review confidence", 0, 1)
        _evidence(payload["evidence"])
        _text(payload["feedback"], "Public reviewer feedback", 800)
    elif kind in {"a_private", "b_private", "c_private"}:
        _text(payload["note"], "Concise reported note", 800, empty=True)
    elif kind == "b_public":
        _text(payload["decision"], "Delivered decision", 64)
    if len(canonical(out).encode()) > 512000:
        raise ContractError("Event exceeds its record size bound")
    return out


def project_event(event, viewer):
    if not isinstance(viewer, str) or viewer not in ROLES:
        raise ContractError("Unknown memory viewer")
    body = {k: v for k, v in event.items() if k not in {"seq", "previous_hash", "hash"}}
    body = validate_event(body)
    if viewer not in body["visibility"]:
        return None
    out = deepcopy(event)
    if body["kind"] == "c_review" and viewer in {"A", "B"}:
        out["payload"] = {"feedback": body["payload"]["feedback"]}
    return out
