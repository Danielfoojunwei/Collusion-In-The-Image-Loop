"""Single-producer host for the frozen, post-main v10 control manifest.

No model calls occur on import. Main completion, source freeze, branch journals,
and original aggregate accounting are checked before any control POST. Real
Codex review execution belongs to the main host and is never repeated here.
"""
import argparse
import json
import os
from pathlib import Path
import sys

from . import funding
from .backend import Backend
from .controls import ControlRunner, manifest
from .coordinator import Coordinator, CoordinatorError
from .engine import canonical, digest, file_sha, process_start_ticks, read_record, safe_path


class ControlHost:
    def __init__(self, out, *, backend_factory=None, coordinator=None):
        self.out = safe_path(out)
        self.coordinator = coordinator or Coordinator(self.out)
        if self.coordinator.out != self.out:
            raise CoordinatorError("control_host_main_root_differs")
        self.backend_factory = backend_factory or Backend
        self.runner = None

    def locked(self):
        return self.coordinator.locked()

    def _ready(self):
        self.coordinator._ready()
        self.coordinator._assert_no_holds()
        if self.coordinator.engine.status()["committed_rounds"] != 12:
            raise CoordinatorError("all_twelve_main_rounds_must_commit_before_controls")
        self.coordinator._check_launch_preflight()
        if self.runner is None:
            self.runner = ControlRunner(self.coordinator.engine, self.out / "controls",
                                        self._backend_for, self._on_abandon)
        self.runner.verify_main()
        schedule = read_record(self.runner.out / "manifest.json")
        if {k: v for k, v in schedule.items() if k != "record_hash"} != manifest():
            raise CoordinatorError("control_manifest_differs_from_frozen_schedule")
        preserved = read_record(self.runner.out / "main_preservation.json")
        if {k: v for k, v in preserved.items() if k != "record_hash"} != self.runner.preservation:
            raise CoordinatorError("control_main_preservation_binding_differs")

    def _validate_context(self, context, *, dispatch=False, allow_abandoned=False):
        self._ready()
        if not isinstance(context, dict) or set(context) != {
                "attempt_id", "working_root", "round", "family", "attempt", "main_preservation_sha256"}:
            raise CoordinatorError("control_context_schema_differs")
        number, family, attempt = context["round"], context["family"], context["attempt"]
        if (type(number) is not int or type(attempt) is not int or not 1 <= attempt <= 3
                or not any(b["round"] == number and b["family"] == family for b in self.runner.schedule["branches"])):
            raise CoordinatorError("control_context_outside_fixed_manifest")
        expected = self.runner._context(number, family, attempt)
        if context != expected:
            raise CoordinatorError("control_context_identity_differs")
        root = safe_path(context["working_root"], self.runner.out)
        original = read_record(root / "attempt.json")
        expected_attempt = {**context, "status": "active", "fixed_manifest_sha256": digest(self.runner.schedule)}
        if {k: v for k, v in original.items() if k != "record_hash"} != expected_attempt:
            raise CoordinatorError("control_attempt_record_differs")
        directories = sorted(root.parent.glob("a*"))
        expected_count = len(directories) if allow_abandoned else attempt
        if (not attempt <= expected_count <= 3
                or [p.name for p in directories] != [f"a{i:02d}" for i in range(1, expected_count + 1)]):
            raise CoordinatorError("control_attempts_not_contiguous_or_current")
        for prior in directories[:attempt - 1]:
            abandoned = read_record(prior / "abandonment.json")
            if (abandoned.get("status") != "abandoned_technical"
                    or abandoned.get("attempt_id") != f"ctrl.r{number:02d}.{family}.{prior.name}"
                    or abandoned.get("fault", {}).get("technical") is not True):
                raise CoordinatorError("prior_control_attempt_not_technically_abandoned")
        if (root / "hold.json").exists():
            read_record(root / "hold.json")
            raise CoordinatorError("control_attempt_has_permanent_hold")
        marker = root / "abandonment.json"
        if marker.exists() and not allow_abandoned:
            raise CoordinatorError("abandoned_control_attempt_cannot_dispatch")
        if dispatch:
            if self.runner._completed(number, family) is not None:
                raise CoordinatorError("completed_control_branch_cannot_dispatch")
            active = []
            for started in sorted((root / "operations").glob("*.started.json")):
                endpoint = started.name.removesuffix(".started.json")
                if (root / "operations" / (endpoint + ".finished.json")).exists():
                    continue
                owner = read_record(started)
                request = root / "requests" / (endpoint + ".json")
                if (owner.get("attempt_id") != context["attempt_id"]
                        or not str(owner.get("request_id", "")).startswith(context["attempt_id"] + ".")
                        or owner.get("request_sha256") != file_sha(safe_path(request, root))
                        or owner.get("producer_pid") != os.getpid()
                        or owner.get("producer_process_start_ticks") != process_start_ticks(os.getpid())
                        or (root / "results" / (endpoint + ".json")).exists()):
                    raise CoordinatorError("control_operation_journal_binding_differs")
                active.append(endpoint)
            if len(active) != 1:
                raise CoordinatorError("exactly_one_active_control_operation_required")
            branch = next(b for b in self.runner.schedule["branches"]
                          if b["round"] == number and b["family"] == family)
            if active[0] not in {e["stage"] for e in branch["endpoints"]}:
                raise CoordinatorError("control_operation_outside_branch_manifest")
        return root

    def _before_dispatch(self, context):
        # Funding calls this both before reservation and at the POST callback.
        self.coordinator.verify_freeze()
        self._validate_context(context, dispatch=True)

    def _backend_for(self, context):
        root = self._validate_context(context)
        guard = funding.create_guard(self.coordinator.aggregate_path,
            run_root=self.out, evidence_dir=root / "provider_evidence",
            active_prefix=context["attempt_id"], ledger=self.coordinator.ledger,
            before_dispatch=lambda: self._before_dispatch(context))
        backend = self.backend_factory(self.coordinator.engine.config, root,
            self.coordinator.aggregate_path, guard=guard, preflight=False)
        # Free provider metadata refresh. The complete-launch bound was already
        # checked before the main run; each actual paid request keeps its own
        # normal key/reservation check and never gains a separate allowance.
        backend.preflight()
        return backend

    def _on_abandon(self, context, marker_path):
        root = self._validate_context(context, allow_abandoned=True)
        marker_path = safe_path(marker_path, root)
        if marker_path != root / "abandonment.json":
            raise CoordinatorError("control_abandonment_path_differs")
        marker = read_record(marker_path)
        if (marker.get("status") != "abandoned_technical"
                or marker.get("attempt_id") != context["attempt_id"]
                or marker.get("fault", {}).get("technical") is not True):
            raise CoordinatorError("typed_control_technical_abandonment_required")
        return funding.seal_abandoned_attempt(self.out, context["attempt_id"], marker_path,
                                              ledger=self.coordinator.ledger)

    def status(self):
        self._ready()
        branches = []
        for branch in self.runner.schedule["branches"]:
            number, family = branch["round"], branch["family"]
            complete = self.runner._completed(number, family)
            branch_root = self.runner.out / "branches" / f"r{number:02d}" / family
            holds = sorted(branch_root.glob("attempts/a*/hold.json"))
            abandoned = sorted(branch_root.glob("attempts/a*/abandonment.json"))
            branches.append({"round": number, "family": family,
                "status": "complete" if complete else "held" if holds else "pending",
                "attempt_id": complete["attempt_id"] if complete else None,
                "technical_abandonments": len(abandoned), "holds": [str(p) for p in holds]})
        final = self.runner.out / "complete.json"
        if final.exists():
            report = read_record(final)
            expected = [{"round": row["round"], "family": row["family"]} for row in branches]
            actual = [{"round": row.get("round"), "family": row.get("family")} for row in report.get("branches", [])]
            if (report.get("status") != "complete" or actual != expected
                    or any(row["status"] != "complete" for row in branches)
                    or report.get("main_preservation_sha256") != self.runner.preservation_sha):
                raise CoordinatorError("completed_control_report_binding_differs")
        return {"status": "complete" if final.exists() else "in_progress",
                "main_committed_rounds": 12, "branches": branches,
                "main_preservation_sha256": self.runner.verify_main(),
                "fixed_normal_bound": {"chat": self.runner.schedule["max_chat"],
                                       "images": self.runner.schedule["max_images"]},
                "budget": self.coordinator._budget_status(),
                "paid_requests_made_by_status": 0}

    def run_branch(self, number, family):
        self._ready()
        try:return self.runner.run_branch(number, family)
        except Exception:
            if self.coordinator.engine.config.get('acceptance',{}).get('restart_entire_episode_on_any_failure'):
                return self.coordinator._abort_episode({'round':number,'stage':'control.'+family,
                    'request_id':f'control.r{number:02d}.{family}'},'control_failure_requires_whole_episode_restart')
            raise

    def run_all(self):
        self._ready()
        try:return self.runner.run_all()
        except Exception:
            if self.coordinator.engine.config.get('acceptance',{}).get('restart_entire_episode_on_any_failure'):
                return self.coordinator._abort_episode({'round':None,'stage':'controls',
                    'request_id':'control.workflow'},'control_failure_requires_whole_episode_restart')
            raise

    def dispatch(self, command):
        if not isinstance(command, dict) or not isinstance(command.get("command"), str):
            raise CoordinatorError("json_command_required")
        name = command["command"]
        expected = {"status": set(), "run_branch": {"round", "family"}, "run_all": set(), "quit": set()}
        if name not in expected or set(command) - {"command"} != expected[name]:
            raise CoordinatorError("unknown_control_command_or_option")
        if name == "quit": return {"status": "bye"}
        if name == "run_branch": return self.run_branch(command["round"], command["family"])
        return getattr(self, name)()

    def serve(self, input_stream=None, output_stream=None):
        input_stream = input_stream or sys.stdin
        output_stream = output_stream or sys.stdout
        with self.locked():
            for line in input_stream:
                try:
                    command = json.loads(line)
                    result = self.dispatch(command)
                    response = {"ok": True, "result": result}
                except funding.OperatorStop:
                    response={'ok':True,'result':{'status':'stopped','automatic_restart':False}}
                    command={'command':'quit'}
                except (ValueError, RuntimeError, OSError) as error:
                    response = {"ok": False, "error": {"class": type(error).__name__,
                        "code": error.code if isinstance(error, CoordinatorError) else "operation_held"}}
                    command = None
                output_stream.write(canonical(response) + "\n")
                output_stream.flush()
                if command and command.get("command") == "quit": break


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("command", choices=("serve", "status", "run_branch", "run_all"))
    parser.add_argument("--round", type=int)
    parser.add_argument("--family")
    args = parser.parse_args(argv)
    host = ControlHost(args.out)
    import signal
    def stopped(signum,frame):
        host.coordinator.request_stop()
        raise funding.OperatorStop()
    signal.signal(signal.SIGINT,stopped)
    signal.signal(signal.SIGTERM,stopped)
    if args.command == "serve":
        host.serve()
        return
    command = {"command": args.command}
    if args.round is not None: command["round"] = args.round
    if args.family is not None: command["family"] = args.family
    with host.locked():
        print(canonical({"ok": True, "result": host.dispatch(command)}), flush=True)


if __name__ == "__main__":
    main()
