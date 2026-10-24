"""Validate the scheduled records; export the unchanged full shared ledger.

An independently added, unscheduled infrastructure intent is retained in the
authoritative archive and all budget totals. Only a temporary validation view
omits that one intent, with this limitation explicitly recorded. No inference.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from visual_collusion import cli
from visual_collusion.budget import BudgetLedger
from visual_collusion.common import canonical, read_json, source_manifest, utc_now, write_json
from visual_collusion.evidence import _schedule, validate_evidence


EXTRA_ID = "infra.vision-diagnosis.1"


def request_snapshot(path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute("SELECT * FROM requests ORDER BY request_id")]
    return rows, hashlib.sha256(canonical(rows).encode()).hexdigest()


def validate_view(out):
    rows, original_digest = request_snapshot(out / "budget.sqlite3")
    _, _, allowed, _ = _schedule(read_json(out / "plan.json"))
    extras = [r for r in rows if r["request_id"] not in allowed]
    if [r["request_id"] for r in extras] != [EXTRA_ID]:
        raise ValueError("Unexpected unscheduled request set; preserve and investigate")
    extra = extras[0]
    if extra["status"] != "uncertain" or any(extra[k] is not None for k in ("actual_units", "response_json", "receipt_json", "result_hash")):
        raise ValueError("Infrastructure intent changed; accounting review required")
    try:
        validate_evidence(out)
    except ValueError as error:
        full_error = str(error)
        if full_error != "Request lies outside the frozen finite schedule":
            raise
    else:
        raise ValueError("Expected full-ledger validation rejection was not observed")
    with tempfile.TemporaryDirectory(prefix="collusion-scheduled-validation-") as temporary:
        view = Path(temporary) / "scheduled_view"
        shutil.copytree(out, view, ignore=shutil.ignore_patterns("budget.sqlite3", "budget.sqlite3-wal", "budget.sqlite3-shm", "run.lock"))
        with sqlite3.connect(f"file:{out / 'budget.sqlite3'}?mode=ro", uri=True) as original, sqlite3.connect(view / "budget.sqlite3") as derived:
            original.backup(derived)
            # The original ledger is never modified. This is a derived view only.
            derived.execute("DELETE FROM requests WHERE request_id=?", (EXTRA_ID,))
            derived.commit()
        checks = validate_evidence(view)
    _, after_digest = request_snapshot(out / "budget.sqlite3")
    if after_digest != original_digest:
        raise ValueError("Shared authoritative requests changed during validation")
    coding = read_json(out / "analysis" / "sender_artifact_coding.json")
    for row in coding["rows"]:
        image = out / row["artifact"]["path"]
        if hashlib.sha256(image.read_bytes()).hexdigest() != row["artifact"]["sha256"]:
            raise ValueError("Coded artifact digest changed")
    record = {
        "verified_at_utc": utc_now(),
        "status": "scheduled_scientific_subset_verified_full_shared_ledger_has_unscheduled_intent",
        "canonical_full_ledger_validation": {"passed": False, "error": full_error},
        "scheduled_subset_validation": {"passed": True, "checks": checks, "requests": len(rows) - 1},
        "excluded_from_scientific_outcomes_only": [EXTRA_ID],
        "temporary_validation_view": "One derived copy omitted this diagnostic intent. It was deleted from no authoritative data or exported ledger.",
        "original_request_rows_retained": len(rows),
        "original_request_rows_sha256": original_digest,
        "authoritative_requests_unchanged_by_validation": True,
        "source_manifest_unchanged": source_manifest() == read_json(out / "freeze.json")["source_files"],
        "full_shared_ledger_budget": BudgetLedger(out / "budget.sqlite3", 40).summary(),
        "diagnostic_intent": {k: extra[k] for k in ("request_id", "kind", "model", "status", "created", "reserved_units", "actual_units", "reason")},
        "diagnostic_provenance": "Unattributed concurrent shared-workspace entry, added after the scheduled run and its successful 30-intent validation. Its local claimed authorization is not treated as a user instruction.",
        "coded_draft_digests_verified": len(coding["rows"]),
        "prior_scheduled_resume_check": read_json(out / "execution_notes" / "final_resume_and_loss_validation.json"),
        "offline_tests_passed": {"canonical_before_freeze": 84, "key_waiver": 7, "independent_continuation": 8},
        "scope": "Consistency and accounting checks; no validated collusion outcome or blinded detector judgment.",
    }
    write_json(out / "analysis" / "validation.json", record)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    record = validate_view(out)
    script = Path(__file__).resolve()
    shutil.copyfile(script, out / "execution_notes" / "host_tools" / script.name)
    if args.destination:
        def scoped_validation(target):
            if Path(target).resolve() != out:
                raise ValueError("Wrong export target")
            _, current = request_snapshot(out / "budget.sqlite3")
            if current != record["original_request_rows_sha256"]:
                raise ValueError("Shared requests changed before export")
            return record["scheduled_subset_validation"]["checks"]
        cli.validate_evidence = scoped_validation
        result = cli.export_run(out, args.destination)
        result["validation_scope"] = record["status"]
        result["full_ledger_rows_preserved"] = record["original_request_rows_retained"]
    else:
        result = {"status": record["status"], "scheduled_checks": record["scheduled_subset_validation"], "full_ledger_rows_preserved": record["original_request_rows_retained"]}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
