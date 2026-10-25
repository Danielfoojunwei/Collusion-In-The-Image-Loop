"""Content-addressed pixel storage preserves exact input reconstruction cheaply."""
from __future__ import annotations
import base64
import copy
import hashlib
from pathlib import Path
from .common import atomic_bytes


def store_pixels(out, data):
    sha = hashlib.sha256(data).hexdigest()
    path = Path(out) / "blobs" / (sha + ".png")
    if path.exists() and path.read_bytes() != data:
        raise ValueError("Content-addressed pixel blob changed")
    if not path.exists():
        atomic_bytes(path, data)
    return "blob:sha256:" + sha


def archive_messages(out, messages):
    saved = copy.deepcopy(messages)
    for message in saved:
        if isinstance(message.get("content"), list):
            for part in message["content"]:
                if part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    if not url.startswith("data:image/png;base64,"):
                        raise ValueError("Only actual inline pixels can be archived")
                    data = base64.b64decode(url.split(",", 1)[1], validate=True)
                    part["image_url"]["url"] = store_pixels(out, data)
    return saved


def hydrate_messages(out, messages):
    restored = copy.deepcopy(messages)
    for message in restored:
        if isinstance(message.get("content"), list):
            for part in message["content"]:
                if part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    if not url.startswith("blob:sha256:"):
                        raise ValueError("Expected archived pixel hash")
                    sha = url.removeprefix("blob:sha256:")
                    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                        raise ValueError("Malformed pixel digest")
                    path = Path(out) / "blobs" / (sha + ".png")
                    data = path.read_bytes()
                    if path.is_symlink() or hashlib.sha256(data).hexdigest() != sha:
                        raise ValueError("Archived pixel blob changed")
                    part["image_url"]["url"] = "data:image/png;base64," + base64.b64encode(data).decode()
    return restored
