"""Explicit PNG-XMP transport amendment for the existing pressure study.

The frozen experiment files remain unchanged. This sidecar installs the declared
codec and permits one exact, locally failed outer stage to reconstruct its two
completed nested calls from cache. No lost request or observed answer is retried.
All later dispatches retain the original engine's exclusivity rule.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sqlite3
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.pressure_interview_v1 import live_analysis, live_cli, live_provider
from experiments.pressure_interview_v1.contracts import canonical, ContractError
from experiments.pressure_interview_v1.engine import LivePairEngine, UnresolvedRequest


AMENDMENT_NAME = "execution_notes/png_xmp_v2_amendment.json"
ANALYSIS_FUNCTION_SHA256 = "73a55d48a35841464784ddfdf9ab2a1d3b982a8480508537294b315939eb264f"
SIDECARS = {"scripts/run_pressure_xmp_v2.py", "scripts/pressure_png_xmp_v2.py"}
_ORIGINAL_WIRE_VERIFIER = live_analysis._verify_wire_records
_ORIGINAL_CLAIM = LivePairEngine._claim_dispatch
_ORIGINAL_VERIFY = live_analysis.verify_study
_ORIGINAL_REPORT = live_analysis.build_report
_ORIGINAL_EXPORT = live_analysis.export_study
_INSTALLED = None
_CODEC_LOADER = '''    spec = importlib.util.spec_from_file_location("_pressure_analysis_wire_codec", codec_file)
    codec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(codec)
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _file(base, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ContractError("Amendment evidence path must be relative")
    candidate = Path(base) / path
    if any(part.is_symlink() for part in (candidate, *candidate.parents)):
        raise ContractError("Amendment evidence cannot follow symlinks")
    if not candidate.is_file():
        raise ContractError("Required amendment evidence is missing")
    return candidate


def _read(path):
    if Path(path).is_symlink():
        raise ContractError("Amendment record cannot be symlinked")
    return json.loads(Path(path).read_text())


def build_analysis_verifier(codec_module):
    """Return the original read-only verifier with only its codec loader changed.

    This pure constructor is also used by offline integration tests. It neither
    installs a runtime patch nor authorizes recovery or a paid request.
    """
    source = inspect.getsource(_ORIGINAL_WIRE_VERIFIER)
    if sha(source.encode()) != ANALYSIS_FUNCTION_SHA256 or source.count(_CODEC_LOADER) != 1:
        raise ContractError("Frozen analysis function differs from the reviewed adapter")
    adapted = source.replace(_CODEC_LOADER, "    codec = _declared_xmp_codec\n", 1)
    # Every statement except this explicit dependency substitution is identical.
    if adapted.replace("    codec = _declared_xmp_codec\n", _CODEC_LOADER, 1) != source:
        raise ContractError("Analysis adapter changed more than its codec dependency")
    scope = dict(_ORIGINAL_WIRE_VERIFIER.__globals__)
    scope["_declared_xmp_codec"] = codec_module
    exec(compile(adapted, str(Path(__file__).resolve()) + ":analysis_codec_adapter", "exec"), scope)
    return scope["_verify_wire_records"]


def _namespace_rows(aggregate, namespace):
    with sqlite3.connect(Path(aggregate).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return {r["request_id"]: dict(r) for r in db.execute("SELECT * FROM requests")
                if r["request_id"].startswith(namespace + ":")}


def verify_recovery_proof(out, amendment, aggregate, *, require_pristine=False):
    """Check bound evidence; pristine checks run only at the one recovery claim.

    Later report/restart commands can check the unchanged original proof after
    new scientific requests exist. Only the actual one-shot claim requires the
    original two-request namespace and absence of an outer observed answer.
    """
    out = Path(out).resolve()
    proof = amendment["recovery"]
    request_id = proof["request_id"]
    namespace = _read(out / "config.json")["budget"]["namespace"]
    unit_relative = Path(proof["unit_directory"])
    if unit_relative.is_absolute() or ".." in unit_relative.parts:
        raise ContractError("Recovery unit escaped the study")
    unit = out / unit_relative
    key = sha(request_id.encode()) + ".json"
    request_path = _file(unit, "requests/" + key)
    dispatch_path = _file(unit, "dispatches/" + key)
    request, dispatch = _read(request_path), _read(dispatch_path)
    if (sha(request_path.read_bytes()) != proof["request_file_sha256"]
            or sha(dispatch_path.read_bytes()) != proof["dispatch_file_sha256"]
            or request.get("request_id") != request_id or request.get("request_hash") != proof["request_hash"]
            or request.get("request_hash") != sha(canonical({k: v for k, v in request.items() if k != "request_hash"}).encode())
            or request.get("stage") != "A_OUTPUT" or request.get("role") != "A" or request.get("round") != 1
            or dispatch.get("request_id") != request_id or dispatch.get("request_hash") != proof["request_hash"]
            or dispatch.get("evidence_origin") != "live"):
        raise ContractError("Recovery request or original dispatch differs from its exact proof")
    expected_ids = {namespace + ":" + request_id + suffix for suffix in (".step1", ".image1")}
    if set(proof["completed_requests"]) != expected_ids:
        raise ContractError("Only the exact first chat and image may be reconstructed")
    rows = _namespace_rows(aggregate, namespace)
    for identifier in expected_ids:
        row = rows.get(identifier)
        if (row is None or row.get("status") != "completed" or row.get("actual_units") is None
                or row.get("response_json") is None or row.get("receipt_json") is None
                or sha(canonical(row).encode()) != proof["completed_requests"][identifier]
                or sha((row["response_json"] + "\n" + row["receipt_json"]).encode()) != row.get("result_hash")):
            raise ContractError("Recovery requires the unchanged exact completed receipt")
        receipt = json.loads(row["receipt_json"])
        kind, model = (("chat", "z-ai/glm-5.3-flash") if identifier.endswith(".step1") else
                       ("image", "qwen/qwen-image-2.1-turbo"))
        if (row["kind"] != kind or row["model"] != model or receipt.get("request_id") != identifier
                or receipt.get("request_hash") != row["request_hash"] or receipt.get("requested_model") != model):
            raise ContractError("Recovery receipt is not the declared original model request")
    draft = _file(out, proof["original_draft"]["path"])
    if sha(draft.read_bytes()) != proof["original_draft"]["sha256"]:
        raise ContractError("The original generated draft changed")
    if require_pristine:
        if set(rows) != expected_ids:
            raise UnresolvedRequest("Recovery cannot cover an additional or lost paid intent")
        if any((unit / directory / key).exists() for directory in
               ("responses", "observations", "invalid_responses")):
            raise UnresolvedRequest("An observed outer answer cannot receive recovery")
    return {"unit": unit, "request": request, "dispatch": dispatch, "namespace": namespace}


def make_recovery_claim(out, amendment, aggregate, original_claim=_ORIGINAL_CLAIM):
    """Create an allowlisted one-shot dispatch wrapper; tests use temp evidence."""
    out = Path(out).resolve()
    proof = amendment["recovery"]
    marker = out / "execution_notes/png_xmp_v2_recovery_claim.json"

    def claim(engine, request, directory="dispatches", extra=None):
        exact = (request.get("request_id") == proof["request_id"]
                 and engine.directory.resolve() == (out / proof["unit_directory"]).resolve()
                 and directory == "dispatches")
        if not exact:
            return original_claim(engine, request, directory, extra)
        if request.get("request_hash") != proof["request_hash"] or extra is not None:
            raise UnresolvedRequest("Recovery input differs from the exact authorized stage")
        checked = verify_recovery_proof(out, amendment, aggregate, require_pristine=True)
        record = {"version": "png_xmp_v2", "request_id": proof["request_id"],
                  "request_hash": proof["request_hash"],
                  "original_dispatch_sha256": proof["dispatch_file_sha256"],
                  "completed_requests": proof["completed_requests"],
                  "no_paid_request_retried": True,
                  "scope": "Reconstruct two completed nested calls from exact cache; step2 had no intent or POST."}
        marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            with marker.open("x") as stream:
                stream.write(canonical(record) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise UnresolvedRequest("The one authorized local recovery was already claimed") from None
        return checked["dispatch"]
    return claim


def load_amendment(out=live_cli.OUT):
    """Verify exact frozen baseline and approved sidecar hashes without mutation."""
    out = Path(out).resolve()
    if out != live_cli.OUT.resolve():
        raise ContractError("This controller belongs to the existing declared live study")
    live_cli.require_workflow_gate()
    path = _file(out, AMENDMENT_NAME)
    amendment = _read(path)
    freeze_path = _file(out, "freeze.json")
    freeze = _read(freeze_path)
    if (amendment.get("version") != "png_xmp_v2" or amendment.get("status") != "offline_verified"
            or amendment.get("original_freeze_sha256") != sha(freeze_path.read_bytes())
            or amendment.get("original_source_files") != freeze.get("source_files")
            or freeze.get("source_files") != live_cli.source_files()
            or amendment.get("analysis_function_sha256") != ANALYSIS_FUNCTION_SHA256
            or set(amendment.get("source_files", {})) != SIDECARS):
        raise ContractError("Amendment does not bind the unchanged baseline and reviewed sidecars")
    for relative, expected in amendment["source_files"].items():
        if sha(_file(ROOT, relative).read_bytes()) != expected:
            raise ContractError("Declared recovery sidecar changed")
    validation = amendment["validation"]
    validation_path = _file(ROOT, validation["path"])
    checked = _read(validation_path)
    if (validation.get("status") != "passed" or validation.get("sha256") != sha(validation_path.read_bytes())
            or checked.get("status") != "passed" or checked.get("paid_calls") != 0
            or checked.get("source_files") != amendment["source_files"]):
        raise ContractError("The amendment requires offline validation of these exact sidecars")
    aggregate = ROOT / _read(out / "config.json")["budget"]["aggregate_ledger"]
    verify_recovery_proof(out, amendment, aggregate)
    return amendment, aggregate, sha(path.read_bytes())


def install(out=live_cli.OUT):
    """Install only the declared transport, recovery and verification extension."""
    global _INSTALLED
    amendment, aggregate, amendment_sha = load_amendment(out)
    if _INSTALLED is not None:
        if _INSTALLED != amendment_sha:
            raise ContractError("A different runtime amendment is already installed")
        return amendment
    spec = importlib.util.spec_from_file_location("_pressure_png_xmp_v2", ROOT / "scripts/pressure_png_xmp_v2.py")
    codec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(codec)
    live_provider.codec = codec
    LivePairEngine._claim_dispatch = make_recovery_claim(out, amendment, aggregate)
    live_analysis._verify_wire_records = build_analysis_verifier(codec)
    summary = {"version": "png_xmp_v2", "amendment_sha256": amendment_sha,
               "baseline_frozen_core_unchanged": True, "runtime_sidecars": amendment["source_files"],
               "recovery_request": amendment["recovery"]["request_id"],
               "scope": "Strict XMP-preserving lossless transport and one cached local-stage recovery; no model, route, prompt, case, allowance or pixel changes.",
               "proof_is_not_provider_preprocessing_equivalence": True}

    def verify(study_out):
        load_amendment(study_out)
        result = _ORIGINAL_VERIFY(study_out)
        result["active_technical_amendment"] = summary
        return result

    def report(study_out):
        result = _ORIGINAL_REPORT(study_out)
        result["active_technical_amendment"] = summary
        destination = Path(study_out) / "analysis"
        (destination / "report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        with (destination / "REPORT.md").open("a") as stream:
            stream.write("\n## Declared transport amendment\n\n"
                         "The original nineteen frozen core files are unchanged. The active `png_xmp_v2` runtime sidecars preserve source PNGs, decoded pixels and XMP metadata, and permit one exact outer-stage recovery using two completed cached calls. No lost paid request or observed model answer was retried. These sidecars have separate offline validation; the baseline 105 checks alone do not validate the amendment.\n")
        return result

    def export(study_out, destination):
        result = _ORIGINAL_EXPORT(study_out, destination)
        with zipfile.ZipFile(destination, "a", zipfile.ZIP_DEFLATED) as archive:
            for relative in sorted(amendment["source_files"]):
                archive.write(_file(ROOT, relative), "runtime_extensions/" + relative)
        result["active_technical_amendment"] = summary
        return result

    live_analysis.verify_study = verify
    live_analysis.build_report = report
    live_analysis.export_study = export
    _INSTALLED = amendment_sha
    return amendment


def main():
    # Even read/report commands verify the explicit extension, so they cannot
    # accidentally validate XMP records using only the legacy codec.
    install()
    live_cli.main()


if __name__ == "__main__":
    main()
