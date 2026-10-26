"""Run unpaid unit/integration checks and bind the exact prospective sources."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import unittest

from .coordinator import HERE, ROOT, canonical, file_sha, source_manifest


def test_manifest():
    # A conservative superset includes transitive legacy fixture helpers.
    paths = {p for p in (ROOT / "experiments").rglob("*.py")
             if "tests" in p.relative_to(ROOT).parts and "__pycache__" not in p.parts}
    return {str(p.relative_to(ROOT)): file_sha(p) for p in sorted(paths)}


def run():
    before = source_manifest()
    config_before = file_sha(HERE / "config.json")
    tests_before = test_manifest()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for directory in (ROOT / "experiments/triad_repaired_v8/tests", HERE / "tests"):
        suite.addTests(loader.discover(str(directory), top_level_dir=str(ROOT)))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    after = source_manifest()
    tests = test_manifest()
    passed = (result.wasSuccessful() and result.testsRun > 168 and before == after
              and tests_before == tests and config_before == file_sha(HERE / "config.json"))
    validation = ROOT / "research_plans/triad_emergent_v9_validation"
    validation.mkdir(parents=True, exist_ok=True)
    log = stream.getvalue()
    (validation / "OFFLINE_SUITE.log").write_text(log)
    report = {"status": "passed" if passed else "failed", "revision": "triad_emergent_v9",
              "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "offline_tests_passed": result.testsRun if passed else None,
              "tests_run": result.testsRun, "failure_count": len(result.failures),
              "error_count": len(result.errors), "skip_count": len(result.skipped),
              "source_files": after, "sources_unchanged_during_tests": before == after,
              "test_files": tests, "config_sha256": file_sha(HERE / "config.json"),
              "suite_log_sha256": hashlib.sha256(log.encode()).hexdigest(),
              "provider_calls": 0, "actual_collusion_established_by_tests": False,
              "scope": "Unpaid software checks; scripted fixtures are not experimental findings"}
    raw = canonical(report) + "\n"
    (validation / "OFFLINE_VALIDATION.json").write_text(raw)
    if passed:
        (HERE / "WORKFLOW_GATE.json").write_text(raw)
    print(json.dumps({k: report[k] for k in ("status", "tests_run", "failure_count", "error_count", "skip_count", "sources_unchanged_during_tests", "provider_calls")}))
    if not passed:
        print(log[-24000:])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
