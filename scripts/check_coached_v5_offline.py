"""Run offline v5 checks and seal only the prospective offline workflow gate."""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.triad_coached_v5.coordinator import (HERE, source_manifest, read_json, digest,
    file_digest, technical_config, condition_config, status_file, CONDITIONS)


def main():
    destination = ROOT / "research_plans/triad_coached_v5_validation"
    destination.mkdir(parents=True, exist_ok=True)
    baseline = read_json(destination / "PRIOR_PRESERVATION_BASELINE.json")
    before = source_manifest()
    suite = unittest.defaultTestLoader.discover(str(HERE / "tests"))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = destination / "OFFLINE_SUITE.log"
    log.write_text(stream.getvalue())
    if not result.wasSuccessful():
        print(stream.getvalue())
        return 1
    if before != source_manifest():
        raise ValueError("Source changed during offline verification; gate not written")
    for name, expected in baseline["original_v4_sources"].items():
        if file_digest(ROOT / name) != expected:
            raise ValueError("An original frozen v4 dependency changed")
    with sqlite3.connect((ROOT / "runs/main/budget.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY request_id")]
    if len(rows) != baseline["ledger_rows"] or digest(rows) != baseline["ledger_rows_sha256"]:
        raise ValueError("Original paid ledger rows changed during implementation")
    config = read_json(HERE / "config.json")
    config = condition_config(config, config["study_condition"])
    tests = {str(p.relative_to(ROOT)): file_digest(p) for p in sorted((HERE / "tests").glob("test_*.py"))}
    gate = {"status": "passed", "revision": "triad_coached_v5", "scope": "offline fixtures only",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "source_files": before,
        "main_config_sha256": digest(config), "technical_config_sha256": digest(technical_config(config)),
        "config_sha256_by_condition": {name: digest(condition_config(config, name)) for name in CONDITIONS},
        "test_files": tests, "tests_run": result.testsRun, "offline_log_sha256": file_digest(log),
        "prior_v4_source_files_verified": len(baseline["original_v4_sources"]),
        "prior_ledger_rows_verified": len(rows), "paid_calls_made": 0,
        "fixture_success_is_collusion_evidence": False, "real_overt_control_rounds_still_required": 2}
    status_file(HERE / "WORKFLOW_GATE.json", gate)
    status_file(destination / "OFFLINE_VALIDATION.json", gate)
    print(json.dumps({"status": "passed", "tests": result.testsRun,
        "original_sources_preserved": len(baseline["original_v4_sources"]),
        "ledger_rows_unchanged": len(rows), "paid_calls": 0,
        "gate_path": str(HERE / "WORKFLOW_GATE.json"), "real_control_still_required": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
