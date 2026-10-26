"""Explicit commands and persistent host, bound only to restart04."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from experiments.triad_emergent_v9.coordinator import canonical, read_json
from .coordinator import Coordinator, REPAIR


def command(host, value):
    action = value["command"]
    if action == "status":
        return host.status()
    if action == "preflight":
        return host.preflight()
    if action == "advance":
        return host.advance(value.get("through_round"))
    if action == "dispatch":
        return host.dispatch(value["job_id"], value["reviewer_agent"])
    if action == "ingest":
        return host.ingest(read_json(value["response_path"]))
    if action == "reviewer-fault":
        return host.reviewer_fault(value["proof_path"])
    if action == "shadows":
        return host.shadows()
    if action == "verify":
        return host.verify()
    if action == "report":
        return host.report()
    if action == "render":
        return host.render(allow_incomplete=value.get("allow_incomplete", False))
    if action == "export":
        return host.export(value["target"])
    if action == "export-visual":
        from .analysis import export_visual
        return export_visual(host, value["target"])
    if action == "quit":
        return {"status": "quitting"}
    raise ValueError("Unknown explicit restart04 host command")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "preflight", "advance", "serve", "dispatch", "ingest", "reviewer-fault",
                                           "shadows", "verify", "report", "render", "export", "export-visual"])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--through-round", type=int)
    parser.add_argument("--job-id")
    parser.add_argument("--reviewer-agent")
    parser.add_argument("--response", type=Path)
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args(argv)
    host = Coordinator(args.out, config_path=args.config)
    if args.command == "serve":
        print(canonical({"status": "ready", "revision": REPAIR, "live_rounds": 12,
                         "new_allowance": False, "technical_restart_policy": "whole_current_round_from_prior_completed_checkpoint"}), flush=True)
        for line in sys.stdin:
            try:
                value = json.loads(line)
                result = command(host, value)
            except Exception as error:
                # Arbitrary messages may contain provider bodies or secrets.
                print(canonical({"status": "host_error", "exception_class": type(error).__name__,
                                 "reason": "Safe hold; inspect immutable evidence before the next explicit command"}), flush=True)
                continue
            print(canonical(result), flush=True)
            if value["command"] == "quit":
                return 0
        return 0
    value = {"command": args.command, "allow_incomplete": args.allow_incomplete}
    for key in ("through_round", "job_id", "reviewer_agent", "target"):
        item = getattr(args, key)
        if item is not None:
            value[key] = str(item) if key == "target" else item
    if args.response is not None:
        value["response_path"] = str(args.response)
    if args.proof is not None:
        value["proof_path"] = str(args.proof)
    try:
        print(canonical(command(host, value)), flush=True)
        return 0
    except Exception as error:
        print(canonical({"status": "host_error", "exception_class": type(error).__name__,
                         "reason": "Safe hold; no implicit redispatch or allowance reset"}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
