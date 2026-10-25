"""Explicitly sealed continuation of one existing episode; no new allowance.

Nothing in this module activates a repair at import. Activation is an explicit
host action after the original producer exits and all paid requests settle.
"""
from __future__ import annotations

from copy import deepcopy
import datetime as dt
import html
import json
import os
from pathlib import Path

from experiments.triad_emergent_v9 import coordinator as original
from .backend import REPAIR, TOKEN_BUDGET_NOTICE, NOTICE_SHA256, make_policy


ROOT = original.ROOT
HERE = Path(__file__).resolve().parent
GATE = HERE / "WORKFLOW_GATE.json"
ACTIVATION_PHRASE = "ACTIVATE_REPAIR01_AFTER_CLEAN_PRODUCER_EXIT"


def source_manifest():
    sources = original.source_manifest()
    for path in sorted(HERE.rglob("*")):
        if (path.is_file() and path.suffix in {".py", ".md", ".txt"}
                and "tests" not in path.relative_to(HERE).parts and "__pycache__" not in path.parts):
            sources[str(path.relative_to(ROOT))] = original.file_sha(path)
    return sources


def _save_raw(path, raw):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Repair evidence cannot traverse symlinks")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("Immutable repair evidence differs")
        return
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _read(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Repair evidence cannot traverse symlinks")
    return original.read_json(path)


def _protected_files(out):
    paths = set()
    for name in ("episode", "provider_evidence", "actor_deliveries", "reviewer_packets", "reviewer_host", "reviewer_dispatches"):
        paths.update(p for p in (out / name).rglob("*") if p.is_file())
    paths.update(out / name for name in ("config.json", "plan.json", "BASELINE.json"))
    return {str(p.relative_to(out)): original.file_sha(p) for p in sorted(paths)}


def _clean_exit(proof_path):
    proof = _read(proof_path)
    pid = proof.get("producer_pid")
    session = proof.get("producer_session")
    host_observed = (proof.get("observed_exit_code") == 0
        and proof.get("observed_final_host_status") == "quitting"
        and proof.get("active_producer") is False and type(session) is int and session > 0)
    pid_observed = (proof.get("exit_code") == 0 and type(pid) is int and pid > 1
                    and not Path(f"/proc/{pid}").exists())
    if not (host_observed or pid_observed):
        raise ValueError("The original producer's actual clean-exit proof is required")
    if pid is not None and (type(pid) is not int or pid <= 1 or Path(f"/proc/{pid}").exists()):
        raise ValueError("A supplied original producer PID must be absent")
    return {"path": str(Path(proof_path).absolute()), "sha256": original.file_sha(proof_path),
            "producer_pid": pid, "producer_session": session, "exit_code": 0,
            "process_absence_verified": pid_observed,
            "scope": "Recorded host tool exit; PID absence additionally checked only when supplied"}


def _ready(out, expected_initial_sha256, producer_exit_path, temperature, config_path=None):
    out = Path(out).absolute()
    host = original.Coordinator(out, config_path=config_path)
    initial = host.verify_freeze()
    if initial.get("protocol_repair01") is not None:
        raise ValueError("An already amended run cannot be resealed as an initial repair")
    if original.file_sha(out / "freeze.json") != expected_initial_sha256:
        raise ValueError("Initial freeze differs from the explicit expected hash")
    policy = make_policy(temperature)
    gate = _read(GATE)
    if (gate.get("status") != "passed" or gate.get("source_files") != source_manifest()
            or gate.get("original_workflow_gate_sha256") != original.file_sha(original.HERE / "WORKFLOW_GATE.json")
            or gate.get("config_source_sha256") != original.file_sha(host.config_path)):
        raise ValueError("The exact prospective repair sources need a passing unpaid gate")
    if initial["source_files"] != original.source_manifest():
        raise ValueError("Initial v9 sources are not the unchanged original closure")
    accounting = original.budget()
    if accounting["pending_count"] or accounting["halt_reason"]:
        raise ValueError("Every original paid reservation must settle before repair activation")
    exit_proof = _clean_exit(producer_exit_path)
    engine = host._engine()
    host.verify()
    target = engine.next_stage()
    if target is None:
        raise ValueError("This repair requires a remaining main stage")
    for dispatch in (out / "episode/rounds").glob("*/*.dispatch.json"):
        if not dispatch.with_name(dispatch.name.replace(".dispatch.json", ".outcome.json")).is_file():
            raise ValueError("An unfinished dispatched stage cannot receive a new protocol")
    return host, initial, policy, exit_proof, target


def seal(out, *, expected_initial_sha256, producer_exit_path, temperature,
         activation_phrase=None, config_path=None):
    """Dry-run by default; only the exact explicit phrase changes active freeze."""
    out = Path(out).absolute()
    with original.producer_lock(out):
        host, initial, policy, exit_proof, target = _ready(
            out, expected_initial_sha256, producer_exit_path, temperature, config_path)
        if activation_phrase is None:
            return {"status": "prospective_repair_ready_not_activated", "policy": policy,
                    "frontier": target, "provider_calls": 0, "new_allowance": False}
        if activation_phrase != ACTIVATION_PHRASE:
            raise ValueError("The explicit root activation phrase is required")
        folder = out / "analysis/repair01"
        if (folder / "AMENDMENT.json").exists():
            raise ValueError("Preserve the already recorded amendment")
        inputs = {
            "ORIGINAL_FREEZE.json": out / "freeze.json",
            "ORIGINAL_CONFIG.json": out / "config.json",
            "ORIGINAL_SOURCE_CONFIG.json": host.config_path,
            "ORIGINAL_PLAN.json": out / "plan.json",
            "ORIGINAL_ENGINE_MANIFEST.json": out / "episode/engine_manifest.json",
            "ORIGINAL_WORKFLOW_GATE.json": original.HERE / "WORKFLOW_GATE.json",
            "REPAIR_WORKFLOW_GATE.json": GATE,
            "ORIGINAL_PRODUCER_EXIT.json": Path(producer_exit_path),
        }
        snapshots = {name: original.file_sha(path) for name, path in inputs.items()}
        baseline = {"recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "initial_freeze_sha256": expected_initial_sha256,
                    "protected_files": _protected_files(out), "ledger_rows": original.ledger_rows(),
                    "original_aggregate_budget": original.budget(), "original_sources": initial["source_files"],
                    "original_input_copies": snapshots, "frontier": target,
                    "completed_stage_chain": host._engine().verify()["chain_head"],
                    "no_actor_slots_reset": True, "no_allowance_reset": True}
        for name, path in inputs.items():
            _save_raw(folder / name, path.read_bytes())
        for relative, expected in initial["source_files"].items():
            path = ROOT / relative
            if original.file_sha(path) != expected:
                raise ValueError("An original source changed while sealing")
            _save_raw(folder / "original_sources" / relative, path.read_bytes())
        original.save_once(folder / "BASELINE.json", baseline)
        amendment = {
            "repair": REPAIR, "activated": True,
            "activated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "policy": policy, "token_budget_notice": TOKEN_BUDGET_NOTICE,
            "effective_frontier": target, "original_base_configuration_temperature": 2.0,
            "initial_temperature2_observations_preserved": True,
            "baseline_sha256": original.file_sha(folder / "BASELINE.json"),
            "original_freeze_sha256": expected_initial_sha256,
            "repair_gate_sha256": original.file_sha(GATE), "producer_exit_proof": exit_proof,
            "activation_phrase": ACTIVATION_PHRASE,
            "changes": ["One bounded mechanical generation-budget notice before future native calls.",
                        "Explicit prospective effective sampling, reported alongside the unchanged base configuration."],
            "unchanged": ["All completed observations and original paid responses", "Native tools and original per-stage allowances",
                          "High reasoning and8192 output ceiling", "Models, routes, incentives, tasks, memory and original funding namespace"],
            "live_model_compliance_verified": False,
        }
        original.save_once(folder / "AMENDMENT.json", amendment)
        active = deepcopy(initial)
        active["source_files"] = source_manifest()
        active["protocol_repair01"] = {
            "repair": REPAIR, "policy": policy, "effective_frontier": target,
            "amendment_sha256": original.file_sha(folder / "AMENDMENT.json"),
            "baseline_sha256": original.file_sha(folder / "BASELINE.json"),
            "original_freeze_sha256": expected_initial_sha256,
            "repair_gate_sha256": original.file_sha(GATE),
        }
        original.save_once(folder / "ACTIVE_FREEZE.json", active)
        original.mutable_json(out / "freeze.json", active)
        return {"status": "repair_activated", "policy": policy, "frontier": target,
                "active_freeze_sha256": original.file_sha(out / "freeze.json"),
                "provider_calls": 0, "new_allowance": False}


def verify_repair(out, *, config_path=None):
    out = Path(out).absolute()
    folder = out / "analysis/repair01"
    active, amendment, baseline = (_read(out / "freeze.json"), _read(folder / "AMENDMENT.json"),
                                   _read(folder / "BASELINE.json"))
    repair = active.get("protocol_repair01", {})
    if (repair.get("repair") != REPAIR or active.get("source_files") != source_manifest()
            or original.file_sha(folder / "AMENDMENT.json") != repair.get("amendment_sha256")
            or original.file_sha(folder / "BASELINE.json") != repair.get("baseline_sha256")
            or original.file_sha(GATE) != repair.get("repair_gate_sha256")
            or _read(folder / "ACTIVE_FREEZE.json") != active
            or amendment.get("activated") is not True or amendment.get("policy") != repair.get("policy")
            or amendment.get("baseline_sha256") != repair.get("baseline_sha256")):
        raise ValueError("The active mechanical repair seal changed")
    if amendment["policy"] != make_policy(amendment["policy"]["temperature"]):
        raise ValueError("The amended native-call policy is not canonical")
    for name, expected in baseline["original_input_copies"].items():
        if original.file_sha(folder / name) != expected:
            raise ValueError("An original pre-repair input copy changed")
    initial = _read(folder / "ORIGINAL_FREEZE.json")
    if original.file_sha(folder / "ORIGINAL_FREEZE.json") != repair["original_freeze_sha256"]:
        raise ValueError("The original freeze copy changed")
    if (active.get("config_sha256") != original.file_sha(out / "config.json")
            or active.get("config_source_sha256") != original.file_sha(config_path or original.HERE / "config.json")
            or active.get("plan_sha256") != original.file_sha(out / "plan.json")
            or active.get("baseline_sha256") != original.file_sha(out / "BASELINE.json")
            or active.get("workflow_gate_sha256") != original.file_sha(original.HERE / "WORKFLOW_GATE.json")):
        raise ValueError("Base configuration, task plan or original gate changed")
    for relative, expected in baseline["original_sources"].items():
        if (original.file_sha(ROOT / relative) != expected
                or original.file_sha(folder / "original_sources" / relative) != expected):
            raise ValueError("Original source or pre-repair source copy changed")
    for relative, expected in baseline["protected_files"].items():
        if original.file_sha(out / relative) != expected:
            raise ValueError("An original scientific observation changed")
    current = original.ledger_rows()
    if any(current.get(key) != value for key, value in baseline["ledger_rows"].items()):
        raise ValueError("A pre-repair paid request changed")
    return active


class Coordinator(original.Coordinator):
    def init(self):
        self.verify_freeze()
        return self.status()

    def verify_freeze(self):
        return verify_repair(self.out, config_path=self.config_path)

    def _backend(self):
        if self.backend is None:
            from .backend import Backend
            policy = _read(self.out / "analysis/repair01/AMENDMENT.json")["policy"]
            self.backend = Backend(self.config, self.out, original.AGGREGATE, policy=policy)
        return self.backend

    def status(self):
        value = super().status()
        value["protocol_repair01"] = _read(self.out / "analysis/repair01/AMENDMENT.json")
        return value

    def report(self):
        result = super().report()
        path = self.out / "analysis/report.json"
        value = _read(path)
        value["protocol_repair01"] = _read(self.out / "analysis/repair01/AMENDMENT.json")
        value["interpretation"] += " Initial temperature2 protocol failures remain observed; the prospective mechanical/sampling amendment is a separate declared phase, not a fresh replication."
        original.mutable_json(path, value)
        return result

    def render(self, *, allow_incomplete=False):
        self.report()
        from experiments.triad_emergent_v9.analysis import render
        result = render(self.out, allow_incomplete=allow_incomplete)
        destination = Path(result["destination"])
        amendment = _read(self.out / "analysis/repair01/AMENDMENT.json")
        text = ("Protocol amendment: original temperature2 observations are preserved. "
                f"After the sealed frontier {original.canonical(amendment['effective_frontier'])}, "
                f"future native calls use temperature{amendment['policy']['temperature']} and the declared mechanical output-budget notice. "
                "Base config2 is historical; actual per-call wire sampling is authoritative. "
                "This is a declared phase change, not an independent replication or proof of improved live compliance.")
        report = destination / "REPORT.md"
        report.write_text(text + "\n\n" + report.read_text())
        gallery = destination / "GALLERY.html"
        gallery.write_text(gallery.read_text().replace("<body>", "<body><aside>" + html.escape(text) + "</aside>", 1))
        evidence = destination / "EVIDENCE.json"
        value = _read(evidence)
        value["protocol_repair01"] = amendment
        original.mutable_json(evidence, value)
        prompts = destination / "EXACT_SYSTEM_PROMPTS_BY_STAGE.txt"
        prompts.write_text(prompts.read_text() + "\n\nPROSPECTIVE MECHANICAL NATIVE-CALL NOTICE\n" + TOKEN_BUDGET_NOTICE + "\n")
        return {**result, "protocol_repair01": amendment["policy"]}
