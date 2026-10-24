"""Validate the prospective continuation offline and bind its exact sources."""
from __future__ import annotations
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.triad_repaired_v7.coordinator import (
    HERE, CONDITIONS, source_manifest, condition_config, technical_config,
    read_json, digest, file_digest, status_file,
)


def main():
    destination = ROOT / "research_plans/triad_repaired_v7_validation"
    baseline = read_json(destination / "PRIOR_PRESERVATION_BASELINE.json")
    before = source_manifest()
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(HERE / "tests"))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = destination / "OFFLINE_SUITE.log"
    log.write_text(stream.getvalue())
    if not result.wasSuccessful():
        print(stream.getvalue())
        return 1
    if source_manifest() != before:
        raise ValueError("Runtime sources changed during verification")
    for relative, expected in baseline["protected_files"].items():
        if file_digest(ROOT / relative) != expected:
            raise ValueError("Original frozen evidence/source changed")
    with sqlite3.connect((ROOT / "runs/main/budget.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY request_id")]
    if len(rows) != baseline["ledger_rows"] or digest(rows) != baseline["ledger_rows_sha256"]:
        raise ValueError("Paid ledger changed during offline verification")
    config = condition_config(read_json(HERE / "config.json"), "shared_coached")
    gate = {"status": "passed", "revision": "triad_repaired_v7", "scope": "Offline fixtures only; prospective rounds9–12",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "source_files": before,
        "main_config_sha256": digest(config), "technical_config_sha256": digest(technical_config(config)),
        "config_sha256_by_condition": {name: digest(condition_config(config, name)) for name in CONDITIONS},
        "test_files": {str(p.relative_to(ROOT)): file_digest(p) for p in sorted((HERE / "tests").glob("test_*.py"))},
        "tests_run": result.testsRun, "offline_log_sha256": file_digest(log),
        "protected_files_verified": len(baseline["protected_files"]), "ledger_rows_verified": len(rows),
        "paid_calls_made": 0, "original_eight_rounds_preserved": True,
        "protocol_break_effective_round": 9, "fixture_success_is_collusion_evidence": False,
        "success_dependent_continuation": False, "real_overt_control_rounds_still_required": 0}
    status_file(HERE / "WORKFLOW_GATE.json", gate)
    status_file(destination / "OFFLINE_VALIDATION.json", gate)
    print(json.dumps({"status": "passed", "tests": result.testsRun,
        "protected_files_unchanged": len(baseline["protected_files"]), "ledger_rows_unchanged": len(rows),
        "paid_calls": 0, "gate_path": str(HERE / "WORKFLOW_GATE.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
