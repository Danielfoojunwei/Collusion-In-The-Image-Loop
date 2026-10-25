"""Small, offline-only file and configuration utilities."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_bytes(path: Path, data: bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())


def append_event(path: Path, event: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(canonical({"time": utc_now(), **event}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


@contextlib.contextmanager
def run_lock(path: Path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("This run already has an active process; parallel paid runs are disabled.") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def load_config(path: Path) -> dict:
    config = read_json(path)
    validate_config(config)
    return config


def validate_config(config: dict):
    expected = {
        "protocol_version": "4.0.0", "seed": 20261023,
        "main_episodes": 6, "honest_episodes": 2, "overt_episodes": 2,
        "prefix_rounds": 2, "continuation_rounds": 2,
        "models": {"producer": "z-ai/glm-5.3-flash", "verifier": "z-ai/glm-5.3-flash",
                   "monitor": "anthropic/claude-sonnet-5.5", "image": "qwen/qwen-image-2.1-turbo"},
        "max_cash_usd": 50, "max_inference_usd": 40, "max_output_tokens": {"agent": 4096, "monitor": 2048},
        "max_agent_steps": 4, "max_images_per_turn": 2,
        "reasoning_effort": {"agent": "high", "monitor": "low"}, "image_quality": "standard", "image_size": "1024x1024",
        "paid_retries": 0, "allow_model_fallback": False,
    }
    if config != expected or set(config) != set(expected):
        raise ValueError("Configuration differs from the frozen v4 canonical study. No silent model, sample, budget, or prompt changes.")


def source_manifest() -> dict:
    root = Path(__file__).resolve().parent
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and p.suffix in {".py", ".txt"} and "__pycache__" not in p.parts}


def parse_object(text: str) -> dict:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Structured output must be a JSON object.")
    return value
