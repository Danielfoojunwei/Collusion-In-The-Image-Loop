"""Frozen-run, single-producer coordinator. Importing this module makes no calls.

The host obtains real Codex reviews separately. This process stops at each
review position; it neither writes reviewer answers nor spawns a substitute.
All paid work passes through the original aggregate ledger and an exact source
freeze check immediately before POST. Scripted transports are injectable only
for offline verification.
"""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import sys

from visual_collusion.budget import BudgetError
from . import funding, reviewer
from .backend import Backend, BackendError, TechnicalFault
from .engine import (Engine, EngineError, PendingOperation, REVISION, canonical,
                     digest, file_sha, process_start_ticks, read_record,
                     recorded, safe_path, save_once)


ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATTERNS = ("src/**/*.py", "experiments/**/*.py", "scripts/**/*.py",
                   "research_plans/triad_committed_sender_v10/**/*.py")
FREEZE_FIELDS = {"revision", "source_root", "source_files", "config_path",
                 "config_sha256", "plan_path", "plan_sha256", "protocol_path",
                 "protocol_sha256", "gate_path", "gate_sha256", "record_hash"}


class CoordinatorError(RuntimeError):
    """A fixed, secret-free hold code; not permission to reroll behavior."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@contextmanager
def producer_lock(out):
    """Hold one kernel flock for a command or an entire JSON-line session."""
    out = safe_path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = safe_path(out / "producer.lock", out)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CoordinatorError("producer_already_running") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Coordinator:
    def __init__(self, out, config_path=None, plan_path=None, *,
                 backend_factory=None, aggregate_path=None, ledger=None,
                 expected_source_root=None):
        self.out = safe_path(out)
        self.config_path = safe_path(config_path or self.out / "config.json")
        self.plan_path = safe_path(plan_path or self.out / "plan.json")
        self.source_root = safe_path(expected_source_root or ROOT)
        self.aggregate_path = safe_path(aggregate_path or funding.AGGREGATE)
        # A different aggregate is accepted only with an explicitly injected
        # offline ledger. Production has no CLI switch for either dependency.
        if ledger is None and self.aggregate_path != funding.AGGREGATE:
            raise CoordinatorError("original_aggregate_required")
        if ledger is not None and safe_path(ledger.path) != self.aggregate_path:
            raise CoordinatorError("injected_ledger_path_differs")
        self.ledger = ledger
        self.backend_factory = backend_factory or Backend
        self.engine = None
        self.backend = None
        self.guard = None
        self._backend_attempt = None
        self._preflight_ok = False
        self._freeze_sha256 = None

    def _require_lock(self):
        if not funding.owns_lock(self.out):
            raise CoordinatorError("exclusive_producer_lock_required")

    def locked(self):
        return producer_lock(self.out)

    def verify_freeze(self):
        """Verify exact manifest bytes and every frozen input; never refresh it."""
        self._require_lock()
        path = safe_path(self.out / "freeze.json", self.out)
        freeze = read_record(path)
        if set(freeze) != FREEZE_FIELDS or freeze.get("revision") != REVISION:
            raise CoordinatorError("freeze_schema_differs")
        freeze_sha = file_sha(path)
        if self._freeze_sha256 is not None and freeze_sha != self._freeze_sha256:
            raise CoordinatorError("freeze_manifest_changed")
        binding_path = self.out / "coordinator_contract.json"
        if binding_path.exists():
            binding = read_record(binding_path)
            if (binding.get("freeze_sha256") != freeze_sha
                    or binding.get("revision") != REVISION
                    or binding.get("aggregate_path") != str(self.aggregate_path)):
                raise CoordinatorError("coordinator_binding_changed")
        if safe_path(freeze["source_root"]) != self.source_root:
            raise CoordinatorError("frozen_source_root_differs")
        files = freeze.get("source_files")
        if not isinstance(files, dict) or not files:
            raise CoordinatorError("freeze_source_inventory_missing")
        required = {str(p.relative_to(self.source_root))
                    for pattern in SOURCE_PATTERNS for p in self.source_root.glob(pattern)
                    if "__pycache__" not in p.parts and p.is_file()}
        if not required.issubset(files):
            raise CoordinatorError("freeze_source_inventory_incomplete")
        for relative, expected in files.items():
            if (not isinstance(relative, str) or Path(relative).is_absolute()
                    or not isinstance(expected, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", expected)):
                raise CoordinatorError("freeze_source_entry_invalid")
            source = safe_path(self.source_root / relative, self.source_root)
            if not source.is_file() or file_sha(source) != expected:
                raise CoordinatorError("frozen_source_bytes_changed")
        for kind in ("config", "plan", "protocol", "gate"):
            source = safe_path(freeze[kind + "_path"])
            if kind == "config" and source != self.config_path:
                raise CoordinatorError("frozen_config_path_differs")
            if kind == "plan" and source != self.plan_path:
                raise CoordinatorError("frozen_plan_path_differs")
            if not source.is_file() or file_sha(source) != freeze[kind + "_sha256"]:
                raise CoordinatorError("frozen_" + kind + "_bytes_changed")
        gate = json.loads(safe_path(freeze["gate_path"]).read_text())
        if (gate.get("status") != "passed"
                or gate.get("evidence_origin") != "offline_scripted_validation_not_live_results"
                or type(gate.get("tests_passed")) is not int or gate["tests_passed"] < 1
                or type(gate.get("tests_failed")) is not int or gate["tests_failed"] != 0
                or gate.get("source_files") != files
                or gate.get("config_sha256") != freeze["config_sha256"]
                or gate.get("plan_sha256") != freeze["plan_sha256"]
                or not isinstance(gate.get("test_log_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", gate["test_log_sha256"])):
            raise CoordinatorError("offline_gate_not_passed_for_exact_frozen_inputs")
        self._freeze_sha256 = freeze_sha
        return {"status": "verified", "freeze_sha256": freeze_sha,
                "source_files": len(files)}

    def init(self):
        self._require_lock()
        freeze = self.verify_freeze()
        config = json.loads(self.config_path.read_text())
        plan = json.loads(self.plan_path.read_text())
        if config.get("revision") != REVISION:
            raise CoordinatorError("configuration_revision_differs")
        binding = {"revision": REVISION, "freeze_sha256": freeze["freeze_sha256"],
                   "aggregate_path": str(self.aggregate_path),
                   "config_path": str(self.config_path), "plan_path": str(self.plan_path),
                   "reviewer_execution": "external_fresh_Codex_host",
                   "unknown_reservations": "retained_in_original_aggregate"}
        save_once(self.out / "coordinator_contract.json", recorded(binding))
        if self.engine is None:
            self.engine = Engine(self.out, config, plan)
        if self.ledger is None:
            self.ledger = funding.FundingLedger(self.aggregate_path, run_root=self.out)
        return self.status()

    def _ready(self):
        self._require_lock()
        if self.engine is None:
            self.init()
        self.verify_freeze()

    def _before_dispatch(self, attempt_id):
        self._require_lock()
        self.verify_freeze()
        self._assert_no_holds()
        self._check_launch_preflight()
        current = self.engine.status()["active_attempt"]
        if current is None or current["attempt_id"] != attempt_id:
            raise CoordinatorError("paid_attempt_is_no_longer_current")
        marker = self.engine._attempt_root(current["round"], current["attempt"]) / "abandonment.json"
        if marker.exists():
            raise CoordinatorError("abandoned_attempt_cannot_dispatch")

    def _get_backend(self, attempt_id=None):
        if self.backend is None or self._backend_attempt != attempt_id:
            self.guard = funding.create_guard(self.aggregate_path,
                run_root=self.out, evidence_dir=self.out / "provider_evidence",
                active_prefix=attempt_id, ledger=self.ledger,
                before_dispatch=lambda: self._before_dispatch(attempt_id))
            self.backend = self.backend_factory(self.engine.config, self.out,
                self.aggregate_path, guard=self.guard, preflight=False)
            self._backend_attempt = attempt_id
            self._preflight_ok = False
        return self.backend

    def preflight(self):
        """Free metadata GETs through the bound backend; no paid pilot."""
        self._ready()
        self._assert_no_holds()
        self._seal_abandonments()
        self.ledger.assert_ready()
        current = self.engine.status()["active_attempt"]
        attempt_id = current["attempt_id"] if current else None
        backend = self._get_backend(attempt_id)
        report = backend.preflight()
        # Root freezes the full bound when it constructs the live config. The
        # backend also enforces a fresh per-request key check before every POST.
        bound = self.engine.config.get("budget", {}).get("predeclared_reservations", {}).get(
            "minimum_key_remaining_before_launch_usd")
        if bound is None:
            raise CoordinatorError("frozen_initial_provider_bound_missing")
        namespace = self.ledger.namespace_summary()
        launch_path = self.out / "launch_preflight.json"
        if namespace["request_count"] == 0:
            remaining = report.get("key", {}).get("remaining_usd")
            try:
                minimum, actual = Decimal(str(bound)), Decimal(str(remaining))
            except InvalidOperation:
                raise CoordinatorError("provider_remaining_bound_invalid") from None
            if (isinstance(bound, bool) or isinstance(remaining, bool)
                    or not minimum.is_finite() or minimum <= 0
                    or not actual.is_finite() or actual < minimum):
                raise CoordinatorError("provider_remaining_cannot_cover_frozen_bound")
            if not launch_path.exists():
                launch = {"minimum_key_remaining_before_launch_usd": str(bound),
                          "provider_remaining_usd": str(remaining),
                          "preflight_report_sha256": digest(report),
                          "freeze_sha256": self._freeze_sha256,
                          "namespace_paid_requests_before_launch_check": 0}
                save_once(launch_path, recorded(launch))
        self._check_launch_preflight()
        self._preflight_ok = True
        return {"status": "ready", "paid_requests_made": 0,
                "provider": report, "budget": self._budget_status()}

    def _budget_status(self):
        if self.ledger is None:
            return None
        return {"aggregate": self.ledger.summary(),
                "namespace": self.ledger.namespace_summary()}

    def _check_launch_preflight(self):
        path = self.out / "launch_preflight.json"
        if not path.is_file():
            raise CoordinatorError("initial_full_bound_preflight_required")
        value = read_record(path)
        bound = self.engine.config.get("budget", {}).get("predeclared_reservations", {}).get(
            "minimum_key_remaining_before_launch_usd")
        if (value.get("freeze_sha256") != self._freeze_sha256
                or value.get("minimum_key_remaining_before_launch_usd") != str(bound)
                or value.get("namespace_paid_requests_before_launch_check") != 0):
            raise CoordinatorError("launch_preflight_binding_differs")

    def _assert_no_holds(self):
        if any((self.out / "coordinator_holds").glob("*.json")):
            raise CoordinatorError("durable_integrity_or_permanent_hold_requires_diagnosis")

    def _record_hold(self, request, code, error):
        body = {"status": "held", "code": code, "request_id": request["request_id"],
                "exception_class": type(error).__name__,
                "automatic_technical_restart_permitted": False,
                "freeze_sha256": self._freeze_sha256}
        save_once(self.out / "coordinator_holds" / (request["request_id"] + ".json"), recorded(body))

    def _seal_abandonments(self):
        results = []
        for path in sorted((self.out / "attempts").glob("r*/a*/abandonment.json")):
            marker = read_record(path)
            result = funding.seal_abandoned_attempt(self.out, marker["attempt_id"],
                                                   path, ledger=self.ledger)
            results.append({"attempt_id": marker["attempt_id"], **result})
        return results

    def _abandon(self, fault):
        abandoned = self.engine.abandon_current(fault)
        sealed = funding.seal_abandoned_attempt(self.out, abandoned["attempt_id"],
                                                abandoned["path"], ledger=self.ledger)
        self.backend = self.guard = None
        self._backend_attempt = None
        self._preflight_ok = False
        return {"attempt_id": abandoned["attempt_id"], "fault": deepcopy(fault),
                "abandonment_path": abandoned["path"], "funding": sealed}

    def _recover_interrupted(self):
        pending = self.engine.pending_operations()
        if not pending:
            return None
        for operation in pending:
            if process_start_ticks(operation["producer_pid"]) == operation["producer_process_start_ticks"]:
                raise PendingOperation("An original actor operation may still be live; no redispatch")
        return self._abandon({"technical": True, "code": "producer_interrupted_during_actor_operation",
                              "request_ids": [p["request_id"] for p in pending],
                              "prior_producer_verified_dead": True})

    def status(self):
        self._require_lock()
        if self.engine is None:
            if (self.out / "freeze.json").is_file():
                return self.init()
            return {"revision": REVISION, "status": "not_initialized",
                    "out": str(self.out), "paid_requests_made_by_status": 0}
        self.verify_freeze()
        state = self.engine.status()
        # Status must be read-only: next_stage() creates a prospective attempt.
        return {**state, "out": str(self.out), "budget": self._budget_status(),
                "freeze_sha256": self._freeze_sha256,
                "durable_holds": [str(p) for p in sorted((self.out / "coordinator_holds").glob("*.json"))]}

    def prepare_review(self):
        self._ready()
        if self.engine.pending_operations():
            raise PendingOperation("Review is blocked by unresolved actor work")
        return reviewer.prepare_packet(self.out, self.engine.review_packet_data())

    def ingest_review(self, envelope_path):
        self._ready()
        path = safe_path(envelope_path, self.out / "reviewer_packets")
        envelope = json.loads(path.read_text())
        # Engine requires this exact genuine bridge first-answer artifact; it
        # rechecks packet, raw/output identity, provenance, and source SHA.
        outcome = self.engine.ingest_review(envelope)
        return {"status": "review_ingested", "round": outcome["round"],
                "attempt": envelope["attempt"], "review_status": envelope["status"],
                "job_id": envelope["job_id"]}

    def advance(self, *, through_round=None, maximum_actor_stages=None):
        self._ready()
        self._assert_no_holds()
        if through_round is not None and (type(through_round) is not int or not 1 <= through_round <= 12):
            raise CoordinatorError("through_round_must_be_1_to_12")
        if maximum_actor_stages is not None and (type(maximum_actor_stages) is not int or maximum_actor_stages < 1):
            raise CoordinatorError("maximum_actor_stages_must_be_positive")
        self._seal_abandonments()
        recovery = []
        recovered = self._recover_interrupted()
        if recovered:
            recovery.append(recovered)
        actor_stages = 0
        while True:
            self.verify_freeze()
            if through_round is not None and self.engine.status()["committed_rounds"] >= through_round:
                return {**self.status(), "stop_reason": "requested_round_committed",
                        "actor_stages": actor_stages, "technical_recoveries": recovery}
            target = self.engine.next_stage()
            if target is None:
                return {**self.status(), "actor_stages": actor_stages,
                        "technical_recoveries": recovery}
            if target["stage"] == "COMMIT_ROUND":
                self.engine.commit_round()
                continue
            if target["stage"] == "REVIEWER":
                return {**self.status(), "status": "awaiting_review",
                        "review_job": self.prepare_review(), "actor_stages": actor_stages,
                        "technical_recoveries": recovery}
            if maximum_actor_stages is not None and actor_stages >= maximum_actor_stages:
                return {**self.status(), "stop_reason": "actor_stage_limit",
                        "next_stage": target, "actor_stages": actor_stages,
                        "technical_recoveries": recovery}
            if self.engine.pending_operations():
                raise PendingOperation("An unresolved operation cannot be redispatched")
            attempt_id = f"r{target['round']:02d}.a{target['attempt']:02d}"
            backend = self._get_backend(attempt_id)
            if not self._preflight_ok:
                self.preflight()
            request = self.engine.actor_request()
            self.engine.begin_actor_request(request)
            try:
                result = backend.perform(request)
            except TechnicalFault as fault:
                if fault.permanent:
                    # Permanent provider/configuration holds cannot justify a
                    # replacement sample, even if a partial charge exists.
                    self._record_hold(request, "permanent_provider_technical_hold", fault)
                    raise CoordinatorError("permanent_provider_technical_hold") from None
                normalized = fault.as_dict()
                normalized["code"] = normalized.pop("fault_class")
                recovery.append(self._abandon(normalized))
                actor_stages += 1
                continue
            except Exception as error:
                self._record_hold(request, "untyped_execution_hold", error)
                raise
            # Invalid/refused model behavior is committed unchanged. Exceptions
            # here are integrity/storage holds, never favorable sampling retries.
            try:
                self.engine.commit_actor_result(request, result)
            except Exception as error:
                self._record_hold(request, "result_integrity_or_storage_hold", error)
                raise
            actor_stages += 1

    def dispatch(self, command):
        if not isinstance(command, dict) or not isinstance(command.get("command"), str):
            raise CoordinatorError("json_command_required")
        name = command["command"]
        options = set(command) - {"command"}
        expected = {"init": set(), "status": set(), "preflight": set(),
                    "prepare_review": set(), "ingest_review": {"envelope_path"},
                    "advance": {"through_round", "maximum_actor_stages"}, "quit": set()}
        if name not in expected or not options.issubset(expected[name]):
            raise CoordinatorError("unknown_command_or_option")
        if name == "quit":
            return {"status": "bye"}
        if name == "ingest_review":
            if "envelope_path" not in command:
                raise CoordinatorError("envelope_path_required")
            return self.ingest_review(command["envelope_path"])
        if name == "advance":
            return self.advance(**{k: command[k] for k in options})
        return getattr(self, name)()

    def serve(self, input_stream=None, output_stream=None):
        """One response per input line; hold flock until EOF or explicit quit."""
        input_stream = input_stream or sys.stdin
        output_stream = output_stream or sys.stdout
        with self.locked():
            for line in input_stream:
                try:
                    command = json.loads(line)
                    result = self.dispatch(command)
                    response = {"ok": True, "result": result}
                except (ValueError, RuntimeError, OSError) as error:
                    # Error strings from transports or arbitrary files can carry
                    # secrets; expose stable class/code only, retaining originals
                    # in their respective immutable evidence archives.
                    response = {"ok": False, "error": {"class": type(error).__name__,
                        "code": error.code if isinstance(error, CoordinatorError) else "operation_held"}}
                    command = None
                output_stream.write(canonical(response) + "\n")
                output_stream.flush()
                if command and command.get("command") == "quit":
                    break


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("command", choices=("serve", "init", "status", "preflight", "advance", "prepare_review", "ingest_review"))
    parser.add_argument("--through-round", type=int)
    parser.add_argument("--maximum-actor-stages", type=int)
    parser.add_argument("--envelope-path")
    args = parser.parse_args(argv)
    coordinator = Coordinator(args.out)
    if args.command == "serve":
        coordinator.serve()
        return
    command = {"command": args.command}
    if args.through_round is not None: command["through_round"] = args.through_round
    if args.maximum_actor_stages is not None: command["maximum_actor_stages"] = args.maximum_actor_stages
    if args.envelope_path is not None: command["envelope_path"] = args.envelope_path
    with coordinator.locked():
        # Reopened status must bind/validate existing disk state too.
        if args.command == "status": coordinator.init()
        result = coordinator.dispatch(command)
        print(canonical({"ok": True, "result": result}), flush=True)


if __name__ == "__main__":
    main()
