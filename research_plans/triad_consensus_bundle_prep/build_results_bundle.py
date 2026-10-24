"""Package the terminal first triad run; perform no inference or model calls."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "runs/main/triad_consensus_live"
CORE = Path("/workspace/image_collusion_triad_first_run_core.zip")
FINAL = Path("/workspace/image_collusion_triad_first_run_results.zip")
PREP = ROOT / "research_plans/triad_consensus_bundle_prep"
VALIDATION = ROOT / "research_plans/triad_consensus_bundle_validation/FINAL_ARCHIVE.json"
TOKEN = re.compile(rb"sk-or-v1-[A-Za-z0-9_-]{20,}")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(2**20), b""):
            result.update(chunk)
    return result.hexdigest()


def scan_stream(stream, label):
    """Hash and scan bounded chunks; never print a matched credential."""
    result, tail, size = hashlib.sha256(), b"", 0
    for chunk in iter(lambda: stream.read(2**20), b""):
        if TOKEN.search(tail + chunk):
            raise ValueError("Credential pattern found in an export entry: " + label)
        result.update(chunk)
        size += len(chunk)
        tail = chunk[-256:]
    return {"sha256": result.hexdigest(), "bytes": size}


def safe_entry(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not name or "\\" in name:
        raise ValueError("Unsafe archive entry")


def safe_source(relative):
    safe_entry(relative)
    path = ROOT / relative
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError("Symlinked reproduction source")
        if ancestor == ROOT:
            break
    resolved = path.resolve(strict=True)
    resolved.relative_to(ROOT.resolve())
    if not resolved.is_file():
        raise ValueError("Reproduction source is not a regular file")
    return resolved


def ledger_projection(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Ledger integrity check failed")
        result = hashlib.sha256()

        def update(row):
            row = [{"sqlite_blob_hex": value.hex()} if isinstance(value, bytes) else value for value in row]
            data = json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
            result.update(len(data).to_bytes(8, "big"))
            result.update(data)

        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        for row in schema:
            update(row)
        tables = {}
        for kind, name, _, sql in schema:
            if kind != "table":
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            columns = db.execute("PRAGMA table_info(" + quoted + ")").fetchall()
            for row in columns:
                update(row)
            without_rowid = "WITHOUT ROWID" in (sql or "").upper()
            ordering = ",".join('"' + column[1].replace('"', '""') + '"' for column in columns) if without_rowid else "rowid"
            selection = "*" if without_rowid else "rowid,*"
            count = 0
            for row in db.execute("SELECT " + selection + " FROM " + quoted + " ORDER BY " + ordering):
                update(row)
                count += 1
            tables[name] = count
        return {"logical_schema_and_all_rows_sha256": result.hexdigest(), "tables": tables}


def main():
    for path in (ROOT / "src", ROOT, ROOT / "scripts"):
        sys.path.insert(0, str(path))
    from run_triad_format_v5 import MemoryAmendedCoordinator

    if CORE.exists() or FINAL.exists() or VALIDATION.exists():
        raise ValueError("Preserve existing archives and validation; choose a new export explicitly")
    coordinator = MemoryAmendedCoordinator()
    verified = coordinator.verify()
    if (verified["rounds_finished"] != 12 or verified["case_limit"] != 1
            or len(verified["cases"]) != 1 or verified["cases"][0]["next_stage"] is not None):
        raise ValueError("Only the terminal twelve-round first case can be exported")
    if not verified["unselected_cases_started"] is False:
        raise ValueError("An unselected case was started")
    if not (OUT / "analysis/FIRST_RUN_REPORT.md").is_file():
        raise ValueError("Final human report missing")
    evidence = json.loads((OUT / "analysis/FIRST_RUN_EVIDENCE.json").read_text())
    if evidence.get("terminal") is not True:
        raise ValueError("Scientific evidence is still provisional")
    # Final evidence and prompt catalogs expose their own terminal flag.
    prompt_catalog = json.loads((OUT / "analysis/SYSTEM_PROMPTS_BY_STAGE.json").read_text())
    if prompt_catalog.get("terminal") is not True:
        raise ValueError("Prompt catalog is still provisional")
    if "Provisional:" in (OUT / "analysis/FIRST_RUN_REPORT.md").read_text():
        raise ValueError("Human report is still provisional")
    gallery = (OUT / "analysis/FIRST_RUN_GALLERY.html").read_text()
    gallery_data = json.loads(gallery.split('<script id="gallery-data" type="application/json">', 1)[1].split('</script>', 1)[0])
    if gallery_data.get("provisional") is not False or gallery_data.get("rounds_finished") != 12:
        raise ValueError("Gallery is still provisional")

    prep_manifest = json.loads((PREP / "REPRODUCTION_FILES.json").read_text())
    extras = {}
    for section in ("required_additional_files", "optional_inherited_public_packets",
                    "recommended_validation_evidence_files"):
        for item in prep_manifest[section]:
            path = safe_source(item["workspace_relative_path"])
            if file_digest(path) != item["sha256"]:
                raise ValueError("A declared reproduction source changed: " + item["workspace_relative_path"])
            if item["archive_path"] in extras:
                raise ValueError("Duplicate declared reproduction entry")
            extras[item["archive_path"]] = path
    for path in (PREP / "REPRODUCTION_FILES.json", PREP / "generate_first_run_analysis.py", Path(__file__)):
        relative = str(path.relative_to(ROOT))
        path = safe_source(relative)
        entry = "reproduction_workspace/" + relative
        if entry in extras:
            raise ValueError("Duplicate manual reproduction entry")
        extras[entry] = path

    coordinator.report()
    core_result = coordinator.export(CORE)
    snapshot = OUT / "analysis/aggregate_budget_snapshot.sqlite3"
    ledger_check = ledger_projection(snapshot)
    if ledger_projection(ROOT / "runs/main/budget.sqlite3") != ledger_check:
        raise ValueError("The consistent aggregate snapshot differs from the terminal ledger")

    entries = {}
    with zipfile.ZipFile(CORE) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate core archive entries")
        for name in names:
            safe_entry(name)
            with archive.open(name) as stream:
                entries[name] = scan_stream(stream, name)
    for name, path in extras.items():
        safe_entry(name)
        if name in entries:
            raise ValueError("Reproduction addition would duplicate a core entry")
        with path.open("rb") as stream:
            entries[name] = scan_stream(stream, name)

    bundle_record = {"schema_version": 1, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "One terminal case_05 run through twelve consecutive rounds; nine other cases unstarted",
        "source_core_archive_sha256": core_result["sha256"],
        "entry_hashes_exclude_this_manifest_to_avoid_self_reference": True,
        "OpenRouter_credential_pattern_scan_matches": 0,
        "aggregate_snapshot_sha256": file_digest(snapshot),
        "complete_aggregate_logical_fingerprint": ledger_check,
        "entries": entries}
    manifest_bytes = (json.dumps(bundle_record, indent=2, ensure_ascii=False) + "\n").encode()
    shutil.copy2(CORE, FINAL)
    with zipfile.ZipFile(FINAL, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in extras.items():
            archive.write(path, name)
        archive.writestr("BUNDLE_CONTENTS.json", manifest_bytes)
    with zipfile.ZipFile(FINAL) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(entries) | {"BUNDLE_CONTENTS.json"}:
            raise ValueError("Final archive membership differs")
        if archive.testzip() is not None:
            raise ValueError("Final archive CRC validation failed")
        for name, expected in entries.items():
            with archive.open(name) as stream:
                if scan_stream(stream, name) != expected:
                    raise ValueError("Final entry bytes changed: " + name)
        if archive.read("BUNDLE_CONTENTS.json") != manifest_bytes:
            raise ValueError("Bundle manifest changed")

    result = {"status": "verified", "path": str(FINAL), "sha256": file_digest(FINAL),
        "bytes": FINAL.stat().st_size, "entries": len(entries) + 1,
        "additional_reproduction_files": len(extras), "CRC": "passed_all_entries",
        "all_entry_hashes_verified": True, "OpenRouter_credential_pattern_scan_matches": 0,
        "complete_aggregate_snapshot_matches_terminal_ledger": True,
        "bundle_manifest_sha256": digest(manifest_bytes), "budget": coordinator.budget(),
        "rounds_finished": verified["rounds_finished"], "other_cases_started": False,
        "private_researcher_archive": True, "no_model_calls": True}
    VALIDATION.parent.mkdir(parents=True, exist_ok=True)
    with VALIDATION.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
