"""Cloud-friendly commands. Pending Codex work is a checkpoint, not an error."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from .common import load_config, read_json, write_json, canonical
from .runtime import prepare_run, advance
from .budget import BudgetLedger, BudgetError
from .providers import OpenRouterProvider, ProviderError
from .bridge import ingest, fail_job
from .analysis import build_report, validate_evidence


def export_run(out, destination):
    out, destination = Path(out).resolve(), Path(destination).resolve()
    validate_evidence(out)
    try:
        destination.relative_to(out)
    except ValueError:
        pass
    else:
        raise ValueError("Export destination must be outside the run directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        backup = Path(temporary) / "budget.sqlite3"
        if (out / "budget.sqlite3").exists():
            with sqlite3.connect(out / "budget.sqlite3") as source, sqlite3.connect(backup) as target:
                source.backup(target)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(out.rglob("*")):
                if not file.is_file() or file.is_symlink():
                    continue
                relative = file.relative_to(out)
                if any(part.startswith(".") for part in relative.parts) or file.name in {"run.lock", "budget.sqlite3", "budget.sqlite3-wal", "budget.sqlite3-shm"}:
                    continue
                archive.write(file, str(relative))
            if backup.exists():
                archive.write(backup, "budget.sqlite3")
    return {"status": "exported", "path": str(destination), "note": "Researcher archive includes private experimental prompts and traces; review before public release."}


def _provider(config, out):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("Set OPENROUTER_API_KEY as a runtime network secret; never put it in chat or tracked files")
    ledger = BudgetLedger(out / "budget.sqlite3", config["max_inference_usd"])
    provider = OpenRouterProvider(key, ledger)
    snapshot = provider.preflight(config["models"], max_inference_usd=config["max_inference_usd"])
    write_json(out / "provider_preflight.json", snapshot)
    return provider


def main(argv=None):
    parser = argparse.ArgumentParser(description="Multi-turn image collusion stress test — bounded, resumable, no paid pilots")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--config", type=Path, default=Path("configs/canonical.json"))
    init.add_argument("--out", type=Path, required=True)
    for name in ("advance", "preflight", "report", "verify"):
        sub = commands.add_parser(name)
        sub.add_argument("--out", type=Path, required=True)
    submit = commands.add_parser("ingest")
    submit.add_argument("--out", type=Path, required=True)
    submit.add_argument("--response", type=Path, required=True)
    failed = commands.add_parser("fail-review", help="Seal one actually observed invalid Codex output without retrying it")
    failed.add_argument("--out", type=Path, required=True)
    failed.add_argument("--job-id", required=True)
    failed.add_argument("--reason", required=True)
    failed.add_argument("--raw-response", type=Path, required=True)
    failed.add_argument("--actor-model", required=True)
    failed.add_argument("--context-isolation", choices=("instruction_scoped", "packet_environment"), required=True)
    mock = commands.add_parser("mock")
    mock.add_argument("--config", type=Path, default=Path("configs/canonical.json"))
    mock.add_argument("--out", type=Path, required=True)
    export = commands.add_parser("export")
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            config = load_config(args.config)
            plan = prepare_run(config, args.out)
            value = {"status": "planned", "counts": plan["counts"], "paid_requests_made": 0}
        elif args.command == "mock":
            from .mock import MockProvider, fulfill_mock_job
            config = load_config(args.config)
            plan = prepare_run(config, args.out, evidence_origin="mock")
            provider = MockProvider(plan, BudgetLedger(args.out / "budget.sqlite3", config["max_inference_usd"]))
            provider.auto_reviews = True
            for _ in range(60):
                value = advance(provider, config, args.out)
                if value["status"] != "awaiting_codex":
                    break
                fulfill_mock_job(args.out, value)
            else:
                raise ValueError("Mock bridge exceeded the frozen task/review job count")
            value = {**value, "fresh_mock_requests": provider.fresh_calls, "research_evidence": False}
            build_report(args.out)
        elif args.command == "ingest":
            if read_json(args.out / "freeze.json")["evidence_origin"] != "live":
                raise ValueError("Live ingest cannot modify an offline mock run")
            value = ingest(args.out, args.response)
        elif args.command == "fail-review":
            if read_json(args.out / "freeze.json")["evidence_origin"] != "live":
                raise ValueError("Live failure recording cannot modify an offline mock run")
            value = fail_job(args.out, args.job_id, args.reason, args.raw_response,
                             actor_model=args.actor_model, context_isolation=args.context_isolation)
        elif args.command == "report":
            report = build_report(args.out)
            value = {"status": report["status"], "evidence_origin": report["evidence_origin"],
                     "live_observations": report["live_observations"], "report_path": str((args.out / "analysis" / "REPORT.md").resolve())}
        elif args.command == "verify":
            value = {"status": "verified", **validate_evidence(args.out)}
        elif args.command == "export":
            value = export_run(args.out, args.destination)
        else:
            config = load_config(args.out / "config.json")
            if read_json(args.out / "freeze.json")["evidence_origin"] != "live":
                raise ValueError("Paid commands cannot run in a mock output directory")
            prepare_run(config, args.out)
            provider = _provider(config, args.out)
            value = provider._preflight if args.command == "preflight" else advance(provider, config, args.out)
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, ProviderError, BudgetError) as exc:
        # Do not echo request bodies, environment variables or API credentials.
        message = str(exc)
        secret = os.environ.get("OPENROUTER_API_KEY")
        if secret:
            message = message.replace(secret, "[REDACTED]")
        print(json.dumps({"status": "stopped", "error": message, "automatic_paid_retry": False}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
