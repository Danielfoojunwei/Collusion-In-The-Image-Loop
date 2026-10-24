"""Same-run continuation after immutable, response-less transient HTTP losses.

Never retry the lost request, infer a vote, release its unknown reservation, or
change frozen experimental messages. The missing actor observation is explicitly
classified as infrastructure failure and later scheduled stages remain intact.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import run_triad_max_format_v1 as frozen
from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard
from visual_collusion.budget import PendingPaidRequest

original = frozen.original
AMENDMENT_PATH = "execution_notes/triad_max_transport_v1_amendment.json"
GATE_PATH = "execution_notes/triad_max_transport_v1_operational_gate.json"
HELPER_PATH = ROOT / "scripts/triad_max_transport_v1.py"
KNOWN_REQUEST = "triad_max_pressure_v3:case_05_max.r02.A_IMAGE_AND_VOTE.step1"
COOLDOWN_SECONDS = 5
_LOCK = threading.RLock()
_ORIGINAL_COMPLETED_SNAPSHOT = frozen.completed_snapshot


def _helper():
    module = importlib.import_module("scripts.triad_max_transport_v1")
    frozen._require(Path(module.__file__).resolve() == HELPER_PATH.resolve(), "Transport helper resolves elsewhere")
    return module


@contextmanager
def _sealed_snapshot_view():
    with _LOCK:
        frozen._require(frozen.completed_snapshot is _ORIGINAL_COMPLETED_SNAPSHOT,
                        "Original format snapshot verifier is unexpectedly patched")
        frozen.completed_snapshot = _helper().completed_snapshot_allowing_sealed_losses
        try:
            yield
        finally:
            frozen.completed_snapshot = _ORIGINAL_COMPLETED_SNAPSHOT


def request_rows(ledger):
    """Read immutable identities of every namespace row, including unknowns."""
    with sqlite3.connect(Path(ledger).absolute().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM requests WHERE request_id LIKE ? ORDER BY request_id",
                          (frozen.NAMESPACE + ":%",)).fetchall()
    return {row["request_id"]: {"row_sha256": original.digest(dict(row)),
            "status": row["status"], "request_hash": row["request_hash"],
            "reserved_units": row["reserved_units"], "actual_units": row["actual_units"],
            "result_hash": row["result_hash"]} for row in rows}


class TransportRecoveryEngine(frozen.ProspectiveRecoveryEngine):
    def _reviewer_payload(self, number, image_copies):
        packet = super()._reviewer_payload(number, image_copies)
        missing = []
        for role, stage in (("A", "A_IMAGE_AND_VOTE"), ("B", "B_IMAGE_AND_VOTE"), ("C", "C_VOTE")):
            outcome = self._outcome(number, stage)
            provenance = (outcome or {}).get("provenance", {})
            if provenance.get("failure_kind") != "infrastructure_transport_loss":
                continue
            diagnostic = None
            for path in outcome.get("evidence_paths", []):
                if Path(path).parent.name == "transport_diagnostics":
                    diagnostic = original.read_json(path)
                    break
            missing.append({"actor": role, "round": number,
                "cause": "Response-less transient HTTP infrastructure failure",
                "http_status": (diagnostic or {}).get("http_status"),
                "actor_response_observed": False, "model_refusal": False,
                "input_processing": "Input bytes were submitted; actual model processing is unverified. No vote or delivered peer image is available."})
        if missing:
            packet["infrastructure_missingness"] = missing
            if any(item["actor"] == "A" for item in missing) and "technical_image_availability_note" in packet:
                note = packet["technical_image_availability_note"]
                if "received_by_A_in_round" in note:
                    note["submitted_to_A_in_round"] = note.pop("received_by_A_in_round")
                note["model_processing"] = "C pixels were included in the dispatched A request; the response-less failure does not prove A processed or read them."
        return packet


class TransportAmendedCoordinator(frozen.FormatAmendedCoordinator):
    def _engine(self, case):
        if case["case_id"] not in self._engines:
            config = deepcopy(self.config)
            prompts = original.load_prompts(case["condition"])
            config.update({"prompts": prompts, "reviewer_prompt": prompts["reviewer"], "evidence_origin": self.evidence_origin})
            self._engines[case["case_id"]] = TransportRecoveryEngine(
                self.out / "cases" / case["case_id"], config, case,
                recovery_loader=self._recovery_for, amendment_checker=self.verify_format_amendment)
        return self._engines[case["case_id"]]

    def _transport_note(self):
        return self.out / AMENDMENT_PATH

    def verify_format_amendment(self, *, require_operational_gate=True):
        # The original verifier still checks all sixteen original receipts and
        # all original files. Only independently sealed unknowns may be omitted.
        with _sealed_snapshot_view():
            result = super().verify_format_amendment(require_operational_gate=require_operational_gate)
        if self._transport_note().exists():
            self._verify_transport_bindings(require_ready=not getattr(self, "_preparing_transport", False))
        return result

    def _verify_transport_bindings(self, *, require_ready=True):
        note = original.read_json(frozen._safe_file(self._transport_note(), self.out))
        frozen._require(note["runner_sha256"] == original.file_digest(__file__), "Transport runner changed")
        frozen._require(note["helper_sha256"] == original.file_digest(HELPER_PATH), "Transport helper changed")
        frozen._require(note["original_format_amendment_sha256"] == original.file_digest(self._amendment_path()), "Original format amendment changed")
        current = request_rows(self._ledger_path())
        for identifier, binding in note["pre_activation_requests"].items():
            frozen._require(current.get(identifier) == binding, "An original paid or unknown request row changed")
        for relative, sha in note["preserved_pre_activation_files"].items():
            frozen._require(original.file_digest(frozen._safe_file(self.out / relative, self.out)) == sha,
                            "A pre-transport immutable file changed")
        # This helper independently checks seal snapshots, diagnostics, wire
        # identities and all completed receipt hashes without changing rows.
        _helper().completed_snapshot_allowing_sealed_losses(self._ledger_path())
        for binding in note["runtime_sources"].values():
            frozen._require(original.file_digest(frozen._safe_file(binding["path"], self.out)) == binding["sha256"], "Transport reproduction snapshot changed")
        if require_ready:
            gate = original.read_json(frozen._safe_file(self.out / GATE_PATH, self.out))
            frozen._require(gate["status"] == "passed" and gate["no_POST"] is True
                and gate["amendment_sha256"] == original.file_digest(self._transport_note()), "Transport operational gate changed")
        return {"status": "max_transport_amendment_verified", "original_request_rows_preserved": len(note["pre_activation_requests"]),
                "unknown_reservations_released": False, "lost_requests_retried": False}

    def verify_transport_amendment(self):
        self.verify_format_amendment()
        return self._verify_transport_bindings()

    def _backend(self):
        self.verify_transport_amendment()
        if self.backend is None:
            ledger = _helper().TransportLedger(self._ledger_path(), 40)
            guard = SharedBudgetGuard(self._ledger_path(), frozen.NAMESPACE, ledger=ledger)
            self.backend = frozen._adapter_module().FormatV1Backend(self.config, self.out, self._ledger_path(), guard=guard)
        frozen._require(isinstance(self.backend, frozen._adapter_module().FormatV1Backend), "Unexpected backend after transport amendment")
        frozen._require(isinstance(self.backend.guard.ledger, _helper().TransportLedger), "Transport loss authority missing from funding guard")
        return self.backend

    def _loss_diagnostic(self, request_id):
        path = self.out / "provider_evidence/transport_diagnostics" / (hashlib.sha256(request_id.encode()).hexdigest() + ".json")
        return frozen._safe_file(path, self.out)

    def _partial_observations(self, stage_id):
        """Retain completed actions/drafts, never turn them into peer delivery."""
        evidence = self.out / "provider_evidence"
        actions, paths, receipts, images = [], [], [], []
        for path in sorted((evidence / "actor_actions").glob("*.json")):
            value = original.read_json(path)
            if value.get("request_id", "").startswith(stage_id + ".step"):
                actions.append(value)
                paths.append(str(path))
        actions.sort(key=lambda x: x.get("step", 0))
        errors = {}
        for path in sorted((evidence / "tool_errors").glob("*.json")):
            value = original.read_json(path)
            if value.get("request_id", "").startswith(stage_id + ".step"):
                errors[value["request_id"]] = value
                paths.append(str(path))
        for index, value in enumerate(actions):
            if value["request_id"] in errors:
                actions[index] = errors[value["request_id"]]
        for path in sorted((evidence / "image_calls").glob("*.json")):
            value = original.read_json(path)
            if value.get("request_id", "").startswith(stage_id + ".image"):
                images.append(value)
                paths.append(str(path))
        images.sort(key=lambda x: int(x["request_id"].rsplit("image", 1)[1]))
        generators = [record for record in actions if not record.get("validation_error")
                      and (record.get("output") or {}).get("action") == "generate_image"]
        image_by_attempt = {int(value["request_id"].rsplit("image", 1)[1]): value for value in images}
        for attempt, record in enumerate(generators, 1):
            image = image_by_attempt.get(attempt)
            if image is None:
                continue
            arguments = record["output"].get("arguments", {})
            frozen._require(image.get("prompt") == arguments.get("prompt") and image.get("reference") == arguments.get("reference"),
                            "Partial generated pixels do not match the original action prompt")
            sha = image["result_sha256"]
            path = evidence / "blobs" / (sha + ".png")
            frozen._validate_png(path, sha)
            record["generated_image"] = {"path": str(path), "sha256": sha}
            record["generated_image_receipt_id"] = image["receipt"]["request_id"]
            record["generation_attempt"] = int(image["request_id"].rsplit("image", 1)[1])
        with sqlite3.connect(self._ledger_path().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            for row in db.execute("SELECT request_id,receipt_json FROM requests WHERE request_id LIKE ? AND status='completed' ORDER BY request_id",
                                  (frozen.NAMESPACE + ":" + stage_id + ".%",)):
                receipts.append(json.loads(row["receipt_json"]))
        return actions, paths, receipts

    def _record_transport_missing(self, case, engine, target, request_id, diagnostic_path):
        frozen._require(request_id.startswith(frozen.NAMESPACE + ":" + target["request_id"] + "."), "Transport loss belongs to another actor stage")
        diagnostic = original.read_json(diagnostic_path)
        frozen._require(diagnostic.get("request_id") == request_id and diagnostic.get("response_observed") is False,
                        "Transport observation has a mismatched response identity")
        request_path = engine._path(target["round"], target["stage"], "request")
        request = original.read_json(request_path)
        identifier = original.digest({"case": case["case_id"], "request_id": target["request_id"]})
        base = self.out / "stage_cache" / identifier
        claim, response = base.with_suffix(".claim.json"), base.with_suffix(".response.json")
        expected = {"request_id": target["request_id"], "request_hash": request["request_hash"],
                    "case_id": case["case_id"], "round": target["round"], "stage": target["stage"]}
        frozen._require(original.read_json(claim) == expected, "Original actor cache claim changed")
        frozen._require(original.read_json(engine._path(target["round"], target["stage"], "dispatch"))["request_hash"] == request["request_hash"], "Original actor dispatch changed")
        # Seal is independently checked before recording an unavailable stage.
        _helper().completed_snapshot_allowing_sealed_losses(self._ledger_path())
        proof = {"revision": "triad_max_transport_v1", "lost_request_id": request_id,
            "stage_id": target["request_id"], "original_request_hash": request["request_hash"],
            "diagnostic_path": str(diagnostic_path), "diagnostic_sha256": original.file_digest(diagnostic_path),
            "response_observed": False, "receipt_observed": False, "unknown_charge_remains_reserved": True,
            "model_refusal": False, "no_retry": True, "no_vote_or_peer_image_inferred": True}
        proof_path = self.out / "execution_notes/transport_v1/missing_stages" / (hashlib.sha256(target["request_id"].encode()).hexdigest() + ".json")
        original.save_once(proof_path, proof)
        actions, paths, receipts = self._partial_observations(target["request_id"])
        result = {"status": "invalid_model_output", "output": None,
            "reason": "TRANSPORT_UNAVAILABLE: response-less infrastructure loss; no actor answer or refusal; no retry; full unknown reservation retained",
            "provenance": {"evidence_origin": self.evidence_origin, "failure_kind": "infrastructure_transport_loss",
                "actor_response_observed": False, "model_refusal": False, "lost_request_id": request_id,
                "unknown_charge_remains_reserved": True, "no_extra_paid_retries": True},
            "receipts": receipts, "action_log": actions, "evidence_paths": [str(proof_path), str(diagnostic_path), *paths]}
        original.save_once(response, result)
        return engine.accept_actor(result)

    def prepare_transport_amendment(self):
        self._preparing_transport = True
        try:
            if not self._transport_note().exists():
                # Before sealing, bind all 16 completed rows and the exact unknown.
                self._verify_freeze()
                rows = request_rows(self._ledger_path())
                frozen._require(len(rows) == 17 and rows.get(KNOWN_REQUEST, {}).get("status") == "uncertain", "Unexpected original transport pause")
                diagnostic_path = self._loss_diagnostic(KNOWN_REQUEST)
                diagnostic = original.read_json(diagnostic_path)
                frozen._require(diagnostic.get("http_status") == 429 and diagnostic.get("response_observed") is False, "Original loss is not the observed HTTP429")
                sources = {}
                for name, source in (("runner", Path(__file__).absolute()), ("helper", HELPER_PATH)):
                    path = self.out / "execution_notes/transport_v1/runtime_code" / source.name
                    original.copy_exact_once(source, path)
                    sources[name] = {"path": str(path), "sha256": original.file_digest(source)}
                preserved = frozen.preserved_files(self.out)
                preserved.pop(AMENDMENT_PATH, None)
                preserved.pop(GATE_PATH, None)
                note = {"revision": "triad_max_transport_v1", "runner_sha256": original.file_digest(__file__),
                    "helper_sha256": original.file_digest(HELPER_PATH), "runtime_sources": sources,
                    "original_format_amendment_sha256": original.file_digest(self._amendment_path()),
                    "original_freeze_sha256": original.file_digest(self.out / "freeze.json"),
                    "pre_activation_requests": rows, "preserved_pre_activation_files": preserved,
                    "initial_loss": {"request_id": KNOWN_REQUEST, "diagnostic_path": str(diagnostic_path), "diagnostic_sha256": original.file_digest(diagnostic_path)},
                    "future_losses": "Only independently sealed response-less transient HTTP408/429/5xx ; other transport failures remain blocking; no retry and full unknown reservation retained",
                    "cooldown_seconds_after_future_loss": COOLDOWN_SECONDS, "no_POST_during_preparation": True,
                    "scientific_inputs_and_allowances_changed": False, "lost_stage_classification": "TRANSPORT_UNAVAILABLE infrastructure missingness, not a model refusal"}
                original.save_once(self._transport_note(), note)
            note = original.read_json(self._transport_note())
            diagnostic_path = Path(note["initial_loss"]["diagnostic_path"])
            ledger = _helper().TransportLedger(self._ledger_path(), 40)
            ledger.seal_existing_response_less_loss(KNOWN_REQUEST, diagnostic_path)
            self.verify_format_amendment()
            case = self.selected_cases[0]
            engine = self._engine(case)
            target = engine.next_stage()
            if target["round"] == 2 and target["stage"] == "A_IMAGE_AND_VOTE":
                self._record_transport_missing(case, engine, target, KNOWN_REQUEST, diagnostic_path)
            target = engine.next_stage()
            frozen._require(target["round"] == 2 and target["stage"] == "B_IMAGE_AND_VOTE", "Initial lost actor was not preserved as unavailable")
            original.save_once(self.out / GATE_PATH, {"status": "passed", "no_POST": True,
                "amendment_sha256": original.file_digest(self._transport_note()),
                "next_stage": target, "A2_missing_not_retried": True, "full_unknown_reservation_retained": True})
        finally:
            self._preparing_transport = False
        return self.verify_transport_amendment()

    def _actor(self, case, engine, target):
        try:
            return super()._actor(case, engine, target)
        except PendingPaidRequest:
            diagnostic = getattr(self.backend, "last_transport_diagnostic", None)
            if not isinstance(diagnostic, dict) or not diagnostic.get("request_id", "").startswith(frozen.NAMESPACE + ":" + target["request_id"] + "."):
                raise
            path = frozen._safe_file(diagnostic["record_path"], self.out)
            self.backend.guard.ledger.seal_existing_response_less_loss(diagnostic["request_id"], path)
            result = self._record_transport_missing(case, engine, target, diagnostic["request_id"], path)
            time.sleep(COOLDOWN_SECONDS)  # Later stage only; never retry lost request.
            return result

    def verify(self):
        result = super().verify()
        result["max_transport_amendment"] = self.verify_transport_amendment()
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "serve", "advance", "status", "budget", "verify", "report", "export", "dispatch", "ingest"])
    parser.add_argument("--response", type=Path)
    parser.add_argument("--job-id")
    parser.add_argument("--reviewer-agent")
    parser.add_argument("--target", type=Path)
    args = parser.parse_args(argv)
    coordinator = TransportAmendedCoordinator()
    if args.command == "prepare":
        print(original.canonical(coordinator.prepare_transport_amendment()), flush=True)
        return 0
    coordinator.verify_transport_amendment()
    if args.command == "serve":
        print(original.canonical({"status": "ready", "revision": "triad_max_transport_v1", "same_run": True}), flush=True)
        for line in sys.stdin:
            try:
                value = json.loads(line)
                result = frozen.original_command(coordinator, value)
            except Exception as error:
                print(original.canonical({"status": "host_error", "exception_class": type(error).__name__, "reason": "Inspect retained evidence; no automatic paid retry"}), flush=True)
                continue
            print(original.canonical(result), flush=True)
            if value.get("command") in {"quit", "exit"}:
                return 0
        return 0
    value = {"command": args.command}
    for key, arg in (("response_path", args.response), ("job_id", args.job_id), ("reviewer_agent", args.reviewer_agent), ("target", args.target)):
        if arg is not None:
            value[key] = str(arg)
    print(original.canonical(frozen.original_command(coordinator, value)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
