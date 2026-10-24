"""Local-only researcher evidence reads from selected whole-round commits.

No provider, Backend or funding-ledger constructor is used.  ``inspect`` writes
only stdout. ``write`` exclusively creates the requested derived researcher
note after independently checking the eight-stage selected checkpoint.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import base64
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.triad_emergent_v9.engine import STAGES
from experiments.triad_v9_checkpoint_restart04.attempts import AttemptStore
from experiments.triad_v9_checkpoint_restart04.engine import Engine
from experiments.triad_v9_checkpoint_restart04.reviewer_host import validate_archived_envelope

DEFAULT_OUT = ROOT / "runs/main/triad_emergent_v9_live"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    path = Path(path)
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Symlink evidence is not read")
    return json.loads(path.read_text())


def stage_path(store, number, stage, suffix="outcome"):
    # Invoke the exact runtime path resolver without constructing an Engine,
    # whose initializer may initialize evidence. This adapter has no backend.
    return Engine._path(SimpleNamespace(store=store), number, stage, suffix)


def inline_hashes(request):
    result = []
    for message in request["messages"]:
        if not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if block.get("type") != "image_url":
                continue
            url = block["image_url"]["url"]
            if not url.startswith("data:image/png;base64,"):
                raise ValueError("Unexpected inline image format")
            result.append(sha(base64.b64decode(url.split(",", 1)[1], validate=True)))
    expected = sorted(item["sha256"] for item in request["image_refs"].values())
    if sorted(result) != expected:
        raise ValueError("Inline pixels differ from declared input descriptors")
    return result


def accounting(out, context):
    attempt = context["attempt_id"]
    prefix = (f"triad_emergent_v9:episode.r{context['round']:02d}."
              if context["legacy"] else f"triad_emergent_v9:{attempt}:")
    path = out.parent / "budget.sqlite3"
    with sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM requests WHERE substr(request_id,1,?)=? ORDER BY request_id",
            (len(prefix), prefix))]
    return {"attempt_id": attempt, "row_count": len(rows),
            "kind_status_counts": dict(Counter(row["kind"] + ":" + row["status"] for row in rows)),
            "actual_usd": sum(row["actual_units"] or 0 for row in rows) / 1e9,
            "reserved_unresolved_usd": sum(row["reserved_units"] for row in rows if row["actual_units"] is None) / 1e9,
            "all_unknown_reserves_retained": True,
            "rows": [{"request_id": row["request_id"], "kind": row["kind"], "status": row["status"],
                      "actual_usd": row["actual_units"] / 1e9 if row["actual_units"] is not None else None,
                      "reserved_usd": row["reserved_units"] / 1e9,
                      "original_ledger_row_sha256": sha(canonical(row).encode())} for row in rows]}


def excluded_artifact_counts(context):
    """Count archived availability without coding failed-attempt semantics."""
    work, number = Path(context["working_root"]), context["round"]
    terminal = []
    for stage in STAGES:
        path = work / f"episode/rounds/r{number:02d}/{stage}.outcome.json"
        if path.is_file():
            terminal.append(read(path))
    rasters = sum(1 for path in (work / "provider_evidence/image_calls").glob("*.json")
                  if f"episode.r{number:02d}." in read(path).get("request_id", ""))
    return {"terminal_stages": len(terminal),
            "terminal_status_counts": dict(Counter(row["status"] for row in terminal)),
            "successful_generated_raster_records": rasters,
            "accepted_image_records": sum(bool(row.get("output", {}).get("image")) for row in terminal),
            "valid_own_log_records": sum(row.get("component_status", {}).get("log") == "valid" for row in terminal),
            "semantic_image_or_intent_coding": "excluded_from_main_round_audit"}


def inspect(out, number):
    out = Path(out).absolute()
    if type(number) is not int or not 7 <= number <= 12:
        raise ValueError("This continuation reader covers committed rounds7–12 only")
    store = AttemptStore(out, legacy_rounds=6, max_attempts=3)
    checkpoint_proof = store.verify()
    selector = store.committed_selector(number)
    if selector is None or selector.get("status") != "committed_complete_round":
        raise ValueError("No selected whole-round commit; do not code a partial attempt")
    work = Path(selector["working_root"])
    if work != store.path_root(number) or selector.get("all_eight_stages") is not True:
        raise ValueError("Selected attempt location or completion declaration differs")
    paths = {stage: {kind: stage_path(store, number, stage, kind)
                     for kind in ("request", "outcome")} for stage in STAGES}
    requests = {stage: read(paths[stage]["request"]) for stage in STAGES}
    records = {stage: read(paths[stage]["outcome"]) for stage in STAGES}
    first_review_proof = validate_archived_envelope(out, number)
    review = records["REVIEWER"]["output"]
    for stage in STAGES[4:]:
        view = json.loads(requests[stage]["messages"][1]["content"])
        if view["reviewer_report"]["report"] != review:
            raise ValueError("Post-review stage did not receive the unchanged first report")
    packet_path = work / f"reviewer_packets/r{number:02d}/packet.json"
    packet = read(packet_path)
    if f"r{number:02d}_C.png" in {item["file"] for item in packet["images"]}:
        raise ValueError("Current C return predates its permitted stage")
    source_paths = [Path(__file__), out / "plan.json", out / "config.json", packet_path,
                    store.manifest_path, store._commit_path(number),
                    work / "attempt.json", work / f"reviewer_host/r{number:02d}/envelope.json",
                    work / f"reviewer_host/r{number:02d}/first_final.txt"]
    source_paths += [path for row in paths.values() for path in row.values()]
    route_checks = {stage: {"declared": {key: value["sha256"] for key, value in requests[stage]["image_refs"].items()},
                            "exact_inline_pixels": inline_hashes(requests[stage])}
                    for stage in STAGES if stage != "REVIEWER"}
    images, drafts = {}, []
    for role, stage in (("A", "A_IMAGE_AND_VOTE"), ("B", "B_IMAGE_AND_VOTE"), ("C", "POSTVERDICT_C_IMAGE")):
        images[role] = records[stage]["output"]["image"]
        for action in records[stage].get("actions", []):
            image = action.get("generated_image")
            if image:
                source_paths.append(Path(image["path"]))
                drafts.append({"role": role, "operation": action.get("action"),
                               "actual_brief": action.get("arguments", {}).get("prompt"), "image": image,
                               "delivered": bool(images[role] and image["sha256"] == images[role]["sha256"])})
        if images[role]:
            source_paths.append(Path(images[role]["path"]))
    next_exposure = {"status": "archive_only_no_round13"} if number == 12 else {"status": "not_yet_available"}
    if number < 12:
        next_path = stage_path(store, number + 1, "A_IMAGE_AND_VOTE", "request")
        if next_path.is_file():
            request = read(next_path)
            actual = request["image_refs"].get("previous_C", {}).get("sha256")
            expected = images["C"]["sha256"] if images["C"] else None
            if actual != expected:
                raise ValueError("Next A request contains another C image")
            next_exposure = {"status": "sealed_next_A_request_contains_exact_C_image" if expected else "no_C_source_image",
                             "sha256": actual, "inline_hashes": inline_hashes(request),
                             "provider_reception": "not_inferred_from_a_request_snapshot_alone",
                             "effect": "not_assessed_in_closed_round_audit",
                             "next_round_committed": store.committed_selector(number + 1) is not None}
            source_paths.append(next_path)
    transport = []
    for path in sorted((work / "provider_evidence/transport_requests").glob("*.json")):
        row = read(path)
        if f"episode.r{number:02d}." not in row.get("request_id", ""):
            continue
        payload = row.get("logical_payload", {})
        transport.append({"request_id": row["request_id"], "endpoint": row.get("endpoint"),
                          "temperature": payload.get("temperature"), "reasoning": payload.get("reasoning"),
                          "max_tokens": payload.get("max_tokens"), "source_file": str(path.relative_to(ROOT))})
        source_paths.append(path)
    for path in sorted((work / "provider_evidence/image_calls").glob("*.json")):
        if f"episode.r{number:02d}." in read(path).get("request_id", ""):
            source_paths.append(path)
    excluded = []
    for context in store.inventory():
        if context["round"] != number or context["attempt_id"] == selector["attempt_id"]:
            continue
        if context["status"] != "abandoned_technical":
            raise ValueError("Unselected attempt has no technical-abandonment status")
        marker = Path(context["abandonment_path"])
        source_paths.append(marker)
        excluded.append({"attempt_id": context["attempt_id"], "status": context["status"],
                         "fault": context["fault"], "excluded_from_main_memory_and_semantic_codes": True,
                         "accounting": accounting(out, context), "artifact_availability_counts": excluded_artifact_counts(context),
                         "abandonment_path": str(marker)})
    bindings = {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in sorted(set(source_paths))}
    plan = read(out / "plan.json")["rounds"][number - 1]
    selected_cost = accounting(out, selector)
    if any(row["status"] != "completed" for row in selected_cost["rows"]):
        raise ValueError("A selected complete round retains unresolved paid receipts")
    result = {"record_kind": "unblinded_researcher_closed_round_audit",
              "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "round": number,
              "status": f"CLOSED_ROUND_{number}_SELECTED_COMPLETE_ATTEMPT",
              "scope": "One dependent explicitly induced trajectory. Technical attempts are not independent rounds.",
              "selected_attempt": {key: selector[key] for key in ("attempt_id", "working_root", "prior_checkpoint_sha256", "chain_head")},
              "checkpoint_proof": checkpoint_proof, "actual_first_review_proof": first_review_proof,
              "facts": {"oracle": plan["oracle"], "private_preference_researcher_only": plan["private_preference"],
                        "ballots": {role: records[stage]["output"]["decision"] for role, stage in (("A", "A_IMAGE_AND_VOTE"), ("B", "B_IMAGE_AND_VOTE"), ("C", "C_VOTE"))},
                        "stage_statuses": {stage: records[stage]["status"] for stage in STAGES},
                        "generated_rasters": len(drafts), "delivered_rasters": sum(bool(value) for value in images.values()),
                        "actual_round_cost_usd": selected_cost["actual_usd"]},
              "private_scores_as_actually_supplied": {role: json.loads(requests[stage]["messages"][1]["content"]).get("own_account_result") for role, stage in (("A", "POSTVERDICT_A_LOG"), ("B", "POSTVERDICT_B_LOG"), ("C", "POSTVERDICT_C_LOG"))},
              "actual_image_generation_actions": drafts, "exact_route_checks": route_checks,
              "full_actual_first_report_before_all4_post_stages_verified": True,
              "actual_blind_review": review, "review_image_manifest": [item["file"] for item in packet["images"]],
              "current_C_image_absent_at_actual_review_verified": True, "next_A_exposure": next_exposure,
              "actual_wire_settings": transport,
              "private_concise_self_reports": {role: {"status": records[stage]["status"], "carryover_note": records[stage]["output"]["carryover_note"], "image_reflection": records[stage]["output"]["image_reflection"]} for role, stage in (("A", "POSTVERDICT_A_LOG"), ("B", "POSTVERDICT_B_LOG"), ("C", "POSTVERDICT_C_LOG"))},
              "selected_main_attempt_accounting": selected_cost, "excluded_technical_attempts": excluded,
              "source_bindings": bindings, "provider_raw_reasoning_exported": False,
              "original_source_data_modified": False, "actor_or_reviewer_coaching_performed": False}
    for relative, expected in bindings.items():
        if sha((ROOT / relative).read_bytes()) != expected:
            raise ValueError("Evidence changed during researcher reading")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "write"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--annotations", type=Path)
    args = parser.parse_args()
    result = inspect(args.out, args.round)
    if args.command == "inspect":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.annotations is None:
        raise ValueError("Writing requires explicit researcher observations after actual image inspection")
    result["researcher_annotations"] = read(args.annotations)
    result["annotations_source_sha256"] = sha(args.annotations.read_bytes())
    target = args.out / f"analysis/researcher_audit/CLOSED_R{args.round:02d}_RESEARCHER_AUDIT_V1.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x") as stream:
        stream.write(canonical(result) + "\n")
    print(json.dumps({"path": str(target), "sha256": sha(target.read_bytes()), "round": args.round,
                      "selected_attempt": result["selected_attempt"]["attempt_id"],
                      "source_files_bound": len(result["source_bindings"])}))


if __name__ == "__main__":
    main()
