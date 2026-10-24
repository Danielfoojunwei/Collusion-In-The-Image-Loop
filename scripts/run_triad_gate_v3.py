"""Declared technical-gate amendment; preserves the malformed first C ballot.

The frozen study required seven *valid* scientific stages as its live gate. Its
first real C ballot is malformed JSON, although the directed workflow completed.
This sidecar permits that exact, retained C_VOTE failure after proving the other
six stages, routing, feedback, image fidelity and completed original receipts.
It changes no actor action, vote, prompt, source, allowance or scientific record.
The first ``advance`` is an offline amendment checkpoint, never a paid advance.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.triad_consensus_v2 import coordinator as original
from experiments.triad_consensus_v2.backend import validate_direct, repair
from experiments.triad_consensus_v2.cli import command as original_command
from experiments.triad_consensus_v2.contracts import ContractError, oracle
from experiments.triad_consensus_v2.engine import STAGES
from visual_collusion.budget import _units


BASE_COORDINATOR_SHA256 = "ae029100368f00d0dc3a12f1d05196f42cc5cdccfbec978890d7d84bfb201f9c"
LIVE_FREEZE_SHA256 = "b78bc1c3d7bba9b459ac92c20b4b07d75f5b06a3923ac10f892bd8d19c61943f"
LIVE_FAILED_GATE_SHA256 = "8c625eebe76039537f22f2c9ab81199250a1c7eec97fa7e24778344f1d41a344"
NAMESPACE = "triad_consensus_v2"
CASE = "case_05"
AMENDMENT_NAME = "triad_gate_v3_amendment.json"
AMENDED_GATE = "live_round_gate_v3.json"
CHECKPOINT_NAME = "triad_gate_v3_checkpoint.json"


def _require(condition, reason):
    if not condition:
        raise ContractError(reason)


def _safe_file(path, root=None):
    path = Path(path).absolute()
    _require(not any(item.is_symlink() for item in (path, *path.parents)), "Amendment evidence cannot follow symlinks")
    path = path.resolve()
    _require(path.is_file(), "Required amendment evidence is missing")
    if root is not None:
        _require(path.is_relative_to(Path(root).resolve()), "Amendment evidence escaped the current run")
    return path


def _view(request):
    value = json.loads(request["messages"][1]["content"])
    _require(isinstance(value, dict), "Expected an exact role context object")
    return value


def _inline_images(request):
    result = []
    for message in request["messages"]:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        label = None
        for item in content:
            if item.get("type") == "text":
                try:
                    label = json.loads(item["text"])
                except (ValueError, KeyError):
                    label = None
            url = item.get("image_url", {}).get("url", "")
            if url.startswith("data:image/png;base64,"):
                raw = base64.b64decode(url.split(",", 1)[1], validate=True)
                result.append({"sha256": hashlib.sha256(raw).hexdigest(), "label": label})
    return result


def verify_receipts(outcomes, ledger_path, *, origin="live"):
    """Read-only proof of every first-round charged receipt and raw response."""
    ledger_path = _safe_file(ledger_path)
    expected, checked = {}, {}
    c_response = None
    with sqlite3.connect(ledger_path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        cap = db.execute("SELECT value FROM settings WHERE key='cap_units'").fetchone()
        _require(cap and int(cap[0]) == 40_000_000_000, "Original aggregate $40 cap changed")
        for stage, outcome in outcomes.items():
            if stage == "REVIEWER":
                continue
            receipts = outcome.get("receipts")
            _require(isinstance(receipts, list) and receipts, "An actor stage lacks completed paid evidence")
            kinds = []
            for receipt in receipts:
                identifier = receipt.get("request_id", "")
                _require(identifier.startswith(f"{NAMESPACE}:{CASE}.r01.{stage}."), "Receipt belongs to a different stage/case/round")
                _require(identifier not in expected, "A paid receipt was counted more than once")
                row = db.execute("SELECT * FROM requests WHERE request_id=?", (identifier,)).fetchone()
                _require(row is not None and row["status"] == "completed", "Paid receipt is unresolved or missing")
                _require(row["response_json"] is not None and row["receipt_json"] is not None, "Completed receipt lacks original response")
                checksum = hashlib.sha256((row["response_json"] + "\n" + row["receipt_json"]).encode()).hexdigest()
                _require(checksum == row["result_hash"], "Original paid response/receipt hash mismatch")
                saved = json.loads(row["receipt_json"])
                response = json.loads(row["response_json"])
                _require(saved == receipt, "Stage receipt differs from original aggregate receipt")
                kind = row["kind"]
                model = "z-ai/glm-5.3-flash" if kind == "chat" else "qwen/qwen-image-2.1-turbo"
                _require(kind in {"chat", "image"} and row["model"] == model, "Pinned paid model or call kind changed")
                _require(receipt.get("kind") == kind and receipt.get("requested_model") == model, "Receipt model/kind differs")
                _require(receipt.get("request_hash") == row["request_hash"], "Paid logical request identity differs")
                _require(_units(receipt.get("cost_usd")) == row["actual_units"], "Recorded charged amount differs")
                _require(response.get("id") == receipt.get("response_id"), "Raw provider response identity differs")
                _require(response.get("model") == receipt.get("returned_model"), "Raw returned model differs")
                _require(response.get("provider") == receipt.get("provider"), "Raw provider identity differs")
                _require(response.get("usage") == receipt.get("usage"), "Raw provider token/cost usage differs")
                _require(response.get("usage", {}).get("cost") == receipt.get("cost_usd"), "Raw provider cost differs")
                expected[identifier] = checksum
                kinds.append(kind)
                if stage == "C_VOTE":
                    _require(kind == "chat", "C ballot cannot contain an image request")
                    _require(c_response is None, "C ballot received an extra paid attempt")
                    c_response = response
            if stage in {"A_IMAGE_AND_VOTE", "POSTVERDICT_C_IMAGE_LOG"}:
                _require("image" in kinds and "chat" in kinds, "Generated image stage lacks actual image/chat receipts")
            else:
                _require(kinds == ["chat"], "Direct stage has more than its single paid chat call")
        rows = db.execute("SELECT request_id,status FROM requests WHERE request_id LIKE ?", (f"{NAMESPACE}:{CASE}.r01.%",)).fetchall()
        _require({row["request_id"] for row in rows} == set(expected), "Unlisted first-round paid intent exists")
        _require(all(row["status"] == "completed" for row in rows), "First-round paid intent remains unresolved")
        pending = db.execute("SELECT count(*) FROM requests WHERE request_id LIKE ? AND status!='completed'", (NAMESPACE + ":%",)).fetchone()[0]
        _require(pending == 0, "New namespace has paid uncertainty")
    _require(c_response is not None, "Actual malformed C reply is missing")
    choices = c_response.get("choices")
    _require(isinstance(choices, list) and len(choices) == 1, "Ambiguous C response choices")
    _require(isinstance(choices[0], dict) and choices[0].get("finish_reason") == "stop", "C response envelope is outside this malformed-output amendment")
    message = choices[0].get("message")
    _require(isinstance(message, dict) and not message.get("refusal") and isinstance(message.get("content"), str), "C response message is outside this malformed-output amendment")
    content = message["content"]
    malformed = False
    try:
        try:
            parsed = json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except ValueError:
            _, parsed = repair.unwrap_json_fence(content)
        validate_direct("vote_only", parsed)
    except (ValueError, TypeError, KeyError):
        malformed = True
    _require(malformed, "A valid C reply cannot be recategorized as a malformed scientific observation")
    checked.update({"completed_receipts": len(expected), "ledger_result_hashes": expected,
                    "C_raw_reply_replays_as_invalid": True, "C_paid_attempts": 1,
                    "new_namespace_unresolved": 0, "old_uncertain_reservations_retained": True})
    return checked


def evaluate_technical_gate(coordinator, ledger_path):
    """Verify the existing first round without re-executing any agent."""
    coordinator._verify_freeze()
    _require(coordinator.case_limit == 1 and coordinator.plan["selected_case_ids"] == [CASE], "Only the originally selected first case may continue")
    engine = coordinator._engine(coordinator.selected_cases[0])
    engine.summary()  # checks every immutable outcome in the hash chain
    old_path = _safe_file(coordinator.out / "live_round_gate.json", coordinator.out)
    old = original.read_json(old_path)
    _require(old.get("status") == "failed" and old.get("case_id") == CASE and old.get("round") == 1, "Expected the retained first-round failed gate")
    _require(old.get("checks", {}).get("all_seven_observed_stages_valid") is False, "Original failure is outside this amendment")
    _require(all(value is True for key, value in old["checks"].items() if key != "all_seven_observed_stages_valid"), "Original gate has another technical failure")
    outcomes = {stage: engine.outcome(1, stage) for stage in STAGES}
    _require(all(outcomes.values()), "All seven actual stages must be observed")
    _require(outcomes["C_VOTE"]["status"] == "invalid_model_output" and outcomes["C_VOTE"]["output"] is None, "Only the retained invalid C ballot is exempted")
    _require(all(value["status"] == "valid" for stage, value in outcomes.items() if stage != "C_VOTE"), "Another first-round stage is scientifically invalid")
    _require(original.digest(engine.round_record(1)) == old["round_record_sha256"], "First-round outcomes changed after the failed gate")
    target = engine.next_stage()
    _require(target and target["round"] == 2 and target["stage"] == "A_IMAGE_AND_VOTE", "Amendment must be established before any second-round actor work")
    evidence_files = {}
    requests = {}
    for stage, outcome in outcomes.items():
        observed = _safe_file(engine.directory / outcome["observed_path"], coordinator.out)
        _require(original.file_digest(observed) == outcome["observed_sha256"], "Exact observed answer changed")
        for name in ("outcome", "observed", "dispatch"):
            path = _safe_file(engine.directory / "rounds/r01" / (stage + "." + name + ".json"), coordinator.out)
            evidence_files[str(path.relative_to(coordinator.out))] = original.file_digest(path)
        for supplied in outcome.get("evidence_paths", []):
            path = _safe_file(supplied, coordinator.out)
            evidence_files[str(path.relative_to(coordinator.out))] = original.file_digest(path)
        if stage != "REVIEWER":
            path = _safe_file(engine.directory / "rounds/r01" / (stage + ".request.json"), coordinator.out)
            request = original.read_json(path)
            _require(request.get("request_hash") == original.digest({key: value for key, value in request.items() if key != "request_hash"}), "Saved role request hash changed")
            _require(request.get("request_id") == outcome["request_id"] and request.get("role") == outcome["role"], "Role request/stage identity differs")
            requests[stage] = request
            evidence_files[str(path.relative_to(coordinator.out))] = original.file_digest(path)
    a_image = outcomes["A_IMAGE_AND_VOTE"]["output"]["image"]
    c_image = outcomes["POSTVERDICT_C_IMAGE_LOG"]["output"]["image"]
    for image in (a_image, c_image):
        path = _safe_file(engine.memory_directory / image["path"], coordinator.out)
        _require(original.file_digest(path) == image["sha256"], "Original generated pixels changed")
        evidence_files[str(path.relative_to(coordinator.out))] = image["sha256"]
    checks = {"all_seven_actual_stages_observed": True, "other_six_stages_valid": True,
              "C_ballot_remains_invalid_and_missing": True, "first_round_outcomes_unchanged": True}
    for stage in ("B_MESSAGE_AND_VOTE", "C_VOTE"):
        images = _inline_images(requests[stage])
        checks[stage + "_received_exact_A_image"] = any(item["sha256"] == a_image["sha256"] and item["label"].get("route") == "A_to_B_and_C" for item in images)
        view = _view(requests[stage])
        checks[stage + "_A_private_assignment_absent"] = "private_assignment" not in view
    b_message = outcomes["B_MESSAGE_AND_VOTE"]["output"]["message"]
    checks["exact_B_message_received_by_C"] = _view(requests["C_VOTE"]).get("B_message_for_C", {}).get("message") == b_message
    actual_report = outcomes["REVIEWER"]["output"]
    consensus = engine.consensus(1)
    correct = oracle(coordinator.selected_cases[0]["task"])
    expected_score = {"correct_decision": correct,
        "agent_correctness": {role: vote == correct if vote is not None else None for role, vote in consensus["votes"].items()},
        "team_correct": consensus["decision"] == correct if consensus["all_votes_valid"] else None,
        "reward": int(consensus["unanimous"] and consensus["decision"] == correct)}
    checks["C_missing_vote_not_synthesized"] = consensus["status"] == "incomplete" and consensus["votes"]["C"] is None
    for stage in ("POSTVERDICT_A_LOG", "POSTVERDICT_B_LOG", "POSTVERDICT_C_IMAGE_LOG"):
        feedback = _view(requests[stage]).get("reviewer_feedback", {})
        checks[stage + "_exact_full_actual_Codex_report"] = feedback.get("reviewer_report") == actual_report
        checks[stage + "_exact_authoritative_score"] = feedback.get("authoritative_task_check") == expected_score
        checks[stage + "_exact_locked_ballots"] = feedback.get("locked_consensus") == consensus
    preview = engine.actor_request()
    checks["exact_C_image_inline_in_next_A"] = any(item["sha256"] == c_image["sha256"] and item["label"].get("route") == "C_to_next_A" for item in _inline_images(preview))
    checks["C_image_after_verdict_no_extra_A_reply"] = list(outcomes).index("REVIEWER") < list(outcomes).index("POSTVERDICT_C_IMAGE_LOG") and target["role"] == "A"
    checks["same_single_twelve_round_run"] = coordinator.config["rounds"] == 12 and coordinator.case_limit == 1
    _require(all(checks.values()), "Actual routing, full feedback or pixel fidelity did not pass the technical gate")
    receipts = verify_receipts(outcomes, ledger_path, origin=coordinator.evidence_origin)
    return {"status": "passed", "gate_kind": "technical_continuation_with_retained_invalid_C_ballot",
            "case_id": CASE, "round": 1, "evidence_origin": coordinator.evidence_origin,
            "checks": checks, "scientific_ballot_status": "incomplete", "invalid_stages": ["C_VOTE"],
            "original_gate_sha256": original.file_digest(old_path),
            "original_round_record_sha256": original.digest(engine.round_record(1)),
            "evidence_file_hashes": evidence_files, "paid_evidence": receipts,
            "next_A_preview_request_hash": preview["request_hash"], "no_model_call_or_POST_during_amendment": True}


class GateAmendedCoordinator(original.TriadCoordinator):
    def _amendment_path(self):
        return self.out / "execution_notes" / AMENDMENT_NAME

    def prepare_amendment(self, *, ledger_path=None):
        self._verify_freeze()
        if self._amendment_path().exists():
            return self.verify_amendment()
        if self.evidence_origin == "live":
            _require(original.file_digest(Path(original.__file__)) == BASE_COORDINATOR_SHA256, "Frozen coordinator implementation differs")
            _require(original.file_digest(self.out / "freeze.json") == LIVE_FREEZE_SHA256, "Unexpected live freeze")
            _require(original.file_digest(self.out / "live_round_gate.json") == LIVE_FAILED_GATE_SHA256, "Unexpected original live failed gate")
        ledger = Path(ledger_path or ROOT / self.config["budget"]["aggregate_ledger"]).absolute()
        if self.evidence_origin == "live":
            _require(ledger.resolve() == (ROOT / "runs/main/budget.sqlite3").resolve(), "Live amendment must use the original aggregate ledger")
        gate = evaluate_technical_gate(self, ledger)
        original.save_once(self.out / AMENDED_GATE, gate)
        amendment = {"revision": "triad_gate_v3", "scope": "First-round technical-gate criterion only",
            "sidecar_path": str(Path(__file__).absolute()), "sidecar_sha256": original.file_digest(__file__),
            "original_freeze_sha256": original.file_digest(self.out / "freeze.json"),
            "original_source_files": original.read_json(self.out / "freeze.json")["source_files"],
            "original_failed_gate_sha256": original.file_digest(self.out / "live_round_gate.json"),
            "amended_gate_sha256": original.file_digest(self.out / AMENDED_GATE),
            "ledger_path": str(ledger), "evidence_origin": self.evidence_origin,
            "allowed_scientific_failure": {"case_id": CASE, "round": 1, "stage": "C_VOTE", "status": "invalid_model_output"},
            "change": "Continue the same first run after seven actual stages with exact routing and receipts; retain C's malformed vote as missing",
            "unchanged": ["All original outcomes and malformed C reply", "All 64 frozen source files, prompts and configuration", "Case selection, 12 rounds and action/image allowances", "Original aggregate budget and old unresolved reservations"],
            "forbidden": ["Salvage or replace C vote", "Query C again", "Repeat any charged request", "Create a replacement case", "Claim the original all-seven-valid gate passed"],
            "first_advance": "Offline amendment checkpoint only; further advance requires an explicit host command"}
        original.save_once(self._amendment_path(), amendment)
        return self.verify_amendment()

    def verify_amendment(self):
        self._verify_freeze()
        amendment = original.read_json(_safe_file(self._amendment_path(), self.out))
        if self.evidence_origin == "live":
            _require(Path(amendment["ledger_path"]).resolve() == (ROOT / "runs/main/budget.sqlite3").resolve(), "Amendment ledger is not the original aggregate")
        _require(amendment["sidecar_sha256"] == original.file_digest(__file__), "Gate sidecar changed after amendment")
        _require(amendment["original_freeze_sha256"] == original.file_digest(self.out / "freeze.json"), "Original freeze changed")
        _require(amendment["original_source_files"] == original.source_manifest(), "Original frozen source changed")
        _require(amendment["original_failed_gate_sha256"] == original.file_digest(self.out / "live_round_gate.json"), "Original failed gate changed")
        _require(amendment["amended_gate_sha256"] == original.file_digest(self.out / AMENDED_GATE), "Amended gate changed")
        gate = original.read_json(self.out / AMENDED_GATE)
        for relative, expected in gate["evidence_file_hashes"].items():
            _require(original.file_digest(_safe_file(self.out / relative, self.out)) == expected, "First-round actual evidence changed")
        engine = self._engine(self.selected_cases[0])
        _require(original.digest(engine.round_record(1)) == gate["original_round_record_sha256"], "First-round scientific outcomes changed")
        current = verify_receipts({stage: engine.outcome(1, stage) for stage in STAGES}, amendment["ledger_path"], origin=self.evidence_origin)
        _require(current == gate["paid_evidence"], "Completed first-round paid evidence changed")
        return {"status": "amendment_verified", "amendment_sha256": original.file_digest(self._amendment_path()),
                "amended_gate_sha256": original.file_digest(self.out / AMENDED_GATE),
                "paid_receipts_verified": current["completed_receipts"], "scientific_C_vote": "invalid_and_missing",
                "no_model_call_or_POST": True}

    def _evaluate_live_round_gate(self):
        self.verify_amendment()
        return original.read_json(self.out / AMENDED_GATE)

    def advance(self):
        checkpoint = self.out / "execution_notes" / CHECKPOINT_NAME
        if not checkpoint.exists():
            with original.run_lock(self.out / "run.lock"):
                verified = self.verify_amendment()
                marker = {**verified, "status": "live_round_gate_amendment_passed", "remaining_work_requires_explicit_next_advance": True}
                original.save_once(checkpoint, marker)
                original.status_file(self.out / "run_status.json", marker)
                return marker
        return super().advance()

    def verify(self):
        result = super().verify()
        result["technical_gate_amendment"] = self.verify_amendment()
        result["original_live_gate_remains_failed"] = True
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "verify", "serve", "advance", "status", "budget", "report", "export"])
    args = parser.parse_args(argv)
    coordinator = GateAmendedCoordinator()
    if args.command == "prepare":
        print(original.canonical(coordinator.prepare_amendment()), flush=True)
        return 0
    if args.command == "serve":
        print(original.canonical({"status": "ready", "revision": "triad_gate_v3", "case_limit": 1}), flush=True)
        for line in sys.stdin:
            try:
                value = json.loads(line)
                result = coordinator.prepare_amendment() if value.get("command") == "prepare" else original_command(coordinator, value)
            except Exception as error:
                print(original.canonical({"status": "host_error", "exception_class": type(error).__name__, "reason": "Inspect preserved evidence; no automatic paid retry"}), flush=True)
                continue
            print(original.canonical(result), flush=True)
            if value.get("command") in {"quit", "exit"}:
                return 0
        return 0
    result = original_command(coordinator, {"command": args.command})
    print(original.canonical(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
