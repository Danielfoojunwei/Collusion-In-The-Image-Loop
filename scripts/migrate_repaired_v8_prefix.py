"""Offline exact67-stage v7→v8 import; the undispatched old review09 is archived.

No provider, runtime or ledger-write imports. No actor9 replay or source edits.
"""
import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.migrate_repaired_v7_prefix import (
    MigrationError, canonical, digest, safe, file_digest, read_json, require,
    check_freeze, check_no_pending, immutable_json, COPY_TOP, STAGES,
)

SOURCE = ROOT / "runs/main/triad_repaired_v7_live"
TARGET = ROOT / "runs/main/triad_repaired_v8_live"


def prefix(source, cid, *, allow_fixture=False):
    case = source / "cases" / cid
    stages = [(n, s) for n in range(1, 9) for s in STAGES] + [(9, s) for s in STAGES[:3]]
    previous = "0" * 64; identifiers = []
    all_outcomes = list((case / "rounds").glob("r*/*.outcome.json"))
    require(len(all_outcomes) == 67, "Source must contain exactly67 terminal observations")
    for n, stage in stages:
        row = read_json(case / "rounds" / f"r{n:02d}" / (stage + ".outcome.json"))
        expected = cid + f".r{n:02d}." + stage
        body = {k:v for k,v in row.items() if k != "record_hash"}
        require(row.get("request_id") == expected and row.get("round") == n and row.get("stage") == stage,
                "Source prefix identity differs")
        require(row.get("status") in {"valid", "invalid_model_output"} and row.get("previous_hash") == previous
                and row.get("record_hash") == digest(body), "Source stage chain or terminal status differs")
        observed = safe(case / row["observed_path"])
        require(observed.is_relative_to(case) and file_digest(observed) == row["observed_sha256"], "Source response bytes differ")
        image = (row.get("output") or {}).get("image")
        if image:
            path = safe(case / "memory" / image["path"])
            require(path.is_relative_to(case / "memory") and file_digest(path) == image["sha256"], "Source delivered image differs")
        if stage == "REVIEWER" and not allow_fixture:
            require(row.get("provenance", {}).get("no_OpenRouter_or_API_reviewer_substitute") is True,
                    "Source reviewer lacks actual provenance")
        previous = row["record_hash"]; identifiers.append(expected)
    for suffix in ("dispatch", "observed", "outcome"):
        require(not (case / "rounds/r09" / ("REVIEWER." + suffix + ".json")).exists(), "Source review09 already dispatched")
    require(not any((source / "reviewer_host/r09").glob("*")), "Source reviewer09 already has launch/answer evidence")
    for n in range(10, 13):
        require(not any((case / "rounds" / f"r{n:02d}").rglob("*")), "Source contains later-round activity")
    return previous, identifiers


def inventory(source, cid):
    """Destination→source mapping; discarded active review data stays as unused provenance."""
    files = {}; unused = []
    case_prefix = "cases/" + cid + "/"
    def add(path, dest):
        safe(path)
        if not path.is_file(): return
        files[dest] = {"source":str(path.relative_to(source)), "sha256":file_digest(path), "bytes":path.stat().st_size}
    roots = [source / n for n in COPY_TOP] + [source / case_prefix / n for n in ("rounds", "memory", "reviewer_packets")]
    for root in roots:
        safe(root)
        if not root.exists(): continue
        for path in sorted(root.rglob("*")):
            safe(path)
            if not path.is_file(): continue
            rel = str(path.relative_to(source))
            inactive = rel.startswith(case_prefix + "reviewer_packets/r09/") or rel.startswith(case_prefix + "rounds/r09/REVIEWER.")
            if rel.startswith("reviewer_jobs/"):
                inactive = read_json(path).get("round") == 9
            if inactive:
                dest = "unused_predecessor_review09/" + rel; unused.append(dest)
            else: dest = rel
            add(path, dest)
    for path in sorted(source.glob("*.json")):
        add(path, "predecessor_provenance/v7/" + path.name)
    for path in sorted((source / "analysis").rglob("*")):
        if path.is_file(): add(path, "predecessor_provenance/v7/" + str(path.relative_to(source)))
    add(source / "continuation_import.json", "continuation_ancestry/v7_continuation_import.json")
    return files, sorted(unused)


def migrate_prefix(source_out=SOURCE, target_out=TARGET, *, repository_root=ROOT, apply=False, allow_fixture=False):
    source, target, root = safe(source_out), safe(target_out), safe(repository_root)
    require(source != target and not source.is_relative_to(target) and not target.is_relative_to(source), "Separate source/target required")
    sf, sc, sp = check_freeze(source, root); tf, tc, tp = check_freeze(target, root)
    require(sf["revision"] == sc["revision"] == "triad_repaired_v7" and tf["revision"] == tc["revision"] == "triad_repaired_v8", "Wrong source/target revision")
    origin = sf["evidence_origin"]
    require(origin == tf["evidence_origin"] and (origin == "live" or allow_fixture and origin == "fixture"), "Fixture evidence cannot enter live import")
    require(len(sp["selected_case_ids"]) == 1 and sp["selected_case_ids"] == tp["selected_case_ids"], "Selected case changed")
    cid = sp["selected_case_ids"][0]
    oldcase = next(c for c in sp["cases"] if c["case_id"] == cid)
    newcase = next(c for c in tp["cases"] if c["case_id"] == cid)
    require(oldcase == newcase, "Full case/task/target schedule changed")
    binding = {"source_out":str(source), "source_config_sha256":file_digest(source / "config.json"),
        "source_plan_sha256":file_digest(source / "plan.json"), "source_freeze_sha256":file_digest(source / "freeze.json"),
        "imported_rounds":8, "imported_stages":67, "first_new_round":9, "first_new_stage":"REVIEWER"}
    require(tc.get("continuation", {}).get("enabled") is True
            and all(tc["continuation"].get(k) == v for k,v in binding.items()), "Frozen source continuation binding changed")
    for key in ("models", "routes", "sampling", "reasoning", "completion_max_tokens", "max_actions", "max_images",
                "rounds", "branches", "coalition", "strict_review_penalties"):
        require(sc.get(key) == tc.get(key), "Fixed setting changed: " + key)
    for config, namespace in [(sc,"triad_repaired_v7"),(tc,"triad_repaired_v8")]:
        b = config["budget"]
        require(b["namespace"] == namespace and b["new_allowance"] is False and b["Codex_excluded"] is True
            and b["max_inference_usd"] == 40 and b["max_OpenRouter_cash_usd"] == 50, "Budget scope differs")
    require(sc["budget"]["aggregate_ledger"] == tc["budget"]["aggregate_ledger"], "Original ledger changed")
    ledger = safe(root / sc["budget"]["aggregate_ledger"])
    check_no_pending(ledger,"triad_repaired_v7:"); check_no_pending(ledger,"triad_repaired_v8:")
    head, ids = prefix(source,cid,allow_fixture=allow_fixture)
    require(tp["continuation"]["original_prefix_chain_head"] == head, "New plan chain head differs")
    files, unused = inventory(source,cid)
    ancestor = read_json(source / "continuation_import.json")
    require(ancestor.get("record_hash") == digest({k:v for k,v in ancestor.items()if k!="record_hash"})
            and ancestor.get("target_out") == str(source)
            and ancestor.get("source_out") == sc.get("continuation",{}).get("source_out"), "Unbound v6 ancestor import")
    for rel,h in ancestor["copied_files_sha256"].items():
        if rel.endswith(".png"): require(files.get(rel,{}).get("sha256") == h, "Ancestor PNG not copied exactly")
    meta = target / "cases" / cid / "engine_manifest.json"
    if meta.exists():
        m = read_json(meta)
        require(m.get("revision") == "triad_repaired_v8" and m.get("case") == newcase
                and all(m.get("config",{}).get(k) == v for k,v in tc.items()), "New engine metadata differs")
    record = {"kind":"exact_v7_prefix_continuation_import", "revision":"triad_repaired_v8", **binding,
        "target_out":str(target), "case_id":cid, "evidence_origin":origin, "prefix_chain_head":head,
        "target_config_sha256":file_digest(target / "config.json"), "target_plan_sha256":file_digest(target / "plan.json"),
        "target_freeze_sha256":file_digest(target / "freeze.json"), "source_request_ids":ids,
        "copied_files_sha256":{p:d["sha256"]for p,d in files.items()},
        "copied_files_source_paths":{p:d["source"]for p,d in files.items()},
        "copied_files_bytes":{p:d["bytes"]for p,d in files.items()}, "copied_file_count":len(files),
        "direct_reference_files_sha256":{p:d["sha256"]for p,d in files.items()if p==d["source"] and p.endswith(".png")},
        "ancestor_import":{"path":"continuation_ancestry/v7_continuation_import.json",
            "sha256":file_digest(source / "continuation_import.json"), "source_out":ancestor["source_out"]},
        "unused_predecessor_review09":unused, "imported_observations_relabelled_as_v8":False,
        "new_allowance":False, "hardlinks_or_symlinks_used":False}
    record["record_hash"] = digest(record)
    owned = (target / "continuation_import.pending.json").exists() or (target / "continuation_import.json").exists()
    if owned:
        for name in ("continuation_import.pending.json","continuation_import.json"):
            if (target / name).exists(): require(read_json(target / name) == record, "Existing import binding differs")
    for relative,d in files.items():
        dst = safe(target / relative)
        if dst.exists():
            require(owned or relative.startswith("inherited_task_authors/"), "Unclaimed target prefix data already exists")
            require(dst.is_file() and file_digest(dst) == d["sha256"], "Existing target copy differs")
    scientific = [target / n for n in COPY_TOP] + [target / "cases" / cid / n for n in ("rounds","memory","reviewer_packets")]
    for directory in scientific:
        if directory.exists():
            for p in directory.rglob("*"):
                safe(p)
                if p.is_file(): require(str(p.relative_to(target)) in files, "Unexpected active scientific target data")
    if not apply: return {**record,"preflight_only":True}
    immutable_json(target / "continuation_import.pending.json",record)
    for relative,d in files.items():
        src,dst = safe(source / d["source"]),safe(target / relative)
        dst.parent.mkdir(parents=True,exist_ok=True)
        if not dst.exists():
            with src.open("rb")as incoming,dst.open("xb")as outgoing: shutil.copyfileobj(incoming,outgoing,1024*1024)
        require(file_digest(dst) == d["sha256"] and (src.stat().st_dev,src.stat().st_ino)!=(dst.stat().st_dev,dst.stat().st_ino),
                "Copied bytes differ or are hardlinked")
    require(inventory(source,cid) == (files,unused), "Source changed while copying")
    check_no_pending(ledger,"triad_repaired_v7:");check_no_pending(ledger,"triad_repaired_v8:")
    immutable_json(target / "continuation_import.json",record)
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=SOURCE);parser.add_argument("--target",type=Path,default=TARGET)
    parser.add_argument("--apply",action="store_true");args=parser.parse_args()
    result=migrate_prefix(args.source,args.target,apply=args.apply)
    print(canonical({k:result[k]for k in("kind","imported_stages","first_new_round","first_new_stage","copied_file_count","prefix_chain_head","record_hash")}))
    print(canonical({"applied":args.apply,"paid_calls":0,"runtime_imports":False}))


if __name__=="__main__":main()
