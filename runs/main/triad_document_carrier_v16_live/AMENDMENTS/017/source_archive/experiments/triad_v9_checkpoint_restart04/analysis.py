"""Read-only committed-checkpoint analysis with separate technical-attempt accounting.

Original observations remain in place. Failed and active attempts never become
canonical completed rounds; all their evidence and funded costs remain archived.
No model, provider or reviewer call is made by this module.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from experiments.triad_emergent_v9.analysis import (
    AnalysisError, Reader, ROOT, _action, _decision, _provider_sources,
    _request_images, _route, canonical, condition_limits, fenced, gallery as _gallery,
    image, markdown as _markdown, safe, sha, system_prompts,
)
from experiments.triad_emergent_v9.contracts import consensus, task_oracle
from experiments.triad_emergent_v9.engine import STAGES, LOG_STAGE, VISUAL_STAGE, VOTE_STAGE, REVISION

RESTART_LIMITS = [
    "A technical fault abandons the entire current round attempt and restarts from the previous completed-round checkpoint; raw failed attempts and all charges or unknown reserves remain in the audit trail.",
    "Retries are technical attempts of the same dependent round, not independent cases. Completed-round results are conditional on technical completion; failure frequency may depend on the complexity of generated images.",
    "Valid refusals, wrong choices, unsupported model-authored native arguments and valid imperfect image content remain observed behavior and never trigger a semantic success-dependent restart.",
    "A saved request proves retained request bytes, not provider receipt or model processing when a provider response is missing.",
]


def _selected_root(out, store, number):
    if number <= store.legacy_rounds:
        return out
    selector = store.committed_selector(number)
    if not isinstance(selector, dict) or not selector.get("working_root"):
        raise AnalysisError("A completed round has no immutable attempt selector")
    root = safe(selector["working_root"])
    if not root.is_relative_to(out):
        raise AnalysisError("Committed attempt escaped the original output root")
    return root


def _selected_provider_sources(reader, out, destination, store, committed):
    """Keep attempt evidence distinct even when logical stage IDs are equal."""
    selected = {number: _selected_root(out, store, number) for number in range(1, committed + 1)}
    roots = {out, *selected.values()}
    roots.update(safe(path.parent) for path in (out / "attempts").glob("r*/a*/provider_evidence"))
    roots.update(safe(path.parent) for path in (out / "shadow_attempts").glob("a*/provider_evidence"))
    generators, calls = [], []
    omitted = {"image_calls": 0, "native_calls": 0, "invalid_transport_metadata_files": 0}
    for root in sorted(roots):
        if root not in set(selected.values()):
            # Failed infrastructure artifacts can intentionally be malformed.
            # Retain/hash their bytes without demanding a scientific raster.
            for path in sorted((root / "provider_evidence/image_calls").glob("*.json")):
                reader.bytes(path)
                omitted["image_calls"] += 1
            for path in sorted((root / "provider_evidence/transport_requests").glob("*.json")):
                raw = reader.bytes(path)
                try:
                    row = json.loads(raw)
                except (ValueError, UnicodeError):
                    omitted["invalid_transport_metadata_files"] += 1
                    continue
                if isinstance(row, dict) and row.get("endpoint") == "/chat/completions":
                    omitted["native_calls"] += 1
            continue
        gs, cs = _provider_sources(reader, root, destination)
        for source, target, key in ((gs, generators, "image_calls"), (cs, calls, "native_calls")):
            for row in source:
                identity = row.get("main_identity")
                if identity and selected.get(identity[0]) == root:
                    row["evidence_root"] = str(root)
                    target.append(row)
                else:
                    omitted[key] += 1
    return generators, calls, omitted


def _attempt_inventory(store, budget=None):
    inventory = store.inventory()
    if isinstance(inventory, dict):
        attempts = inventory.get("attempts", [])
    elif isinstance(inventory, list):
        attempts = inventory
    else:
        raise AnalysisError("Attempt inventory is unavailable")
    if budget and budget.get("aggregate_path"):
        aggregate = safe(budget["aggregate_path"])
        with sqlite3.connect(aggregate.as_uri() + "?mode=ro", uri=True) as db:
            funded = db.execute("SELECT request_id,status,reserved_units,actual_units FROM requests ORDER BY request_id").fetchall()
        for attempt in attempts:
            prefix = (f"triad_emergent_v9:episode.r{attempt['round']:02d}." if attempt.get("legacy")
                      else "triad_emergent_v9:" + attempt["attempt_id"] + ":")
            rows = [row for row in funded if row[0].startswith(prefix)]
            known = sum(row[3] for row in rows if row[3] is not None)
            unresolved = sum(row[2] for row in rows if row[1] in {"pending", "uncertain"})
            attempt["funding"] = {"request_id_prefix": prefix, "request_count": len(rows),
                "request_status_counts": dict(Counter(row[1] for row in rows)),
                "known_actual_usd": known / 1_000_000_000,
                "unknown_or_pending_reserve_usd": unresolved / 1_000_000_000,
                "committed_usd": (known + unresolved) / 1_000_000_000,
                "actual_cost_unknown_for_requests": [row[0] for row in rows if row[3] is None],
                "unknown_charges_asserted_zero": False,
                "included_in_original_aggregate_not_additional_allowance": True}
    counts = Counter(row.get("status", "unknown") for row in attempts)
    return {"policy": "whole_round_from_previous_committed_checkpoint", "maximum_technical_attempts_per_round": 3,
            "records": attempts, "status_counts": dict(counts), "attempt_records": len(attempts),
            "failed_attempts_excluded_from_canonical_memory": True,
            "failed_attempts_and_costs_retained": True,
            "not_independent_replications": True}


def build_report(host, verification):
    """Return source-verifiable committed observations without writing originals."""
    engine, store = host._engine(), host._store()
    committed = store.committed_rounds()
    rows = [row for row in engine.report_rows() if row["round"] <= committed]
    if len(rows) != committed or any(not row.get("complete") for row in rows):
        raise AnalysisError("A committed round is missing its full eight-stage result")
    return {"revision": REVISION, "artifact_revision": "checkpoint_restart04_report_v1",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "interpretation": "One explicitly induced, incentive-conditioned dependent trajectory; no guaranteed collusion, causal incentive effect, spontaneous emergence, or detector accuracy.",
            "condition": host.config.get("condition"), "verification": verification,
            "budget": verification.get("budget"), "summary": engine.summary(), "rounds": rows,
            "committed_rounds": committed, "technical_attempts": _attempt_inventory(store, verification.get("budget")),
            "limitations": RESTART_LIMITS, "provider_raw_reasoning_exported": False}


def _selected_shadows(reader, out):
    selector_path = out / "shadow_checkpoints_restart04/COMMIT.json"
    selector = reader.json(selector_path, optional=True)
    if selector is None:
        if (out / "shadows/report.json").exists():
            raise AnalysisError("A published shadow report has no immutable workflow selector")
        return None, None, None
    body = {key: value for key, value in selector.items() if key != "record_hash"}
    if sha(canonical(body).encode()) != selector.get("record_hash"):
        raise AnalysisError("Selected shadow workflow digest changed")
    root, report_path = safe(selector["working_root"]), safe(selector["report_path"])
    if (not root.is_relative_to(out / "shadow_attempts") or not report_path.is_relative_to(root)
            or selector.get("attempt_id") != "shadows." + root.name):
        raise AnalysisError("Selected shadow workspace or identity changed")
    for relative, expected in selector.get("evidence_files", {}).items():
        path = safe(out / relative)
        if Path(relative).is_absolute() or ".." in Path(relative).parts or not path.is_relative_to(root):
            raise AnalysisError("Selected shadow evidence escaped its workflow")
        if sha(reader.bytes(path)) != expected:
            raise AnalysisError("Selected shadow workflow evidence changed")
    report = reader.json(report_path)
    if reader.hashes[str(report_path)] != selector.get("report_sha256"):
        raise AnalysisError("Selected shadow publication changed")
    published = reader.json(out / "shadows/report.json", optional=True)
    if published is not None and published != report:
        raise AnalysisError("The global shadow publication differs from its selected workflow")
    if report.get("selected_technical_attempt") != selector.get("attempt_id"):
        raise AnalysisError("Published shadow attempt identity changed")
    return root, report, selector


def build(host, *, destination=None, allow_incomplete=False):
    out = safe(host.out)
    engine, store = host._engine(), host._store()
    committed = store.committed_rounds()
    destination = safe(destination or out / "analysis/human_readable")
    reader = Reader()
    if getattr(store, "manifest_path", None):
        reader.json(store.manifest_path)
    config, plan = reader.json(out / "config.json"), reader.json(out / "plan.json")
    freeze = reader.json(out / "freeze.json")
    report = reader.json(out / "analysis/report.json")
    if plan.get("revision") != REVISION or len(plan.get("rounds", [])) != 12:
        raise AnalysisError("A declared v9 twelve-round plan is required")
    if freeze.get("config_sha256") != reader.hashes[str(out / "config.json")] or freeze.get("plan_sha256") != reader.hashes[str(out / "plan.json")]:
        raise AnalysisError("Frozen task plan or config changed")
    frozen = {}
    for relative, expected in freeze.get("source_files", {}).items():
        path = safe(ROOT / relative)
        if not path.is_relative_to(ROOT):
            raise AnalysisError("Frozen runtime dependency escaped its workspace")
        frozen[relative] = sha(reader.bytes(path)) == expected
    if not frozen or not all(frozen.values()):
        raise AnalysisError("Frozen runtime closure is absent or changed")
    manifest = reader.json(out / "episode/engine_manifest.json")
    if manifest.get("plan") != plan or manifest.get("config") != config:
        raise AnalysisError("Episode manifest differs from the declared fresh study")
    for metadata in _attempt_inventory(store)["records"]:
        for key in ("marker_path", "abandonment_path", "selector_path"):
            if metadata.get(key):
                path = safe(metadata[key])
                if not path.is_relative_to(out):
                    raise AnalysisError("Attempt metadata escaped the output root")
                reader.bytes(path)
    source_rows = {row["round"]: row for row in report.get("rounds", [])}
    if len(source_rows) != len(report.get("rounds", [])):
        raise AnalysisError("Canonical report duplicates a round")
    generators, native_calls, nonselected_provider_counts = _selected_provider_sources(reader, out, destination, store, committed)
    rounds, prompts, all_records = [], [], []
    previous_hash, gap = "0" * 64, False
    requests_by_round = {}
    for planned in plan["rounds"]:
        number = planned["round"]
        folder = engine._path(number, STAGES[0], "request").parent if number <= committed else None
        records, request_meta = {}, {}
        for stage in STAGES:
            record = reader.json(folder / (stage + ".outcome.json"), optional=True) if folder else None
            request = reader.json(folder / (stage + ".request.json"), optional=True) if folder else None
            if record is None:
                gap = True
            else:
                body = {k: v for k, v in record.items() if k != "record_hash"}
                if (gap or body.get("round") != number or body.get("stage") != stage
                        or body.get("previous_hash") != previous_hash or sha(canonical(body).encode()) != record.get("record_hash")):
                    raise AnalysisError("A retained stage chain is altered or has a gap")
                previous_hash = record["record_hash"]
                records[stage] = record
                all_records.append(record)
                if request is None:
                    raise AnalysisError("A terminal stage lost its exact pre-turn request")
            if request is None:
                continue
            if stage == "REVIEWER":
                job = request["job"]
                packet = reader.json(job["packet_path"])
                if packet != request["packet"] or reader.hashes[str(safe(job["packet_path"]))] != job["packet_sha256"]:
                    raise AnalysisError("Review packet source binding changed")
                text = reader.bytes(job["instructions_path"]).decode("utf-8")
                prompts.append({"round": number, "stage": stage, "system_messages": [text],
                                "schema": reader.json(job["schema_path"]), "source_file": str(folder / (stage + ".request.json")),
                                "packet_sha256": job["packet_sha256"], "listed_image_count": len(packet["images"]),
                                "native_calls": []})
                request_meta[stage] = {"packet": packet, "job": job}
            else:
                body = {k: v for k, v in request.items() if k != "request_hash"}
                if sha(canonical(body).encode()) != request.get("request_hash"):
                    raise AnalysisError("Original actor request digest changed")
                if record and (record.get("request_hash") != request["request_hash"]
                        or record.get("request_file_sha256") != reader.hashes[str(folder / (stage + ".request.json"))]):
                    raise AnalysisError("Stage does not bind its exact original actor request")
                system = [m["content"] for m in request["messages"] if m.get("role") == "system"]
                refs = _request_images(reader, request, out, destination)
                view = json.loads(request["messages"][1]["content"])
                request_meta[stage] = {"image_refs": refs, "view": view, "request_hash": request["request_hash"],
                                       "source_file": str(folder / (stage + ".request.json"))}
                prompts.append({"round": number, "stage": stage, "role": request["role"], "mode": request["mode"],
                    "system_messages": system, "source_file": str(folder / (stage + ".request.json")),
                    "request_hash": request["request_hash"], "input_image_handles": refs,
                    "native_calls": [c for c in native_calls if c["main_identity"] == (number, stage)]})
        requests_by_round[number] = request_meta
        ballots = {role: _decision(records.get(VOTE_STAGE[role])) for role in "ABC"}
        correct = task_oracle(planned["task"])
        if planned["oracle"] != correct:
            raise AnalysisError("Planned oracle differs from authoritative facts")
        if number in source_rows and (source_rows[number]["ballots"] != ballots or source_rows[number]["oracle"] != correct):
            raise AnalysisError("Canonical report differs from the original ballot observations")
        agreement = consensus(ballots)
        outcome = ("incomplete" if agreement["status"] == "incomplete" else "disagreement"
                   if agreement["status"] == "disagreement" else "unanimous_correct"
                   if agreement["decision"] == correct else "unanimous_wrong")
        accepted = {role: image(reader, records.get(VISUAL_STAGE[role], {}).get("output", {}).get("image"), out, destination)
                    if records.get(VISUAL_STAGE[role], {}).get("component_status", {}).get("image") == "delivered" else None
                    for role in "ABC"}
        actions = {stage: [_action(reader, a, out, destination) for a in record.get("actions", [])]
                   for stage, record in records.items() if stage != "REVIEWER"}
        logs = {}
        for role in "ABC":
            record = records.get(LOG_STAGE[role])
            valid = bool(record and record.get("component_status", {}).get("log") == "valid")
            logs[role] = {"status": (record or {}).get("component_status", {}).get("log", "not_reached"),
                          "available": valid, "carryover_note": record["output"]["carryover_note"] if valid else None,
                          "image_reflection": record["output"]["image_reflection"] if valid else None}
        review_record = records.get("REVIEWER")
        review = {"status": (review_record or {}).get("status", "not_reached"),
                  "reported_output": (review_record or {}).get("output"),
                  "valid_report": review_record["output"] if review_record and review_record["status"] == "valid" else None,
                  "raw_first_final": (review_record or {}).get("raw_answer"),
                  "provenance": (review_record or {}).get("provenance")}
        matching = []
        for stage in ("POSTVERDICT_A_LOG", "POSTVERDICT_B_LOG", "POSTVERDICT_C_IMAGE", "POSTVERDICT_C_LOG"):
            if stage in request_meta:
                expected = {"status": review["status"], "report": review["reported_output"]}
                same = request_meta[stage]["view"].get("reviewer_report") == expected
                if not same:
                    raise AnalysisError("A post-review stage did not receive the full actual report")
                matching.append({"stage": stage, "full_actual_report_matches": True})
        rounds.append({"round": number, "task": planned["task"], "oracle": correct,
            "private_preference_researcher_only": planned["private_preference"], "ballots": ballots,
            "policy_correctness": {r: None if ballots[r] is None else ballots[r] == correct for r in "ABC"},
            "team_outcome": outcome, "consensus": agreement,
            "private_preference_adopted": None if agreement["status"] == "incomplete" else agreement["decision"] == planned["private_preference"],
            "stage_status": {s: records[s]["status"] if s in records else "not_reached" for s in STAGES},
            "component_status": {s: records[s].get("component_status") if s in records else None for s in STAGES},
            "images": accepted, "actions": actions, "private_logs": logs, "reviewer": review,
            "scores": source_rows.get(number, {}).get("scores"), "post_review_input_checks": matching,
            "complete": "POSTVERDICT_C_LOG" in records,
            "actual_image_generator_calls": [g for g in generators if g["main_identity"] and g["main_identity"][0] == number]})
    complete = sum(r["complete"] for r in rounds)
    if complete != committed or len(all_records) != 8 * committed:
        raise AnalysisError("An immutable committed checkpoint lacks its complete eight-stage evidence")
    if not allow_incomplete and (complete != 12 or len(all_records) != 96 or len(source_rows) != 12):
        raise AnalysisError("Refusing a final report before twelve rounds and96 terminal stages; use allow_incomplete only for explicit status rendering")
    for row in rounds:
        n, current = row["round"], requests_by_round[row["round"]]
        row["directed_image_checks"] = [
            {"route": "A_to_B", **_route(current.get("B_IMAGE_AND_VOTE"), "current_A", row["images"]["A"])},
            {"route": "A_to_C", **_route(current.get("C_VOTE"), "current_A", row["images"]["A"])},
            {"route": "B_to_C", **_route(current.get("C_VOTE"), "current_B", row["images"]["B"])}]
        row["C_return_delivery"] = ({"status": "archive_only", "next_A_exists": False}
            if n == 12 else _route(requests_by_round.get(n + 1, {}).get("A_IMAGE_AND_VOTE"), "previous_C", row["images"]["C"]))
    shadow_root, shadow_report, shadow_selector = _selected_shadows(reader, out)
    if shadow_selector is not None and committed != 12:
        raise AnalysisError("A shadow workflow was published before the main twelve-round checkpoint")
    shadow_rows = []
    for n in config.get("shadows", {}).get("rounds", [2, 4, 6, 8, 10, 12]):
        for family, role in (("Aneutral", "B"), ("Aneutral", "C"), ("Bneutral", "C"), ("Cprevneutral", "A")):
            path = ((shadow_root / "shadows") if shadow_root else (out / "shadow_checkpoints_restart04/unstarted")) / family / f"r{n:02d}" / (role + ".outcome.json")
            record = reader.json(path, optional=True)
            if shadow_selector is not None:
                if record is None:
                    raise AnalysisError("A selected complete shadow workflow lost an endpoint")
                if shadow_selector.get("evidence_files", {}).get(str(path.relative_to(out))) != reader.hashes[str(safe(path))]:
                    raise AnalysisError("A shadow endpoint is absent from its immutable workflow inventory")
            if record:
                body = {k: v for k, v in record.items() if k != "record_hash"}
                if sha(canonical(body).encode()) != record.get("record_hash"):
                    raise AnalysisError("A shadow outcome hash changed")
            result = (record or {}).get("result") or {}
            shadow_rows.append({"family": family, "round": n, "role": role,
                "status": (record or {}).get("status", "not_reached"), "reason": (record or {}).get("reason"),
                "source_file": str(path) if record else None, "source_request": (record or {}).get("source_request"),
                "substitution_proof": (record or {}).get("substitution_proof"), "decision": _decision(result),
                "component_status": result.get("component_status"),
                "accepted_image": image(reader, result.get("output", {}).get("image"), out, destination),
                "actions": [_action(reader, a, out, destination) for a in result.get("actions", [])]})
    return {"revision": REVISION, "artifact_revision": "v9_checkpoint_restart04_chronology_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "out": str(out), "destination": str(destination),
        "condition": plan["condition"], "main_rounds_complete": complete, "main_rounds_planned": 12,
        "terminal_main_stages": len(all_records), "main_stage_status_counts": dict(Counter(r["status"] for r in all_records)),
        "main_component_status_counts": dict(Counter(v for r in all_records for v in r.get("component_status", {}).values() if v != "not_applicable")),
        "main_accepted_raster_slots": sum(bool(p) for r in rounds for p in r["images"].values()),
        "main_peer_raster_slots": sum(bool(p) for r in rounds for role, p in r["images"].items() if not (r["round"] == 12 and role == "C")),
        "confirmed_peer_receptions": sum(c["status"] == "received" for r in rounds for c in r["directed_image_checks"] + [r["C_return_delivery"]]),
        "main_completed_image_receipts": sum(g["scope"] == "main" for g in generators),
        "main_recorded_generated_drafts": sum(bool(a.get("generated_image")) for r in rounds for acts in r["actions"].values() for a in acts),
        "stage_chain_verified": True, "frozen_runtime_checks": frozen, "budget": report.get("budget"),
        "rounds": rounds, "exact_stage_prompts": prompts, "shadows": {"report": shadow_report, "rows": shadow_rows, "selected_workflow": shadow_selector,
            "planned_extra_actor_turns": 24, "included_in_main_counts": False}, "limitations": [*condition_limits(plan["condition"]), *RESTART_LIMITS],
        "provider_raw_reasoning_exported": False, "provider_calls_by_renderer": 0,
        "technical_attempts": report.get("technical_attempts"),
        "nonselected_provider_evidence_counts": nonselected_provider_counts,
        "canonical_rounds_are_committed_only": True,
        "source_files_sha256": dict(reader.hashes), "_reader": reader}


def markdown(data):
    text = _markdown(data)
    note = ["## Technical attempt accounting", "",
            "Only rounds with an immutable completed-round checkpoint appear as canonical observations below. An abandoned attempt may contain valid actor actions or a real reviewer report; those remain archived and never enter a restarted attempt's memory. All known costs and unresolved reserves remain in the original aggregate.", "",
            fenced(data.get("technical_attempts")), "",
            "Provider evidence outside selected committed attempts (including the current attempt, technical failures and separate shadow calls):",
            fenced(data.get("nonselected_provider_evidence_counts")), ""]
    return text.replace("## Main-round overview", "\n".join(note) + "\n## Main-round overview", 1)


def gallery(data):
    text = _gallery(data)
    section = ('<section id="technical-attempts"><h2>Technical attempts and checkpoints</h2>'
               '<p>Only immutable completed-round checkpoints enter the main chronology. Failed and active attempts remain separate; all costs and unknown reserves remain counted. A saved request without a response is not proof of provider reception.</p><pre>'
               + html.escape(json.dumps(data.get("technical_attempts"), ensure_ascii=False, indent=2))
               + '</pre></section>')
    return text.replace("</body></html>", section + "</body></html>")


def render(host, *, destination=None, allow_incomplete=False):
    data = build(host, destination=destination, allow_incomplete=allow_incomplete)
    reader = data.pop("_reader")
    files = {"REPORT.md": markdown(data), "GALLERY.html": gallery(data),
             "EXACT_SYSTEM_PROMPTS_BY_STAGE.txt": system_prompts(data),
             "EVIDENCE.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n"}
    if not reader.verify():
        raise AnalysisError("A source changed during rendering; no derived output was written")
    destination = safe(data["destination"])
    destination.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = safe(destination / name)
        fd, temporary = tempfile.mkstemp(prefix=".restart04-render-", dir=destination)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return {"status": "rendered", "destination": str(destination), "files": list(files),
            "main_rounds_complete": data["main_rounds_complete"], "terminal_main_stages": data["terminal_main_stages"],
            "shadows_report_available": data["shadows"]["report"] is not None,
            "technical_attempts": data["technical_attempts"],
            "canonical_rounds_are_committed_only": True,
            "provider_calls": 0, "source_evidence_modified": False, "provider_raw_reasoning_exported": False}


def _final_checks(host):
    verification = host.verify()
    if host._store().committed_rounds() != 12 or host._engine().next_stage() is not None:
        raise AnalysisError("Refusing to label an unfinished live trajectory a final archive")
    reader = Reader()
    shadow_root, controls, selector = _selected_shadows(reader, safe(host.out))
    if controls is None:
        raise AnalysisError("The fixed controls must finish before the final archive")
    for number in (2, 4, 6, 8, 10, 12):
        for family, role in (("Aneutral", "B"), ("Aneutral", "C"), ("Bneutral", "C"), ("Cprevneutral", "A")):
            path = shadow_root / "shadows" / family / f"r{number:02d}" / (role + ".outcome.json")
            endpoint = reader.json(path)
            body = {key: value for key, value in endpoint.items() if key != "record_hash"}
            if (endpoint.get("family") != family or endpoint.get("round") != number or endpoint.get("role") != role
                    or endpoint.get("status") not in {"observed", "unavailable"}
                    or sha(canonical(body).encode()) != endpoint.get("record_hash")
                    or selector.get("evidence_files", {}).get(str(path.relative_to(host.out))) != reader.hashes[str(safe(path))]):
                raise AnalysisError("A fixed shadow endpoint is missing or differs from its immutable selected workflow")
    if (controls.get("status") != "complete" or controls.get("planned_shadow_turns") != 24
            or controls.get("observed_shadow_turns", 0) + controls.get("unavailable_shadow_turns", 0) != 24
            or controls.get("main_preservation_verified") is not True):
        raise AnalysisError("Final export requires all fixed control endpoints and main preservation")
    return verification


def _file_sha(path):
    digest = hashlib.sha256()
    with safe(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _archive_target(host, target):
    out, target = safe(host.out), safe(target)
    if target.is_relative_to(out):
        raise AnalysisError("An export archive must be outside the evidence root")
    if target.exists():
        raise AnalysisError("Preserve an existing archive")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _snapshot_budget(host):
    """Use SQLite's consistent backup; never alter the original aggregate."""
    aggregate = safe(ROOT / "runs/main/budget.sqlite3")
    folder = safe(host.out / "analysis")
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".budget-restart04-", suffix=".sqlite3", dir=folder)
    os.close(fd)
    temporary = safe(name)
    try:
        with sqlite3.connect(aggregate.as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(temporary) as destination:
                original.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise AnalysisError("Aggregate snapshot failed verification")
        raw = temporary.read_bytes()
        snapshot = safe(folder / ("aggregate_budget_snapshot_restart04_" + sha(raw) + ".sqlite3"))
        try:
            os.link(temporary, snapshot)
        except FileExistsError:
            if snapshot.read_bytes() != raw:
                raise AnalysisError("An immutable aggregate snapshot differs") from None
        return snapshot
    finally:
        temporary.unlink(missing_ok=True)


def export(host, target):
    """Private reproducibility archive, including every abandoned attempt."""
    _final_checks(host)
    target = _archive_target(host, target)
    snapshot = _snapshot_budget(host)
    freeze = json.loads(safe(host.out / "freeze.json").read_text())
    sources = []
    for relative, expected in freeze["source_files"].items():
        path = safe(ROOT / relative)
        if not path.is_relative_to(ROOT) or sha(path.read_bytes()) != expected:
            raise AnalysisError("Frozen reproduction source changed")
        sources.append((path, "reproduction_workspace/" + relative))
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(safe(host.out).rglob("*")):
            safe(path)
            if path.is_file() and path.name != "producer.lock" and not any(part.startswith(".") for part in path.relative_to(host.out).parts):
                archive.write(path, str(path.relative_to(host.out)))
        for path, member in sources:
            archive.write(path, member)
    return {"path": str(target), "bytes": target.stat().st_size, "sha256": _file_sha(target),
            "budget_snapshot": str(snapshot), "private_researcher_archive": True,
            "abandoned_attempts_included": True,
            "contains_private_actor_provider_evidence": True}


def export_visual(host, target):
    """Portable original-image chronology; excludes raw provider responses."""
    _final_checks(host)
    target = _archive_target(host, target)
    data = build(host, allow_incomplete=False)
    reader = data.pop("_reader")
    destination = safe(data["destination"])
    members = {}
    for row in data["rounds"]:
        for png in row["images"].values():
            if png:
                members[png["path"]] = png["sha256"]
        for actions in row["actions"].values():
            for action in actions:
                if action.get("generated_image"):
                    png = action["generated_image"]
                    members[png["path"]] = png["sha256"]
        for call in row["actual_image_generator_calls"]:
            png = call["image"]
            members[png["path"]] = png["sha256"]
    for row in data["shadows"]["rows"]:
        if row["accepted_image"]:
            png = row["accepted_image"]
            members[png["path"]] = png["sha256"]
        for action in row["actions"]:
            if action.get("generated_image"):
                png = action["generated_image"]
                members[png["path"]] = png["sha256"]
    texts = {"REPORT.md": markdown(data), "GALLERY.html": gallery(data),
             "EXACT_SYSTEM_PROMPTS_BY_STAGE.txt": system_prompts(data),
             "EVIDENCE.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n"}
    if not reader.verify():
        raise AnalysisError("An evidence source changed during portable export")
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in texts.items():
            archive.writestr(str((destination / name).relative_to(host.out)), text)
        for filename, expected in sorted(members.items()):
            path = safe(filename)
            raw = path.read_bytes()
            if not path.is_relative_to(host.out) or sha(raw) != expected:
                raise AnalysisError("A portable raster changed")
            archive.writestr(str(path.relative_to(host.out)), raw)
        archive.writestr("README.txt", "Open analysis/human_readable/GALLERY.html. Original raster bytes are preserved. Reports include concise private actor self-reports for researcher review; raw provider responses and hidden reasoning are excluded. Failed-attempt inventory and total budget remain reported separately from the committed chronology.\n")
    return {"path": str(target), "bytes": target.stat().st_size, "sha256": _file_sha(target),
            "private_researcher_visual_archive": True, "provider_raw_reasoning_exported": False,
            "original_raster_bytes_preserved": True}
