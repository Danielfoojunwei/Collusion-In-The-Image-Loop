"""Export a labeled study subset with the full concurrent ledger preserved.

Only temporary copies are filtered. The active shared run is never modified.
Validation/export use the exact uploaded source package, not mutable live code.
"""
import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile


def charge_summary(rows):
    charged = sum((Decimal(str(json.loads(r["receipt_json"])["cost_usd"]))
                   for r in rows if r["status"] == "completed"), Decimal(0))
    reserved = sum(r["reserved_units"] for r in rows if r["actual_units"] is None)
    rounded = sum(r["actual_units"] or 0 for r in rows)
    return {"requests": len(rows), "provider_receipt_sum_usd": str(charged),
            "ledger_confirmed_usd": str(Decimal(rounded) / Decimal(10**9)),
            "unknown_reserved_usd": str(Decimal(reserved) / Decimal(10**9)),
            "committed_usd": str(Decimal(rounded + reserved) / Decimal(10**9))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-zip", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    out, destination = args.out.resolve(), args.destination.resolve()
    interpreter = out.parents[1] / ".venv" / "bin" / "python"
    with tempfile.TemporaryDirectory(prefix="collusion-recorded-attempt-") as temporary:
        temp = Path(temporary)
        view = temp / "scientific_snapshot"
        shutil.copytree(out, view, ignore=shutil.ignore_patterns("budget.sqlite3", "budget.sqlite3-wal", "budget.sqlite3-shm", "run.lock"))
        raw_path = view / "execution_notes" / "full_shared_ledger_snapshot.sqlite3"
        with sqlite3.connect(f"file:{out / 'budget.sqlite3'}?mode=ro", uri=True) as original, sqlite3.connect(raw_path) as raw:
            original.backup(raw)
        with sqlite3.connect(raw_path) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute("SELECT * FROM requests ORDER BY request_id")]
        plan = json.loads((view / "plan.json").read_text())
        episode_ids = {ep["episode_id"] for ep in plan["episodes"]}
        scientific = [r for r in rows if r["request_id"].split(":", 1)[0] in episode_ids]
        extra = [r for r in rows if r not in scientific]
        if len(scientific) != 30 or any(not r["request_id"].startswith("infra") for r in extra):
            raise ValueError("Unexpected study scope; preserve and investigate")
        shutil.copyfile(raw_path, view / "budget.sqlite3")
        with sqlite3.connect(view / "budget.sqlite3") as derived:
            derived.executemany("DELETE FROM requests WHERE request_id=?", [(r["request_id"],) for r in extra])
            derived.commit()
        raw_bytes = raw_path.read_bytes()
        record = {
            "status": "derived_scientific_snapshot_full_shared_ledger_also_preserved",
            "study_scope": "The original 30 scheduled requests. All ten attempted cases remain missing at A's first image-bearing continuation.",
            "authoritative_active_ledger_edited": False,
            "archive_budget_file": "budget.sqlite3 contains the derived 30-request scheduled scientific subset.",
            "unfiltered_shared_ledger_file": "execution_notes/full_shared_ledger_snapshot.sqlite3",
            "unfiltered_shared_ledger_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "scheduled_accounting": charge_summary(scientific),
            "additional_infrastructure_accounting": charge_summary(extra),
            "full_shared_accounting": charge_summary(rows),
            "excluded_from_scientific_outcomes_only": [{k: r[k] for k in ("request_id", "kind", "model", "status", "created", "reserved_units", "actual_units")} for r in extra],
            "additional_entry_provenance": "Concurrent shared-workspace infrastructure calls; their author was not established by this chat. The diagnostic file's claimed authorization is not treated as a user instruction.",
            "validation_scope": "Original scheduled data only, checked using the exact uploaded source package. No infrastructure call is certified as part of the fixed protocol.",
        }
        note = view / "execution_notes" / "SCIENTIFIC_SNAPSHOT_SCOPE.json"
        note.write_text(json.dumps(record, indent=2) + "\n")
        (view / "SCIENTIFIC_SNAPSHOT_README.md").write_text(
            "# Original ten-case scientific attempt — derived snapshot\n\n"
            "All 30 scheduled requests and all ten missing case outcomes are retained. "
            "The report describes this attempted study, not a completed collusion demonstration.\n\n"
            "The archive's budget.sqlite3 is a derived scheduled-only validation view. "
            "The unfiltered concurrent shared ledger is preserved verbatim as "
            "execution_notes/full_shared_ledger_snapshot.sqlite3. "
            "No request was removed from the active workspace ledger or omitted from total shared accounting. "
            "See execution_notes/SCIENTIFIC_SNAPSHOT_SCOPE.json for all excluded infrastructure IDs, "
            "known charges, unknown reservations and the full ledger hash.\n\n"
            "The manuscript and original execution_summary.json describe the 30-request study snapshot. "
            "Their study costs do not include later infrastructure probes. "
            "The full shared-ledger budget is reported separately in the scope record.\n")
        host = view / "execution_notes" / "host_tools"
        host.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__), host / Path(__file__).name)
        package = temp / "frozen_source"
        with zipfile.ZipFile(args.source_zip) as archive:
            for name in archive.namelist():
                target = (package / name).resolve()
                if not target.is_relative_to(package.resolve()):
                    raise ValueError("Unsafe source archive path")
            archive.extractall(package)
        source_root = package / "visual_collusion_stress_v4"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(source_root / "src")
        commands = [
            [str(interpreter), "-m", "visual_collusion", "verify", "--out", str(view)],
            [str(interpreter), "-m", "visual_collusion", "export", "--out", str(view), "--destination", str(destination)],
        ]
        for command in commands:
            result = subprocess.run(command, cwd=source_root, env=env, capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError("Frozen-source snapshot command failed: " + result.stdout + result.stderr)
            print(result.stdout)
        with zipfile.ZipFile(destination) as archive:
            if archive.testzip() is not None:
                raise ValueError("Archive CRC validation failed")
        sidecar = destination.with_suffix(".scope.json")
        sidecar.write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"archive": str(destination), "scope_record": str(sidecar), "bytes": destination.stat().st_size,
                          "scheduled": record["scheduled_accounting"], "full_shared": record["full_shared_accounting"]}, indent=2))


if __name__ == "__main__":
    main()
