#!/usr/bin/env python3
"""Administrative orchestration only. No judgments, API clients or data edits.

Only frozen CLI ``advance`` starts paid stages. Frozen Runner.status is called
under its arm lock, and only when that arm has no running producer. All files
written by this script stay beneath this administrative driver directory.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from next_batch.v15_glm_restored.storage import IntegrityError, canonical, lock, read, sha
from next_batch.v16_document_carrier.runner import Runner

PROJECT = Path(__file__).resolve().parents[4]
DEFAULT_OUT = Path(__file__).resolve().parent.parent
ARMS = tuple(f"b{i:02}" for i in range(1, 6))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    with temporary.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def plain_json(path):
    return json.loads(Path(path).read_text())


def process_ticks(pid):
    try:
        return Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (FileNotFoundError, IndexError, ValueError, ProcessLookupError):
        return None


def producer_busy(out, arm):
    path = Path(out) / "arms" / arm / "producer.lock"
    if not path.exists():
        return False
    with path.open("rb") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(stream, fcntl.LOCK_UN)
    return False


def final_json(raw):
    decoder = json.JSONDecoder()
    for offset, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, used = decoder.raw_decode(raw[offset:])
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and not raw[offset + used:].strip():
            return value
    return None


def read_plan(out):
    path = Path(out) / "DISPATCH_ORDER.json"
    body = read(path)
    entries = body.get("order")
    if not isinstance(entries, list) or len(entries) != 12:
        raise ValueError("Prospective dispatch order must contain exactly12 rounds")
    result = {}
    for number, entry in enumerate(entries, 1):
        order = entry.get("dispatch_order")
        if entry.get("round") != number or not isinstance(order, list) or sorted(order) != list(ARMS):
            raise ValueError("Prospective dispatch order is not a permutation of the five arms")
        result[number] = tuple(order)
    return result, hashlib.sha256(path.read_bytes()).hexdigest()


def matched_round(statuses):
    if set(statuses) != set(ARMS):
        return None
    counts = [statuses[arm]["committed_rounds"] for arm in ARMS]
    if any(type(number) is not int or not 0 <= number <= 12 for number in counts):
        raise ValueError("Invalid actual committed-round count")
    if max(counts) - min(counts) > 1:
        raise ValueError("Observed arm progress violates round-synchronous dispatch")
    return 13 if min(counts) == 12 else min(counts) + 1


class Driver:
    def __init__(self, out=DEFAULT_OUT):
        self.out = Path(out).resolve()
        self.directory = self.out / "driver"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.plan, self.plan_hash = read_plan(self.out)
        self.runner = Runner(self.out)
        self.active = {}
        self.statuses = {}
        self.invocations = []
        self.events = []
        self.phase = "waiting_for_start"
        self.round = None
        self.start_signal_hash = None
        self.hold = plain_json(self.directory / "HELD.json") if (self.directory / "HELD.json").exists() else None
        self.stop_signal = None
        self.recovered = []
        self.counter = 0
        for path in sorted((self.directory / "invocations").glob("*.json")):
            record = plain_json(path)
            self.counter = max(self.counter, record.get("sequence", 0))
            self.invocations.append(record)
            if record.get("status") == "running":
                self.recovered.append((path, record))
        self.record_event("driver_started", pid=os.getpid(), start_ticks=process_ticks(os.getpid()), project=str(PROJECT), python=sys.executable)

    def record_event(self, event, **fields):
        entry = {"at_unix": time.time(), "event": event, **fields}
        self.events.append(entry)
        path = self.directory / "events.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps(entry, ensure_ascii=False), flush=True)

    def freeze(self, reason, **fields):
        if self.hold is None:
            self.hold = {"at_unix": time.time(), "reason": reason, "new_dispatches_frozen": True, "running_requests_preserved": True, **fields}
            atomic_json(self.directory / "HELD.json", self.hold)
            self.record_event("new_dispatches_frozen", **self.hold)

    def stopped(self):
        return self.stop_signal is not None or any(path.exists() for path in (self.out / "STOP_REQUESTED.json", self.directory / "STOP_REQUESTED.json"))

    def poll_children(self):
        for arm, child in list(self.active.items()):
            code = child["process"].poll()
            if code is None:
                continue
            child["stream"].close()
            record = child["record"]
            record.update(status="exited", returncode=code, exited_at_unix=time.time())
            record["final_output"] = final_json(Path(record["log_path"]).read_text(errors="replace"))
            atomic_json(child["record_path"], record)
            del self.active[arm]
            self.statuses.pop(arm, None)
            self.record_event("cli_exited", arm=arm, invocation=record["sequence"], pid=record["pid"], returncode=code, final_status=(record["final_output"] or {}).get("status"))
            if code not in (0, 3):
                self.freeze("frozen_cli_nonrecoverable_exit", arm=arm, returncode=code, invocation=record["sequence"], log_path=record["log_path"])
            elif (record["final_output"] or {}).get("status") == "held":
                self.freeze("frozen_cli_held_output", arm=arm, invocation=record["sequence"], log_path=record["log_path"])

    def reconcile(self):
        remaining = []
        for path, record in self.recovered:
            if process_ticks(record.get("pid")) == record.get("pid_start_ticks") and record.get("pid_start_ticks") is not None:
                remaining.append((path, record))
                continue
            record.update(status="exit_status_unavailable_after_driver_restart", observed_inactive_at_unix=time.time(), final_output=final_json(Path(record["log_path"]).read_text(errors="replace")))
            atomic_json(path, record)
            self.record_event("prior_invocation_inactive", arm=record["arm"], invocation=record["sequence"], actual_returncode_available=False)
            if (record.get("final_output") or {}).get("status") == "held":
                self.freeze("prior_frozen_cli_held_output", arm=record["arm"], invocation=record["sequence"])
        self.recovered = remaining

    def inspect_inactive(self, arm):
        if arm in self.active or any(record["arm"] == arm for _, record in self.recovered):
            return
        if producer_busy(self.out, arm):
            self.statuses.pop(arm, None)
            return
        try:
            with lock(self.out / "arms" / arm):
                actual = self.runner.status(arm)
        except IntegrityError as failure:
            if str(failure) == "Another producer owns this run":
                self.statuses.pop(arm, None)
                return
            raise
        if len(actual.get("arms", [])) != 1 or actual["arms"][0].get("arm") != arm:
            raise ValueError("Actual frozen Runner.status returned the wrong arm")
        self.statuses[arm] = actual["arms"][0]

    def pending(self):
        queue = []
        for arm in ARMS:
            status = self.statuses.get(arm, {})
            if status.get("status") != "awaiting_review":
                continue
            job_id = status["job_id"]
            assessment = self.out / "reviews" / job_id / "assessment.json"
            if assessment.exists():
                continue
            packet_path = Path(status["public_packet"])
            expected = self.out / "reviews" / job_id / "public" / "packet.json"
            if packet_path.resolve() != expected.resolve():
                raise ValueError("Review status points outside its actual public packet")
            packet = read(packet_path)
            queue.append({"arm": arm, "round": status["round"], "attempt": status["attempt"], "job_id": job_id, "kind": packet["kind"], "public_packet": str(packet_path), "packet_sha256": sha(canonical(packet).encode()), "packet_file_sha256": hashlib.sha256(packet_path.read_bytes()).hexdigest(), "public_directory": str(packet_path.parent), "instructions": packet["instructions"], "schema": packet["schema"], "assessment_path": str(assessment), "launch_path": str(assessment.parent / "launch.json"), "service": "Root must launch a fresh genuine Codex product context using only this public packet and public files; record and ingest its first actual response with the frozen CLI."})
        return queue

    def save(self):
        queue = self.pending()
        atomic_json(self.directory / "PENDING_REVIEW.json", {"updated_at_unix": time.time(), "phase": self.phase, "new_dispatches_frozen": bool(self.hold or self.stopped()), "jobs": queue})
        atomic_json(self.directory / "STATE.json", {"updated_at_unix": time.time(), "pid": os.getpid(), "pid_start_ticks": process_ticks(os.getpid()), "phase": self.phase, "current_matched_round": self.round, "dispatch_order_sha256": self.plan_hash, "start_allowed": (self.directory / "START_ALLOWED.json").exists(), "start_signal_sha256": self.start_signal_hash, "stop_requested": self.stopped(), "hold": self.hold, "active": [{k: value["record"].get(k) for k in ("arm", "round", "pid", "pid_start_ticks", "sequence", "command", "log_path", "started_at_unix")} for value in self.active.values()], "prior_driver_children_still_active": [record for _, record in self.recovered], "actual_arm_statuses": self.statuses, "pending_review_job_ids": [item["job_id"] for item in queue], "invocation_count": self.counter, "maximum_concurrent_cli_processes": 5, "paid_dispatch_method": "Only frozen CLI advance; no reviewer judgments or direct API clients."})

    def dispatch(self, arm, number):
        if self.hold or self.stopped() or not (self.directory / "START_ALLOWED.json").exists():
            return
        if len(self.active) + len(self.recovered) >= 5 or producer_busy(self.out, arm):
            return
        self.counter += 1
        stem = f"{self.counter:05}.r{number:02}.{arm}"
        log_path = self.directory / "logs" / (stem + ".log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record_path = self.directory / "invocations" / (stem + ".json")
        command = [sys.executable, "-m", "next_batch.v16_document_carrier.cli", "advance", "--out", str(self.out), "--arm", arm, "--through-round", str(number)]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(PROJECT / "src") + os.pathsep + str(PROJECT)
        stream = log_path.open("xb")
        record = {"sequence": self.counter, "arm": arm, "round": number, "status": "launch_intent", "command": command, "cwd": str(PROJECT), "log_path": str(log_path), "started_at_unix": time.time(), "environment_recorded": {"PYTHONPATH": environment["PYTHONPATH"]}, "credentials_never_recorded": True}
        atomic_json(record_path, record)
        try:
            process = subprocess.Popen(command, cwd=PROJECT, env=environment, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        except BaseException:
            stream.close()
            record["status"] = "launch_failed_before_pid"
            atomic_json(record_path, record)
            raise
        record.update(status="running", pid=process.pid, pid_start_ticks=process_ticks(process.pid))
        atomic_json(record_path, record)
        self.invocations.append(record)
        self.active[arm] = {"process": process, "stream": stream, "record": record, "record_path": record_path}
        self.statuses.pop(arm, None)
        self.record_event("cli_started", arm=arm, round=number, invocation=self.counter, pid=process.pid, command=command, log_path=str(log_path))

    def tick(self):
        self.poll_children()
        self.reconcile()
        allowed = self.directory / "START_ALLOWED.json"
        if not allowed.exists():
            self.phase = "held" if self.hold else "stopping_without_killing_requests" if self.stopped() else "waiting_for_start"
            self.save()
            return bool((self.hold or self.stopped()) and not self.active and not self.recovered)
        if hashlib.sha256((self.out / "DISPATCH_ORDER.json").read_bytes()).hexdigest() != self.plan_hash:
            self.freeze("prospective_dispatch_order_changed")
        if self.start_signal_hash is None:
            self.start_signal_hash = hashlib.sha256(allowed.read_bytes()).hexdigest()
            self.record_event("root_start_signal_observed", path=str(allowed), sha256=self.start_signal_hash)
        # No arm locks or Runner calls occur before the explicit root signal.
        for arm in ARMS:
            if arm not in self.statuses or (self.statuses[arm].get("status") == "awaiting_review" and (self.out / "reviews" / self.statuses[arm]["job_id"] / "assessment.json").exists()):
                self.inspect_inactive(arm)
        if self.hold or self.stopped():
            self.phase = "held" if self.hold else "stopping_without_killing_requests"
            self.save()
            return not self.active and not self.recovered
        self.round = matched_round(self.statuses)
        if self.round is None:
            self.phase = "producers_running_or_external_lock"
            self.save()
            return False
        if self.round == 13:
            self.phase = "all_60_rounds_complete"
            self.record_event("all_60_rounds_complete")
            self.save()
            return True
        self.phase = "running_matched_round"
        # Never dispatch n+1 until every actual arm status confirms commit n.
        for arm in self.plan[self.round]:
            status = self.statuses[arm]
            if status["committed_rounds"] >= self.round or arm in self.active:
                continue
            if status.get("status") == "awaiting_review" and not (self.out / "reviews" / status["job_id"] / "assessment.json").exists():
                continue
            self.dispatch(arm, self.round)
        self.save()
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--interval", type=float, default=3)
    args = parser.parse_args(argv)
    if not 1 <= args.interval <= 30:
        parser.error("Administrative polling interval must be1..30 seconds")
    directory = args.out.resolve() / "driver"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "driver.lock").open("a+b") as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another administrative driver is active")
        driver = Driver(args.out)
        def signal_stop(signum, frame):
            driver.stop_signal = signum
            driver.record_event("driver_signal_freezes_new_dispatches", signal=signum, running_requests_preserved=True)
        signal.signal(signal.SIGTERM, signal_stop)
        signal.signal(signal.SIGINT, signal_stop)
        while True:
            try:
                finished = driver.tick()
            except Exception as failure:
                driver.freeze("administrative_inspection_failure", error_class=type(failure).__name__, message=str(failure))
                driver.phase = "held"
                with contextlib.suppress(Exception):
                    driver.save()
                finished = not driver.active and not driver.recovered
            if finished:
                return 2 if driver.hold else 0
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
