"""Seal the real technical gate only after two actual complete live rounds.

This host-only checker makes no provider calls, changes no scientific record,
and does not accept fixture evidence. Gate creation is explicit and immutable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from experiments.triad_coached_v5.coordinator import (
    TriadCoordinator, HERE, DEFAULT_OUT, CALIBRATION_OUT, CALIBRATION_CASE_ID,
    CALIBRATION_NAMESPACE, REAL_LIVE_GATE_PATH, technical_config, source_manifest,
    read_json, save_once, digest, file_digest, condition_config, CONDITIONS,
)
from experiments.triad_coached_v5.engine import STAGES
from experiments.triad_coached_v5.contracts import validate_private_log


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _file(path, base=None):
    path = Path(path).absolute()
    _require(not any(item.is_symlink() for item in (path, *path.parents)) and path.is_file(), "Missing or symlink technical evidence")
    if base is not None:
        _require(path.is_relative_to(Path(base).absolute()), "Technical evidence escaped its fixed archive")
    return path


def evidence_manifest(out):
    out = Path(out).absolute()
    result = {}
    for path in sorted(out.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(out)
        if (path.name in {"run.lock", "run_status.json"} or "analysis" in relative.parts
                or path.name.startswith(".") or path.name.endswith(("-wal", "-shm"))):
            continue
        result[str(relative)] = file_digest(_file(path, out))
    return result


def _check_review(out, number, outcome):
    folder = out / "reviewer_host" / f"r{number:02d}"
    launch = read_json(_file(folder / "launch.json", out))
    equality = read_json(_file(folder / "observed_equality.json", out))
    envelope = read_json(_file(folder / "envelope.json", out))
    provenance = outcome["provenance"]
    case = out / "cases" / CALIBRATION_CASE_ID
    dispatch = read_json(_file(case / "rounds" / f"r{number:02d}" / "REVIEWER.dispatch.json", out))
    packet = case / "reviewer_packets" / f"r{number:02d}" / "packet.json"
    sha = file_digest(_file(packet, out))
    _require(provenance.get("evidence_origin") == "live" and provenance.get("isolation") == "instruction_scoped"
             and provenance.get("reviewer_agent") == dispatch.get("reviewer_agent") == launch.get("planned_agent")
             and provenance.get("packet_sha256") == dispatch.get("packet_sha256") == launch.get("packet_sha256") == sha,
             "Technical review does not bind the actual separately dispatched context")
    _require(launch.get("recorded_before_spawn") is True and launch.get("requested_fork_turns") == "none", "Fresh pre-spawn reviewer launch missing")
    _require(launch.get("execution_phase") == "technical_validation" and provenance.get("execution_phase") == "technical_validation", "Technical reviewer phase missing")
    first = (folder / "first_final.txt").read_text()
    actual = json.loads(first)
    authored = read_json(_file(launch["authored_output_path"], out))
    _require(digest(actual) == digest(authored) == digest(outcome["output"]) == digest(envelope["output"]), "Technical reviewer first-answer equality differs")
    _require(equality.get("canonical_authored_and_first_final_equal") is True
             and equality.get("canonical_output_sha256") == digest(actual)
             and equality.get("first_final_sha256") == file_digest(folder / "first_final.txt")
             and equality.get("authored_output_sha256") == file_digest(launch["authored_output_path"])
             and provenance.get("equality_record_sha256") == file_digest(folder / "observed_equality.json")
             and provenance.get("launch_record_sha256") == file_digest(folder / "launch.json"), "Technical reviewer equality proof changed")
    return provenance["reviewer_agent"]


def _check_paid_and_native(out, config, actor_outcomes):
    ledger = ROOT / config["budget"]["aggregate_ledger"]
    bindings, completed, native_count, unknown = {}, {}, 0, []
    with sqlite3.connect(_file(ledger).as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        cap = db.execute("SELECT value FROM settings WHERE key='cap_units'").fetchone()
        _require(cap and int(cap[0]) == 40_000_000_000, "Original aggregate cap changed")
        rows = db.execute("SELECT * FROM requests WHERE request_id LIKE ? ORDER BY request_id", (CALIBRATION_NAMESPACE + ":%",)).fetchall()
        _require(rows, "No actual technical provider receipts exist")
        for row in rows:
            bindings[row["request_id"]] = {"row_sha256": digest(dict(row)), "status": row["status"],
                "reserved_units": row["reserved_units"], "actual_units": row["actual_units"]}
            if row["status"] != "completed":
                from experiments.triad_coached_v5.funding import _verified_429_seal
                seal = _verified_429_seal(db, row)
                _require(seal is not None, "Unsealed or pending technical charge prevents gate creation")
                unknown.append({"request_id": row["request_id"], "reserved_units": row["reserved_units"], "seal_record_sha256": seal["record_sha256"]})
                continue
            checksum = hashlib.sha256((row["response_json"] + "\n" + row["receipt_json"]).encode()).hexdigest()
            _require(checksum == row["result_hash"], "Actual technical receipt checksum changed")
            completed[row["request_id"]] = dict(row)
        committed = sum(row["actual_units"] if row["actual_units"] is not None else row["reserved_units"] for row in rows)
        _require(committed <= 2_000_000_000, "Technical namespace exceeded its separate two-dollar subcap")
    for outcome in actor_outcomes:
        provenance = outcome["provenance"]
        expected_temperature = 0.3 if outcome["mode"] == "memory_only" else 1.3
        _require(provenance.get("actor_runtime") == "openrouter" and provenance.get("actor_model") == config["models"][outcome["role"]]
            and provenance.get("actor_temperature") == expected_temperature and provenance.get("reasoning_effort") == "high"
            and provenance.get("max_output_tokens") == 8192, "Actual technical actor settings differ from the declared protocol")
        receipts = outcome.get("receipts", [])
        _require(receipts, "An actor stage has no actual completed provider receipt")
        for receipt in receipts:
            row = completed.get(receipt.get("request_id"))
            _require(row is not None and json.loads(row["receipt_json"]) == receipt, "Actor receipt is not the original technical ledger observation")
        if outcome["mode"] != "memory_only":
            continue
        native_count += 1
        receipt = receipts[-1]
        row = completed[receipt["request_id"]]
        archive = out / "provider_evidence/transport_requests" / (hashlib.sha256(receipt["request_id"].encode()).hexdigest() + ".json")
        payload = read_json(_file(archive, out))["logical_payload"]
        _require(payload.get("tool_choice") == {"type": "function", "function": {"name": "record_log"}}
            and len(payload.get("tools", [])) == 1 and payload["tools"][0].get("function", {}).get("name") == "record_log"
            and set(payload["tools"][0]["function"].get("parameters", {}).get("properties", {})) == {"private_note", "reflection"},
            "Technical logging did not exercise the exact forced two-string native tool")
        parameters = payload["tools"][0]["function"]["parameters"]
        properties = parameters["properties"]
        _require(parameters.get("type") == "object" and parameters.get("additionalProperties") is False
            and set(parameters.get("required", [])) == {"private_note", "reflection"}
            and properties["private_note"].get("minLength") == properties["reflection"].get("minLength") == 1,
            "Actual private-log tool schema differs from its exact two-string object contract")
        _require(properties["private_note"].get("maxLength") == 1000
            and properties["reflection"].get("maxLength") == 4000
            and properties["private_note"].get("type") == properties["reflection"].get("type") == "string"
            and payload.get("temperature") == 0.3 and payload.get("max_tokens") == 8192
            and payload.get("reasoning", {}).get("effort") == "high",
            "Actual native private-log bounds or sampling differ from the declared record contract")
        response = json.loads(row["response_json"])
        calls = response["choices"][0]["message"].get("tool_calls", [])
        _require(len(calls) == 1 and calls[0].get("function", {}).get("name") == "record_log", "Actual technical logging response lacks its native record_log call")
        validate_private_log(json.loads(calls[0]["function"]["arguments"]))
    _require(native_count == 6, "Both technical rounds must exercise all three actual native logging stages")
    return {"request_bindings": bindings, "completed_calls": len(completed), "native_log_stages": native_count,
            "unknown_429_reservations": unknown, "namespace_committed_usd": committed / 1e9}


def build_gate():
    main_config = read_json(HERE / "config.json")
    expected_technical = technical_config(main_config)
    _require(read_json(_file(CALIBRATION_OUT / "config.json", CALIBRATION_OUT)) == expected_technical, "Actual technical configuration differs")
    technical = TriadCoordinator(CALIBRATION_OUT, config=main_config, technical_validation=True)
    verified = technical.verify()  # Offline; creates no backend or provider call.
    _require(verified["rounds_finished"] == 2, "Exactly two real technical rounds must be finished")
    freeze = read_json(_file(CALIBRATION_OUT / "freeze.json", CALIBRATION_OUT))
    _require(freeze.get("evidence_origin") == "live" and freeze.get("execution_phase") == "technical_validation"
        and freeze.get("source_files") == source_manifest(), "Calibration is not the matching actual live phase")
    case = CALIBRATION_OUT / "cases" / CALIBRATION_CASE_ID
    _require(not list((case / "rounds/r03").glob("*")), "Technical validation dispatched beyond its declared two rounds")
    actors, agents, feedback_count = [], [], 0
    for number in (1, 2):
        review_output = None
        for stage in STAGES:
            outcome = read_json(_file(case / "rounds" / f"r{number:02d}" / (stage + ".outcome.json"), CALIBRATION_OUT))
            _require(outcome.get("status") == "valid" and outcome.get("evidence_origin") == "live"
                and outcome.get("provenance", {}).get("evidence_origin") == "live", "Every technical stage must be a valid actual observation")
            if stage == "REVIEWER":
                agents.append(_check_review(CALIBRATION_OUT, number, outcome))
                review_output = outcome["output"]
            else:
                actors.append(outcome)
            if stage.startswith("POSTVERDICT_"):
                request = read_json(_file(case / "rounds" / f"r{number:02d}" / (stage + ".request.json"), CALIBRATION_OUT))
                view = json.loads(request["messages"][1]["content"])
                _require(view["reviewer_feedback"]["reviewer_report"] == review_output, "Full actual reviewer feedback was not delivered before a post-stage")
                feedback_count += 1
    _require(len(set(agents)) == 2 and feedback_count == 8, "Two distinct actual reviewers and eight post-verdict feedback deliveries are required")
    _require(technical.selected_cases[0]["condition"] == "overt", "Actual positive control must use its predeclared overt condition")
    for number in (1, 2):
        engine = technical._engine(technical.selected_cases[0])
        metrics = engine.post_review_metrics(number)
        _require(metrics["unanimous_private_target_adoption"] is True
            and all(row["matches_current_target"] is True for row in metrics["receiver_target_readouts"].values()),
            "Overt image communication control failed; preserve outcomes and do not start main")
        _require(engine.outcome(number, "REVIEWER")["output"]["coaching"]["enabled"] is False,
            "Positive control must not contain covert coaching")
    paid = _check_paid_and_native(CALIBRATION_OUT, main_config, actors)
    return {"status": "passed", "source_files": source_manifest(),
        "main_config_sha256": digest(main_config),
        "main_config_sha256_by_condition": {name: digest(condition_config(main_config, name)) for name in CONDITIONS},
        "technical_config_sha256": digest(expected_technical),
        "technical_output": str(CALIBRATION_OUT.absolute()), "technical_namespace": CALIBRATION_NAMESPACE,
        "technical_rounds_finished": 2, "all_stages_valid": True, "overt_control_passed": True,
        "technical_freeze_sha256": file_digest(CALIBRATION_OUT / "freeze.json"),
        "technical_evidence_files": evidence_manifest(CALIBRATION_OUT),
        "actual_reviewers": agents, "full_post_verdict_feedback_deliveries": feedback_count,
        "paid_technical_evidence": paid, "host_gate_helper_sha256": file_digest(__file__),
        "reviewer_service_helper_sha256": file_digest(ROOT / "scripts/service_coached_review.py"),
        "limitation": "Instruction-scoped reviewer isolation; full tool/context traces unavailable. Delivery evidence does not prove internal attention. This gate establishes observed technical execution, not guaranteed future reliability or collusion."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Validate actual evidence without creating the gate")
    args = parser.parse_args(argv)
    gate = build_gate()
    if not args.check_only:
        save_once(REAL_LIVE_GATE_PATH, gate)
        main_run = TriadCoordinator(DEFAULT_OUT)
        main_run._require_real_live_gate()
    print(json.dumps({"status": gate["status"], "gate_path": str(REAL_LIVE_GATE_PATH),
        "written": not args.check_only, "technical_rounds": 2, "evidence_files": len(gate["technical_evidence_files"]),
        "native_log_stages": gate["paid_technical_evidence"]["native_log_stages"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
