"""The fixed v9 scientific engine with whole-round transactional selection.

Only path selection, attempt identities and checkpoint publication change.
Actor prompts, routes, component classification and public review schemas are
inherited unchanged from the frozen v9 engine. No network calls occur here.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from experiments.triad_emergent_v9 import engine as base
from .attempts import AttemptIntegrityError, AttemptStore


class Engine(base.Engine):
    def __init__(self, out, config, plan, backend=None, *, store):
        if not isinstance(store, AttemptStore) or store.out != base._safe_path(out):
            raise AttemptIntegrityError("Engine requires the exact global output checkpoint store")
        self.store = store
        self.store.verify()
        super().__init__(out, config, plan, backend=backend)

    @property
    def active_attempt(self):
        return self.store.current()

    def attempt_context(self):
        return self.store.current()

    def _path(self, number, stage, suffix="outcome"):
        if type(number) is not int or number not in range(1, 13) or stage not in base.STAGES:
            raise base.EngineError("Unknown round or stage")
        if suffix not in {"outcome", "request", "dispatch"}:
            raise base.EngineError("Unknown stage evidence suffix")
        return self.store.path_root(number) / "episode/rounds" / f"r{number:02d}" / f"{stage}.{suffix}.json"

    def _require_active(self, target):
        # Validate the active attempt's existing requests/outcomes too. A
        # checkpoint-only check would catch an altered working chain only at
        # _commit, after the next paid request had already happened.
        self.verify()
        current = self.store.current()
        if (not target or not current or current["legacy"] or current["round"] != target["round"]
                or current["round"] != self.store.committed_rounds() + 1):
            raise AttemptIntegrityError("Begin a fresh attempt from the last whole-round checkpoint before dispatch")
        return current

    def actor_request(self):
        self._require_active(self.next_stage())
        return super().actor_request()

    def run_next_actor(self):
        self._require_active(self.next_stage())
        return super().run_next_actor()

    def validate_actor_result(self, request, result):
        result = super().validate_actor_result(request, result)
        current = self.store.current()
        if current is None or request["round"] != current["round"]:
            raise AttemptIntegrityError("Actor result lost its active technical-attempt context")
        workspace = Path(current["working_root"])
        descriptors = [result["output"].get("image")]
        descriptors.extend(action.get("generated_image") for action in result["actions"] if isinstance(action, dict))
        for descriptor in descriptors:
            if descriptor is None:
                continue
            path = base._safe_path(descriptor["path"], existing=True)
            if ".." in path.parts or not path.is_relative_to(workspace):
                raise AttemptIntegrityError("Actor result used a raster from another technical attempt")
            base.validate_png(descriptor)
        return result

    def _commit(self, target, body):
        current = self._require_active(target)
        body = {**body, "attempt_id": current["attempt_id"]}
        if target["stage"] == "REVIEWER":
            body["reviewer_job_id"] = f"{base.REVISION}.{current['attempt_id']}.REVIEWER"
        return super()._commit(target, body)

    def export_reviewer_packet(self):
        target = self.next_stage()
        current = self._require_active(target)
        if target["stage"] != "REVIEWER":
            raise base.EngineError("No public review is ready")
        from experiments.triad_emergent_v9.review import reviewer_instructions, reviewer_schema
        number = target["round"]
        folder = Path(current["working_root"]) / "reviewer_packets" / f"r{number:02d}"
        images, paths = [], []
        for old in range(1, number + 1):
            for role in "ABC":
                if role == "C" and old == number:
                    continue
                descriptor = self.image_for(old, role)
                if not descriptor:
                    continue
                name = f"r{old:02d}_{role}.png"
                path = folder / "images" / name
                base.save_bytes_once(path, base.validate_png(descriptor))
                images.append({"file": name, "round": old, "role": role,
                               "route": descriptor["route"], "sha256": descriptor["sha256"]})
                paths.append(str(path))
        packet = {"round": number, "task": deepcopy(self.plan["rounds"][number - 1]["task"]),
                  "ballots": self.ballots(number),
                  "history": [{"round": old, "task": deepcopy(self.plan["rounds"][old - 1]["task"]),
                               "ballots": self.ballots(old), "review": self._public_review(old)} for old in range(1, number)],
                  "images": images, "image_history_all_delivered": True,
                  "direct_route_notice": "C images precede the following A turn. Shared reviews can indirectly describe public prior images."}
        base.save_once(folder / "packet.json", packet)
        base.save_once(folder / "schema.json", reviewer_schema())
        base.save_bytes_once(folder / "instructions.txt", reviewer_instructions().encode("utf-8"))
        job = {"status": "awaiting_codex", "job_id": f"{base.REVISION}.{current['attempt_id']}.REVIEWER",
               "round": number, "packet_path": str(folder / "packet.json"),
               "packet_sha256": base.file_digest(folder / "packet.json"),
               "instructions_path": str(folder / "instructions.txt"),
               "schema_path": str(folder / "schema.json"), "images": paths}
        base.save_once(self._path(number, "REVIEWER", "request"), {"packet": packet, "job": job})
        return job

    def ingest_review(self, output, provenance, raw_answer=None, *, job_id=None):
        self._require_active(self.next_stage())
        # Packet bytes can match across attempts. Bind the separately verified
        # outer job identity without changing the original observed provenance.
        job = self.export_reviewer_packet()
        if job_id != job["job_id"]:
            raise AttemptIntegrityError("Actual review provenance belongs to another technical attempt")
        return super().ingest_review(output, provenance, raw_answer=raw_answer)

    def complete_round_checkpoint(self):
        return self.store.commit_completed_round(self)

    def summary(self):
        value = super().summary()
        committed = self.store.committed_rounds()
        current = self.store.current()
        working = sum(self.outcome(current["round"], stage) is not None for stage in base.STAGES) if current else 0
        value.update({"complete_rounds": committed, "committed_rounds": committed,
                      "committed_terminal_stages": committed * len(base.STAGES),
                      "working_terminal_stages": working, "active_attempt": current,
                      "awaiting_round_commit": working == len(base.STAGES),
                      "technical_attempts_are_not_independent_rounds": True})
        return value

    def verify(self):
        self.store.verify()
        result = super().verify()
        # Every post-amendment observed reviewer must bind its selected attempt.
        for number in range(self.store.legacy_rounds + 1, 13):
            observed = self.outcome(number, "REVIEWER")
            if observed is None:
                continue
            request = base.read_json(self._path(number, "REVIEWER", "request"))
            context = self.store.committed_selector(number) or self.store.current()
            if (not context or observed.get("reviewer_job_id") != request["job"]["job_id"]
                    or observed.get("attempt_id") != context["attempt_id"]):
                raise AttemptIntegrityError("Stored actual review lost its attempt-qualified job binding")
        for record in self._records():
            if record["round"] > self.store.legacy_rounds:
                context = self.store.committed_selector(record["round"]) or self.store.current()
                if not context or record.get("attempt_id") != context["attempt_id"]:
                    raise AttemptIntegrityError("Stored actor outcome belongs to another technical attempt")
                for action in record.get("actions", []):
                    descriptor = action.get("generated_image") if isinstance(action, dict) else None
                    if descriptor is None:
                        continue
                    path = base._safe_path(descriptor["path"], existing=True)
                    if ".." in path.parts or not path.is_relative_to(Path(context["working_root"])):
                        raise AttemptIntegrityError("Stored generated draft belongs to another technical attempt")
                    base.validate_png(descriptor)
        return result


TriadEngine = Engine
