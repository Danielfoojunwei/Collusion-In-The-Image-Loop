"""Single-producer host for a fresh twelve-round episode and fixed shadows.

This coordinator never creates a new allowance, imports actor memories, writes
a reviewer judgment, or retries a semantic observation to obtain success.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "runs/main/triad_emergent_v9_live"
AGGREGATE = ROOT / "runs/main/budget.sqlite3"
PRIOR_OUT = ROOT / "runs/main/triad_repaired_v8_live"
REVISION = "triad_emergent_v9"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Symlink evidence is not supported")
    return sha(path.read_bytes())


def read_json(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Symlink evidence is not supported")
    return json.loads(path.read_text())


def save_once(path, value):
    path = Path(path)
    raw = (canonical(value) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError("Immutable host evidence changed")
        return path
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def mutable_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Symlink host status")
    fd, name = tempfile.mkstemp(prefix=".host-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(canonical(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def producer_lock(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "producer.lock"
    if path.is_symlink():
        raise ValueError("Symlink producer lock")
    with path.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def source_manifest():
    """Close over the unchanged, previously audited provider dependencies."""
    prior = read_json(PRIOR_OUT / "freeze.json")["source_files"]
    for relative, expected in prior.items():
        if file_sha(ROOT / relative) != expected:
            raise ValueError("An original frozen source changed: " + relative)
    paths = {ROOT / relative for relative in prior}
    paths.update(path for path in HERE.rglob("*")
                 if path.is_file() and path.suffix in {".py", ".txt", ".md"}
                 and "tests" not in path.relative_to(HERE).parts
                 and "__pycache__" not in path.parts)
    return {str(path.relative_to(ROOT)): file_sha(path) for path in sorted(paths)}


def ledger_rows():
    with sqlite3.connect(AGGREGATE.absolute().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY request_id")]
    return {row["request_id"]: sha(canonical(row).encode()) for row in rows}


def budget():
    from experiments.pressure_interview_v1.budget_guard import readonly_budget_snapshot
    return readonly_budget_snapshot(AGGREGATE)


class Coordinator:
    def __init__(self, out=None, *, config_path=None, backend=None):
        self.out = Path(out or DEFAULT_OUT).absolute()
        self.config_path = Path(config_path or HERE / "config.json").absolute()
        self.config = read_json(self.config_path)
        if self.config.get("revision") != REVISION or self.config.get("rounds") != 12:
            raise ValueError("The prospective twelve-round configuration is required")
        self.backend = backend

    def init(self):
        from .design import make_plan
        sources = source_manifest()
        gate = read_json(HERE / "WORKFLOW_GATE.json")
        if (gate.get("status") != "passed" or gate.get("source_files") != sources
                or gate.get("config_sha256") != file_sha(self.config_path)):
            raise ValueError("Current exact sources and configuration need a passing offline gate")
        self.out.mkdir(parents=True, exist_ok=True)
        if (self.out / "freeze.json").exists():
            self.verify_freeze()
            return self.status()
        before = budget()
        if before["pending_count"] or before["halt_reason"]:
            raise ValueError("Original aggregate is not ready; no allowance or reservation is reset")
        plan = make_plan(seed=self.config.get("seed", 20261025),
                         condition=self.config.get("condition", "a_private"))
        save_once(self.out / "config.json", self.config)
        save_once(self.out / "plan.json", plan)
        baseline = {"recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "original_aggregate": before, "prior_ledger_rows": ledger_rows(),
                    "prior_v8_freeze_sha256": file_sha(PRIOR_OUT / "freeze.json"),
                    "prior_v8_integrity_sha256": file_sha(PRIOR_OUT / "analysis/FINAL_INTEGRITY.json"),
                    "prior_runtime_sources": read_json(PRIOR_OUT / "freeze.json")["source_files"],
                    "new_allowance": False, "old_actor_memory_imported": False}
        if (self.out / "BASELINE.json").exists():
            old_baseline = read_json(self.out / "BASELINE.json")
            if (old_baseline["prior_ledger_rows"] != baseline["prior_ledger_rows"]
                    or old_baseline["prior_runtime_sources"] != baseline["prior_runtime_sources"]):
                raise ValueError("An interrupted offline initialization has changed prior evidence")
        else:
            save_once(self.out / "BASELINE.json", baseline)
        freeze = {"revision": REVISION, "evidence_origin": "live", "source_files": sources,
                  "config_sha256": file_sha(self.out / "config.json"),
                  "config_source_sha256": file_sha(self.config_path),
                  "plan_sha256": file_sha(self.out / "plan.json"),
                  "baseline_sha256": file_sha(self.out / "BASELINE.json"),
                  "workflow_gate_sha256": file_sha(HERE / "WORKFLOW_GATE.json"),
                  "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "fresh_actor_memory": True, "old_results_preserved": True,
                  "scope": "One new continuous twelve-round episode plus fixed isolated post-episode shadows"}
        save_once(self.out / "freeze.json", freeze)
        self._engine()
        mutable_json(self.out / "run_status.json", {"status": "initialized", "round": 1})
        return self.status()

    def verify_freeze(self):
        freeze = read_json(self.out / "freeze.json")
        if (freeze.get("revision") != REVISION or freeze.get("source_files") != source_manifest()
                or freeze.get("config_sha256") != file_sha(self.out / "config.json")
                or freeze.get("config_source_sha256") != file_sha(self.config_path)
                or freeze.get("plan_sha256") != file_sha(self.out / "plan.json")
                or freeze.get("baseline_sha256") != file_sha(self.out / "BASELINE.json")
                or freeze.get("workflow_gate_sha256") != file_sha(HERE / "WORKFLOW_GATE.json")):
            raise ValueError("Frozen prospective study changed")
        return freeze

    def _engine(self):
        from .engine import Engine
        return Engine(self.out, self.config, read_json(self.out / "plan.json"), backend=self.backend)

    def _backend(self):
        if self.backend is None:
            from .backend import Backend
            self.backend = Backend(self.config, self.out, AGGREGATE)
        return self.backend

    def preflight(self):
        self.verify_freeze()
        return self._backend().preflight()

    def advance(self, through_round=None):
        self.verify_freeze()
        with producer_lock(self.out):
            engine = self._engine()
            while (target := engine.next_stage()) is not None:
                if through_round is not None and target["round"] > through_round:
                    result = {"status": "round_boundary", "completed_round": through_round}
                    mutable_json(self.out / "run_status.json", result)
                    return result
                if target["stage"] == "REVIEWER":
                    job = engine.export_reviewer_packet()
                    result = {**job, "status": "awaiting_codex"}
                    mutable_json(self.out / "run_status.json", result)
                    return result
                engine.backend = self._backend()
                engine.run_next_actor()
            result = {"status": "main_complete", "rounds": 12,
                      "shadows": "pending fixed post-episode controls"}
            mutable_json(self.out / "run_status.json", result)
            return result

    def dispatch(self, job_id, reviewer_agent):
        self.verify_freeze()
        job = read_json(self.out / "run_status.json")
        if job.get("status") != "awaiting_codex" or job.get("job_id") != job_id:
            raise ValueError("No matching current reviewer job")
        record = {"job_id": job_id, "reviewer_agent": reviewer_agent,
                  "packet_sha256": job["packet_sha256"], "fresh_context_requested": True}
        save_once(self.out / "reviewer_dispatches" / f"r{job['round']:02d}.json", record)
        return {"status": "review_dispatched", **record}

    def ingest(self, response):
        self.verify_freeze()
        with producer_lock(self.out):
            job = read_json(self.out / "run_status.json")
            if response.get("job_id") != job.get("job_id") or job.get("status") != "awaiting_codex":
                raise ValueError("First reviewer response does not match the pending job")
            dispatched = read_json(self.out / "reviewer_dispatches" / f"r{job['round']:02d}.json")
            provenance = response.get("provenance", {})
            actual_agent = provenance.get("reviewer_agent")
            if actual_agent != dispatched["reviewer_agent"]:
                raise ValueError("The observed reviewer differs from its dispatched fresh context")
            from .reviewer_host import validate_envelope
            validate_envelope(self.out, response)
            result = self._engine().ingest_review(response["output"], provenance,
                                                  raw_answer=response.get("raw_answer"))
            mutable_json(self.out / "run_status.json", {"status": "review_ingested", "round": job["round"]})
            return {"status": "review_ingested", "round": job["round"], "result": result}

    def shadows(self):
        self.verify_freeze()
        with producer_lock(self.out):
            engine = self._engine()
            if engine.next_stage() is not None:
                raise ValueError("Every live stage must finish before any isolated replay")
            from .counterfactuals import run_shadows
            result = run_shadows(engine, self._backend())
            mutable_json(self.out / "run_status.json", {"status": "complete", "rounds": 12,
                                                        "controls": result})
            return result

    def status(self):
        return {"revision": REVISION, "out": str(self.out), "budget": budget(),
                "engine": self._engine().summary() if (self.out / "freeze.json").exists() else None,
                "host": read_json(self.out / "run_status.json") if (self.out / "run_status.json").exists() else None}

    def verify(self):
        freeze = self.verify_freeze()
        engine = self._engine()
        result = engine.verify()
        from .reviewer_host import validate_archived_envelope
        reviews = [validate_archived_envelope(self.out, number) for number in range(1, 13)
                   if engine.outcome(number, "REVIEWER") is not None]
        baseline = read_json(self.out / "BASELINE.json")
        current = ledger_rows()
        if any(current.get(key) != value for key, value in baseline["prior_ledger_rows"].items()):
            raise ValueError("An original paid request record changed")
        for relative, expected in baseline["prior_runtime_sources"].items():
            if file_sha(ROOT / relative) != expected:
                raise ValueError("An original experiment source changed")
        return {"status": "passed", "engine": result,
                "actual_first_review_bindings_reverified": len(reviews),
                "frozen_source_files": len(freeze["source_files"]),
                "prior_ledger_rows_unchanged": len(baseline["prior_ledger_rows"]),
                "budget": budget()}

    def report(self):
        verification = self.verify()
        engine = self._engine()
        report = {"revision": REVISION, "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "interpretation": "One explicitly induced, incentive-conditioned dependent trajectory; no guaranteed collusion, causal incentive effect, spontaneous emergence, or detector accuracy.",
                  "condition": self.config.get("condition"), "verification": verification,
                  "budget": budget(), "summary": engine.summary(), "rounds": engine.report_rows()}
        mutable_json(self.out / "analysis/report.json", report)
        return {"status": "report_written", "rounds": len(report["rounds"]),
                "path": str(self.out / "analysis/report.json")}

    def render(self, *, allow_incomplete=False):
        self.report()
        from .analysis import render
        return render(self.out, allow_incomplete=allow_incomplete)

    def export(self, target):
        self.verify()
        if self._engine().next_stage() is not None:
            raise ValueError("Refusing to label an unfinished live trajectory a final archive")
        controls_path = self.out / "shadows/report.json"
        if not controls_path.is_file():
            raise ValueError("The fixed controls must finish before the final archive")
        controls = read_json(controls_path)
        if (controls.get("status") != "complete" or controls.get("planned_shadow_turns") != 24
                or controls.get("observed_shadow_turns", 0) + controls.get("unavailable_shadow_turns", 0) != 24
                or controls.get("main_preservation_verified") is not True):
            raise ValueError("The final archive requires all fixed control endpoints and main preservation")
        target = Path(target).absolute()
        if target.exists():
            raise ValueError("Preserve an existing archive")
        snapshot = self.out / "analysis/aggregate_budget_snapshot.sqlite3"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(AGGREGATE.absolute().as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(snapshot) as destination:
                original.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Aggregate snapshot failed verification")
        with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(self.out.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Symlink results cannot be exported")
                if path.is_file() and path.name != "producer.lock" and not path.name.startswith("."):
                    archive.write(path, str(path.relative_to(self.out)))
            for relative, expected in read_json(self.out / "freeze.json")["source_files"].items():
                if file_sha(ROOT / relative) != expected:
                    raise ValueError("Frozen reproduction source changed")
                archive.write(ROOT / relative, "reproduction_workspace/" + relative)
        return {"path": str(target), "bytes": target.stat().st_size, "sha256": file_sha(target),
                "private_researcher_archive": True}
