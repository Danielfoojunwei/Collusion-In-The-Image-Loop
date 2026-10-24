"""Analysis-only support for preserved, billed backend-invalid observations.

No execution command is exposed. The frozen core and v2 transport/recovery
sidecars remain unchanged. This adapter adds the missing proof path for an
inner model action that failed before an outer stage answer could exist.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import inspect
import json
from pathlib import Path
import re
import sqlite3
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import run_pressure_xmp_v2 as v2
from experiments.pressure_interview_v1 import live_analysis, live_cli, live_provider
from experiments.pressure_interview_v1.contracts import ContractError, canonical
from experiments.pressure_interview_v1.memory import load_image
from visual_collusion.budget import _units


MANIFEST_NAME = "execution_notes/invalid_backend_analysis_v3.json"
STAGE_FUNCTION_SHA256 = "7fa154b583ceffb9763467ef8f327f62464df446fe72c1cebcfd2ca9e5b85d02"
SOURCE_NAME = "scripts/pressure_invalid_analysis_v3.py"
_ORIGINAL_STAGE = live_analysis._verify_stage_files
_INSTALLED = None
_INVALID_LOOP = '''    for invalid in _records(unit / "invalid_responses"):
        identifier = invalid.get("request_id")
        observations = [o for o in _records(unit / "observations") if o.get("request_id") == identifier]
        if len(observations) != 1 or invalid.get("observation_hash") != observations[0]["observation_hash"]:
            raise ContractError("Invalid first-answer seal lacks its observation")
'''
_AMENDED_LOOP = '''    for invalid in _records(unit / "invalid_responses"):
        if invalid.get("status") == "terminal_invalid_backend_observation":
            _strict_backend_invalid(unit, origin, requests, invalid)
        else:
            identifier = invalid.get("request_id")
            observations = [o for o in _records(unit / "observations") if o.get("request_id") == identifier]
            if len(observations) != 1 or invalid.get("observation_hash") != observations[0]["observation_hash"]:
                raise ContractError("Invalid first-answer seal lacks its observation")
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or any(p.is_symlink() for p in path.parents):
        raise ContractError("Required invalid-observation evidence is missing or symlinked")
    return json.loads(path.read_text())


def _bound_file(out, supplied, relative):
    """Read the canonical local copy; old absolute archive paths are labels only."""
    relative, supplied = Path(relative), Path(supplied)
    if relative.is_absolute() or ".." in relative.parts or ".." in supplied.parts:
        raise ContractError("Invalid-observation path escaped its evidence scope")
    if supplied.is_absolute():
        if tuple(supplied.parts[-len(relative.parts):]) != relative.parts:
            raise ContractError("Invalid-observation path names different evidence")
    elif supplied != relative:
        raise ContractError("Invalid-observation relative evidence differs")
    path = Path(out) / relative
    if path.is_symlink() or not path.is_file() or any(p.is_symlink() for p in path.parents):
        raise ContractError("Invalid-observation bound evidence is missing or symlinked")
    return path


def _checked_row(row, identifier):
    if (row is None or row.get("request_id") != identifier or row.get("status") != "completed"
            or row.get("actual_units") is None or row.get("response_json") is None
            or row.get("receipt_json") is None
            or sha((row["response_json"] + "\n" + row["receipt_json"]).encode()) != row.get("result_hash")):
        raise ContractError("Backend-invalid receipt is absent, unresolved or changed")
    response, receipt = json.loads(row["response_json"]), json.loads(row["receipt_json"])
    if not isinstance(response, dict) or not isinstance(receipt, dict):
        raise ContractError("Backend-invalid raw response and receipt must be complete objects")
    if (not isinstance(response.get("usage"), dict) or receipt.get("usage") != response["usage"]
            or receipt.get("provider") != response.get("provider")
            or _units(response["usage"].get("cost")) != row["actual_units"]):
        raise ContractError("Backend-invalid raw usage, provider or cost differs from its receipt")
    expected = {"chat": "z-ai/glm-5.3-flash", "image": "qwen/qwen-image-2.1-turbo"}
    if (row.get("kind") not in expected or row.get("model") != expected[row["kind"]]
            or any(receipt.get(k) != row[v] for k, v in (("request_id", "request_id"),
                    ("request_hash", "request_hash"), ("kind", "kind"), ("requested_model", "model")))
            or _units(receipt.get("cost_usd")) != row["actual_units"]):
        raise ContractError("Backend-invalid receipt differs from its pinned paid intent")
    allowed_models = {row["model"], row["model"].split("/", 1)[-1]}
    if row["kind"] == "chat" and (response.get("model") not in allowed_models
            or receipt.get("returned_model") != response.get("model")):
        raise ContractError("Backend-invalid raw chat response is not the pinned returned model")
    if row["kind"] == "image" and (response.get("model") not in allowed_models | {None}
            or receipt.get("returned_model") != response.get("model")):
        raise ContractError("Backend-invalid raw image response differs from its returned model receipt")
    return response, receipt


def _chat_object(response):
    try:
        choices = response["choices"]
        if not isinstance(choices, list) or len(choices) != 1 or choices[0].get("finish_reason") != "stop":
            raise ValueError()
        message = choices[0]["message"]
        if message.get("refusal") or not isinstance(message.get("content"), str) or not message["content"].strip():
            raise ValueError()
        text = message["content"]
        try:
            value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except ValueError:
            _, value = live_provider.repair.unwrap_json_fence(text)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise ContractError("Preserved raw chat response has no complete declared object") from None


def _initial_refs(unit, request):
    refs = {"none": None}
    role, number = request["role"], request["round"]
    own = "a_public" if role == "A" else "b_public"
    peer = "b_public" if role == "A" else "a_public"
    log = unit / "memory/events.jsonl"
    if not log.is_file() or log.is_symlink():
        raise ContractError("Backend-invalid replay needs its retained role memory")
    events = [json.loads(line) for line in log.read_text().splitlines()]
    for name, kind in (("last_self", own), ("last_peer", peer)):
        candidates = [event for event in events if event.get("kind") == kind
            and isinstance(event.get("round_number"), int)
            and (event["round_number"] < number or
                 (role == "B" and kind == "a_public" and event["round_number"] == number))]
        if candidates:
            refs[name] = load_image(unit / "memory", candidates[-1]["payload"]["image"])
    for name, descriptor in request.get("reference_overrides", {}).items():
        if name not in {"last_self", "last_peer"}:
            raise ContractError("Backend-invalid replay has an unknown reference override")
        refs[name] = load_image(unit / "memory", descriptor)
    return refs


def _action_failure(unit, out, request, identifiers, rows, known):
    """Reconstruct the saved tool contract from raw replies; no inference occurs."""
    refs, images, step, index = _initial_refs(unit, request), 0, 1, 0
    namespace = identifiers[0].split(":", 1)[0]
    expected_last = known["request_id"]
    while index < len(identifiers):
        identifier = identifiers[index]
        local = request["request_id"] + f".step{step}"
        if identifier != namespace + ":" + local or rows[identifier]["kind"] != "chat":
            raise ContractError("Backend-invalid action order differs from the actual tool loop")
        value = _chat_object(json.loads(rows[identifier]["response_json"]))
        relative = "provider_evidence/actor_actions/" + sha(local.encode()) + ".json"
        artifact = _read(_bound_file(out, relative, relative))
        if artifact != {"step": step, "request_id": local, "output": value, "receipt_id": identifier}:
            raise ContractError("Saved action differs from the first raw provider answer")
        failure = None
        try:
            live_provider.validate_action(value, request["role"], step, images, refs)
        except ContractError as error:
            failure = str(error)
        if failure is None and value["action"] == "decline":
            failure = "Actor explicitly declined"
        if (failure is None and value["action"] == "retrieve_memory"
                and value["memory_handle"] not in request.get("context_manifest", {}).get("handle_bindings", {})):
            failure = "Actor requested an unavailable archive handle"
        if failure is not None:
            if (index != len(identifiers) - 1 or local != expected_last or failure != known["reason"]
                    or len(known["evidence_paths"]) != 1):
                raise ContractError("Recorded backend failure differs from its actual raw action")
            _bound_file(out, known["evidence_paths"][0], relative)
            return
        if value["action"] == "deliver":
            raise ContractError("A valid delivered action cannot be relabeled backend-invalid")
        if value["action"] == "generate_image":
            images += 1
            index += 1
            image_local = request["request_id"] + f".image{images}"
            image_id = namespace + ":" + image_local
            if index >= len(identifiers) or identifiers[index] != image_id or rows[image_id]["kind"] != "image":
                raise ContractError("Generated-image receipt is missing from the invalid tool prefix")
            response = json.loads(rows[image_id]["response_json"])
            try:
                data = response["data"]
                if len(data) != 1 or data[0].get("media_type") != "image/png":
                    raise ValueError()
                png = base64.b64decode(data[0]["b64_json"], validate=True)
            except (KeyError, IndexError, TypeError, ValueError):
                raise ContractError("Generated-image raw response is not the preserved PNG") from None
            image_path = "provider_evidence/image_calls/" + sha(image_local.encode()) + ".json"
            record = _read(_bound_file(out, image_path, image_path))
            reference = refs[value["reference"]]
            expected = {"request_id": image_local, "prompt": value["image_prompt"],
                        "reference": value["reference"], "reference_sha256": sha(reference) if reference is not None else None,
                        "result_sha256": sha(png), "receipt": json.loads(rows[image_id]["receipt_json"])}
            if record != expected:
                raise ContractError("Image generation artifact differs from the bound raw image call")
            blob = "provider_evidence/blobs/" + sha(png) + ".png"
            if _bound_file(out, blob, blob).read_bytes() != png:
                raise ContractError("Original generated PNG differs from its paid raw response")
            refs["latest_draft"] = png
        index += 1
        step += 1
    raise ContractError("No actual terminal model-contract failure was demonstrated")


def strict_backend_invalid(unit, origin, requests, invalid):
    unit = Path(unit)
    if unit.is_symlink() or any(parent.is_symlink() for parent in unit.parents):
        raise ContractError("Backend-invalid unit cannot follow an evidence symlink")
    unit = unit.resolve()
    if unit.parent.parent.name != "episodes":
        raise ContractError("Backend-invalid evidence has an unknown study layout")
    out = unit.parents[2]
    fields = {"request_id", "request_hash", "status", "error_class", "receipt_ids", "evidence_paths", "reason", "no_retry"}
    request = requests.get(invalid.get("request_id"))
    if (origin != "live" or set(invalid) != fields or invalid.get("status") != "terminal_invalid_backend_observation"
            or invalid.get("error_class") != "KnownInvalidBackendOutput" or invalid.get("no_retry") is not True
            or request is None or request.get("stage") not in {"A_OUTPUT", "B_OUTPUT", "A_INTERVIEW"}
            or request.get("role") != ("B" if request.get("stage") == "B_OUTPUT" else "A")
            or invalid.get("request_hash") != request.get("request_hash")):
        raise ContractError("Invalid backend seal is not an exact live actor-stage failure")
    key = sha(request["request_id"].encode()) + ".json"
    if any((unit / name / key).exists() for name in ("responses", "observations")):
        raise ContractError("Backend-invalid seal conflicts with an observed outer answer")
    config = _read(out / "config.json")
    namespace = config["budget"]["namespace"]
    identifiers = invalid["receipt_ids"]
    prefix = namespace + ":" + request["request_id"] + "."
    if (not isinstance(identifiers, list) or not identifiers
            or any(not isinstance(value, str) or not value.startswith(prefix) for value in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ContractError("Backend-invalid receipts are absent, duplicated or belong to another stage")
    aggregate = ROOT / config["budget"]["aggregate_ledger"]
    if not aggregate.exists():
        aggregate = out / "analysis/aggregate_budget_snapshot.sqlite3"
    if not aggregate.is_file() or aggregate.is_symlink():
        raise ContractError("Backend-invalid evidence requires its original aggregate receipts")
    with sqlite3.connect(aggregate.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = {row["request_id"]: dict(row) for row in db.execute("SELECT * FROM requests")
                if row["request_id"].startswith(prefix)}
    if set(rows) != set(identifiers):
        raise ContractError("Backend-invalid receipt list omits or invents a stage intent")
    receipts = [_checked_row(rows[identifier], identifier)[1] for identifier in identifiers]
    paths = invalid["evidence_paths"]
    if not isinstance(paths, list) or not paths or any(not isinstance(value, str) for value in paths):
        raise ContractError("Backend-invalid seal lacks its preserved known-invalid record")
    supplied = Path(paths[0])
    relative = "provider_evidence/known_invalid/" + supplied.name
    known_path = _bound_file(out, supplied, relative)
    known = _read(known_path)
    if (set(known) != {"request_id", "reason", "receipt_ids", "receipts", "evidence_paths", "known_invalid_observation", "evidence_origin"}
            or known.get("known_invalid_observation") is not True or known.get("evidence_origin") != "live"
            or not isinstance(known.get("request_id"), str)
            or known_path.name != sha(known["request_id"].encode()) + ".json"
            or known.get("receipt_ids") != identifiers or known.get("receipts") != receipts
            or not isinstance(known.get("reason"), str) or invalid["reason"] != known["reason"][:200]
            or not isinstance(known.get("evidence_paths"), list)
            or any(not isinstance(value, str) for value in known["evidence_paths"])
            or paths[1:] != known.get("evidence_paths")):
        raise ContractError("Known-invalid artifact differs from its sealed raw receipt proof")
    if known["reason"] == "Paid provider output was incomplete, refused or malformed":
        # None of this study's ten seals uses the provider-parser branch. A
        # missing chat object would not prove an image response malformed.
        # This narrow amendment therefore admits no such unobserved category.
        raise ContractError("Provider-parser invalidity is outside this amendment's proven action/interview scope")
    if request["stage"] == "A_INTERVIEW":
        if (identifiers != [namespace + ":" + request["request_id"] + ".answer"]
                or known["request_id"] != request["request_id"] or known["evidence_paths"]):
            raise ContractError("Interview invalidity lacks its exact answer receipt")
        value = _chat_object(json.loads(rows[identifiers[0]]["response_json"]))
        try:
            if set(value) != {"answer"}:
                raise ContractError("Interview answer fields differ from schema")
            live_provider._text(value["answer"], 800, empty=False)
        except ContractError as error:
            if str(error) == known["reason"]:
                return
        raise ContractError("The raw interview answer does not demonstrate the recorded failure")
    if not re.fullmatch(re.escape(request["request_id"]) + r"\.step[1-4]", known["request_id"]):
        raise ContractError("Actor contract invalidity is not bound to an actual bounded step")
    _action_failure(unit, out, request, identifiers, rows, known)


def build_stage_verifier():
    """Pure offline adapter; all original stage/outer/C checks stay intact."""
    source = inspect.getsource(_ORIGINAL_STAGE)
    if sha(source.encode()) != STAGE_FUNCTION_SHA256 or source.count(_INVALID_LOOP) != 1:
        raise ContractError("Frozen stage verifier differs from the reviewed analysis-only adapter")
    adapted = source.replace(_INVALID_LOOP, _AMENDED_LOOP, 1)
    if adapted.replace(_AMENDED_LOOP, _INVALID_LOOP, 1) != source:
        raise ContractError("Analysis-only adapter changed more than the invalid-proof branch")
    scope = dict(_ORIGINAL_STAGE.__globals__)
    scope["_strict_backend_invalid"] = strict_backend_invalid
    exec(compile(adapted, str(Path(__file__).resolve()) + ":stage_invalid_adapter", "exec"), scope)
    return scope["_verify_stage_files"]


def load_manifest(out=live_cli.OUT):
    v2.load_amendment(out)
    out = Path(out).resolve()
    path = v2._file(out, MANIFEST_NAME)
    manifest = _read(path)
    source_files = {SOURCE_NAME: sha(Path(__file__).read_bytes())}
    status_path = v2._file(out, "run_status.json")
    status = _read(status_path)
    if (manifest.get("version") != "invalid_backend_analysis_v3" or manifest.get("status") != "offline_verified"
            or manifest.get("analysis_only") is not True or manifest.get("source_files") != source_files
            or manifest.get("original_stage_function_sha256") != STAGE_FUNCTION_SHA256
            or manifest.get("original_freeze_sha256") != sha(v2._file(out, "freeze.json").read_bytes())
            or manifest.get("v2_amendment_sha256") != sha(v2._file(out, v2.AMENDMENT_NAME).read_bytes())
            or manifest.get("run_status_sha256") != sha(status_path.read_bytes())
            or status.get("status") not in {"complete", "complete_with_invalids"}):
        raise ContractError("Analysis-only amendment does not bind the completed frozen study")
    validation = manifest["validation"]
    validation_path = v2._file(ROOT, validation["path"])
    validated = _read(validation_path)
    if (validation.get("status") != "passed" or validation.get("sha256") != sha(validation_path.read_bytes())
            or validated.get("status") != "passed" or validated.get("paid_calls") != 0
            or validated.get("source_files") != source_files):
        raise ContractError("Analysis-only amendment requires offline validation of this exact source")
    return manifest, sha(path.read_bytes())


def install(out=live_cli.OUT):
    global _INSTALLED
    manifest, manifest_sha = load_manifest(out)
    if _INSTALLED is not None:
        if _INSTALLED != manifest_sha:
            raise ContractError("A different analysis-only amendment is already installed")
        return manifest
    v2.install(out)
    before_verify, before_report, before_export = live_analysis.verify_study, live_analysis.build_report, live_analysis.export_study
    live_analysis._verify_stage_files = build_stage_verifier()
    summary = {"version": "invalid_backend_analysis_v3", "analysis_only": True,
               "manifest_sha256": manifest_sha, "runtime_or_scientific_reexecution": False,
               "source_files": manifest["source_files"], "original_core_and_v2_sidecars_unchanged": True,
               "scope": "Require preserved raw provider actions and exact completed receipts for backend-invalid stages; retain every ordinary outer/C check and every failed outcome."}

    def verify(study_out):
        load_manifest(study_out)
        result = before_verify(study_out)
        result["active_analysis_only_amendment"] = summary
        result["verified_backend_invalid_stages"] = sum(
            _read(path).get("status") == "terminal_invalid_backend_observation"
            for path in (Path(study_out) / "episodes").glob("*/*/invalid_responses/*.json"))
        result["backend_invalids_remain_failures"] = True
        return result

    def report(study_out):
        result = before_report(study_out)
        result["active_analysis_only_amendment"] = summary
        destination = Path(study_out) / "analysis"
        (destination / "report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        with (destination / "REPORT.md").open("a") as stream:
            stream.write("\n## Analysis-only invalid-observation verification\n\n"
                "The `invalid_backend_analysis_v3` sidecar verifies saved inner-action failures using the original raw model responses, action artifacts and exact completed aggregate receipts. It creates no outer observations, changes no outcomes and re-executes no scientific work. Ordinary outer-invalid and Codex checks remain unchanged.\n")
        return result

    def export(study_out, destination):
        result = before_export(study_out, destination)
        with zipfile.ZipFile(destination, "a", zipfile.ZIP_DEFLATED) as archive:
            archive.write(Path(__file__), "analysis_extensions/" + SOURCE_NAME)
        result["active_analysis_only_amendment"] = summary
        return result

    live_analysis.verify_study, live_analysis.build_report, live_analysis.export_study = verify, report, export
    _INSTALLED = manifest_sha
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "report", "export"))
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    install()
    if args.command == "verify":
        live_cli.emit(live_analysis.verify_study(live_cli.OUT))
    elif args.command == "report":
        result = live_analysis.build_report(live_cli.OUT)
        live_cli.emit({"status": "report_written", "counts": result["counts"], "analysis_only": True})
    else:
        destination = args.destination or ROOT.parent / "image_collusion_pressure_interview_results.zip"
        live_cli.emit(live_analysis.export_study(live_cli.OUT, destination))


if __name__ == "__main__":
    main()
