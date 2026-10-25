"""One producer, whole-round checkpoints, bounded technical restarts.

No actor behavior or reviewer verdict authorizes a restart. The versioned
backend emits typed infrastructure faults; integrity and budget failures hold.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path

from experiments.triad_emergent_v9 import coordinator as base
from experiments.triad_emergent_v9.engine import STAGES
from experiments.triad_v9_protocol_repair02 import coordinator as parent

ROOT, HERE = base.ROOT, Path(__file__).resolve().parent
GATE = HERE / "WORKFLOW_GATE.json"
REPAIR = "triad_v9_checkpoint_restart04"


def process_start_ticks(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[19])
    except FileNotFoundError:
        return None


def source_manifest():
    sources = parent.source_manifest()
    for path in sorted(HERE.rglob("*")):
        if (path.is_file() and path.suffix in {".py", ".md", ".txt"}
                and "tests" not in path.relative_to(HERE).parts and "__pycache__" not in path.parts):
            sources[str(path.relative_to(ROOT))] = base.file_sha(path)
    return sources


def attempt_inventory(out):
    """Return audit identities separately from experimental round counts."""
    out = Path(out).absolute()
    folder = out / "checkpoints_restart04"
    return {"attempts": [base.read_json(path) for path in sorted((out / "attempts").glob("r*/a*/attempt.json"))],
            "control_records": {str(path.relative_to(out)): base.read_json(path)
                                for path in sorted(folder.rglob("*.json"))},
            "interpretation": "Repeated technical attempts are not independent experimental rounds; failed-attempt charges and full unresolved reserves remain counted."}


class Coordinator(base.Coordinator):
    def init(self):
        raise ValueError("Restart04 continues the sealed existing episode; use the explicit amendment seal")

    def verify_freeze(self):
        from .seal import verify_repair
        return verify_repair(self.out, config_path=self.config_path)

    def _store(self):
        from .attempts import AttemptStore
        return AttemptStore(self.out, legacy_rounds=6, max_attempts=3)

    def _engine(self):
        from .engine import Engine
        return Engine(self.out, self.config, base.read_json(self.out / "plan.json"),
                      backend=self.backend, store=self._store())

    def _backend(self):
        from .backend import Backend, make_policy
        context = self._store().current()
        if context is None:
            raise ValueError("A paid main backend requires an active round attempt")
        if self.backend is None or (getattr(self.backend, "attempt_context", context) != context):
            self.backend = Backend(self.config, Path(context["working_root"]), base.AGGREGATE,
                                   global_out=self.out, attempt_context=context, policy=make_policy(),
                                   cached_preflight=getattr(self, "_free_preflight", None))
        return self.backend

    def _status(self, result, *, mirror=False):
        base.mutable_json(self.out / "run_status.json", result)
        if mirror:
            base.mutable_json(self._store().working_root() / "run_status.json", result)
        return result

    def _new_attempt(self, number):
        """Free preflight is performed before an irreversible paid attempt begins."""
        from .backend import Backend, make_policy
        # The backend supplies a GET-only preflight independent of paid attempt
        # identity. A failure here consumes neither a paid intent nor an attempt.
        if self.backend is not None:
            checked = self.backend.preflight()
        else:
            checked = Backend.preflight_before_attempt(self.config, self.out, base.AGGREGATE, policy=make_policy())
        self.verify_freeze()
        context = self._store().begin_attempt(number)
        self._free_preflight = checked
        self.backend = None if getattr(self.backend, "attempt_context", None) is not None else self.backend
        return context

    def _archive_fault(self, fault, *, pending_crash_proof_path=None):
        from .funding import seal_abandoned_attempt
        store, context = self._store(), self._store().current()
        if context is None:
            raise ValueError("No current attempt can be abandoned")
        # The immutable fault marker is written before its exact paid-prefix
        # authority. Any interruption blocks paid dispatch until revalidated.
        marker = store.abandon_attempt(deepcopy(fault))
        path = store.abandonment_path(context["round"], context["attempt"])
        kwargs = {"pending_crash_proof_path": pending_crash_proof_path} if pending_crash_proof_path is not None else {}
        seal_abandoned_attempt(self.out, context, fault, aggregate_path=base.AGGREGATE,
                               abandonment_path=path, expected_abandonment_sha256=base.file_sha(path), **kwargs)
        self.backend = None
        return marker

    def _ensure_abandoned_charge_seals(self):
        """Finish an interrupted marker-to-funding seal without another POST."""
        from .funding import seal_abandoned_attempt
        for item in self._store().inventory():
            if item["status"] != "abandoned_technical":
                continue
            self._verify_external_fault_proof(item["fault"])
            context = {key: item[key] for key in (
                "round", "attempt", "attempt_id", "working_root", "prior_checkpoint_sha256", "legacy")}
            path = Path(item["abandonment_path"])
            kwargs = {"legacy_proof_path": self.out / "analysis/restart04/LEGACY_ABORT.json"} if context["legacy"] else {}
            if item["fault"].get("pending_crash_proof_path"):
                kwargs["pending_crash_proof_path"] = Path(item["fault"]["pending_crash_proof_path"])
            authority = self.out / "analysis/restart04/abandoned_charges" / (context["attempt_id"] + ".json")
            if not authority.exists() and not context["legacy"]:
                pending = self._open_operations()
                if pending:
                    proof_path = self._make_crash_proof(pending[-1])
                    if proof_path is not None:
                        kwargs["pending_crash_proof_path"] = proof_path
            seal_abandoned_attempt(self.out, context, item["fault"], aggregate_path=base.AGGREGATE,
                                   abandonment_path=path, expected_abandonment_sha256=base.file_sha(path), **kwargs)

    def _verify_external_fault_proof(self, fault):
        if fault.get("reviewer_fault_record_path"):
            path = Path(fault["reviewer_fault_record_path"])
            if (not path.absolute().is_relative_to(self.out) or base.file_sha(path) != fault.get("reviewer_fault_record_sha256")):
                raise ValueError("Archived reviewer infrastructure proof changed")

    def _hold(self, reason, *, fault=None):
        return self._status({"status": "technical_hold", "reason": reason,
                             "fault": fault, "completed_rounds": self._store().committed_rounds(),
                             "attempts": attempt_inventory(self.out), "new_allowance": False})

    def _storage_fault(self, error, phase):
        from .faults import fault_record
        fault = fault_record("local_storage_failure", error, phase=phase)
        self._archive_fault(fault)
        return fault

    def _open_operations(self):
        folder = self.out / "checkpoints_restart04/operations"
        pending = []
        for number, path in enumerate(sorted(folder.glob("*.started.json")), 1):
            record = base.read_json(path)
            body = {key: value for key, value in record.items() if key != "record_hash"}
            if (path.name != f"{number:06d}.started.json" or record.get("sequence") != number
                    or record.get("operation") not in {"advance", "ingest_actual_first_review"}
                    or type(record.get("committed_rounds")) is not int or not 0 <= record["committed_rounds"] <= 12
                    or type(record.get("producer_pid")) is not int or record["producer_pid"] <= 1
                    or type(record.get("producer_process_start_ticks")) is not int or record["producer_process_start_ticks"] <= 0
                    or record.get("record_hash") != base.sha(base.canonical(body).encode())):
                raise ValueError("Producer operation journal identity or digest changed")
            finished = path.with_name(path.name.replace(".started.json", ".finished.json"))
            if not finished.exists():
                pending.append(path)
            else:
                proof = base.read_json(finished)
                if (set(proof) != {"operation_file_sha256", "status"} or not isinstance(proof["status"], str)
                        or proof["operation_file_sha256"] != base.file_sha(path)):
                    raise ValueError("Finished producer operation lost its exact start binding")
        return pending

    def _durable_operation(self, path, value):
        base.save_once(path, value)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _begin_operation(self, action):
        folder = self.out / "checkpoints_restart04/operations"
        number = len(list(folder.glob("*.started.json"))) + 1
        path = folder / f"{number:06d}.started.json"
        body = {"operation": action, "sequence": number,
                "committed_rounds": self._store().committed_rounds(), "active_attempt": self._store().current(),
                "producer_pid": os.getpid(), "producer_process_start_ticks": process_start_ticks(os.getpid())}
        self._durable_operation(path, {**body, "record_hash": base.sha(base.canonical(body).encode())})
        return path

    def _finish_operation(self, path, status):
        self._durable_operation(path.with_name(path.name.replace(".started.json", ".finished.json")),
                                {"operation_file_sha256": base.file_sha(path), "status": status})

    def _recover_interrupted_operations(self):
        pending = self._open_operations()
        if not pending:
            return
        current, engine = self._store().current(), self._engine()
        if current is not None and not all(engine.outcome(current["round"], stage) is not None for stage in STAGES):
            from .faults import fault_record
            fault = fault_record("interrupted_producer_operation", phase="stage_or_round_publication")
            proof_path = self._make_crash_proof(pending[-1])
            if proof_path is not None:
                fault["pending_crash_proof_path"] = str(proof_path)
            self._archive_fault(fault, pending_crash_proof_path=proof_path)
        for path in pending:
            self._finish_operation(path, "interruption_archived_or_exact_complete_round_recoverable")

    def _make_crash_proof(self, marker_path):
        """Called only after reacquiring the exclusive experiment producer lock."""
        marker = base.read_json(marker_path)
        if process_start_ticks(marker["producer_pid"]) == marker["producer_process_start_ticks"]:
            return None
        ticks = process_start_ticks(os.getpid())
        proof_path = marker_path.with_name(marker_path.name.replace(
            ".started.json", f".crash_proof.{os.getpid()}.{ticks}.json"))
        proof = {"operation_marker_path": str(marker_path), "operation_marker_sha256": base.file_sha(marker_path),
                 "old_producer_pid": marker["producer_pid"], "old_process_start_ticks": marker["producer_process_start_ticks"],
                 "recovered_by_pid": os.getpid(), "recovered_process_start_ticks": ticks,
                 "exclusive_producer_lock_reacquired": True}
        base.save_once(proof_path, proof)
        return proof_path

    def advance(self, through_round=None):
        self.verify_freeze()
        with base.producer_lock(self.out):
            self._ensure_abandoned_charge_seals()
            previous = base.read_json(self.out / "run_status.json") if (self.out / "run_status.json").exists() else {}
            if (previous.get("status") == "technical_hold" and isinstance(previous.get("fault"), dict)
                    and previous["fault"].get("recoverable") is False):
                return previous
            self._recover_interrupted_operations()
            operation = self._begin_operation("advance")
            result = self._advance(through_round)
            self._finish_operation(operation, result["status"])
            return result

    def _advance(self, through_round=None):
        self.verify_freeze()
        from .backend import TechnicalRoundFault, PermanentTechnicalFault
        from .attempts import AttemptLimitExceeded
        while True:
            self.verify_freeze()
            self._open_operations()  # Recheck exact journal hashes before dispatch.
            store, engine = self._store(), self._engine()
            current = store.current()
            if current is not None and all(engine.outcome(current["round"], stage) is not None for stage in STAGES):
                # Recovery at the complete-but-unpublished boundary is a
                # verification and atomic commit, never another paid call.
                from .reviewer_host import validate_archived_envelope
                validate_archived_envelope(self.out, current["round"], working_root=current["working_root"])
                try:
                    engine.complete_round_checkpoint()
                except OSError as error:
                    # All eight observations exist. Publishing their exact
                    # checkpoint can be retried without sampling anything.
                    from .faults import fault_record
                    return self._hold("Complete round publication interrupted; recover the exact saved round with zero new provider calls",
                                      fault=fault_record("round_publication_storage", error))
                self.backend = None
                continue
            target = engine.next_stage()
            if target is None:
                return self._status({"status": "main_complete", "rounds": store.committed_rounds(),
                                     "shadows": "pending fixed post-episode controls"})
            if through_round is not None and target["round"] > through_round:
                return self._status({"status": "round_boundary", "completed_round": store.committed_rounds()})
            if current is None:
                try:
                    self._new_attempt(target["round"])
                except AttemptLimitExceeded:
                    return self._hold("Fixed technical-attempt limit reached; no additional paid dispatch")
                except PermanentTechnicalFault as error:
                    return self._hold("Permanent preflight incompatibility requires repair", fault=error.fault)
                except TechnicalRoundFault as error:
                    return self._hold("Free preflight unavailable; no paid attempt consumed", fault=error.fault)
                continue
            if target["stage"] == "REVIEWER":
                try:
                    job = engine.export_reviewer_packet()
                except OSError as error:
                    self._storage_fault(error, "reviewer_packet_export")
                    continue
                return self._status(job, mirror=True)
            # An existing incomplete dispatch after a process crash is an
            # infrastructure failure. Never submit that request ID again.
            if engine._path(target["round"], target["stage"], "dispatch").exists():
                fault = {"technical": True, "fault_class": "incomplete_dispatched_stage", "stage": target["stage"],
                         "round": target["round"], "exception_class": "UnresolvedDispatch",
                         "stage_outcome_committed": False,
                         "complete_response_observation": "unknown_until_archive_inspected"}
                self._archive_fault(fault)
                continue
            try:
                engine.backend = self._backend()
                engine.run_next_actor()
            except PermanentTechnicalFault as error:
                return self._hold("Permanent API/schema/accounting incompatibility requires repair", fault=error.fault)
            except TechnicalRoundFault as error:
                if error.fault.get("phase") == "free_preflight":
                    return self._hold("Free metadata check unavailable; preserve this same attempt before dispatch", fault=error.fault)
                self._archive_fault(error.fault)
                continue
            except OSError as error:
                self._storage_fault(error, "actor_stage_publication")
                continue

    def preflight(self):
        self.verify_freeze()
        from .backend import Backend, make_policy
        return Backend.preflight_before_attempt(self.config, self.out, base.AGGREGATE, policy=make_policy())

    def dispatch(self, job_id, reviewer_agent):
        self.verify_freeze()
        with base.producer_lock(self.out):
            from .reviewer_host import _attempt_job
            root, job = _attempt_job(self.out)
            if job["job_id"] != job_id:
                raise ValueError("No matching current attempt reviewer job")
            record = {"job_id": job_id, "reviewer_agent": reviewer_agent,
                      "packet_sha256": job["packet_sha256"], "fresh_context_requested": True}
            base.save_once(root / "reviewer_dispatches" / f"r{job['round']:02d}.json", record)
            return {"status": "review_dispatched", **record}

    def ingest(self, response):
        self.verify_freeze()
        with base.producer_lock(self.out):
            from .reviewer_host import _attempt_job, validate_envelope
            root, job = _attempt_job(self.out)
            if response.get("job_id") != job["job_id"]:
                raise ValueError("First review does not match the exact pending attempt")
            dispatch = base.read_json(root / "reviewer_dispatches" / f"r{job['round']:02d}.json")
            if response.get("provenance", {}).get("reviewer_agent") != dispatch["reviewer_agent"]:
                raise ValueError("Actual reviewer differs from the dispatched fresh context")
            validate_envelope(self.out, response)
            if self._open_operations():
                raise ValueError("An interrupted producer operation must be recovered by advance before reviewer ingestion")
            operation = self._begin_operation("ingest_actual_first_review")
            try:
                result = self._engine().ingest_review(response["output"], response["provenance"], response.get("raw_answer"),
                                                     job_id=response["job_id"])
            except OSError as error:
                fault = self._storage_fault(error, "actual_first_review_publication")
                result = self._hold("Reviewer publication failed; exact first answer archived, whole current round will restart on advance", fault=fault)
                self._finish_operation(operation, result["status"])
                return result
            self._status({"status": "review_ingested", "round": job["round"],
                          "attempt_id": self._store().current()["attempt_id"]})
            self._finish_operation(operation, "review_ingested")
            return {"status": "review_ingested", "round": job["round"], "result": result}

    def reviewer_fault(self, proof_path):
        """Apply only a hash-bound genuine host/bridge fault, never a verdict."""
        self.verify_freeze()
        with base.producer_lock(self.out):
            from . import reviewer_host
            from experiments.triad_emergent_v9 import reviewer_host as original
            root, job = reviewer_host._attempt_job(self.out)
            path = original._file(proof_path, self.out / "checkpoints_restart04/reviewer_faults")
            proof = original._read(path)
            if (path.name != original.digest(proof) + ".json" or proof.get("technical") is not True
                    or proof.get("fault_class") != "reviewer_bridge_infrastructure"
                    or proof.get("operation") not in {"prepare", "envelope", "fresh_context_host_failure"}
                    or proof.get("context") != self._store().current() or proof.get("job_id") != job["job_id"]
                    or proof.get("packet_sha256") != job["packet_sha256"]
                    or proof.get("helper_sha256") != base.file_sha(reviewer_host.__file__)):
                raise ValueError("Reviewer technical proof does not bind the exact pending job")
            for name, expected in proof["evidence_files"].items():
                if base.file_sha(original._file(name, self.out)) != expected:
                    raise ValueError("Observed reviewer infrastructure evidence changed")
            if proof["operation"] == "fresh_context_host_failure" and not proof.get("host_evidence_path"):
                raise ValueError("Fresh reviewer launch failure requires actual saved host-tool evidence")
            # If a first answer already exists, assembling its exact envelope
            # retries no judgment and makes zero model or provider requests.
            first = root / f"reviewer_host/r{job['round']:02d}/first_final.txt"
            if first.is_file():
                launch = original._read(first.parent / "launch.json")
                try:
                    result = original.envelope(root, launch["planned_agent"], first)
                    original.validate_envelope(root, original._read(result["envelope_path"]))
                except OSError:
                    pass
                else:
                    return {"status": "reviewer_exact_first_answer_recovered", "response_path": result["envelope_path"],
                            "job_id": job["job_id"], "new_reviewer_judgments": 0, "provider_calls": 0}
            fault = {"technical": True, "fault_class": "reviewer_bridge_infrastructure",
                     "exception_class": proof["exception_class"], "recoverable": True,
                     "reviewer_fault_record_path": str(path), "reviewer_fault_record_sha256": base.file_sha(path)}
            self._archive_fault(fault)
            return self._status({"status": "technical_round_abandoned", "round": job["round"],
                                 "reason": "Actual reviewer infrastructure failure; advance restarts from the prior whole-round checkpoint",
                                 "fault": fault, "new_allowance": False})

    def status(self):
        return {"revision": REPAIR, "out": str(self.out), "budget": base.budget(),
                "engine": self._engine().summary() if (self.out / "checkpoints_restart04/manifest.json").exists() else None,
                "host": base.read_json(self.out / "run_status.json") if (self.out / "run_status.json").exists() else None,
                "attempts": attempt_inventory(self.out)}

    def verify(self):
        freeze = self.verify_freeze()
        engine, store = self._engine(), self._store()
        checks = engine.verify()
        pending_operations = self._open_operations()
        from .reviewer_host import validate_archived_envelope
        reviews = [validate_archived_envelope(self.out, n) for n in range(1, store.committed_rounds() + 1)]
        for item in store.inventory():
            if item["status"] == "abandoned_technical":
                self._verify_external_fault_proof(item["fault"])
        return {"status": "passed", "engine": checks, "checkpoints": store.verify(),
                "operation_journal": {"status": "passed", "unfinished_operations": [str(path) for path in pending_operations]},
                "actual_first_review_bindings_reverified": len(reviews),
                "frozen_source_files": len(freeze["source_files"]), "budget": base.budget()}

    def report(self):
        verification = self.verify()
        from .analysis import build_report
        report = build_report(self, verification)
        base.mutable_json(self.out / "analysis/report.json", report)
        return {"status": "report_written", "rounds": len(report["rounds"]), "path": str(self.out / "analysis/report.json")}

    def render(self, *, allow_incomplete=False):
        self.report()
        from .analysis import render
        return render(self, allow_incomplete=allow_incomplete)

    def shadows(self):
        self.verify()
        with base.producer_lock(self.out):
            if self._store().committed_rounds() != 12:
                raise ValueError("All twelve whole-round commits must precede fixed isolated controls")
            from .counterfactuals import run_shadows
            from .counterfactuals import ShadowError
            from .faults import TechnicalRoundFault, PermanentTechnicalFault, fault_record
            try:
                result = run_shadows(self)
            except PermanentTechnicalFault as error:
                return self._hold("Permanent isolated-control incompatibility requires repair", fault=error.fault)
            except TechnicalRoundFault as error:
                return self._hold("Isolated-control technical workflow held; all failed evidence and costs retained", fault=error.fault)
            except (ShadowError, OSError) as error:
                return self._hold("Isolated-control restart allowance or integrity held; all prior observations retained",
                                  fault=fault_record("shadow_workflow_hold", error, recoverable=False))
            return self._status({"status": "complete", "rounds": 12, "controls": result})

    def export(self, target):
        from .analysis import export
        return export(self, target)
