"""Import the stopped v6 eight-round prefix into a frozen v7 continuation.

Offline only: no runtime imports, network, provider constructor or ledger writes.
The old run stays untouched. Imported records keep their v6 IDs, bytes and
provenance. New v7 configuration applies prospectively from round9.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runs/main/triad_repaired_v6_live"
TARGET = ROOT / "runs/main/triad_repaired_v7_live"
STAGES = ("A_IMAGE_AND_VOTE", "B_IMAGE_AND_VOTE", "C_VOTE", "REVIEWER",
          "POSTVERDICT_A_LOG", "POSTVERDICT_B_LOG", "POSTVERDICT_C_IMAGE", "POSTVERDICT_C_LOG")
COPY_TOP = ("actor_deliveries", "prompt_audit", "provider_evidence", "reviewer_dispatches",
            "reviewer_host", "reviewer_jobs", "reviewer_observations", "stage_cache",
            "inherited_task_authors", "responses", "claims")
PREFIX_ROUNDS = 8
MANIFEST = "continuation_import.json"
CLAIM = "continuation_import.pending.json"


class MigrationError(ValueError):
    """A migration precondition or retained-evidence check failed."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def safe(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise MigrationError("Symlinks are not permitted in migration paths")
    return path


def file_digest(path):
    path = safe(path)
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(safe(path).read_text())


def require(condition, message):
    if not condition:
        raise MigrationError(message)


def check_freeze(out, repository_root):
    freeze = read_json(out / "freeze.json")
    config = read_json(out / "config.json")
    plan = read_json(out / "plan.json")
    require(digest(config) == freeze["config_sha256"], "Frozen config digest mismatch")
    require(digest(plan) == freeze["plan_sha256"], "Frozen plan digest mismatch")
    for relative, expected in freeze["source_files"].items():
        path = safe(repository_root / relative)
        require(path.is_relative_to(repository_root), "Frozen source path escaped repository")
        require(file_digest(path) == expected, "Frozen runtime source changed: " + relative)
    return freeze, config, plan


def check_no_pending(ledger, prefix="triad_repaired_v6:"):
    require(ledger.is_file(), "Original aggregate ledger is required")
    with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True) as db:
        count = db.execute("SELECT COUNT(*) FROM requests WHERE status='pending' AND request_id LIKE ?",
                           (prefix + "%",)).fetchone()[0]
        cap = db.execute("SELECT value FROM settings WHERE key='cap_units'").fetchone()
    require(count == 0, "V6 has a pending paid request; do not migrate")
    require(cap is not None and int(cap[0]) == 40_000_000_000, "Original inference cap is not $40")


def prefix_records(source, case_id, *, allow_fixture=False):
    case = source / "cases" / case_id
    require(case.is_dir(), "Source case directory is missing")
    previous = "0" * 64
    identifiers = []
    for number in range(1, 13):
        folder = case / "rounds" / f"r{number:02d}"
        if number > PREFIX_ROUNDS:
            require(not folder.exists() or not any(folder.rglob("*")), "Source has round9+ data or claims")
            continue
        for stage in STAGES:
            path = folder / (stage + ".outcome.json")
            require(path.is_file(), "Source eight-round prefix is incomplete")
            row = read_json(path)
            expected_id = case_id + f".r{number:02d}." + stage
            require(row.get("request_id") == expected_id, "Source stage identity differs")
            require(row.get("round") == number and row.get("stage") == stage, "Source round/stage differs")
            require(row.get("status") in {"valid", "invalid_model_output"}, "Source stage is unresolved")
            body = {k: v for k, v in row.items() if k != "record_hash"}
            require(row.get("previous_hash") == previous and digest(body) == row.get("record_hash"),
                    "Source immutable stage chain differs")
            observed = safe(case / row["observed_path"])
            require(observed.is_relative_to(case) and file_digest(observed) == row["observed_sha256"],
                    "Source observed response identity differs")
            image = (row.get("output") or {}).get("image")
            if image:
                path = safe(case / "memory" / image["path"])
                require(path.is_relative_to(case / "memory") and file_digest(path) == image["sha256"],
                        "Source delivered raster identity differs")
            if stage == "REVIEWER" and not allow_fixture:
                provenance = row.get("provenance", {})
                require(provenance.get("evidence_origin") == "live"
                        and provenance.get("no_OpenRouter_or_API_reviewer_substitute") is True
                        and provenance.get("reviewer_agent"), "Source lacks actual reviewer provenance")
                host = source / "reviewer_host" / f"r{number:02d}"
                require((host / "first_final.txt").read_bytes() == (host / "host_captured_first_final.txt").read_bytes(),
                        "Source reviewer first-final capture differs")
            previous = row["record_hash"]
            identifiers.append(expected_id)
    require(len(list((case / "rounds").glob("r*/*.outcome.json"))) == 64,
            "Source does not have exactly64 terminal prefix records")
    return previous, identifiers


def inventory(source, case_id):
    """Only scientific evidence; never import another run's mutable coordinator state."""
    roots = [source / name for name in COPY_TOP]
    case = source / "cases" / case_id
    roots.extend(case / name for name in ("rounds", "memory", "reviewer_packets"))
    files = {}
    future = re.compile(re.escape(case_id).encode() + rb"\.r(?:09|1[0-2])\.")
    for root in roots:
        safe(root)
        if not root.exists():
            continue
        require(root.is_dir(), "Scientific copy root is not a directory")
        for path in sorted(root.rglob("*")):
            safe(path)
            if path.is_dir():
                continue
            require(path.is_file(), "Scientific evidence contains a nonregular file")
            if path.suffix == ".json":
                require(future.search(path.read_bytes()) is None, "Source evidence contains a round9+ dispatch/call")
            relative = str(path.relative_to(source))
            files[relative] = {"sha256": file_digest(path), "bytes": path.stat().st_size}
    require(files, "Scientific prefix inventory is empty")
    return files


def immutable_json(path, value):
    raw = (canonical(value) + "\n").encode()
    safe(path)
    if path.exists():
        require(path.read_bytes() == raw, "Existing immutable migration record differs")
        return
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def verify_copies(source, target, files):
    for relative, metadata in files.items():
        src, dst = safe(source / relative), safe(target / relative)
        require(dst.is_file() and file_digest(dst) == metadata["sha256"]
                and dst.stat().st_size == metadata["bytes"], "Copied scientific evidence differs: " + relative)
        require((src.stat().st_dev, src.stat().st_ino) != (dst.stat().st_dev, dst.stat().st_ino),
                "Migration evidence cannot be hardlinked to the source")


def migrate_prefix(source_out=SOURCE, target_out=TARGET, *, repository_root=ROOT, apply=False, allow_fixture=False):
    """Validate and optionally import the exact stopped prefix; identical repeats are safe."""
    source, target, repository_root = safe(source_out), safe(target_out), safe(repository_root)
    require(source != target and not target.is_relative_to(source) and not source.is_relative_to(target),
            "Source and target must be separate run directories")
    sf, sc, sp = check_freeze(source, repository_root)
    tf, tc, tp = check_freeze(target, repository_root)
    require(sf.get("revision") == sc.get("revision") == "triad_repaired_v6", "Source revision is not v6")
    require(tf.get("revision") == tc.get("revision") == "triad_repaired_v7", "Target revision is not v7")
    origin = sf.get("evidence_origin")
    require(origin == tf.get("evidence_origin") and (origin == "live" or allow_fixture and origin == "fixture"),
            "Live import cannot use fixture evidence")
    require(len(sp.get("selected_case_ids", [])) == 1 and sp["selected_case_ids"] == tp.get("selected_case_ids"),
            "Continuation must keep the same selected case ID")
    cid = sp["selected_case_ids"][0]
    source_case = next(c for c in sp["cases"] if c["case_id"] == cid)
    target_case = next(c for c in tp["cases"] if c["case_id"] == cid)
    require(source_case == target_case, "Continuation task, assignment schedule or full case changed")
    binding = {"source_out": str(source), "source_config_sha256": file_digest(source / "config.json"),
               "source_plan_sha256": file_digest(source / "plan.json"),
               "source_freeze_sha256": file_digest(source / "freeze.json"),
               "imported_rounds": 8, "first_new_round": 9}
    continuation = tc.get("continuation", {})
    require(continuation.get("enabled") is True and all(continuation.get(k) == v for k, v in binding.items()),
            "Frozen continuation source binding differs")
    for key in ("models", "routes", "sampling", "reasoning", "completion_max_tokens", "max_actions", "max_images",
                "coalition", "ballot_contract", "vote_interface", "vote_format_attempts", "target_design", "rounds", "branches",
                "strict_review_penalties", "reflection_contract", "context", "text_contract", "routing", "stopping", "transport"):
        require(sc.get(key) == tc.get(key), "Continuation changes a fixed nonpressure setting: " + key)
    for config, namespace in [(sc, "triad_repaired_v6"), (tc, "triad_repaired_v7")]:
        budget = config.get("budget", {})
        require(budget.get("namespace") == namespace and budget.get("new_allowance") is False
                and budget.get("Codex_excluded") is True and budget.get("max_inference_usd") == 40
                and budget.get("max_OpenRouter_cash_usd") == 50, "Continuation budget scope differs")
    require(sc["budget"]["aggregate_ledger"] == tc["budget"]["aggregate_ledger"], "Continuation changed the ledger")
    ledger = safe(repository_root / sc["budget"]["aggregate_ledger"])
    check_no_pending(ledger)
    check_no_pending(ledger, "triad_repaired_v7:")
    chain, request_ids = prefix_records(source, cid, allow_fixture=allow_fixture)
    files = inventory(source, cid)
    target_case_dir = target / "cases" / cid
    engine_manifest = target_case_dir / "engine_manifest.json"
    if engine_manifest.exists():
        meta = read_json(engine_manifest)
        require(meta.get("revision") == "triad_repaired_v7" and meta.get("case") == target_case
                and all(meta.get("config", {}).get(k) == v for k, v in tc.items()),
                "Pre-existing new engine manifest differs")
    record = {"kind": "exact_v6_prefix_continuation_import", "revision": "triad_repaired_v7",
              **binding, "target_out": str(target), "evidence_origin": origin, "case_id": cid,
              "imported_stages": 64, "prefix_chain_head": chain,
              "target_config_sha256": file_digest(target / "config.json"),
              "target_plan_sha256": file_digest(target / "plan.json"),
              "target_freeze_sha256": file_digest(target / "freeze.json"),
              "copied_files_sha256": {p: d["sha256"] for p, d in files.items()},
              "copied_files_bytes": {p: d["bytes"] for p, d in files.items()},
              "copied_file_count": len(files), "source_request_ids": request_ids, "new_allowance": False,
              "imported_observations_relabelled_as_v7": False, "hardlinks_or_symlinks_used": False}
    record["record_hash"] = digest(record)
    owns_existing = (target / CLAIM).exists() or (target / MANIFEST).exists()
    if owns_existing:
        for name in (CLAIM, MANIFEST):
            if (target / name).exists():
                require(read_json(target / name) == record, "Existing migration binding differs")
    else:
        for relative in files:
            # These exact author records are written by offline v7 initialization.
            if relative.startswith("inherited_task_authors/"):
                continue
            require(not (target / relative).exists(), "Target already contains scientific prefix data without an import claim")
    roots = [target / name for name in COPY_TOP] + [target_case_dir / name for name in ("rounds", "memory", "reviewer_packets")]
    for root in roots:
        safe(root)
        if root.exists():
            require(root.is_dir(), "Target scientific evidence root is not a directory")
            for path in root.rglob("*"):
                safe(path)
                if path.is_file():
                    require(str(path.relative_to(target)) in files, "Target contains unrecognized or round9+ scientific data")
    for relative, metadata in files.items():
        path = safe(target / relative)
        if path.exists():
            require(path.is_file() and file_digest(path) == metadata["sha256"], "Pre-existing target evidence differs")
    if not apply:
        return {**record, "preflight_only": True}
    immutable_json(target / CLAIM, record)
    for relative in files:
        src, dst = safe(source / relative), safe(target / relative)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            with src.open("rb") as incoming, dst.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                outgoing.flush()
                os.fsync(outgoing.fileno())
    require(inventory(source, cid) == files, "Source changed during migration; do not continue")
    check_no_pending(ledger)
    check_no_pending(ledger, "triad_repaired_v7:")
    verify_copies(source, target, files)
    immutable_json(target / MANIFEST, record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--target", type=Path, default=TARGET)
    parser.add_argument("--apply", action="store_true", help="Copy the validated prefix; default is a read-only preflight")
    args = parser.parse_args()
    result = migrate_prefix(args.source, args.target, apply=args.apply)
    print(canonical({k: result[k] for k in ("kind", "case_id", "imported_rounds", "imported_stages",
                                          "first_new_round", "prefix_chain_head", "copied_file_count", "record_hash")}))
    print(canonical({"applied": args.apply, "paid_calls_made": 0, "runtime_imports": False}))


if __name__ == "__main__":
    main()
