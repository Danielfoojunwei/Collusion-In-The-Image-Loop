"""Offline initialization and a persistent single-producer JSON-line host loop."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .coordinator import TriadCoordinator, DEFAULT_OUT, CALIBRATION_OUT, canonical, read_json


def command(coordinator, value):
    action = value.get("command")
    if action in {"init", "initialize"}:
        return coordinator.init()
    if action == "advance":
        return coordinator.advance(through_round=value.get("through_round"))
    if action == "status":
        return coordinator.status()
    if action == "budget":
        return coordinator.budget()
    if action == "dispatch":
        return coordinator.mark_review_dispatched(value["job_id"], value["reviewer_agent"])
    if action == "ingest":
        response = read_json(value["response_path"]) if "response_path" in value else value
        return coordinator.ingest(response["job_id"], response["output"], response["provenance"],
                                  raw_answer=response.get("raw_answer"))
    if action == "verify":
        return coordinator.verify()
    if action == "report":
        report = coordinator.report()
        return {"status": "report_written", "rounds": len(report["rounds"]),
                "path": str(coordinator.out / "analysis/report.json")}
    if action == "export":
        return coordinator.export(value.get("target"))
    if action in {"quit", "exit"}:
        return {"status": "quitting"}
    raise ValueError("Unknown host command")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "status", "budget", "advance", "serve", "dispatch", "ingest", "verify", "report", "export"])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--technical-validation", action="store_true",
                        help="Actual live two-round technical validation in its fixed separate namespace/output")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--case-limit", type=int, default=1)
    parser.add_argument("--job-id")
    parser.add_argument("--reviewer-agent")
    parser.add_argument("--response", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--through-round", type=int)
    args = parser.parse_args(argv)
    coordinator = TriadCoordinator(args.out, config_path=args.config, case_limit=args.case_limit,
                                   technical_validation=args.technical_validation)
    if args.command == "serve":
        print(canonical({"status": "ready", "case_limit": coordinator.case_limit,
                         "execution_phase": coordinator.execution_phase,
                         "round_limit": coordinator.execution_round_limit,
                         "scope": "Prospective rounds9–12 continuation; first8 retained under original rules"}), flush=True)
        for line in sys.stdin:
            try:
                value = json.loads(line)
                result = command(coordinator, value)
            except Exception as error:
                print(canonical({"status": "host_error", "exception_class": type(error).__name__,
                                 "reason": "Inspect immutable local evidence; no automatic retry"}), flush=True)
                continue
            print(canonical(result), flush=True)
            if value.get("command") in {"quit", "exit"}:
                return 0
        return 0
    value = {"command": args.command}
    if args.job_id:
        value["job_id"] = args.job_id
    if args.reviewer_agent:
        value["reviewer_agent"] = args.reviewer_agent
    if args.response:
        value["response_path"] = str(args.response)
    if args.target:
        value["target"] = str(args.target)
    if args.through_round is not None:
        value["through_round"] = args.through_round
    try:
        print(canonical(command(coordinator, value)), flush=True)
    except Exception as error:
        print(canonical({"status": "host_error", "exception_class": type(error).__name__,
                         "reason": "Inspect immutable local evidence; no automatic retry"}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
