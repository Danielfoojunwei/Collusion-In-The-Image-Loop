"""Combined unpaid gate; never invokes or rewrites the original v9 gate."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import unittest

from experiments.triad_emergent_v9 import coordinator as original
from experiments.triad_emergent_v9.offline_gate import test_manifest
from .coordinator import HERE, ROOT, source_manifest


def run():
    before = source_manifest()
    old_gate = original.file_sha(original.HERE / "WORKFLOW_GATE.json")
    config = original.file_sha(original.HERE / "config.json")
    tests_before = test_manifest()
    suite, loader = unittest.TestSuite(), unittest.TestLoader()
    for folder in (ROOT / "experiments/triad_repaired_v8/tests", original.HERE / "tests", HERE / "tests"):
        suite.addTests(loader.discover(str(folder), top_level_dir=str(ROOT)))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    after, tests = source_manifest(), test_manifest()
    passed = (result.wasSuccessful() and result.testsRun >= 321 and before == after
              and tests_before == tests and config == original.file_sha(original.HERE / "config.json")
              and old_gate == original.file_sha(original.HERE / "WORKFLOW_GATE.json"))
    validation = ROOT / "research_plans/triad_v9_protocol_repair01_validation"
    validation.mkdir(parents=True, exist_ok=True)
    log = stream.getvalue()
    (validation / "OFFLINE_SUITE.log").write_text(log)
    report = {"status": "passed" if passed else "failed", "repair": "triad_v9_protocol_repair01",
              "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skips": len(result.skipped), "source_files": after, "test_files": tests,
              "sources_unchanged_during_tests": before == after,
              "config_source_sha256": config, "original_workflow_gate_sha256": old_gate,
              "original_gate_unchanged": old_gate == original.file_sha(original.HERE / "WORKFLOW_GATE.json"),
              "suite_log_sha256": hashlib.sha256(log.encode()).hexdigest(), "provider_calls": 0,
              "repair_activated": False, "actual_live_model_compliance_verified": False,
              "scope": "Software verification with temporary ledgers and scripted transports, not experimental findings"}
    raw = original.canonical(report) + "\n"
    (validation / "OFFLINE_VALIDATION.json").write_text(raw)
    if passed:
        (HERE / "WORKFLOW_GATE.json").write_text(raw)
    print(json.dumps({k: report[k] for k in ("status", "tests_run", "failures", "errors", "skips", "provider_calls", "original_gate_unchanged")}))
    if not passed:
        print(log[-24000:])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
