"""Resolve only hash-bound copied v7 images and their preserved v6 ancestry.

Pure local validation: no provider, ledger, network or original image reads.
"""
import hashlib
import json
from pathlib import Path
import re

from experiments.pressure_interview_v1.contracts import ContractError


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ContractError("Image reference cannot follow a symlink")
    return path.resolve()


def checked_json(path):
    path = safe(path)
    if not path.is_file():
        raise ContractError("Missing copied reference binding")
    raw = path.read_bytes()
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ContractError("Duplicate copied reference binding key")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise ContractError("Invalid copied reference binding") from None
    return value, raw


def imported_binding(backend):
    out = safe(backend.out)
    rule = backend.config.get("continuation", {})
    manifest, raw = checked_json(out / "continuation_import.json")
    plan, plan_raw = checked_json(out / "plan.json")
    frozen, _ = checked_json(out / "freeze.json")
    _, config_raw = checked_json(out / "config.json")
    body = {k: v for k, v in manifest.items() if k != "record_hash"}
    required = {"source_out": rule.get("source_out"), "source_config_sha256": rule.get("source_config_sha256"),
                "source_plan_sha256": rule.get("source_plan_sha256"), "source_freeze_sha256": rule.get("source_freeze_sha256"),
                "imported_rounds": 8, "imported_stages": 67, "first_new_round": 9, "first_new_stage": "REVIEWER"}
    if (rule.get("enabled") is not True or manifest.get("record_hash") != sha(canonical(body).encode())
            or any(manifest.get(k) != v or plan.get("continuation", {}).get(k) != v or rule.get(k) != v for k, v in required.items())
            or manifest.get("kind") != "exact_v7_prefix_continuation_import" or manifest.get("revision") != "triad_repaired_v8"
            or manifest.get("evidence_origin") != frozen.get("evidence_origin")
            or manifest.get("target_out") != str(out) or manifest.get("new_allowance") is not False
            or manifest.get("prefix_chain_head") != plan.get("continuation", {}).get("original_prefix_chain_head")
            or manifest.get("target_config_sha256") != sha(config_raw) or manifest.get("target_plan_sha256") != sha(plan_raw)
            or frozen.get("config_sha256") != sha(canonical(backend.config).encode())
            or frozen.get("plan_sha256") != sha(canonical(plan).encode())):
        raise ContractError("Copied reference manifest differs from the frozen v8 continuation")
    gate_path = out / "live_round_gate.json"
    if gate_path.exists() or gate_path.is_symlink():
        gate, _ = checked_json(gate_path)
        if gate.get("status") != "passed" or gate.get("continuation_import_sha256") != sha(raw):
            raise ContractError("Copied reference manifest differs from the integrity gate")
    files = manifest.get("copied_files_sha256", {})
    direct = manifest.get("direct_reference_files_sha256", {})
    if not isinstance(files, dict) or not isinstance(direct, dict) or any(files.get(k) != v for k, v in direct.items()):
        raise ContractError("Direct copied reference inventory differs")
    source_name = rule.get("source_out")
    if not isinstance(source_name, str) or not Path(source_name).is_absolute():
        raise ContractError("Copied reference source must be absolute")
    aliases = {safe(source_name): direct}
    ancestor = manifest.get("ancestor_import", {})
    relative = ancestor.get("path")
    if not isinstance(relative, str):
        raise ContractError("Missing bound v6 ancestry")
    path = safe(out / relative)
    if not path.is_relative_to(out) or files.get(relative) != ancestor.get("sha256"):
        raise ContractError("Ancestor binding escaped the copied inventory")
    old, old_raw = checked_json(path)
    oldbody = {k: v for k, v in old.items() if k != "record_hash"}
    source_config, source_raw = checked_json(out / "predecessor_provenance/v7/config.json")
    if (sha(source_raw) != rule["source_config_sha256"] or sha(old_raw) != ancestor.get("sha256")
            or old.get("record_hash") != sha(canonical(oldbody).encode())
            or old.get("revision") != "triad_repaired_v7" or old.get("target_out") != str(safe(source_name))
            or old.get("source_out") != ancestor.get("source_out")
            or old.get("source_out") != source_config.get("continuation", {}).get("source_out")):
        raise ContractError("Ancestor alias differs from its exact v7 provenance")
    inherited = {}
    for name, expected in old.get("copied_files_sha256", {}).items():
        if name.endswith(".png"):
            if files.get(name) != expected:
                raise ContractError("Ancestor image was not copied exactly into v8")
            inherited[name] = expected
    aliases[safe(ancestor["source_out"])] = inherited
    return aliases


def references(backend, request):
    out = safe(backend.out)
    base = safe(request.get("memory_directory", out))
    if not base.is_relative_to(out):
        raise ContractError("Current memory directory must remain inside v8")
    result = {"none": None}
    aliases = None
    for name, descriptor in request.get("image_refs", {}).items():
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_:.-]{0,119}", name) is None or name in {"none", "latest_draft"}:
            raise ContractError("Invalid harness image-reference name")
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get("path"), str):
            raise ContractError("Expected an exact role-permitted image descriptor")
        original = Path(descriptor["path"])
        original = safe(original if original.is_absolute() else base / original)
        selected = original
        if not original.is_relative_to(out):
            if aliases is None:
                aliases = imported_binding(backend)
            selected = None
            for source, files in aliases.items():
                if original.is_relative_to(source):
                    relative = str(original.relative_to(source))
                    if files.get(relative) == descriptor.get("sha256"):
                        selected = safe(out / relative)
                        break
            if selected is None:
                raise ContractError("Image descriptor is absent from the exact v7/v6 copied inventory")
        if not selected.is_relative_to(out) or not selected.is_file():
            raise ContractError("Copied image must exist inside v8")
        raw = selected.read_bytes()
        if sha(raw) != descriptor.get("sha256"):
            raise ContractError("Copied image hash changed")
        result[name] = raw
    return result
