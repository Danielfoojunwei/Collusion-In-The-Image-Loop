"""Durable hash-chained role memory; no network or paid provider dependency."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile

try:
    from .contracts import ContractError, canonical, project_event, validate_event, validate_image_descriptor
except ImportError:
    from contracts import ContractError, canonical, project_event, validate_event, validate_image_descriptor


ZERO_HASH = "0" * 64


def _digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def safe_artifact_path(directory, descriptor):
    validate_image_descriptor(descriptor)
    root = Path(directory).resolve()
    supplied = Path(descriptor["path"])
    if ".." in supplied.parts:
        raise ContractError("Image path cannot traverse a parent directory")
    path = supplied if supplied.is_absolute() else root / supplied
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ContractError("Image must belong to this memory directory") from None
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ContractError("Image artifact cannot use symlinks")
    if not path.is_file():
        raise ContractError("Image artifact is missing")
    return path


def load_image(directory, descriptor):
    path = safe_artifact_path(directory, descriptor)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ContractError("Image artifact exceeds the source byte bound")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
        raise ContractError("Image artifact SHA-256 changed")
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.size != (1024, 1024) or getattr(image, "n_frames", 1) != 1:
                raise ContractError("Image artifact must be a single exact 1024-square PNG")
            image.verify()
    except (OSError, SyntaxError, ValueError) as error:
        if isinstance(error, ContractError):
            raise
        raise ContractError("Image artifact cannot be decoded") from None
    return data


def _write_exclusive(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != data:
            raise ContractError("Immutable saved memory artifact changed") from None
        return
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        # Never replace or reinterpret a partially written file automatically.
        raise


class EventStore:
    """Append-only local observed-event log with explicit information-flow views.

    The hash chain detects modification of the retained chain and its prefix.
    A separate durable head record detects a missing/truncated tail. This is a
    local integrity mechanism, not external notarization or an OS privacy fence.
    """

    def __init__(self, path):
        supplied = Path(path)
        if supplied.is_symlink():
            raise ContractError("Memory directory cannot be a symlink")
        supplied.mkdir(parents=True, exist_ok=True)
        self.path = supplied.resolve()
        self.directory = self.path
        self.log_path = self.path / "events.jsonl"
        self.head_path = self.path / "head.json"
        with self._lock():
            meta = {"schema_version": "pressure_interview_memory_v1", "kind": "observed_event_store"}
            _write_exclusive(self.path / "meta.json", (canonical(meta) + "\n").encode())
            if not self.log_path.exists():
                if self.head_path.exists():
                    raise ContractError("Memory log missing beside durable head")
                _write_exclusive(self.log_path, b"")
            if self.log_path.is_symlink() or self.head_path.is_symlink():
                raise ContractError("Memory metadata cannot be symlinked")
            if not self.head_path.exists():
                if self.log_path.stat().st_size:
                    raise ContractError("Nonempty memory has no durable head")
                self._set_head(0, ZERO_HASH)
            self._read_unlocked()

    @contextmanager
    def _lock(self):
        lock = self.path / "memory.lock"
        if lock.is_symlink():
            raise ContractError("Memory lock cannot be a symlink")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _set_head(self, count, checksum):
        with tempfile.NamedTemporaryFile(dir=self.path, prefix=".head-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((canonical({"count": count, "hash": checksum}) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.head_path)
        directory_fd = os.open(self.path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _read_unlocked(self):
        if self.log_path.is_symlink() or self.head_path.is_symlink():
            raise ContractError("Memory evidence cannot be symlinked")
        raw = self.log_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ContractError("Memory has an incomplete event; no automatic truncation")
        rows, previous, identities = [], ZERO_HASH, set()
        for number, line in enumerate(raw.splitlines(), 1):
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                raise ContractError("Memory event is not valid JSON") from None
            if not isinstance(event, dict) or not {"seq", "previous_hash", "hash"} <= set(event):
                raise ContractError("Memory chain fields are missing")
            body = validate_event({key: value for key, value in event.items() if key not in {"seq", "previous_hash", "hash"}})
            expected = {**body, "seq": number, "previous_hash": previous}
            if event != {**expected, "hash": _digest(expected)}:
                raise ContractError("Memory event integrity changed")
            if event["id"] in identities:
                raise ContractError("Memory repeats an event identity")
            identities.add(event["id"])
            rows.append(event)
            previous = event["hash"]
        try:
            head = json.loads(self.head_path.read_text())
        except (ValueError, UnicodeDecodeError, FileNotFoundError):
            raise ContractError("Durable memory head is missing or invalid") from None
        if head != {"count": len(rows), "hash": previous}:
            raise ContractError("Memory head does not match retained events; interrupted append or tampering")
        return rows

    def events(self):
        with self._lock():
            return deepcopy(self._read_unlocked())

    def events_for(self, viewer):
        return [visible for event in self.events() if (visible := project_event(event, viewer)) is not None]

    def append(self, event_id, role=None, kind=None, payload=None, visibility=None,
               round_number=None, branch=None):
        if isinstance(event_id, dict):
            if any(value is not None for value in (role, kind, payload, visibility, round_number, branch)):
                raise ContractError("Use an event object or named event fields, not both")
            body = validate_event(event_id)
        else:
            event = {"id": event_id, "role": role, "kind": kind, "payload": payload,
                     "round_number": round_number, "branch": branch}
            if visibility is not None:
                event["visibility"] = visibility
            body = validate_event(event)
        with self._lock():
            rows = self._read_unlocked()
            existing = next((row for row in rows if row["id"] == body["id"]), None)
            if existing is not None:
                previous_body = {key: value for key, value in existing.items() if key not in {"seq", "previous_hash", "hash"}}
                if previous_body != body:
                    raise ContractError("A stable event identity was reused with different content")
                return deepcopy(existing)
            if "image" in body["payload"]:
                load_image(self.path, body["payload"]["image"])
            event = {**body, "seq": len(rows) + 1,
                     "previous_hash": rows[-1]["hash"] if rows else ZERO_HASH}
            event["hash"] = _digest(event)
            data = (canonical(event) + "\n").encode()
            with self.log_path.open("ab") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            self._set_head(len(rows) + 1, event["hash"])
            return deepcopy(event)

    def fork(self, branchdir, upto_event_id=None):
        """Copy a verified prefix and exact referenced pixels, then diverge locally."""
        if Path(branchdir).resolve() == self.path:
            raise ContractError("A memory fork must have a separate directory")
        with self._lock():
            rows = self._read_unlocked()
            if upto_event_id is not None:
                index = next((index for index, row in enumerate(rows) if row["id"] == upto_event_id), None)
                if index is None:
                    raise ContractError("Fork checkpoint is not in the retained history")
                rows = rows[:index + 1]
            child = EventStore(branchdir)
            binding = {"schema_version": "pressure_interview_memory_fork_v1",
                       "parent_prefix_count": len(rows),
                       "parent_prefix_hash": rows[-1]["hash"] if rows else ZERO_HASH}
            _write_exclusive(child.path / "fork.json", (canonical(binding) + "\n").encode())
            child_rows = child.events()
            if len(child_rows) < len(rows) and child_rows != rows[:len(child_rows)]:
                raise ContractError("Existing fork prefix differs from the checkpoint")
            if len(child_rows) >= len(rows) and child_rows[:len(rows)] != rows:
                raise ContractError("Existing fork prefix differs from the checkpoint")
            for event in rows:
                image = event["payload"].get("image")
                if image:
                    data = load_image(self.path, image)
                    original = safe_artifact_path(self.path, image)
                    relative = original.relative_to(self.path)
                    if Path(image["path"]).is_absolute():
                        raise ContractError("Forkable artifacts require relative source paths")
                    destination = child.path
                    for part in relative.parts:
                        destination = destination / part
                        if destination.is_symlink():
                            raise ContractError("Fork destination artifact cannot use symlinks")
                    _write_exclusive(destination, data)
                body = {key: value for key, value in event.items() if key not in {"seq", "previous_hash", "hash"}}
                child.append(body)
            return child
