"""Combined unpaid gate; no earlier gate-writing function is invoked."""
import datetime as dt
import hashlib
import io
import json
import unittest

from experiments.triad_emergent_v9 import coordinator as base
from experiments.triad_emergent_v9.offline_gate import test_manifest
from experiments.triad_v9_protocol_repair02 import coordinator as parent
from .coordinator import ROOT, HERE, source_manifest


def run():
    before, tests_before = source_manifest(), test_manifest()
    original_gate, parent_gate = base.file_sha(base.HERE / "WORKFLOW_GATE.json"), base.file_sha(parent.GATE)
    config = base.file_sha(base.HERE / "config.json")
    suite, loader = unittest.TestSuite(), unittest.TestLoader()
    folders = [ROOT / "experiments/triad_repaired_v8/tests", base.HERE / "tests"]
    folders += [ROOT / f"experiments/triad_v9_protocol_repair{number:02d}/tests" for number in (1, 2, 3)]
    for folder in folders:
        suite.addTests(loader.discover(str(folder), top_level_dir=str(ROOT)))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    after, tests = source_manifest(), test_manifest()
    passed = (result.wasSuccessful() and result.testsRun >= 341 and before == after and tests_before == tests
        and original_gate == base.file_sha(base.HERE / "WORKFLOW_GATE.json")
        and parent_gate == base.file_sha(parent.GATE) and config == base.file_sha(base.HERE / "config.json"))
    folder = ROOT / "research_plans/triad_v9_protocol_repair03_validation"
    folder.mkdir(parents=True, exist_ok=True)
    log = stream.getvalue()
    (folder / "OFFLINE_SUITE.log").write_text(log)
    report = {"status": "passed" if passed else "failed", "repair": "triad_v9_protocol_repair03",
        "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "tests_run": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
        "source_files": after, "test_files": tests, "sources_unchanged_during_tests": before == after,
        "original_gate_sha256": original_gate, "parent_gate_sha256": parent_gate,
        "config_source_sha256": config, "suite_log_sha256": hashlib.sha256(log.encode()).hexdigest(),
        "provider_calls": 0, "repair_activated": False, "actual_live_model_compliance_verified": False,
        "named_acceptance_cases": ["exact lost intent0POST; slots2–4 and2images only", "unknown reserve retained; other unknown blocks",
            "slot2 wire and action distinguish infrastructure loss", "wire/policy/path/row drift blocks",
            "IncompleteRead class-only diagnostic", "successful response saved before later receipt callback failure",
            "parent174/ledger/scientific chain preserved", "free preflight fails before claim",
            "completed stage cache commit after crash0POST", "partial paid prefix cannot replay"]}
    raw = base.canonical(report) + "\n"
    (folder / "OFFLINE_VALIDATION.json").write_text(raw)
    if passed:
        (HERE / "WORKFLOW_GATE.json").write_text(raw)
    print(json.dumps({key: report[key] for key in ("status", "tests_run", "failures", "errors", "skips", "provider_calls")}))
    if not passed:
        print(log[-24000:])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
