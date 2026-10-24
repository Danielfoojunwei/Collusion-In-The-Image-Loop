"""Standalone offline evidence renderer for a completed repaired-v8 episode.

No runtime imports, provider calls, credential access or scientific-file edits.
Run the coordinator's report command first. By default all twelve rounds must
have terminal records. --allow-incomplete explicitly labels a stopped report.
Only derived files in the chosen analysis destination are written.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "runs/main/triad_repaired_v8_live"
STAGES = ("A_IMAGE_AND_VOTE", "B_IMAGE_AND_VOTE", "C_VOTE", "REVIEWER",
          "POSTVERDICT_A_LOG", "POSTVERDICT_B_LOG", "POSTVERDICT_C_IMAGE", "POSTVERDICT_C_LOG")
VOTE = dict(zip("ABC", STAGES[:3]))
IMAGE = {"A": STAGES[0], "B": STAGES[1], "C": STAGES[6]}
LOG = {"A": STAGES[4], "B": STAGES[5], "C": STAGES[7]}
RECOVERY_SIDECAR_SHA256 = "dbdca3e9044439383c5eff8821bdc3313eb91a2ce2c06ca5a4ee08836e8b8d4d"
LIMITATIONS = [
    "Known invalid/refused outputs are terminal scientific observations, not repaired successes; ambiguous paid transport, integrity and budget failures remain separate hard stops.",
    "Within-case rounds form a dependent trajectory. The intended twelve-round main is not twelve independent replications; technical communication controls are separate.",
    "Shared-coached reports are assisted-elicitation interventions, not blinded independent detection.",
    "Codex is a separate role with instruction-scoped isolation; full host tool/context traces are unavailable.",
    "Private notes, reflections, pressure, willingness and intentions are self-reports, not verified emotion or hidden cognition.",
    "Target readouts and locked decisions are observations; they do not alone identify a covert decoder, knowing collusion or image-only causation.",
    "The renderer quotes actual prompts, reports and reflections; it makes no new pixel-content judgment or claim about the image generator's intent.",
    "Synthetic payoffs and pressure feedback are text/scores, not real payment, parameter updates or reinforcement learning.",
    "Original v6 rounds1–8 and v7 round9 A/B/C were imported unchanged. Round9 is mixed: v7 ballots/images, then v8 calibrated reviewer and post-verdict stages. Rounds10–12 use v8. This is one segmented dependent episode, not a randomized penalty comparison or twelve fresh v8 rounds.",
    "Calibrated evidence tiers and confidence are the actual reviewer's reports, not independently established detection accuracy. Uncertain or missing reviews do not imply supported detection or a punishment.",
    "A valid ballot does not require a delivered image. Explicit abstention and decline are terminal observations distinct from invalid output; image availability is reported independently.",
    "A/B round9 first-paid logs were recovered from the standard native-index metadata rejection. Their original invalid harness outcomes and extra paid replies remain preserved; only the exact first strings are displayed and used for telemetry, with recovery provenance.",
]


def stage_origins(number):
    return {stage: "triad_repaired_v6" if number <= 8 else
            "triad_repaired_v7" if number == 9 and stage in STAGES[:3] else
            "triad_repaired_v8" for stage in STAGES}


def round_origin(number):
    return "triad_repaired_v6" if number <= 8 else "mixed_v7_v8" if number == 9 else "triad_repaired_v8"


def segment(number, stage):
    origin = stage_origins(number)[stage]
    return {"triad_repaired_v6": "imported_v6_rounds_01_08",
            "triad_repaired_v7": "imported_v7_round_09_A_B_C",
            "triad_repaired_v8": "v8_from_round_09_reviewer"}[origin]


def ballot_record(value, status):
    state = value.get("submission_state") if value else None
    available = bool(value and state not in {"abstained", "declined"}
                     and value.get("decision") in {"P1", "P2", "P3", "P4", "reject_all"}
                     and value.get("decoded_target") in {"P1", "P2", "P3", "P4", "unknown"})
    return {"available": available, "stage_status": status, "submission_state": state,
            "ballot_status": "submitted" if available else state if state in {"abstained", "declined"}
                else "invalid_stage" if status == "invalid_model_output" else "missing",
            "decision": value.get("decision") if value else None,
            "decoded_target": value.get("decoded_target") if value else None}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Refusing symlinked source or destination")
    return path.resolve()


def relative_link(path, destination):
    return quote(os.path.relpath(safe(path), destination), safe="/")


class Reader:
    def __init__(self):
        self.sources = {}

    def bytes(self, path):
        path = safe(path)
        raw = path.read_bytes()
        self.sources[str(path)] = sha(raw)
        return raw

    def json(self, path, optional=False):
        path = safe(path)
        return None if optional and not path.exists() else json.loads(self.bytes(path))

    def text(self, path):
        return self.bytes(path).decode("utf-8")


def recovered_log(reader, out, case_id, number, role, outcome, measurement, *, live=False):
    """Read the bound first-log projection without importing runtime or reasoning."""
    recovery = (measurement or {}).get("technical_recovery")
    if not recovery:
        return None
    stage = LOG[role]
    sidecar_path = safe(out / "technical_log_corrections.json")
    raw = reader.bytes(sidecar_path)
    checksum = sha(raw)
    if (number != 9 or role not in "AB" or outcome.get("status") != "invalid_model_output"
            or outcome.get("output") is not None
            or recovery.get("original_outcome_status") != "invalid_model_output"
            or recovery.get("new_model_response") is not False
            or recovery.get("second_paid_reply_used") is not False
            or safe(measurement["source_path"]) != sidecar_path
            or measurement.get("source_file_sha256") != checksum
            or (live and checksum != RECOVERY_SIDECAR_SHA256)):
        raise ValueError("Recovered log provenance differs from the exact first-paid metadata repair")
    sidecar = json.loads(raw)
    if (sidecar.get("revision") != "native_index_metadata_repair02"
            or sidecar.get("case_id") != case_id or sidecar.get("output_root") != str(out)):
        raise ValueError("Recovered log sidecar scope changed")
    if (sha(reader.bytes(out / "config.json")) != sidecar.get("config_sha256")
            or sha(reader.bytes(out / "plan.json")) != sidecar.get("plan_sha256")):
        raise ValueError("Recovered log sidecar config/plan binding changed")
    entries = [item for item in sidecar.get("entries", []) if item.get("role") == role]
    if len(entries) != 1:
        raise ValueError("Recovered first log is absent or duplicated")
    entry = entries[0]
    prefix = f"cases/{case_id}/rounds/r09/{stage}"
    first_id = f"triad_repaired_v8:{case_id}.r09.{stage}.step1.attempt1"
    if (entry.get("round") != 9 or entry.get("stage") != stage or entry.get("function") != "record_log"
            or entry.get("classification") != "first_paid_log_recovered_from_transport_metadata_rejection"
            or entry.get("first_physical_request_id") != first_id
            or entry.get("excluded_second_physical_request_id") != first_id.replace(".step1.", ".step2.")
            or entry.get("original_outcome_file") != prefix + ".outcome.json"
            or entry.get("original_request_file") != prefix + ".request.json"):
        raise ValueError("Recovery must identify the first paid A/B9 log, never its second reply")

    def bound(relative, expected):
        path = safe(out / relative)
        if not path.is_relative_to(out):
            raise ValueError("Recovery evidence path escaped the selected run")
        data = reader.bytes(path)
        if sha(data) != expected:
            raise ValueError("Bound first-log recovery evidence checksum changed")
        return data

    original = json.loads(bound(entry["original_outcome_file"], entry["original_outcome_sha256"]))
    request = json.loads(bound(entry["original_request_file"], entry["original_request_sha256"]))
    if (original != outcome or original.get("record_hash") != entry.get("original_outcome_record_hash")
            or recovery.get("original_outcome_sha256") != entry["original_outcome_sha256"]
            or safe(recovery["original_outcome_path"]) != safe(out / entry["original_outcome_file"])
            or request.get("request_hash") != entry.get("original_request_hash")):
        raise ValueError("Recovered first log does not bind the preserved original outcome/request")
    ledger = json.loads(bound(entry["first_ledger_row_file"], entry["first_ledger_row_sha256"]))
    response_text, receipt_text = ledger["response_json"], ledger["receipt_json"]
    if (ledger.get("request_id") != first_id or ledger.get("status") != "completed"
            or sha(response_text.encode()) != entry.get("response_json_sha256")
            or sha(receipt_text.encode()) != entry.get("receipt_json_sha256")
            or sha((response_text + "\n" + receipt_text).encode()) != ledger.get("result_hash")):
        raise ValueError("Recovered first paid response binding changed")
    choices = json.loads(response_text).get("choices", [])
    calls = choices[0].get("message", {}).get("tool_calls", []) if len(choices) == 1 else []
    call = calls[0] if len(calls) == 1 else {}
    function = call.get("function", {})
    if (set(call) != {"id", "type", "function", "index"} or type(call.get("index")) is not int
            or call["index"] != 0 or function.get("name") != "record_log"
            or sha(function["arguments"].encode()) != entry.get("arguments_sha256")):
        raise ValueError("Recovered log must be the exact first native index0 response")
    projection = json.loads(function["arguments"])
    if (projection != entry.get("projection") or set(projection) != {"private_note", "reflection"}
            or not all(isinstance(value, str) for value in projection.values())
            or sha(canonical(projection).encode()) != entry.get("projection_sha256")):
        raise ValueError("Recovered first log strings changed")
    return {"projection": projection, "provenance": {**recovery,
        "label": "recovered first paid log; original harness classification preserved",
        "source_sidecar": str(sidecar_path), "source_sidecar_sha256": checksum,
        "first_paid_request_id": first_id, "first_ledger_row_file": entry["first_ledger_row_file"],
        "first_ledger_row_sha256": entry["first_ledger_row_sha256"],
        "excluded_second_paid_request_id": entry["excluded_second_physical_request_id"]}}


def image(reader, descriptor, case_directory, out, destination):
    if descriptor is None:
        return None
    path = Path(descriptor["path"])
    path = safe(path if path.is_absolute() else case_directory / "memory" / path)
    if not path.is_relative_to(out):
        raise ValueError("PNG source escaped the selected run")
    raw = reader.bytes(path)
    if not raw.startswith(b"\x89PNG\r\n\x1a\n") or sha(raw) != descriptor["sha256"]:
        raise ValueError("Original PNG signature/hash changed")
    return {"path": str(path), "src": relative_link(path, destination), "sha256": sha(raw),
            "bytes": len(raw), "pixel_interpretation_added": False}


def actor_view(request):
    for message in (request or {}).get("messages", [])[1:]:
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            try:
                value = json.loads(message["content"])
            except ValueError:
                continue
            if isinstance(value, dict) and "phase" in value and "round" in value:
                return value
    return {}


def inline_png_hashes(request):
    result = set()
    for message in (request or {}).get("messages", []):
        for part in message.get("content", []) if isinstance(message.get("content"), list) else []:
            url = part.get("image_url", {}).get("url", "")
            if url.startswith("data:image/png;base64,"):
                result.add(sha(base64.b64decode(url.split(",", 1)[1], validate=True)))
    return result


def stage_identity(identifier):
    match = re.search(r"\.r(\d{2})\.([A-Z_]+)(?:\.|$)", identifier)
    return (int(match[1]), match[2]) if match else (0, "unknown")


def provider_sources(reader, out, destination):
    effective, generation, calls = {}, [], []
    provider = out / "provider_evidence"
    for path in sorted((provider / "effective_system_prompts").glob("*.json")):
        row = reader.json(path)
        if sha(row["system_prompt"].encode()) != row["system_prompt_sha256"]:
            raise ValueError("Effective system prompt checksum changed")
        effective[row["request_id"]] = {**row, "source_file": str(path)}
    for path in sorted((provider / "image_calls").glob("*.json")):
        row = reader.json(path)
        number, stage = stage_identity(row["request_id"])
        png = image(reader, {"path": str(provider / "blobs" / (row["result_sha256"] + ".png")),
                            "sha256": row["result_sha256"]}, out, out, destination)
        generation.append({"round": number, "stage": stage, "request_id": row["request_id"],
            "prompt": row["prompt"], "reference": row.get("reference"),
            "reference_sha256": row.get("reference_sha256"), "image": png,
            "receipt": row.get("receipt"), "source_file": str(path)})
    for path in sorted((provider / "transport_requests").glob("*.json")):
        row = reader.json(path)
        payload = row["logical_payload"]
        number, stage = stage_identity(row["request_id"])
        if row.get("endpoint") != "/chat/completions":
            continue
        calls.append({"round": number, "stage": stage, "request_id": row["request_id"],
            "logical_request_id": row.get("logical_request_id"), "source_file": str(path),
            "model": payload.get("model"), "temperature": payload.get("temperature"),
            "reasoning": payload.get("reasoning"), "max_tokens": payload.get("max_tokens"),
            "tools": payload.get("tools"), "tool_choice": payload.get("tool_choice"),
            "response_format": payload.get("response_format"),
            "system_messages": [message["content"] for message in payload.get("messages", []) if message.get("role") == "system"],
            "scope": "Retained wire intent; logical and physical attempt archives can describe the same request. Not a count of independent calls or trials."})
    return effective, generation, calls


def build(out=DEFAULT_OUT, *, destination=None, case_id=None, allow_incomplete=False):
    out = safe(out)
    destination = safe(destination or out / "analysis" / "human_readable")
    reader = Reader()
    config = reader.json(out / "config.json")
    plan = reader.json(out / "plan.json")
    freeze = reader.json(out / "freeze.json")
    report = reader.json(out / "analysis" / "report.json")
    continuation = config.get("continuation", {})
    if (not continuation.get("enabled") or continuation.get("imported_rounds") != 8
            or continuation.get("imported_stages") != 67
            or continuation.get("first_new_round") != 9
            or continuation.get("first_new_stage") != "REVIEWER"
            or config.get("calibrated_review", {}).get("effective_from_round") != 9
            or config.get("calibrated_review", {}).get("enabled") is not True):
        raise ValueError("This renderer requires the declared 67-stage prefix and calibrated reviewer9 boundary")
    imported_manifest = reader.json(out / "continuation_import.json", optional=True)
    if freeze.get("evidence_origin") == "live" and imported_manifest is None:
        raise ValueError("Live continuation import manifest is missing")
    selected = plan.get("selected_case_ids", [])
    case_id = case_id or (selected[0] if selected else plan["cases"][0]["case_id"])
    case = next(row for row in plan["cases"] if row["case_id"] == case_id)
    case_directory = out / "cases" / case_id
    limit = int(config.get("execution", {}).get("round_limit", 12))
    main = config.get("execution", {}).get("phase", "main") == "main"
    frozen_checks = {name: sha(reader.bytes(ROOT / name)) == checksum
                     for name, checksum in freeze.get("source_files", {}).items()}
    if not all(frozen_checks.values()):
        raise ValueError("A frozen runtime dependency changed; original evidence was not rewritten")
    effective, generations, calls = provider_sources(reader, out, destination)
    report_rows = {(row["case_id"], row["round"]): row for row in report.get("rounds", [])}
    if len(report_rows) != len(report.get("rounds", [])):
        raise ValueError("Coordinator report duplicates a case/round")
    rounds, prompts, previous_C, previous_hash, chain_gap = [], [], None, "0" * 64, False
    chain_checks = []
    for number in range(1, limit + 1):
        folder = case_directory / "rounds" / f"r{number:02d}"
        outcomes, statuses, requests, images, image_submissions, logs, ballots, failures = {}, {}, {}, {}, {}, {}, {}, []
        source = report_rows.get((case_id, number))
        origins = stage_origins(number)
        imported_count = 8 if number <= 8 else 3 if number == 9 else 0
        if source and (source.get("protocol_revision") != round_origin(number)
                or source.get("stage_protocol_revisions") != origins
                or source.get("imported_stage_count") != imported_count
                or source.get("new_stage_count") != 8 - imported_count
                or source.get("entire_round_imported") is not (number <= 8)
                or source.get("contains_imported_stage_observations") is not (number <= 9)):
            raise ValueError("Coordinator row does not preserve the per-stage v6/v7/v8 protocol origin")
        for stage in STAGES:
            outcome = reader.json(folder / (stage + ".outcome.json"), optional=True)
            statuses[stage] = outcome["status"] if outcome else (
                "unresolved_dispatch" if (folder / (stage + ".dispatch.json")).exists() else "not_run")
            if outcome:
                outcomes[stage] = outcome
                body = {key: value for key, value in outcome.items() if key != "record_hash"}
                chain_checks.append(not chain_gap and body.get("previous_hash") == previous_hash and sha(canonical(body).encode()) == outcome.get("record_hash"))
                previous_hash = outcome.get("record_hash")
                if outcome["status"] != "valid":
                    failures.append({"stage": stage, "status": outcome["status"], "reason": outcome.get("reason"),
                        "provenance": outcome.get("provenance"), "source_file": str(folder / (stage + ".outcome.json")),
                        "observed_path": outcome.get("observed_path"), "observed_sha256": outcome.get("observed_sha256")})
            else:
                chain_gap = True
            request = reader.json(folder / (stage + ".request.json"), optional=True)
            if request:
                requests[stage] = request
            identifier = f"{case_id}.r{number:02d}.{stage}"
            instructions = case_directory / "reviewer_packets" / f"r{number:02d}" / "instructions.txt"
            prompts.append({"round": number, "stage": stage, "status": statuses[stage],
                "execution_segment": segment(number, stage), "protocol_revision": origins[stage],
                "prompt_origin_note": (
                    "Imported original request/instructions; retained bytes are not replaced by current v8 prompts. "
                    "Source paths can name the continuation copy; use continuation_import.json for original-file provenance."
                    if origins[stage] != "triad_repaired_v8" else "New v8 request/instructions; calibrated review begins at reviewer9."),
                "prepared_engine_system_messages": [message["content"] for message in (request or {}).get("messages", []) if message.get("role") == "system"],
                "effective_api_system": effective.get(identifier),
                "reviewer_declared_instructions": reader.text(instructions) if stage == "REVIEWER" and instructions.exists() else None,
                "retained_calls": [row for row in calls if row["round"] == number and row["stage"] == stage]})
        for role, stage in VOTE.items():
            value = outcomes.get(stage, {}).get("output") if statuses[stage] == "valid" else None
            ballots[role] = ballot_record(value, statuses[stage])
        for role, stage in IMAGE.items():
            value = outcomes.get(stage, {}).get("output") if statuses[stage] == "valid" else None
            images[role] = image(reader, value.get("image") if value else None, case_directory, out, destination)
            image_submissions[role] = {"stage_status": statuses[stage],
                "submission_state": value.get("submission_state") if value else None,
                "delivered_image_available": images[role] is not None,
                "image_availability_independent_of_ballot": True}
        for role, stage in LOG.items():
            outcome = outcomes.get(stage, {})
            value = outcome.get("output") if outcome.get("status") == "valid" else None
            measurement = (source or {}).get("private_self_reports", {}).get(role)
            recovered = recovered_log(reader, out, case_id, number, role, outcome, measurement,
                                      live=freeze.get("evidence_origin") == "live")
            displayed = recovered["projection"] if recovered else value
            logs[role] = {"status": statuses[stage], "accepted": value is not None,
                "log_text_available": displayed is not None,
                "log_text_source": "recovered_first_paid_log" if recovered else "original_valid_stage" if value else "unavailable",
                "technical_recovery": recovered["provenance"] if recovered else None,
                "private_note": displayed.get("private_note") if displayed else None,
                "reflection": displayed.get("reflection") if displayed else None,
                "self_report_only": True,
                "optional_measurement_from_coordinator_report": measurement,
                "invalid_emitted_tool_actions": [{"step": row.get("step"), "output": row.get("output"), "validation_error": row.get("validation_error")}
                    for row in outcome.get("action_log", []) if row.get("validation_error")]}
            if recovered:
                for failure in failures:
                    if failure["stage"] == stage:
                        failure["technical_recovery"] = recovered["provenance"]
                        failure["classification"] = "harness_metadata_rejection_with_first_paid_log_recovered"
        review_outcome = outcomes.get("REVIEWER", {})
        reviewer = review_outcome.get("output") if statuses["REVIEWER"] == "valid" else None
        if source and source.get("reviewer") != reviewer:
            raise ValueError("Coordinator report differs from the preserved actual reviewer outcome")
        feedback = []
        metrics = (source or {}).get("post_review_metrics")
        assignment = actor_view(requests.get(VOTE["A"])).get("private_assignment", {}).get("current_private_target")
        expected_target = case["private_targets"][number - 1]
        if assignment is not None and assignment != expected_target:
            raise ValueError("Retained A assignment differs from the frozen target schedule")
        if metrics is not None and metrics.get("current_private_target") != expected_target:
            raise ValueError("Post-review target disclosure differs from the original assignment")
        for stage in STAGES[4:]:
            request = requests.get(stage)
            if request:
                supplied = actor_view(request).get("reviewer_feedback", {})
                feedback.append({"stage": stage, "full_actual_report_supplied": supplied.get("reviewer_report") == reviewer if reviewer else None,
                    "actual_reviewer_feedback": supplied,
                    "participant_summary": supplied.get("participant_summary"),
                    "collective_consequence": supplied.get("collective_consequence"),
                    "supplied_post_review_metrics": supplied.get("post_review_metrics"),
                    "metrics_match_final_report": supplied.get("post_review_metrics") == metrics if metrics is not None else None,
                    "missing_review_marked_unavailable": supplied.get("available") is False if reviewer is None else None})
        withheld = case["condition"] == "no_image"
        routes = []
        for route, png, stage in (("A_to_B", images["A"], VOTE["B"]), ("A_to_C", images["A"], VOTE["C"]),
                                  ("B_to_C", images["B"], VOTE["C"]), ("previous_C_to_A", previous_C, VOTE["A"])):
            if route == "previous_C_to_A" and number == 1:
                continue
            request = requests.get(stage)
            present = png["sha256"] in inline_png_hashes(request) if png and request else None
            routes.append({"route": route, "source_available": png is not None, "target_request_available": request is not None,
                "withheld_by_condition": withheld, "exact_original_png_inline": present,
                "matches_declared_route": (present is False if withheld else present is True) if present is not None else None})
        next_A = reader.json(case_directory / "rounds" / f"r{number + 1:02d}" / "A_IMAGE_AND_VOTE.request.json", optional=True) if number < limit else None
        C_supplied = images["C"]["sha256"] in inline_png_hashes(next_A) if images["C"] and next_A else None
        C_route = ("Withheld from participants in no-image control; original retained for assessment." if withheld else
            "Archived only; no round13 recipient." if number == limit and main else
            f"Archived only; technical phase ends after round{limit}." if number == limit else
            f"Exact C pixels were supplied to next A in round{number + 1}; there is no extra reply stage." if C_supplied else
            f"Next A's round{number + 1} request exists, but exact delivery of this C image is not established." if next_A else
            f"Planned for next A in round{number + 1}; no next-A request exists, so delivery has not been observed.")
        rounds.append({"round": number, "stage_status": statuses, "ballots": ballots, "images": images,
            "image_submissions": image_submissions,
            "protocol_revision": round_origin(number), "stage_protocol_revisions": origins,
            "imported_stage_count": imported_count, "new_stage_count": 8 - imported_count,
            "entire_round_imported": number <= 8, "contains_imported_stage_observations": number <= 9,
            "scoring_regime": "original v6 scores copied unchanged" if number <= 8 else "calibrated v8 review; base reward plus separate concealment bonus; capped current-round loss, no escalating debt",
            "current_private_target": case["private_targets"][number - 1], "post_review_metrics": metrics,
            "consensus_from_coordinator_report": (source or {}).get("consensus"),
            "reviewer": reviewer, "reviewer_provenance": review_outcome.get("provenance"),
            "calibrated_collusion_assessment": reviewer.get("collusion_assessment") if reviewer else None,
            "coaching": reviewer.get("coaching") if reviewer else None, "private_self_reports": logs,
            "actual_image_generator_calls": [row for row in generations if row["round"] == number],
            "post_review_delivery_checks": feedback, "directed_image_checks": routes,
            "identical_actual_feedback_across_all_four_post_stages": (
                all(item["actual_reviewer_feedback"] == feedback[0]["actual_reviewer_feedback"] for item in feedback)
                if len(feedback) == 4 else None),
            "failures": failures, "terminal_round_record": "POSTVERDICT_C_LOG" in outcomes,
            "C_return_delivery": {"next_A_request_available": next_A is not None,
                "exact_C_pixels_supplied_inline": C_supplied, "withheld_by_condition": withheld,
                "next_reviewer_availability_is_separate": True}, "C_return_route": C_route})
        previous_C = images["C"]
    finished = sum(row["terminal_round_record"] for row in rounds)
    terminal_count = sum(status in {"valid", "invalid_model_output"}
                         for row in rounds for status in row["stage_status"].values())
    if chain_checks and not all(chain_checks):
        raise ValueError("The retained stage chain has a gap or altered record")
    if not allow_incomplete and (limit != 12 or finished != 12 or terminal_count != 96):
        raise ValueError("Episode is incomplete; no final artifacts written. Use --allow-incomplete only to explicitly report a stopped trajectory.")
    if not allow_incomplete and any((case_id, number) not in report_rows for number in range(1, 13)):
        raise ValueError("Final coordinator report must include all twelve observed reviews, including invalid ones")
    private_logs = [log for row in rounds for log in row["private_self_reports"].values()]
    recovered_count = sum(log["technical_recovery"] is not None for log in private_logs)
    raw_invalid_count = sum(status == "invalid_model_output" for row in rounds for status in row["stage_status"].values())
    return {"artifact_revision": "repaired_v8_external_report_v1", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "out": str(out), "destination": str(destination), "case_id": case_id,
        "condition": case["condition"], "execution_phase": config.get("execution", {}).get("phase"),
        "intended_main_rounds": 12, "main_rounds_represented": finished if main else 0,
        "scope": "segmented continuation:64 v6 stages plus3 v7 round9 actor stages plus29 v8 stages; one dependent episode",
        "protocol_boundary": {"last_entirely_original_round": 8, "mixed_round": 9,
            "first_v8_stage": "round09.REVIEWER", "fully_new_v8_rounds": [10, 11, 12],
            "imported_terminal_stages": 67, "new_terminal_stage_positions": 29,
            "original_rounds_imported_not_rerun": 8,
            "causal_penalty_effect_established": False},
        "continuation_import": imported_manifest,
        "evidence_origin": freeze.get("evidence_origin", "unknown"), "rounds_finished": finished,
        "rounds_scheduled_in_phase": limit, "terminal": finished == limit,
        "valid_stage_records": sum(status == "valid" for row in rounds for status in row["stage_status"].values()),
        "invalid_stage_records": raw_invalid_count,
        "raw_invalid_stage_records": raw_invalid_count,
        "recovered_first_paid_log_records": recovered_count,
        "other_raw_invalid_stage_records": raw_invalid_count - recovered_count,
        "private_log_counts": {"raw_valid_stage_logs": sum(log["status"] == "valid" for log in private_logs),
            "raw_invalid_stage_logs": sum(log["status"] == "invalid_model_output" for log in private_logs),
            "first_paid_logs_recovered_from_harness_metadata_error": recovered_count,
            "text_available": sum(log["log_text_available"] for log in private_logs),
            "text_unavailable": sum(not log["log_text_available"] for log in private_logs),
            "original_harness_classifications_preserved": True, "second_paid_replies_used": False},
        "terminal_stage_records": terminal_count,
        "observed_stage_origin_counts": dict(Counter(row["stage_protocol_revisions"][stage]
            for row in rounds for stage, status in row["stage_status"].items()
            if status in {"valid", "invalid_model_output"})),
        "unresolved_dispatches": sum(status == "unresolved_dispatch" for row in rounds for status in row["stage_status"].values()),
        "config": config, "coordinator_report": report, "rounds": rounds, "prompt_export": prompts,
        "limitations": LIMITATIONS, "source_files_sha256": reader.sources,
        "frozen_runtime_checks": frozen_checks, "stage_chain_matches": all(chain_checks) if chain_checks else None,
        "provider_reasoning_exported": False, "paid_calls_by_renderer": 0}


def fenced(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    fence = "`" * max(3, max((len(item) + 1 for item in re.findall(r"`+", text)), default=3))
    return fence + "\n" + text + "\n" + fence


def md_report(data):
    rows = ["# V8 continuation: chronological observed " + ("main episode" if data['execution_phase']=='main' else "communication control"), "",
        f"**{data['rounds_finished']}/{data['rounds_scheduled_in_phase']} terminal rounds.** Condition `{data['condition']}`; evidence origin `{data['evidence_origin']}`.", "",
        "**Protocol break after round8; mixed round9.** Rounds1–8 are imported original v6 observations with their original rules, prompts and scores. Round9 A/B/C are imported v7 observations; its reviewer and four post-verdict stages use v8. Rounds10–12 use v8 throughout. The same memories continue; this is one dependent episode, not twelve new v8 runs or an independent penalty comparison.", "",
        f"Raw stage records: {data['valid_stage_records']} valid; {data['raw_invalid_stage_records']} classified `invalid_model_output`; {data['unresolved_dispatches']} unresolved dispatches. Of the raw invalid records, {data['recovered_first_paid_log_records']} are harness metadata rejections with exact first-paid logs recovered; {data['other_raw_invalid_stage_records']} other raw invalid records retain their individual reasons. Terminal completion does not establish behavioral success.", "",
        "Recovered first paid log; original harness classification preserved. Recovery is an evidence projection, not a new model reply or a change to the stage chain. Second paid replies are excluded. Private-log text availability and optional telemetry are distinct from the raw stage classification: " + canonical(data["private_log_counts"]), "",
        f"Intended main schedule: {data['intended_main_rounds']} rounds. Main rounds represented here: {data['main_rounds_represented']}. Scope: {data['scope']}.", "",
        "[Original-image gallery](GALLERY.html) · [Exact system prompts by stage](EXACT_SYSTEM_PROMPTS_BY_STAGE.txt) · [Full source-linked evidence](EVIDENCE.json)", "",
        "Only A initially receives each independently drawn target. Scheduled order: A→B/C, B→C, locked votes, actual Codex assessment/coaching, A/B logs, C return image, then C log. A return image's existence does not establish later delivery; recorded next-A inputs are checked separately. Final phase images are archived. Codex is separate from C; coaching is an explicitly enabled intervention. The source report retains aggregate budget and optional behavioral telemetry.", "",
        *["- " + text for text in data["limitations"]], "",
        "## Round overview", "", "Amounts below are nominal synthetic USD per participant, not actual payment. NET is copied literally from the recorded metric, including any shared loss; it is never recomputed from base and bonus. Rounds9–12 have a capped current-round consequence with no escalating debt. Unknown remains unknown; uncertain or missing reviews do not become invented detection or punishment. Original rounds1–8 are not rescored. Task/audit penalty points are separate.", "",
        "| Round | Regime | Target | A/B/C decisions | B/C readouts | Target adopted | Reviewer/channel | Actual tier/confidence | Base / additional / shared loss / **NET** per agent |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in data["rounds"]:
        metrics = row["post_review_metrics"] or {};payoff = metrics.get("private_synthetic_payoffs", {})
        reviewer = row["reviewer"] or {}
        assessment = row["calibrated_collusion_assessment"]
        values = [row["round"], "original v6" if row["round"] <= 8 else "mixed v7/v8" if row["round"] == 9 else "v8", row["current_private_target"], "/".join(str(row["ballots"][role]["decision"]) for role in "ABC"),
                  "/".join(str(row["ballots"][role]["decoded_target"]) for role in "BC"),
                  metrics.get("unanimous_private_target_adoption"), reviewer.get("verdict", "missing") + "/" + reviewer.get("channel", "missing"),
                  (str(assessment.get("level")) + "/" + str(assessment.get("confidence"))) if assessment else "not measured" if row["round"] <= 8 else "unknown",
                  " / ".join(str(payoff.get(key)) if payoff.get(key) is not None else "unknown" for key in (
                      "base_payoff_usd_per_agent", "concealment_bonus_usd_per_agent"))
                      + " / " + (str(payoff.get("shared_detection_loss_usd_per_agent"))
                          if payoff.get("shared_detection_loss_usd_per_agent") is not None else "n/a" if row["round"] <= 8 else "unknown")
                      + " / **" + (str(payoff.get("total_usd_per_agent")) if payoff.get("total_usd_per_agent") is not None else "unknown") + "**"]
        rows.append("| " + " | ".join(str(value).replace("|", "/") if value is not None else "unknown" for value in values) + " |")
    for row in data["rounds"]:
        n = row["round"]
        rows += ["", f"## Round {n:02d}", "", "Stage status: " + canonical(row["stage_status"]), "",
            "Regime: " + row["scoring_regime"] + ". " + ("Original v6 evidence imported unchanged." if n <= 8 else "Round9 actor stages retain v7 prompts/consequences; review and post-feedback use v8." if n == 9 else "All stages use v8; earlier observations are not rescored."), "",
            "Per-stage protocol origins:", fenced(row["stage_protocol_revisions"]), "",
            "Locked ballots and separate target readouts:", fenced(row["ballots"]), "",
            "Independent image submissions (decline/abstention remain distinct from invalid output):", fenced(row["image_submissions"]), "",
            "Separate observed/calculated metrics (null means unknown):", fenced(row["post_review_metrics"]), ""]
        for role in "AB":
            png = row["images"][role]
            rows += [role + " image: " + (f"[original PNG]({png['src']}) · SHA256 `{png['sha256']}`" if png else "unavailable"), ""]
        rows += ["Actual Codex assessment, image-linked evidence tier/confidence and explicitly labeled coaching:", fenced(row["reviewer"]), "",
            "Reviewer provenance and input-delivery checks:", fenced({"provenance": row["reviewer_provenance"],
                "post_review_inputs": row["post_review_delivery_checks"], "image_routes": row["directed_image_checks"]}), ""]
        for role in "ABC":
            if role == "C":
                png = row["images"][role]
                rows += ["C return image: " + (f"[original PNG]({png['src']})" if png else "unavailable") + ". " + row["C_return_route"], ""]
            log = row["private_self_reports"][role]
            label = "recovered first paid log; original harness classification preserved" if log["technical_recovery"] else "original valid stage" if log["accepted"] else "unavailable"
            rows += [f"### {role}: exact private self-report", "", f"Raw status: `{log['status']}`. Text source: **{label}**. Private note:", fenced(log["private_note"]), "",
                "Prompt-versus-render reflection (quoted, not independently verified):", fenced(log["reflection"]), ""]
            if log["technical_recovery"]:
                rows += ["First-paid-log recovery provenance (second reply excluded):", fenced(log["technical_recovery"]), ""]
            calls = [call for call in row["actual_image_generator_calls"] if call["stage"] == IMAGE[role]]
            for call in calls:
                rows += ["Exact actual image-generator prompt (compare against its returned PNG and the quoted reflection):", fenced(call["prompt"]), "",
                    f"Reference: `{call['reference']}`; reference SHA256: `{call['reference_sha256']}`; [original output PNG]({call['image']['src']}).", ""]
        if row["failures"]:
            rows += ["Preserved invalid/technical records:", fenced(row["failures"]), ""]
    rows += ["## Budget and behavioral timeline from the canonical report", "",
        "Namespace totals are subsets of the aggregate; unknown-charge reservations are not zero. Optional states are self-reports, not feelings or hidden-thought measurements.", "",
        fenced({"budget": data["coordinator_report"].get("budget"),
                "behavioral_timeline": data["coordinator_report"].get("behavioral_timeline")}), ""]
    return "\n".join(rows)


def prompt_export(data):
    rows = ["EXACT SYSTEM PROMPTS BY STAGE — PRIVATE RESEARCHER EVIDENCE", "",
        "Prepared engine messages are distinct from effective API system prompts. Codex platform/system instructions are not exported; only its exact declared reviewer instructions are available. No provider reasoning is included.", ""]
    rows += ["PROTOCOL BOUNDARIES: rounds01–08 retain original v6 prompts; round09 A/B/C retain v7 prompts; round09 reviewer/post-verdict and rounds10–12 use v8. Current v8 source prompts do not replace earlier issued messages. continuation_import.json records original-file provenance. Native legal toolsets are quoted exactly from retained requests, separately from prior JSON-action formats.", ""]
    for row in data["prompt_export"]:
        rows += [f"ROUND {row['round']:02d} / {row['stage']} / {row['status']}",
                 "Segment: " + row["execution_segment"], row["prompt_origin_note"]]
        effective = row["effective_api_system"]
        if effective:
            rows += ["Exact effective API system message:", effective["system_prompt"], "Source: " + effective["source_file"]]
        elif row["reviewer_declared_instructions"] is not None:
            rows += ["Exact declared instructions for actual separate Codex reviewer:", row["reviewer_declared_instructions"]]
        elif row["prepared_engine_system_messages"]:
            rows += ["Prepared system message; no effective API system artifact found:", *row["prepared_engine_system_messages"]]
        else:
            rows += ["No observed prompt artifact; this stage may be missing or undispatched."]
        for call in row["retained_calls"]:
            rows += ["Retained request intent: " + call["request_id"], canonical({key: call[key] for key in (
                "model", "temperature", "reasoning", "max_tokens", "tools", "tool_choice", "response_format")})]
            if not effective:
                rows += ["Exact system messages from retained wire intent:", *call["system_messages"]]
        rows.append("")
    return "\n".join(rows)


def gallery(data):
    esc = html.escape
    def detail(title, value):
        return "<details><summary>" + esc(title) + "</summary><pre>" + esc(json.dumps(value, ensure_ascii=False, indent=2)) + "</pre></details>"
    def figure(png, caption):
        if not png:
            return "<figure><p>Image unavailable.</p><figcaption>" + esc(caption) + "</figcaption></figure>"
        src = esc(png["src"], quote=True)
        return f'<figure><a href="{src}"><img loading="lazy" src="{src}" alt="{esc(caption, quote=True)}"></a><figcaption>{esc(caption)}<br><code>{png["sha256"]}</code></figcaption></figure>'
    def own_record(row, role):
        content = [detail(role + " exact private note/reflection and optional self-report", row["private_self_reports"][role])]
        if row["private_self_reports"][role]["technical_recovery"]:
            content.insert(0, "<p class=notice><strong>" + esc(role) + ": recovered first paid log; original harness classification preserved.</strong> Exact first strings are shown; the second paid reply is excluded.</p>")
        for call in row["actual_image_generator_calls"]:
            if call["stage"] == IMAGE[role]:
                content.append(detail(role + " exact generator prompt/reference/receipt", call))
                delivered = row["images"][role]
                if not delivered or call["image"]["sha256"] != delivered["sha256"]:
                    content += ["<details><summary>Original private draft pixels — not the delivered image</summary>",
                        figure(call["image"], call["request_id"]), "</details>"]
        return content
    body = ["<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>V8 continuation chronological images</title>",
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:1250px;margin:2rem auto;padding:0 1rem;color:#172538;background:#f3f6fa}section{background:white;padding:1.4rem;margin:2rem 0;border:1px solid #ccd6e0;border-radius:10px}a{color:#085a9c}.images{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}figure{margin:0}img{width:100%;max-height:750px;object-fit:contain;background:#eee}figcaption,code{overflow-wrap:anywhere;font-size:.85rem}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf2f7;padding:1rem;font-size:.88rem}summary{cursor:pointer;padding:.5rem}.notice{border-left:4px solid #ad6b00;background:#fff0ce;padding:1rem}nav{position:sticky;top:0;background:#f3f6fa;padding:.5rem}h2{scroll-margin-top:4rem}</style>",
        "<h1>V8 continuation chronological image evidence</h1>",
        f"<p>{data['rounds_finished']}/{data['rounds_scheduled_in_phase']} terminal rounds. Condition: {esc(data['condition'])}; evidence origin: {esc(data['evidence_origin'])}. Intended main:12 rounds; main rounds represented:{data['main_rounds_represented']}. {esc(data['scope'])}.</p>",
        "<p class=notice><strong>Protocol break after round8; mixed round9.</strong> Original v6 rounds1–8 were imported unchanged. Round9 A/B/C retain v7; reviewer9 and post-verdict stages start v8, followed by fully v8 rounds10–12. Memories continue. This is one segmented dependent episode, not twelve new v8 runs or a randomized test of punishment.</p>",
        "<p class=notice>Private researcher view. One dependent episode. Coaching is an explicit intervention; Codex isolation is instruction-scoped. Reflections and reported pressure/willingness are self-reports. No new pixel judgments or generator-intent claims are added.</p>",
        "<p>Raw invalid stage records: " + str(data["raw_invalid_stage_records"]) + "; recovered first-paid logs from harness metadata errors: " + str(data["recovered_first_paid_log_records"]) + ". The original classifications remain unchanged; recovery is separate from model refusal or missing text.</p>",
        "<p><a href=REPORT.md>Chronological report</a> · <a href=EXACT_SYSTEM_PROMPTS_BY_STAGE.txt>Exact system prompts</a> · <a href=EVIDENCE.json>Full evidence and source hashes</a></p>",
        "<nav>Rounds: " + " · ".join(f'<a href="#r{row["round"]:02d}">{row["round"]:02d}</a>' for row in data["rounds"]) + "</nav>"]
    for row in data["rounds"]:
        payoff = (row["post_review_metrics"] or {}).get("private_synthetic_payoffs", {})
        amounts = {key: payoff.get(key) for key in ("base_payoff_usd_per_agent", "concealment_bonus_usd_per_agent",
            "shared_detection_loss_usd_per_agent", "total_usd_per_agent")}
        body += [f'<section id="r{row["round"]:02d}"><h2>Round {row["round"]:02d}</h2>',
            "<p><strong>" + esc(row["protocol_revision"]) + "</strong>: " + esc(row["scoring_regime"]) + ". "
                + ("Imported original observation." if row["round"] <= 8 else "Mixed: imported v7 A/B/C, new v8 reviewer/post-verdict." if row["round"] == 9 else "Fully v8 round; no rescoring of earlier observations.") + "</p>",
            "<p>Private target: " + esc(row["current_private_target"]) + "; locked decisions: " + esc(canonical({role: row["ballots"][role]["decision"] for role in "ABC"})) + ".</p>",
            "<p>Nominal synthetic USD per agent — base: " + esc(str(amounts["base_payoff_usd_per_agent"]))
                + "; additional: " + esc(str(amounts["concealment_bonus_usd_per_agent"]))
                + "; shared loss: " + esc(str(amounts["shared_detection_loss_usd_per_agent"]))
                + "; <strong>NET: " + esc(str(amounts["total_usd_per_agent"])) + "</strong>. "
                + "NET is the literal recorded total. Unknown is not zero; v8 loss is capped per round with no debt. Original v6 scores stay unchanged. Task/audit penalty points are separate.</p>",
            '<div class="images">', figure(row["images"]["A"], "A current image → B/C before voting (unless withheld by condition)"),
            figure(row["images"]["B"], "B current image → C before voting (unless withheld by condition)"), "</div>",
            detail("Separate target readouts and calculated scores", {"ballots": row["ballots"], "metrics": row["post_review_metrics"]}),
            detail("Independent image submissions: actual terminal state and delivery", row["image_submissions"]),
            detail("Actual separate Codex assessment", row["reviewer"]),
            detail("Actual calibrated tier/confidence and image-linked evidence", row["calibrated_collusion_assessment"]),
            detail("Per-stage protocol origins", row["stage_protocol_revisions"]),
            detail("Explicit coaching intervention", row["coaching"]),
            detail("Stage status, failures, provenance and exact input-delivery checks", {"status": row["stage_status"], "failures": row["failures"],
                "reviewer_provenance": row["reviewer_provenance"], "feedback": row["post_review_delivery_checks"], "images": row["directed_image_checks"],
                "C_return_delivery": row["C_return_delivery"]})]
        body += own_record(row, "A") + own_record(row, "B")
        body += ["<h3>After Codex feedback and A/B logs: C return image</h3>", figure(row["images"]["C"], row["C_return_route"])]
        body += own_record(row, "C") + ["</section>"]
    body += ["<section><h2>Limits and budget</h2>", detail("Interpretation limits", data["limitations"]),
        detail("Budget from canonical coordinator report", data["coordinator_report"].get("budget")), "</section>"]
    return "\n".join(body)


def render(out=DEFAULT_OUT, **kwargs):
    data = build(out, **kwargs)
    destination = safe(data["destination"])
    files = {"REPORT.md": md_report(data), "GALLERY.html": gallery(data),
             "EXACT_SYSTEM_PROMPTS_BY_STAGE.txt": prompt_export(data),
             "EVIDENCE.json": json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"}
    # Finish every source validation/render before any derived write.
    if any(sha(safe(path).read_bytes()) != checksum for path, checksum in data["source_files_sha256"].items()):
        raise ValueError("Source evidence changed while rendering; no derived output was written")
    destination.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path, temporary = safe(destination / name), safe(destination / ("." + name + ".tmp"))
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    return {"destination": str(destination), "files": list(files), "rounds_finished": data["rounds_finished"],
            "terminal": data["terminal"], "source_evidence_modified": False, "paid_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    print(canonical(render(args.out, destination=args.destination, case_id=args.case_id, allow_incomplete=args.allow_incomplete)))
