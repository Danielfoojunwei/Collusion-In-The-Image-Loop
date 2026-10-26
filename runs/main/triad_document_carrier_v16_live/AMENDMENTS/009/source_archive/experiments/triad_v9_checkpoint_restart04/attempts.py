"""Whole-round publication and immutable technical-attempt archives.

This module makes no provider calls and never settles a ledger reservation.
The coordinator must hold the experiment's producer lock for all mutations.
An attempt's files stay in place after abandonment; only a complete eight-stage
selector can advance the committed checkpoint used by subsequent rounds.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re

from experiments.triad_emergent_v9.engine import (
    EngineError, STAGES, _safe_path, digest, file_digest, read_json, save_once,
)


REVISION = "triad_v9_checkpoint_restart04"


class AttemptIntegrityError(EngineError):
    """Ambiguous or changed evidence requires a hold, never a paid retry."""


class AttemptLimitReached(EngineError):
    """The fixed technical-attempt allowance has been exhausted."""


# Coordinator-facing spelling retained as an explicit alias.
AttemptLimitExceeded = AttemptLimitReached


def _require(test, message):
    if not test:
        raise AttemptIntegrityError(message)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _seal(body):
    return {**deepcopy(body), "record_hash": digest(body)}


def _checked(path):
    value = read_json(path)
    _require(isinstance(value, dict) and _hash(value.get("record_hash")), "Checkpoint record is malformed")
    _require(digest({k: v for k, v in value.items() if k != "record_hash"}) == value["record_hash"],
             "Checkpoint record digest changed")
    return value


def _save_durable(path, value):
    """The immutable link is the single publication point; fsync its directory."""
    path = save_once(path, value)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


class AttemptStore:
    def __init__(self, out, *, legacy_rounds=6, max_attempts=3):
        self.out = _safe_path(out)
        _require(".." not in self.out.parts, "Checkpoint output cannot contain parent traversal")
        _require(type(legacy_rounds) is int and 0 <= legacy_rounds <= 12, "Invalid legacy checkpoint length")
        _require(type(max_attempts) is int and 1 <= max_attempts <= 3, "The technical-attempt cap cannot exceed three")
        self.legacy_rounds, self.max_attempts = legacy_rounds, max_attempts
        self.root = self.out / "checkpoints_restart04"
        self.manifest_path = self.root / "manifest.json"
        self._verified_roots = None

    def _relative(self, path):
        path = _safe_path(path)
        _require(".." not in path.parts and path.is_relative_to(self.out), "Checkpoint evidence escaped the run")
        return str(path.relative_to(self.out))

    def _evidence_path(self, relative):
        _require(isinstance(relative, str), "Evidence path must be text")
        path = Path(relative)
        _require(not path.is_absolute() and ".." not in path.parts, "Unsafe checkpoint evidence path")
        return _safe_path(self.out / path, existing=True)

    def _legacy_snapshot(self):
        files, previous = {}, "0" * 64
        for number in range(1, self.legacy_rounds + 1):
            for stage in STAGES:
                base = self.out / "episode/rounds" / f"r{number:02d}"
                outcome, request = base / f"{stage}.outcome.json", base / f"{stage}.request.json"
                _safe_path(outcome, existing=True)
                _safe_path(request, existing=True)
                record = read_json(outcome)
                body = {k: v for k, v in record.items() if k != "record_hash"}
                _require(record.get("round") == number and record.get("stage") == stage
                         and record.get("previous_hash") == previous and record.get("record_hash") == digest(body),
                         "Legacy checkpoint is not a complete ordered outcome chain")
                previous = record["record_hash"]
                files[self._relative(outcome)] = file_digest(outcome)
                files[self._relative(request)] = file_digest(request)
                self._referenced_artifacts(record, files)
                dispatch = base / f"{stage}.dispatch.json"
                if dispatch.exists():
                    files[self._relative(dispatch)] = file_digest(dispatch)
        return files, previous

    def _referenced_artifacts(self, record, files):
        """Pin copied shared-memory pixels as well as attempt-local raw files."""
        # Inspect only harness-authored raster fields. A valid model response
        # can contain unsupported native arguments named "path" and "sha256";
        # those are observed text, not authority to read an arbitrary file.
        output = record.get("output")
        descriptors = [output.get("image")] if isinstance(output, dict) else []
        descriptors.extend(action.get("generated_image") for action in record.get("actions", [])
                           if isinstance(action, dict))
        for descriptor in descriptors:
            if descriptor is None:
                continue
            _require(isinstance(descriptor, dict) and isinstance(descriptor.get("path"), str)
                     and _hash(descriptor.get("sha256")), "Actual raster descriptor changed")
            path = _safe_path(descriptor["path"], existing=True)
            _require(".." not in path.parts and path.is_relative_to(self.out) and file_digest(path) == descriptor["sha256"],
                     "Referenced scientific artifact changed or escaped the run")
            files[self._relative(path)] = descriptor["sha256"]

    def initialize(self, legacy_chain_head, legacy_stage_hashes):
        """Explicitly pin the already closed legacy prefix, without copying it.

        ``legacy_stage_hashes`` contains run-relative paths and raw file hashes.
        It may contain only the 48 outcome files; exact requests and dispatches
        are additionally pinned here. Caller-supplied files must all match.
        """
        _require(_hash(legacy_chain_head) and isinstance(legacy_stage_hashes, dict),
                 "An exact legacy checkpoint head and file vector are required")
        files, head = self._legacy_snapshot()
        _require(head == legacy_chain_head, "Legacy checkpoint head differs from the authorized baseline")
        for relative, expected in legacy_stage_hashes.items():
            _require(_hash(expected) and file_digest(self._evidence_path(relative)) == expected,
                     "An authorized legacy file hash differs")
            files[relative] = expected
        required = {f"episode/rounds/r{number:02d}/{stage}.outcome.json"
                    for number in range(1, self.legacy_rounds + 1) for stage in STAGES}
        _require(required <= set(legacy_stage_hashes), "The authorized baseline omitted a closed outcome")
        body = {"revision": REVISION, "legacy_rounds": self.legacy_rounds,
                "max_attempts_per_round": self.max_attempts, "legacy_chain_head": head,
                "legacy_files": files, "only_whole_rounds_publish": True,
                "failed_attempts_never_feed_memory": True}
        _save_durable(self.manifest_path, _seal(body))
        self.verify()
        return deepcopy(read_json(self.manifest_path))

    def _manifest(self):
        _require(self.manifest_path.is_file(), "Whole-round checkpoint store has not been explicitly initialized")
        record = _checked(self.manifest_path)
        _require(record.get("revision") == REVISION and record.get("legacy_rounds") == self.legacy_rounds
                 and record.get("max_attempts_per_round") == self.max_attempts
                 and record.get("only_whole_rounds_publish") is True
                 and record.get("failed_attempts_never_feed_memory") is True,
                 "The fixed whole-round checkpoint policy changed")
        return record

    def _attempt_dir(self, number, attempt):
        return self.out / "attempts" / f"r{number:02d}" / f"a{attempt:02d}"

    def _commit_path(self, number):
        return self.root / "commits" / f"r{number:02d}.json"

    def abandonment_path(self, number, attempt):
        return self._attempt_dir(number, attempt) / "abandonment.json"

    def inventory(self):
        """Report every technical attempt separately from scientific rounds."""
        self.verify()
        committed = {r["attempt_id"]: r for r in self._commits()}
        result = []
        for record in self._attempts():
            row = self._context(record)
            path = self.abandonment_path(record["round"], record["attempt"])
            if path.exists():
                row.update(status="abandoned_technical", abandonment_path=str(path),
                           fault=deepcopy(read_json(path)["fault"]))
            elif record["attempt_id"] in committed:
                row.update(status="committed_complete_round", selector_path=str(self._commit_path(record["round"])))
            else:
                row.update(status="active")
            result.append(row)
        return result

    def _context(self, record):
        return {key: deepcopy(record[key]) for key in (
            "round", "attempt", "attempt_id", "working_root", "prior_checkpoint_sha256", "legacy")}

    def _attempts(self):
        records = []
        for path in sorted((self.out / "attempts").glob("r*/a*/attempt.json")):
            record = _checked(path)
            number, attempt = record.get("round"), record.get("attempt")
            _require(type(number) is int and self.legacy_rounds < number <= 12
                     and type(attempt) is int and 1 <= attempt <= self.max_attempts,
                     "Attempt identity is outside the fixed schedule")
            _require(path == self._attempt_dir(number, attempt) / "attempt.json"
                     and record.get("attempt_id") == f"r{number:02d}.a{attempt:02d}"
                     and type(record.get("legacy")) is bool, "Attempt location or identity changed")
            workspace = self.out if record["legacy"] else self._attempt_dir(number, attempt)
            _require(record.get("working_root") == str(workspace)
                     and _hash(record.get("prior_checkpoint_sha256")), "Attempt workspace or checkpoint binding changed")
            _require(not record["legacy"] or (number == self.legacy_rounds + 1 and attempt == 1),
                     "Only the pre-repair failed first attempt can use legacy paths")
            records.append(record)
        return records

    def _commits(self):
        records, gap = [], False
        for number in range(self.legacy_rounds + 1, 13):
            path = self._commit_path(number)
            if not path.exists():
                gap = True
                continue
            _require(not gap, "A committed round skipped its prior checkpoint")
            record = _checked(path)
            _require(record.get("round") == number, "Committed selector round identity changed")
            records.append(record)
        return records

    def committed_rounds(self):
        self._manifest()
        return self.legacy_rounds + len(self._commits())

    def committed_selector(self, number):
        _require(type(number) is int and 1 <= number <= 12, "Unknown committed round")
        self._manifest()
        return deepcopy(next((r for r in self._commits() if r["round"] == number), None))

    def _prior_checkpoint(self, number):
        _require(number > self.legacy_rounds, "A new attempt cannot replace the legacy prefix")
        path = self.manifest_path if number == self.legacy_rounds + 1 else self._commit_path(number - 1)
        return file_digest(_safe_path(path, existing=True))

    def current(self):
        commits = {r["attempt_id"] for r in self._commits()}
        active = [r for r in self._attempts()
                  if r["attempt_id"] not in commits
                  and not (self._attempt_dir(r["round"], r["attempt"]) / "abandonment.json").exists()]
        _require(len(active) <= 1, "More than one technical attempt is active")
        return self._context(active[0]) if active else None

    def working_root(self):
        current = self.current()
        _require(current is not None, "No active whole-round attempt")
        return _safe_path(current["working_root"])

    def path_root(self, number):
        """Resolve only a committed attempt or the one active current attempt."""
        # A store mutation and every dispatch boundary run verify(), refreshing
        # this selection. Do not reparse every large archive once per stage
        # during the inherited engine's repeated complete-chain traversals.
        if self._verified_roots is None:
            self.verify()
        if number in self._verified_roots:
            return _safe_path(self._verified_roots[number])
        # Never fall back to failed legacy r07 or an abandoned attempt. This
        # namespace is deliberately absent and cannot be dispatched by Engine.
        sentinel = self.root / "unstarted" / f"r{number:02d}"
        _require(not sentinel.exists(), "Untracked stage evidence exists in the unstarted namespace")
        return sentinel

    def begin_attempt(self, number):
        self.verify()
        _require(type(number) is int and number == self.committed_rounds() + 1 and number <= 12,
                 "New attempts must start immediately after the last whole checkpoint")
        active = self.current()
        if active:
            _require(active["round"] == number and not active["legacy"], "An unresolved attempt already owns this frontier")
            return active
        previous = [r for r in self._attempts() if r["round"] == number]
        attempt = len(previous) + 1
        if attempt > self.max_attempts:
            raise AttemptLimitReached("Three technical attempts are exhausted; preserve the unfinished round and hold")
        workspace = self._attempt_dir(number, attempt)
        _require(not workspace.exists(), "Untracked files occupy the next attempt workspace")
        body = {"revision": REVISION, "round": number, "attempt": attempt,
                "attempt_id": f"r{number:02d}.a{attempt:02d}", "legacy": False,
                "working_root": str(workspace), "prior_checkpoint_sha256": self._prior_checkpoint(number)}
        _save_durable(workspace / "attempt.json", _seal(body))
        self.verify()
        return self.current()

    def register_legacy_abort(self, fault, *, round_number=None):
        """Account for the pre-amendment lost attempt without changing its files."""
        self._manifest()
        number = self.legacy_rounds + 1 if round_number is None else round_number
        _require(number == self.legacy_rounds + 1 and self.committed_rounds() == self.legacy_rounds,
                 "Legacy technical-loss registration can occur only at the preserved frontier")
        workspace = self._attempt_dir(number, 1)
        body = {"revision": REVISION, "round": number, "attempt": 1,
                "attempt_id": f"r{number:02d}.a01", "legacy": True,
                "working_root": str(self.out), "prior_checkpoint_sha256": self._prior_checkpoint(number)}
        _save_durable(workspace / "attempt.json", _seal(body))
        existing = workspace / "abandonment.json"
        if existing.exists():
            record = _checked(existing)
            _require(record.get("fault") == fault, "The original technical-loss authority changed")
            self.verify()
            return record
        _require(self.current() and self.current()["attempt_id"] == body["attempt_id"],
                 "Another attempt already owns the frontier")
        return self.abandon_attempt(fault)

    def _inventory(self, context):
        workspace = Path(context["working_root"])
        if context["legacy"]:
            # The original global run has mutable host status and other rounds.
            # Pin this round's available stage/reviewer evidence only; the
            # activation seal separately pins all historical paid evidence.
            roots = [workspace / "episode/rounds" / f"r{context['round']:02d}",
                     workspace / "reviewer_packets" / f"r{context['round']:02d}",
                     workspace / "reviewer_host" / f"r{context['round']:02d}",
                     self._attempt_dir(context["round"], context["attempt"]) / "attempt.json"]
        else:
            roots = [workspace]
        files = {}
        for root in roots:
            paths = [root] if root.is_file() else sorted(root.rglob("*")) if root.exists() else []
            for path in paths:
                if not path.is_file() or path.name in {"abandonment.json", "run_status.json"} or path.name.startswith(".v9-"):
                    continue
                files[self._relative(path)] = file_digest(_safe_path(path, existing=True))
        for stage in STAGES:
            path = workspace / "episode/rounds" / f"r{context['round']:02d}" / f"{stage}.outcome.json"
            if path.is_file():
                self._referenced_artifacts(read_json(path), files)
        return files

    def abandon_attempt(self, fault):
        self.verify()
        _require(isinstance(fault, dict) and fault.get("technical") is True
                 and isinstance(fault.get("fault_class"), str) and fault["fault_class"],
                 "Abandonment requires explicit technical provenance, never a behavioral score")
        current = self.current()
        _require(current is not None, "No active attempt to abandon")
        # A complete durable round is recovered and published, not resampled.
        stage_root = Path(current["working_root"]) / "episode/rounds" / f"r{current['round']:02d}"
        _require(not all((stage_root / f"{stage}.outcome.json").is_file() for stage in STAGES),
                 "A complete eight-stage attempt must recover its exact checkpoint instead of restarting")
        body = {"revision": REVISION, **current, "fault": deepcopy(fault),
                "evidence_files": self._inventory(current), "status": "abandoned_technical",
                "paid_costs_and_unknown_reserves_remain_counted": True,
                "excluded_from_future_actor_and_reviewer_memory": True}
        path = self._attempt_dir(current["round"], current["attempt"]) / "abandonment.json"
        _save_durable(path, _seal(body))
        self.verify()
        return deepcopy(read_json(path))

    def commit_completed_round(self, engine):
        self.verify()
        _require(engine.out == self.out and engine.store is self, "Round commit requires this exact engine/store")
        current = self.current()
        if current is None:
            commits = self._commits()
            _require(bool(commits), "No active complete round to publish")
            # Idempotent status recovery after the publication point.
            return deepcopy(commits[-1])
        _require(not current["legacy"], "The failed pre-amendment attempt cannot be committed")
        number = current["round"]
        _require(number == self.committed_rounds() + 1, "Round commit is out of order")
        _require(all(engine.outcome(number, stage) is not None for stage in STAGES),
                 "Only eight complete durable stages may publish a round checkpoint")
        proof = engine.verify()
        stages = {stage: {
            "record_hash": engine.outcome(number, stage)["record_hash"],
            "outcome_file_sha256": file_digest(engine._path(number, stage)),
            "request_file_sha256": file_digest(engine._path(number, stage, "request")),
        } for stage in STAGES}
        body = {"revision": REVISION, **current, "chain_head": proof["chain_head"],
                "stage_hashes": stages, "evidence_files": self._inventory(current),
                "status": "committed_complete_round", "all_eight_stages": True,
                "actual_first_review_preserved": True}
        _save_durable(self._commit_path(number), _seal(body))
        self.verify()
        return deepcopy(read_json(self._commit_path(number)))

    def _verify_files(self, files):
        _require(isinstance(files, dict), "Checkpoint evidence inventory is malformed")
        for relative, expected in files.items():
            _require(_hash(expected) and file_digest(self._evidence_path(relative)) == expected,
                     "Pinned checkpoint or archived attempt evidence changed")

    def verify(self):
        manifest = self._manifest()
        self._verify_files(manifest.get("legacy_files"))
        files, head = self._legacy_snapshot()
        _require(head == manifest.get("legacy_chain_head")
                 and all(manifest["legacy_files"].get(path) == value for path, value in files.items()),
                 "The closed legacy checkpoint changed")
        attempts, commits = self._attempts(), self._commits()
        by_id = {record["attempt_id"]: record for record in attempts}
        _require(len(by_id) == len(attempts), "Duplicate attempt identity")
        committed_ids = set()
        previous_head = head
        for selector in commits:
            attempt = by_id.get(selector.get("attempt_id"))
            _require(attempt is not None and not attempt["legacy"]
                     and self._context(selector) == self._context(attempt)
                     and selector.get("status") == "committed_complete_round"
                     and selector.get("all_eight_stages") is True
                     and selector.get("actual_first_review_preserved") is True,
                     "Committed selector lost its complete attempt binding")
            _require(selector["prior_checkpoint_sha256"] == self._prior_checkpoint(selector["round"]),
                     "Committed selector points at another prior checkpoint")
            _require(not (self._attempt_dir(attempt["round"], attempt["attempt"]) / "abandonment.json").exists(),
                     "An abandoned attempt cannot also be committed")
            self._verify_files(selector.get("evidence_files"))
            stages = selector.get("stage_hashes")
            _require(isinstance(stages, dict) and set(stages) == set(STAGES), "Committed round lost a staged outcome")
            for stage in STAGES:
                base = Path(attempt["working_root"]) / "episode/rounds" / f"r{attempt['round']:02d}"
                record = read_json(base / f"{stage}.outcome.json")
                body = {k: v for k, v in record.items() if k != "record_hash"}
                _require(record.get("round") == attempt["round"] and record.get("stage") == stage
                         and record.get("previous_hash") == previous_head and record.get("record_hash") == digest(body)
                         and record["record_hash"] == stages[stage].get("record_hash")
                         and file_digest(base / f"{stage}.outcome.json") == stages[stage].get("outcome_file_sha256")
                         and file_digest(base / f"{stage}.request.json") == stages[stage].get("request_file_sha256"),
                         "Committed whole-round chain or exact stage file changed")
                previous_head = record["record_hash"]
            _require(selector.get("chain_head") == previous_head, "Committed checkpoint chain head changed")
            committed_ids.add(attempt["attempt_id"])
        frontier = self.legacy_rounds + len(commits) + 1
        abandoned_count, active = 0, []
        for number in range(self.legacy_rounds + 1, 13):
            group = [record for record in attempts if record["round"] == number]
            _require([r["attempt"] for r in group] == list(range(1, len(group) + 1)),
                     "Technical attempt numbers have a gap")
            for record in group:
                _require(number <= frontier and record["prior_checkpoint_sha256"] == self._prior_checkpoint(number),
                         "Attempt was created from an uncommitted or altered checkpoint")
                path = self._attempt_dir(number, record["attempt"]) / "abandonment.json"
                if path.exists():
                    abandonment = _checked(path)
                    _require(self._context(abandonment) == self._context(record)
                             and abandonment.get("status") == "abandoned_technical"
                             and abandonment.get("fault", {}).get("technical") is True
                             and record["attempt_id"] not in committed_ids,
                             "Technical abandonment record changed its identity")
                    self._verify_files(abandonment.get("evidence_files"))
                    abandoned_count += 1
                elif record["attempt_id"] not in committed_ids:
                    active.append(record)
                _require(record["attempt"] == len(group) or path.exists(),
                         "A new technical attempt bypassed an unresolved earlier attempt")
        _require(len(active) <= 1 and all(r["round"] == frontier for r in active),
                 "More than one attempt or an out-of-order attempt is active")
        self._verified_roots = {number: self.out for number in range(1, self.legacy_rounds + 1)}
        self._verified_roots.update({record["round"]: Path(record["working_root"]) for record in commits})
        self._verified_roots.update({record["round"]: Path(record["working_root"]) for record in active})
        return {"status": "passed", "revision": REVISION, "committed_rounds": frontier - 1,
                "committed_terminal_stages": (frontier - 1) * len(STAGES),
                "attempts": len(attempts), "abandoned_attempts": abandoned_count,
                "active_attempt": self._context(active[0]) if active else None,
                "committed_chain_head": previous_head, "provider_calls": 0}
