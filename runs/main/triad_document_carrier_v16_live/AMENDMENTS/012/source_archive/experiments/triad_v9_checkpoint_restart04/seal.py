"""Direct parent174 verification and explicit, offline-only restart04 seal."""
from __future__ import annotations

import argparse
from copy import deepcopy
import datetime as dt
from pathlib import Path
import sqlite3

from experiments.triad_emergent_v9 import coordinator as base
from experiments.triad_emergent_v9 import funding as original_funding
from experiments.triad_emergent_v9.offline_gate import test_manifest
from experiments.triad_v9_protocol_repair01 import coordinator as first
from experiments.triad_v9_protocol_repair02 import coordinator as parent
from .coordinator import ROOT, HERE, GATE, REPAIR, source_manifest

ACTIVATION_PHRASE = "ACTIVATE_RESTART04_WHOLE_ROUND_FROM_COMPLETED_CHECKPOINT"


def verify_parent_snapshot(out, snapshot, *, config_path=None):
    """Validate the original active174 snapshot directly, never swap freezes."""
    out, folder = Path(out).absolute(), Path(out).absolute() / "analysis/repair02"
    amendment, baseline = first._read(folder / "AMENDMENT.json"), first._read(folder / "BASELINE.json")
    record = snapshot.get("protocol_repair02", {})
    if (snapshot.get("source_files") != parent.source_manifest() or first._read(folder / "ACTIVE_FREEZE.json") != snapshot
            or record.get("policy") != parent.make_policy() or amendment.get("policy") != parent.make_policy()
            or amendment.get("activated") is not True or record.get("amendment_sha256") != base.file_sha(folder / "AMENDMENT.json")
            or record.get("baseline_sha256") != base.file_sha(folder / "BASELINE.json")
            or record.get("repair_gate_sha256") != base.file_sha(parent.GATE)
            or record.get("parent_freeze_sha256") != base.file_sha(folder / "PARENT_FREEZE.json")):
        raise ValueError("The preserved parent174 snapshot changed")
    for name, expected in baseline["original_input_copies"].items():
        if base.file_sha(folder / name) != expected:
            raise ValueError("A repair02 preserved input changed")
    parent.verify_parent_snapshot(out, first._read(folder / "PARENT_FREEZE.json"), config_path=config_path)
    for relative, expected in baseline["protected_files"].items():
        if base.file_sha(out / relative) != expected:
            raise ValueError("A repair02 protected observation changed")
    for relative, expected in baseline["parent_sources"].items():
        if base.file_sha(folder / "parent_sources" / relative) != expected:
            raise ValueError("A repair02 source copy changed")
    rows = base.ledger_rows()
    if any(rows.get(key) != value for key, value in baseline["ledger_rows"].items()):
        raise ValueError("A repair02 paid record changed")
    for relative, expected in snapshot["source_files"].items():
        if base.file_sha(ROOT / relative) != expected:
            raise ValueError("An existing174 runtime source changed")
    return snapshot


def _raw_rows():
    with sqlite3.connect(Path(base.AGGREGATE).absolute().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return {row["request_id"]: dict(row) for row in db.execute("SELECT * FROM requests ORDER BY request_id")}


def _legacy_loss(out, snapshot, proof_path, engine):
    out, proof_path = Path(out).absolute(), Path(proof_path).absolute()
    proof, target, rows = first._read(proof_path), engine.next_stage(), _raw_rows()
    row = proof.get("failed_request")
    expected_id = original_funding.NAMESPACE + ":episode.r07.A_IMAGE_AND_VOTE.visual1"
    if (target != {"round": 7, "stage": "A_IMAGE_AND_VOTE", "request_id": "episode.r07.A_IMAGE_AND_VOTE",
                   "role": "A", "mode": "visual_vote"}
            or engine.summary()["terminal_stages"] != 48 or not isinstance(row, dict)
            or row.get("request_id") != expected_id or rows.get(expected_id) != row
            or row.get("status") != "uncertain" or row.get("actual_units") is not None
            or row.get("response_json") is not None or row.get("receipt_json") is not None
            or row.get("result_hash") is not None or not row.get("reserved_units", 0) > 0
            or proof.get("next_stage") != target or proof.get("terminal_main_stages") != 48
            or proof.get("freeze_sha256") != base.file_sha(out / "freeze.json")
            or proof.get("budget", {}).get("request_count") != len(rows)):
        raise ValueError("Only the exact original unknown R7 first-slot loss can initialize restart04")
    first._clean_exit(proof_path)
    if not proof_path.is_relative_to(out):
        raise ValueError("Original producer exit evidence must remain inside the same run")
    prefix = original_funding.NAMESPACE + ":episode.r07."
    if [key for key in rows if key.startswith(prefix)] != [expected_id]:
        raise ValueError("The original failed round has unexpected additional paid intents")
    request = out / "episode/rounds/r07/A_IMAGE_AND_VOTE.request.json"
    dispatch = request.with_name("A_IMAGE_AND_VOTE.dispatch.json")
    if (first._read(request) != engine.actor_request()
            or first._read(dispatch) != {"request_id": target["request_id"],
                "request_hash": first._read(request)["request_hash"],
                "request_file_sha256": base.file_sha(request), "one_dispatch": True}
            or request.with_name("A_IMAGE_AND_VOTE.outcome.json").exists()):
        raise ValueError("The original unresolved request and dispatch must remain exact")
    archive = out / "provider_evidence/transport_requests" / (base.sha(expected_id.split(":", 1)[1].encode()) + ".json")
    first._read(archive)
    return {"failed_request": row, "producer_exit_proof": {"path": str(proof_path), "sha256": base.file_sha(proof_path)},
            "parent_freeze_sha256": base.sha((base.canonical(snapshot) + "\n").encode()),
            "bound_files": {str(path.relative_to(out)): base.file_sha(path) for path in (request, dispatch, archive, proof_path)},
            "interpretation": "Infrastructure response loss; no model behavior or actual charge amount was observed for this missing response."}


def _ready(out, expected_parent_sha256, producer_exit_path, config_path=None):
    out = Path(out).absolute()
    snapshot = first._read(out / "freeze.json")
    if (snapshot.get("protocol_checkpoint_restart04") is not None or snapshot.get("protocol_repair03") is not None
            or base.file_sha(out / "freeze.json") != expected_parent_sha256):
        raise ValueError("The exact original active parent174 freeze is required; repair03 remains inactive")
    verify_parent_snapshot(out, snapshot, config_path=config_path)
    gate = first._read(GATE)
    if (gate.get("status") != "passed" or gate.get("source_files") != source_manifest()
            or gate.get("test_files") != test_manifest()
            or gate.get("parent_gate_sha256") != base.file_sha(parent.GATE)
            or gate.get("original_gate_sha256") != base.file_sha(base.HERE / "WORKFLOW_GATE.json")
            or gate.get("config_source_sha256") != base.file_sha(config_path or base.HERE / "config.json")):
        raise ValueError("The exact restart04 closure and tests need a passing new unpaid fault gate")
    accounting = base.budget()
    if accounting["pending_count"] or accounting["halt_reason"]:
        raise ValueError("Pending reservation or funding halt requires repair before activation")
    host = base.Coordinator(out, config_path=config_path)
    engine, scientific = parent._scientific_check(host)
    declaration = _legacy_loss(out, snapshot, producer_exit_path, engine)
    with sqlite3.connect(Path(base.AGGREGATE).absolute().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute("SELECT * FROM requests WHERE status IN ('pending','uncertain')"):
            if dict(row) != declaration["failed_request"] and not original_funding.FundingLedger._sealed(db, row):
                raise ValueError("An unrelated unresolved paid intent lacks its original authority")
    return snapshot, declaration, scientific, engine


def seal(out, *, expected_parent_sha256, producer_exit_path, activation_phrase=None, config_path=None):
    out = Path(out).absolute()
    from .backend import make_policy
    with base.producer_lock(out):
        snapshot, declaration, scientific, engine = _ready(out, expected_parent_sha256, producer_exit_path, config_path)
        if activation_phrase is None:
            return {"status": "restart04_ready_not_activated", "policy": make_policy(),
                    "completed_rounds": 6, "next_round": 7, "next_attempt": 2,
                    "provider_calls": 0, "new_allowance": False}
        if activation_phrase != ACTIVATION_PHRASE:
            raise ValueError("The explicit restart04 activation phrase is required")
        folder = out / "analysis/restart04"
        if (folder / "AMENDMENT.json").exists():
            raise ValueError("Preserve the existing restart04 amendment")
        inputs = {"PARENT_FREEZE.json": out / "freeze.json", "PARENT_WORKFLOW_GATE.json": parent.GATE,
                  "RESTART_WORKFLOW_GATE.json": GATE, "PRODUCER_EXIT.json": Path(producer_exit_path),
                  "ORIGINAL_CONFIG.json": out / "config.json", "ORIGINAL_PLAN.json": out / "plan.json",
                  "ORIGINAL_ENGINE_MANIFEST.json": out / "episode/engine_manifest.json"}
        protected = first._protected_files(out)
        for name in ("repair01", "repair02"):
            protected.update({str(path.relative_to(out)): base.file_sha(path)
                              for path in (out / "analysis" / name).rglob("*") if path.is_file()})
        baseline = {"protected_files": protected, "ledger_rows": base.ledger_rows(), "parent_sources": snapshot["source_files"],
                    "original_input_copies": {name: base.file_sha(path) for name, path in inputs.items()},
                    "scientific_verification": scientific, "original_budget": base.budget(),
                    "legacy_completed_rounds": 6, "legacy_chain_head": engine.verify()["chain_head"],
                    "all_prior_rows_and_unknown_reserves_retained": True, "new_allowance": False}
        for name, path in inputs.items():
            first._save_raw(folder / name, path.read_bytes())
        for relative in snapshot["source_files"]:
            first._save_raw(folder / "parent_sources" / relative, (ROOT / relative).read_bytes())
        base.save_once(folder / "BASELINE.json", baseline)
        from .attempts import AttemptStore
        store = AttemptStore(out)
        from experiments.triad_emergent_v9.engine import STAGES
        files = {str(engine._path(n, stage).relative_to(out)): base.file_sha(engine._path(n, stage))
                 for n in range(1, 7) for stage in STAGES}
        store.initialize(baseline["legacy_chain_head"], files)
        fault = {"technical": True, "fault_class": "legacy_response_lost", "round": 7,
                 "stage": "A_IMAGE_AND_VOTE", "exception_class": "UnobservedTransportException",
                 "request_id": declaration["failed_request"]["request_id"], "model_behavior_observed": False}
        marker = store.register_legacy_abort(fault)
        context = {key: marker[key] for key in ("round", "attempt", "attempt_id", "working_root", "prior_checkpoint_sha256", "legacy")}
        declaration["context"] = context
        base.save_once(folder / "LEGACY_ABORT.json", declaration)
        from .funding import seal_abandoned_attempt
        marker_path = out / "attempts/r07/a01/abandonment.json"
        seal_abandoned_attempt(out, context, fault, aggregate_path=base.AGGREGATE,
                               abandonment_path=marker_path, expected_abandonment_sha256=base.file_sha(marker_path),
                               legacy_proof_path=folder / "LEGACY_ABORT.json")
        amendment = {"repair": REPAIR, "activated": True, "activated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                     "policy": make_policy(), "parent_freeze_sha256": expected_parent_sha256,
                     "baseline_sha256": base.file_sha(folder / "BASELINE.json"), "restart_gate_sha256": base.file_sha(GATE),
                     "legacy_abort_sha256": base.file_sha(folder / "LEGACY_ABORT.json"),
                     "checkpoint_manifest_sha256": base.file_sha(store.manifest_path),
                     "authorization": "Latest user explicitly requires whole-current-round restart from the prior completed-round checkpoint for API or harness faults.",
                     "legacy_rounds_unchanged": 6, "original_failed_round_attempt_preserved": True,
                     "max_technical_attempts_per_round": 3, "new_allowance": False,
                     "behavioral_outcomes_never_trigger_restart": True, "network_success_guaranteed": False}
        base.save_once(folder / "AMENDMENT.json", amendment)
        active = deepcopy(snapshot)
        active["source_files"] = source_manifest()
        active["protocol_checkpoint_restart04"] = {key: amendment[key] for key in (
            "repair", "policy", "parent_freeze_sha256", "baseline_sha256", "restart_gate_sha256",
            "legacy_abort_sha256", "checkpoint_manifest_sha256")}
        active["protocol_checkpoint_restart04"]["amendment_sha256"] = base.file_sha(folder / "AMENDMENT.json")
        base.save_once(folder / "ACTIVE_FREEZE.json", active)
        base.mutable_json(out / "freeze.json", active)
        return {"status": "restart04_activated", "completed_rounds": 6, "next_round": 7, "next_attempt": 2,
                "active_freeze_sha256": base.file_sha(out / "freeze.json"), "provider_calls": 0, "new_allowance": False}


def verify_repair(out, *, config_path=None):
    from .backend import make_policy
    from .attempts import AttemptStore
    out, folder = Path(out).absolute(), Path(out).absolute() / "analysis/restart04"
    active, amendment, baseline = first._read(out / "freeze.json"), first._read(folder / "AMENDMENT.json"), first._read(folder / "BASELINE.json")
    record = active.get("protocol_checkpoint_restart04", {})
    files = {"amendment_sha256": folder / "AMENDMENT.json", "baseline_sha256": folder / "BASELINE.json",
             "restart_gate_sha256": GATE, "legacy_abort_sha256": folder / "LEGACY_ABORT.json",
             "checkpoint_manifest_sha256": AttemptStore(out).manifest_path, "parent_freeze_sha256": folder / "PARENT_FREEZE.json"}
    if (active.get("source_files") != source_manifest() or record.get("policy") != make_policy()
            or amendment.get("policy") != make_policy() or amendment.get("activated") is not True
            or any(record.get(key) != base.file_sha(path) for key, path in files.items())
            or first._read(folder / "ACTIVE_FREEZE.json") != active):
        raise ValueError("The active restart04 source/policy/amendment seal changed")
    for name, expected in baseline["original_input_copies"].items():
        if base.file_sha(folder / name) != expected:
            raise ValueError("A preserved restart04 input changed")
    verify_parent_snapshot(out, first._read(folder / "PARENT_FREEZE.json"), config_path=config_path)
    for relative, expected in baseline["protected_files"].items():
        if base.file_sha(out / relative) != expected:
            raise ValueError("A pre-restart04 observation changed")
    for relative, expected in baseline["parent_sources"].items():
        if base.file_sha(folder / "parent_sources" / relative) != expected:
            raise ValueError("A preserved parent174 source copy changed")
    current = base.ledger_rows()
    if any(current.get(key) != expected for key, expected in baseline["ledger_rows"].items()):
        raise ValueError("A pre-restart04 aggregate row changed")
    AttemptStore(out).verify()
    return active


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--expected-parent-freeze-sha256", required=True)
    parser.add_argument("--producer-exit-proof", type=Path, required=True)
    parser.add_argument("--activation-phrase", choices=[ACTIVATION_PHRASE])
    args = parser.parse_args(argv)
    print(base.canonical(seal(args.out, expected_parent_sha256=args.expected_parent_freeze_sha256,
                              producer_exit_path=args.producer_exit_proof, activation_phrase=args.activation_phrase,
                              config_path=args.config)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
