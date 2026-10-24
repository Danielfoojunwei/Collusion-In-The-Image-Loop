"""Explicit offline/read-only/live commands and one persistent JSON-line host."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .coordinator import Coordinator, canonical, read_json


def command(host, value):
    action = value["command"]
    if action == "init":
        return host.init()
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
    if action == "quit":
        return {"status": "quitting"}
    raise ValueError("Unknown explicit host command")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "status", "preflight", "advance", "serve", "dispatch", "ingest", "shadows", "verify", "report", "render", "export"])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--through-round", type=int)
    parser.add_argument("--job-id")
    parser.add_argument("--reviewer-agent")
    parser.add_argument("--response", type=Path)
    parser.add_argument("--target", type=Path)
    args = parser.parse_args(argv)
    host = Coordinator(args.out, config_path=args.config)
    if args.command == "serve":
        print(canonical({"status": "ready", "revision": "triad_emergent_v9", "live_rounds": 12,
                         "private_allowance_created": False}), flush=True)
        for line in sys.stdin:
            try:
                value = json.loads(line)
                result = command(host, value)
            except Exception as error:
                print(canonical({"status": "host_error", "exception_class": type(error).__name__,
                                 "reason": "Inspect local immutable evidence; never automatically redispatch"}), flush=True)
                continue
            print(canonical(result), flush=True)
            if value["command"] == "quit":
                return 0
        return 0
    value = {"command": args.command}
    for key in ("through_round", "job_id", "reviewer_agent", "target"):
        if getattr(args, key) is not None:
            value[key] = str(getattr(args, key)) if key == "target" else getattr(args, key)
    if args.response is not None:
        value["response_path"] = str(args.response)
    try:
        print(canonical(command(host, value)), flush=True)
        return 0
    except Exception as error:
        print(canonical({"status": "host_error", "exception_class": type(error).__name__,
                         "reason": "Inspect local immutable evidence; no automatic retry"}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
