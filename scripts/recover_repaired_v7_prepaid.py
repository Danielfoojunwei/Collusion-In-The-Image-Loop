"""Resume the exact first A9 dispatch only after proving it made no paid call.

This narrowly scoped operation preserves both existing dispatch markers. It is
not a general retry for uncertain stages. Any v7 paid ledger row blocks it.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.triad_repaired_v7.coordinator import (
    TriadCoordinator, DEFAULT_OUT, ContractError, run_lock, read_json,
    save_once, digest, file_digest, canonical,
)
from experiments.triad_repaired_v7.backend import TriadBackend


def require_no_paid_rows(ledger):
    with sqlite3.connect(Path(ledger).as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT request_id FROM requests WHERE request_id LIKE 'triad_repaired_v7:%'").fetchall()
    if rows:
        raise ContractError("A paid or reserved v7 attempt exists; this operation cannot redispatch it")


def checked_request(coordinator):
    """All checks are offline and cannot construct or call a provider."""
    coordinator._verify_freeze()
    coordinator._require_workflow_gate()
    if coordinator.out != DEFAULT_OUT.absolute():
        raise ContractError("Only the exact first v7 continuation is eligible")
    ledger = ROOT / coordinator.config["budget"]["aggregate_ledger"]
    require_no_paid_rows(ledger)
    case, engine, target = coordinator._pending()
    if target["round"] != 9 or target["stage"] != "A_IMAGE_AND_VOTE":
        raise ContractError("Only the unchanged first A9 stage is eligible")
    request = engine.actor_request()
    preview = coordinator.out / "analysis/UNPAID_A9_PREVIEW.json"
    stage = engine.directory / "rounds/r09"
    if request != read_json(preview) or request != read_json(stage / "A_IMAGE_AND_VOTE.request.json"):
        raise ContractError("The original preview or claimed request changed")
    identifier = digest({"case": case["case_id"], "request_id": request["request_id"]})
    expected = {"request_id": request["request_id"], "request_hash": request["request_hash"],
                "case_id": case["case_id"], "round": 9, "stage": "A_IMAGE_AND_VOTE"}
    cache = coordinator.out / "stage_cache" / identifier
    if read_json(cache.with_suffix(".claim.json")) != expected:
        raise ContractError("The original coordinator claim changed")
    dispatch = read_json(stage / "A_IMAGE_AND_VOTE.dispatch.json")
    if dispatch.get("request_hash") != request["request_hash"]:
        raise ContractError("The original engine dispatch changed")
    response = cache.with_suffix(".response.json")
    if response.exists() or engine.outcome(9, "A_IMAGE_AND_VOTE"):
        raise ContractError("A9 already has a response or observation")
    original = coordinator.out / "analysis/FIRST_ADVANCE_PREPAID_FAILURE.json"
    before = coordinator.out / "analysis/prepaid_repair_01/freeze.before.json"
    if not original.is_file() or not before.is_file():
        raise ContractError("Exact original no-POST failure and freeze evidence are required")
    failure = read_json(original)
    if (failure.get("new_v7_paid_rows_count") != 0 or failure.get("experimental_response") is not False
            or failure.get("actor_request_hash") != request["request_hash"]):
        raise ContractError("Original no-POST evidence is not bound to this dispatch")
    validator = object.__new__(TriadBackend)
    validator.out, validator.config = coordinator.out, coordinator.config
    validator.evidence_dir = coordinator.out / "provider_evidence"
    validator._references(request)
    return case, engine, target, request, response, {
        "operation": "resume_exact_A9_after_hash_bound_reference_repair_before_any_POST",
        "request_hash": request["request_hash"], "paid_v7_rows_before_operation": 0,
        "original_failure_sha256": file_digest(original), "original_freeze_sha256": file_digest(before),
        "active_freeze_sha256": file_digest(coordinator.out / "freeze.json"),
        "preview_sha256": file_digest(preview), "both_original_dispatch_claims_preserved": True,
        "model_inputs_changed": False, "image_bytes_changed": False,
        "first_new_round": 9, "ends_before_B9": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Make the first actual A9 call after offline preflight")
    args = parser.parse_args()
    coordinator = TriadCoordinator()
    with run_lock(coordinator.out / "run.lock"):
        case, engine, target, request, response, evidence = checked_request(coordinator)
        if not args.execute:
            print(canonical({"status": "prepaid_recovery_checks_passed", "evidence": evidence, "paid_calls": 0}))
            return
        save_once(coordinator.out / "analysis/PREPAID_RESUMPTION_AUTHORIZATION.json", evidence)
        backend = coordinator._backend()
        observed = backend.run_actor(request)
        save_once(response, observed)
        outcome = engine.accept_actor(observed)
        print(canonical({"status": "original_A9_dispatch_completed", "outcome_status": outcome["status"],
                         "next": engine.next_stage(), "original_claims_preserved": True}))


if __name__ == "__main__":
    main()
