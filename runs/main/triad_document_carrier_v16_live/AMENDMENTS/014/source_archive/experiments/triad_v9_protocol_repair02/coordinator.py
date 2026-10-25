"""Prospective sampling amendment over an explicitly preserved parent snapshot."""
from __future__ import annotations

from copy import deepcopy
import datetime as dt
import html
from pathlib import Path

from experiments.triad_emergent_v9 import coordinator as base
from experiments.triad_emergent_v9.offline_gate import test_manifest
from experiments.triad_v9_protocol_repair01 import coordinator as parent
from .backend import REPAIR, TOKEN_BUDGET_NOTICE, make_policy


ROOT = base.ROOT
HERE = Path(__file__).resolve().parent
GATE = HERE / "WORKFLOW_GATE.json"
ACTIVATION_PHRASE = "ACTIVATE_REPAIR02_USER_SELECTED_TEMPERATURE1"


def source_manifest():
    sources = parent.source_manifest()
    for path in sorted(HERE.rglob("*")):
        if (path.is_file() and path.suffix in {".py", ".md", ".txt"}
                and "tests" not in path.relative_to(HERE).parts and "__pycache__" not in path.parts):
            sources[str(path.relative_to(ROOT))] = base.file_sha(path)
    return sources


def verify_parent_snapshot(out, snapshot, *, config_path=None):
    """Validate parent167 without consulting or temporarily replacing active freeze."""
    out = Path(out).absolute()
    folder = out / "analysis/repair01"
    amendment, baseline = parent._read(folder / "AMENDMENT.json"), parent._read(folder / "BASELINE.json")
    repair = snapshot.get("protocol_repair01", {})
    if (snapshot.get("source_files") != parent.source_manifest()
            or parent._read(folder / "ACTIVE_FREEZE.json") != snapshot
            or base.file_sha(folder / "AMENDMENT.json") != repair.get("amendment_sha256")
            or base.file_sha(folder / "BASELINE.json") != repair.get("baseline_sha256")
            or base.file_sha(parent.GATE) != repair.get("repair_gate_sha256")
            or amendment.get("activated") is not True or amendment.get("policy") != repair.get("policy")
            or amendment.get("baseline_sha256") != repair.get("baseline_sha256")
            or base.file_sha(folder / "ORIGINAL_FREEZE.json") != repair.get("original_freeze_sha256")):
        raise ValueError("The preserved parent167 repair snapshot differs")
    if amendment["policy"] != parent.make_policy(2.0):
        raise ValueError("The preserved parent phase must retain temperature2")
    for name, expected in baseline["original_input_copies"].items():
        if base.file_sha(folder / name) != expected:
            raise ValueError("A parent original-input copy changed")
    for relative, expected in snapshot["source_files"].items():
        if base.file_sha(ROOT / relative) != expected:
            raise ValueError("An existing167 runtime source changed")
    for relative, expected in baseline["original_sources"].items():
        if base.file_sha(folder / "original_sources" / relative) != expected:
            raise ValueError("A parent source copy changed")
    for relative, expected in baseline["protected_files"].items():
        if base.file_sha(out / relative) != expected:
            raise ValueError("A parent scientific observation changed")
    current = base.ledger_rows()
    if any(current.get(key) != value for key, value in baseline["ledger_rows"].items()):
        raise ValueError("A parent paid record changed")
    checks = {"config_sha256": out / "config.json", "plan_sha256": out / "plan.json",
              "baseline_sha256": out / "BASELINE.json", "workflow_gate_sha256": base.HERE / "WORKFLOW_GATE.json",
              "config_source_sha256": Path(config_path or base.HERE / "config.json")}
    if any(snapshot.get(key) != base.file_sha(path) for key, path in checks.items()):
        raise ValueError("Historical config2, task plan or original gate changed")
    return snapshot


def _scientific_check(host):
    engine = host._engine()
    result = engine.verify()
    from experiments.triad_emergent_v9.reviewer_host import validate_archived_envelope
    reviews = [validate_archived_envelope(host.out, number) for number in range(1, 13)
               if engine.outcome(number, "REVIEWER") is not None]
    return engine, {"engine": result, "actual_first_review_bindings": len(reviews)}


def _current_exit(path, target, stage_count, ledger_count):
    proof = parent._read(path)
    if (proof.get("active_producer") is not False or proof.get("observed_exit_code") != 0
            or proof.get("observed_final_host_status") not in {"one_original_stage_complete", "quitting"}
            or type(proof.get("producer_session")) is not int or proof["producer_session"] <= 0
            or proof.get("next_stage") != target
            or proof.get("terminal_main_stages", proof.get("terminal_stages")) != stage_count
            or proof.get("budget", {}).get("request_count") != ledger_count
            or proof.get("budget", {}).get("pending_count") != 0):
        raise ValueError("The current producer's actual exit proof must bind this frontier and ledger")
    return {"path": str(Path(path).absolute()), "sha256": base.file_sha(path),
            "producer_session": proof["producer_session"], "observed_exit_code": 0,
            "observed_final_host_status": proof["observed_final_host_status"],
            "scope": "Exact recorded host-tool exit, frontier and accounting; no fabricated PID"}


def _ready(out, expected_parent_sha256, producer_exit_path, config_path=None):
    out = Path(out).absolute()
    snapshot = parent._read(out / "freeze.json")
    if (snapshot.get("protocol_repair02") is not None
            or base.file_sha(out / "freeze.json") != expected_parent_sha256):
        raise ValueError("The exact current parent167 freeze is required")
    verify_parent_snapshot(out, snapshot, config_path=config_path)
    gate = parent._read(GATE)
    if (gate.get("status") != "passed" or gate.get("source_files") != source_manifest()
            or gate.get("test_files") != test_manifest()
            or gate.get("parent_gate_sha256") != base.file_sha(parent.GATE)
            or gate.get("original_gate_sha256") != base.file_sha(base.HERE / "WORKFLOW_GATE.json")
            or gate.get("config_source_sha256") != base.file_sha(config_path or base.HERE / "config.json")):
        raise ValueError("The exact new sources and test helpers need a passing repair02 gate")
    accounting = base.budget()
    if accounting["pending_count"] or accounting["halt_reason"]:
        raise ValueError("Every paid reservation must settle before repair02")
    host = base.Coordinator(out, config_path=config_path)
    engine, proof = _scientific_check(host)
    target = engine.next_stage()
    if target is None:
        raise ValueError("A remaining unstarted main stage is required")
    for dispatch in (out / "episode/rounds").glob("*/*.dispatch.json"):
        if not dispatch.with_name(dispatch.name.replace(".dispatch.json", ".outcome.json")).is_file():
            raise ValueError("An unfinished stage cannot be reset by repair02")
    rows = base.ledger_rows()
    exit_proof = _current_exit(producer_exit_path, target, proof["engine"]["terminal_stages"], len(rows))
    return host, snapshot, target, rows, exit_proof, proof


def seal(out, *, expected_parent_sha256, producer_exit_path, activation_phrase=None, config_path=None):
    out = Path(out).absolute()
    with base.producer_lock(out):
        host, snapshot, target, rows, exit_proof, proof = _ready(
            out, expected_parent_sha256, producer_exit_path, config_path)
        if activation_phrase is None:
            return {"status": "repair02_ready_not_activated", "policy": make_policy(),
                    "frontier": target, "provider_calls": 0, "new_allowance": False}
        if activation_phrase != ACTIVATION_PHRASE:
            raise ValueError("The exact explicit repair02 activation phrase is required")
        folder = out / "analysis/repair02"
        if (folder / "AMENDMENT.json").exists():
            raise ValueError("Preserve the existing repair02 amendment")
        inputs = {"PARENT_FREEZE.json": out / "freeze.json", "PARENT_WORKFLOW_GATE.json": parent.GATE,
                  "REPAIR_WORKFLOW_GATE.json": GATE, "PRODUCER_EXIT.json": Path(producer_exit_path),
                  "ORIGINAL_CONFIG.json": out / "config.json", "ORIGINAL_PLAN.json": out / "plan.json",
                  "ORIGINAL_ENGINE_MANIFEST.json": out / "episode/engine_manifest.json"}
        protected = parent._protected_files(out)
        protected.update({str(p.relative_to(out)): base.file_sha(p)
                          for p in (out / "analysis/repair01").rglob("*") if p.is_file()})
        baseline = {"recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "parent_freeze_sha256": expected_parent_sha256,
                    "original_input_copies": {name: base.file_sha(path) for name, path in inputs.items()},
                    "protected_files": protected, "ledger_rows": rows,
                    "parent_sources": snapshot["source_files"], "scientific_verification": proof,
                    "effective_frontier": target, "original_budget": base.budget(),
                    "no_slots_or_memory_reset": True, "no_new_allowance": True}
        for name, path in inputs.items():
            parent._save_raw(folder / name, path.read_bytes())
        for relative, expected in snapshot["source_files"].items():
            path = ROOT / relative
            if base.file_sha(path) != expected:
                raise ValueError("A parent runtime source changed while sealing")
            parent._save_raw(folder / "parent_sources" / relative, path.read_bytes())
        base.save_once(folder / "BASELINE.json", baseline)
        amendment = {"repair": REPAIR, "activated": True,
            "activated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "policy": make_policy(),
            "token_budget_notice": TOKEN_BUDGET_NOTICE, "notice_unchanged_from_repair01": True,
            "effective_frontier": target, "parent_freeze_sha256": expected_parent_sha256,
            "baseline_sha256": base.file_sha(folder / "BASELINE.json"), "repair_gate_sha256": base.file_sha(GATE),
            "producer_exit_proof": exit_proof, "activation_phrase": ACTIVATION_PHRASE,
            "authorization": "User explicitly selected temperature1.0 for A/B/C while retaining high reasoning and all prior observations.",
            "only_parameter_change": "Future native actor temperature2.0 to1.0; original config2 remains historical.",
            "unchanged": ["Exact390-byte mechanical notice", "High reasoning and8192 output ceiling",
                          "Models, routes, incentives, tasks, memory, tool allowances and original v9 funding namespace"],
            "live_model_compliance_verified": False}
        base.save_once(folder / "AMENDMENT.json", amendment)
        active = deepcopy(snapshot)
        active["source_files"] = source_manifest()
        active["protocol_repair02"] = {"repair": REPAIR, "policy": make_policy(), "effective_frontier": target,
            "amendment_sha256": base.file_sha(folder / "AMENDMENT.json"),
            "baseline_sha256": base.file_sha(folder / "BASELINE.json"),
            "parent_freeze_sha256": expected_parent_sha256, "repair_gate_sha256": base.file_sha(GATE)}
        base.save_once(folder / "ACTIVE_FREEZE.json", active)
        base.mutable_json(out / "freeze.json", active)
        return {"status": "repair02_activated", "policy": make_policy(), "frontier": target,
                "active_freeze_sha256": base.file_sha(out / "freeze.json"), "provider_calls": 0, "new_allowance": False}


def verify_repair(out, *, config_path=None):
    out = Path(out).absolute()
    folder = out / "analysis/repair02"
    active, amendment, baseline = parent._read(out / "freeze.json"), parent._read(folder / "AMENDMENT.json"), parent._read(folder / "BASELINE.json")
    record = active.get("protocol_repair02", {})
    if (active.get("source_files") != source_manifest() or record.get("policy") != make_policy()
            or amendment.get("policy") != make_policy() or amendment.get("activated") is not True
            or record.get("amendment_sha256") != base.file_sha(folder / "AMENDMENT.json")
            or record.get("baseline_sha256") != base.file_sha(folder / "BASELINE.json")
            or record.get("repair_gate_sha256") != base.file_sha(GATE)
            or parent._read(folder / "ACTIVE_FREEZE.json") != active):
        raise ValueError("Repair02 active policy or source seal changed")
    for name, expected in baseline["original_input_copies"].items():
        if base.file_sha(folder / name) != expected:
            raise ValueError("A preserved repair02 input changed")
    if base.file_sha(folder / "PARENT_FREEZE.json") != record.get("parent_freeze_sha256"):
        raise ValueError("The preserved parent167 freeze changed")
    verify_parent_snapshot(out, parent._read(folder / "PARENT_FREEZE.json"), config_path=config_path)
    for relative, expected in baseline["protected_files"].items():
        if base.file_sha(out / relative) != expected:
            raise ValueError("An earlier scientific observation or repair01 artifact changed")
    for relative, expected in baseline["parent_sources"].items():
        if base.file_sha(folder / "parent_sources" / relative) != expected:
            raise ValueError("A parent167 source copy changed")
    current = base.ledger_rows()
    if any(current.get(key) != value for key, value in baseline["ledger_rows"].items()):
        raise ValueError("A pre-repair02 paid record changed")
    return active


class Coordinator(parent.Coordinator):
    def verify_freeze(self):
        return verify_repair(self.out, config_path=self.config_path)

    def _backend(self):
        if self.backend is None:
            from .backend import Backend
            self.backend = Backend(self.config, self.out, base.AGGREGATE, policy=make_policy())
        return self.backend

    def status(self):
        value = super().status()
        value["protocol_repair02"] = parent._read(self.out / "analysis/repair02/AMENDMENT.json")
        return value

    def report(self):
        result = super().report()
        path = self.out / "analysis/report.json"
        value = parent._read(path)
        value["protocol_repair02"] = parent._read(self.out / "analysis/repair02/AMENDMENT.json")
        value["interpretation"] += " User-selected temperature1 begins only at the repair02 frontier; the earlier temperature2 phases remain distinct observations."
        base.mutable_json(path, value)
        return result

    def render(self, *, allow_incomplete=False):
        self.report()
        from experiments.triad_emergent_v9.analysis import render
        result = render(self.out, allow_incomplete=allow_incomplete)
        destination = Path(result["destination"])
        phases = {key: parent._read(self.out / f"analysis/{key}/AMENDMENT.json") for key in ("repair01", "repair02")}
        text = ("Declared phases: initial temperature2 without the mechanical notice; repair01 temperature2 with the exact notice; "
                "repair02 user-selected temperature1 with the same notice, high reasoning and8192 output ceiling. "
                "All earlier observations remain unchanged. Original config2 is historical; actual per-call wire sampling and sealed frontiers are authoritative. "
                "These operational phase changes do not establish a causal temperature effect or independent replications.")
        path = destination / "REPORT.md"
        path.write_text(text + "\n\n" + path.read_text())
        path = destination / "GALLERY.html"
        path.write_text(path.read_text().replace("<body>", "<body><aside>" + html.escape(text) + "</aside>", 1))
        path = destination / "EVIDENCE.json"
        data = parent._read(path)
        data["protocol_amendments"] = phases
        base.mutable_json(path, data)
        path = destination / "EXACT_SYSTEM_PROMPTS_BY_STAGE.txt"
        path.write_text(path.read_text() + "\n\nUNCHANGED PROSPECTIVE NATIVE-CALL NOTICE\n" + TOKEN_BUDGET_NOTICE + "\n")
        return {**result, "protocol_repair02": make_policy()}
