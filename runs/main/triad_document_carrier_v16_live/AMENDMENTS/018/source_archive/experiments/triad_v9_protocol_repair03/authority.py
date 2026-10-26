"""Read-only exact-row authority; an unknown charge never becomes a receipt."""
from copy import deepcopy
from pathlib import Path
import re
import sqlite3

from visual_collusion.budget import BudgetError, canonical, request_digest
from experiments.triad_emergent_v9.coordinator import file_sha, read_json
from experiments.triad_emergent_v9.backend import sha

REPAIR = "triad_v9_protocol_repair03"
LOSS_REASON = "Paid callback failed or lacked a conclusive matching receipt; no retry"


def raw_rows(path):
    path = Path(path).absolute()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return {r["request_id"]: dict(r) for r in db.execute("SELECT * FROM requests")}


def eligible(row):
    return (isinstance(row, dict) and row.get("status") == "uncertain"
        and row.get("kind") == "chat" and row.get("model") == "z-ai/glm-5.3-flash"
        and re.fullmatch(r"triad_emergent_v9:episode\.r[0-9]{2}\.A_IMAGE_AND_VOTE\.visual1", row.get("request_id", "")) is not None
        and row.get("reason") == LOSS_REASON
        and type(row.get("reserved_units")) is int and row["reserved_units"] > 0
        and all(row.get(k) is None for k in ("actual_units", "response_json", "receipt_json", "result_hash", "finished")))


class Authority:
    def __init__(self, path, expected_sha256):
        self.path, self.expected_sha256 = Path(path).absolute(), expected_sha256
        self.record = self.read()

    def read(self):
        if any(p.is_symlink() for p in (self.path, *self.path.parents)) or file_sha(self.path) != self.expected_sha256:
            raise BudgetError("Lost-slot declaration changed")
        record = read_json(self.path)
        row = record.get("row")
        if (record.get("repair") != REPAIR or not eligible(row)
                or record.get("row_sha256") != sha(canonical(row).encode())
                or record.get("slot") != 1 or record.get("no_retry") is not True
                or record.get("unknown_charge_retained") is not True):
            raise BudgetError("An exact response-less first-slot declaration is required")
        out = Path(record["out"]).absolute()
        for relative, expected in record["bound_files"].items():
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise BudgetError("Lost-slot evidence must use bounded relative paths")
            path = out / relative
            if not path.is_relative_to(out) or any(p.is_symlink() for p in (path, *path.parents)) or file_sha(path) != expected:
                raise BudgetError("Lost-slot request, dispatch or host evidence changed")
        if Path(record["transport_archive"]).is_absolute() or ".." in Path(record["transport_archive"]).parts:
            raise BudgetError("Lost-slot archive path escapes the run")
        archive = read_json(out / record["transport_archive"])
        mapping = archive["mapping"]
        if (archive.get("request_id") != row["request_id"].split(":", 1)[1]
                or archive.get("endpoint") != "/chat/completions"
                or mapping.get("wire_body_sha256") != record.get("wire_body_sha256")
                or request_digest({"endpoint": "/chat/completions", "wire_body_sha256": mapping["wire_body_sha256"]}) != row["request_hash"]):
            raise BudgetError("Lost-slot declaration does not bind its exact wire intent")
        return record

    def matches(self, row):
        record = self.read()
        return dict(row) == record["row"]

    def event(self):
        record = self.read()
        row = record["row"]
        return {"record_type": "transport_slot_loss", "request_id": row["request_id"],
            "request_hash": row["request_hash"], "reservation_status": "uncertain",
            "reserved_usd": row["reserved_units"] / 1e9, "actual_cost_usd": None,
            "response_observed": False, "operation_executed_by_harness": False,
            "original_slot_consumed": 1, "no_retry": True, "unknown_charge_retained": True,
            "declaration_sha256": self.expected_sha256}
