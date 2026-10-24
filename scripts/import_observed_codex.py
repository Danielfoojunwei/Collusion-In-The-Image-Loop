"""Import one observed Codex answer with explicit unavailable-trace provenance.

Host utility only: never generates, retries, or edits the reviewer's answer.
"""
import argparse
import json
from pathlib import Path

from visual_collusion.bridge import ingest, validate_packet
from visual_collusion.common import digest, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--raw-response", type=Path, required=True)
    parser.add_argument("--agent", required=True)
    args = parser.parse_args()
    folder = args.out / "review_jobs" / args.job_id
    packet = validate_packet(folder)
    output = json.loads(args.raw_response.read_text())
    destination = args.out / "host_responses" / args.job_id
    destination.mkdir(parents=True, exist_ok=True)
    wrapper_path = (destination / "trace_wrapper.json").resolve()
    wrapper = {
        "origin": "host_export",
        "job_id": args.job_id,
        "packet_sha256": packet["packet_sha256"],
        "output_sha256": digest(output),
        "packet_root": str(folder.resolve()),
        "coverage": "unavailable",
        "raw_trace_path": None,
        "sha256": None,
        "events": [],
        "audit": {
            "status": "unverified",
            "reason": "Actual Codex collaboration agent " + args.agent
            + " was launched with fork_turns=none and the exact public-packet wrapper. "
            "The host receives the final answer but exposes no full context/tool trace export. "
            "Shared-workspace isolation is instruction_scoped; inherited context cannot be fully audited. "
            "This answer is excluded from blinded-detector claims.",
        },
    }
    envelope = {
        "job_id": args.job_id,
        "packet_sha256": packet["packet_sha256"],
        "actor_runtime": "codex",
        "actor_model": "GPT-6 (host-reported family; inherited model, exact snapshot unavailable)",
        "context_isolation": "instruction_scoped",
        "trace_path": str(wrapper_path),
        "output": output,
    }
    envelope_path = destination / "response_envelope.json"
    for path, value in ((wrapper_path, wrapper), (envelope_path, envelope)):
        if path.exists() and json.loads(path.read_text()) != value:
            raise ValueError("Host envelope already exists with different content")
        write_json(path, value)
    print(json.dumps(ingest(args.out, envelope_path), indent=2))


if __name__ == "__main__":
    main()
