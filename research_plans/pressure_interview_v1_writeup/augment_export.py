"""Add allowlisted reproduction sources to an existing verified private run ZIP.

This does not call the experimental runtime, query credentials, or change study
records. Run only after the operator confirms terminal execution and the core
export has been produced. The original archive is retained unchanged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
REVISION = "pressure_interview_v1"
STUDY = ROOT / "runs/main/pressure_interview_live"

# These imported legacy modules are required even though their old paid studies
# are not resumed. The set is explicit, including transitive local imports.
RUNTIME_SCRIPTS = (
    "continue_repaired_execution.py", "lossless_wire_codec.py",
    "repair_image_route.py", "run_authorized_key.py",
    "run_continue_independent.py", "run_lossless_webp_v5.py",
)
DOCS = (
    "ANALYSIS_PLAN.md", "CODEX_REVIEWER.md", "DEVILS_ADVOCATE.md",
    "HACKATHON_ALIGNMENT.md", "MODELS.md", "PAPER_BLUEPRINT.md",
    "PROTOCOL.md", "VERIFICATION.md",
)
NEW_METADATA = (
    "config.json", "PLAN.md", "EXECUTION_PLAN.json", "WORKFLOW_GATE.json",
    "LIVE_MANIFEST.json", "LIVE_VALIDATION.json", "LIVE_IMPLEMENTATION_MANIFEST.json",
)
VALIDATION = (
    "research_plans/pressure_interview_original_freeze_check.json",
    "research_plans/pressure_interview_live_workflow_validation/tested_bindings.json",
    "research_plans/pressure_interview_live_workflow_validation/result.json",
    "research_plans/pressure_interview_live_workflow_validation/tests.log",
    "research_plans/pressure_interview_live_workflow_validation/interrupted_attempt.json",
)
MANUSCRIPT_FILES = (
    "analysis/PAPER_DRAFT.md", "analysis/PAPER_DRAFT.pdf",
    "analysis/manuscript_source_manifest.json",
)
ACTIVE_AMENDMENT_NOTES = (
    "execution_notes/png_xmp_v2_amendment.json",
    "execution_notes/invalid_backend_analysis_v3.json",
)
RESEARCHER_EVIDENCE = (
    "FIRST_LIVE_ATTEMPT.json", "FIRST_LIVE_ATTEMPT.md",
    "FIRST_CASE_TERMINAL_AFTER_RESUME.json", "FIRST_CASE_TERMINAL_AFTER_RESUME.md",
    "TERMINAL_EVIDENCE_INVENTORY.json", "TERMINAL_EVIDENCE_INVENTORY.md",
)


def digest_file(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Required evidence must be a regular nonsymlink file")
    return json.loads(path.read_text())


def checked_file(root, relative, expected=None):
    root, relative = Path(root).resolve(), Path(relative)
    if relative.is_absolute() or ".." in relative.parts or any(part.startswith(".") for part in relative.parts):
        raise ValueError("Unsafe allowlisted source path")
    candidate = root / relative
    if any(parent.is_symlink() for parent in [candidate, *candidate.parents] if parent != root.parent):
        raise ValueError("Reproduction sources cannot follow symlinks")
    if not candidate.is_file():
        raise ValueError("Allowlisted reproduction source is missing: " + str(relative))
    if expected is not None and digest_file(candidate) != expected:
        raise ValueError("Frozen reproduction source changed: " + str(relative))
    return candidate


def additions(study=STUDY):
    study = Path(study).resolve()
    if study != STUDY.resolve():
        raise ValueError("Only the newest study is in this exporter scope")
    report = read_json(study / "analysis/report.json")
    freeze = read_json(study / "freeze.json")
    if (report.get("evidence_origin") != "live"
            or report.get("revision") != REVISION
            or report.get("verification", {}).get("status") != "verified"
            or freeze.get("evidence_origin") != "live"):
        raise ValueError("A verified newest live-study report is required")
    if report.get("run_status") not in {"complete", "complete_with_invalids", "halted"}:
        raise ValueError("Run is not in a documented terminal/blocked state")
    manuscript_manifest = study / "analysis/manuscript_source_manifest.json"
    if manuscript_manifest.is_file():
        manuscript = read_json(manuscript_manifest)
        for name, expected in manuscript.get("source_hashes", {}).items():
            checked_file(study, name, expected)
        for name, key in (("analysis/PAPER_DRAFT.md", "markdown_sha256"), ("analysis/PAPER_DRAFT.pdf", "pdf_sha256")):
            if key not in manuscript:
                raise ValueError("Manuscript lacks its saved output identity")
            checked_file(study, name, manuscript[key])
    original = read_json(ROOT / "runs/main/freeze.json")
    files = {}

    def add(path, name):
        path = Path(path)
        if name in files and files[name] != path:
            raise ValueError("Conflicting reproduction path")
        files[name] = path

    for name, expected in original["source_files"].items():
        path = checked_file(ROOT / "src/visual_collusion", name, expected)
        add(path, "reproduction_workspace/src/visual_collusion/" + name)
    for name, expected in freeze["source_files"].items():
        path = checked_file(ROOT / "experiments" / REVISION, name, expected)
        add(path, "reproduction_workspace/experiments/" + REVISION + "/" + name)
    # Source identities in these named amendment manifests extend the explicit
    # reproduction allowlist. They never alter the original frozen core files.
    for name in ACTIVE_AMENDMENT_NOTES:
        note = study / name
        if not note.is_file():
            continue
        amendment = read_json(note)
        add(checked_file(study, name), name)
        for source_name, expected in amendment.get("source_files", {}).items():
            relative = Path(source_name)
            if not relative.parts or relative.parts[0] != "scripts" or relative.suffix != ".py":
                raise ValueError("Amendment source is outside the named Python-script scope")
            add(checked_file(ROOT, relative, expected), "reproduction_workspace/" + source_name)
        validation = amendment.get("validation")
        if isinstance(validation, dict) and "path" in validation:
            relative = Path(validation["path"])
            if not relative.parts or relative.parts[0] != "research_plans" or relative.suffix != ".json":
                raise ValueError("Amendment validation is outside its recorded JSON scope")
            validation_source = checked_file(ROOT, relative, validation.get("sha256"))
            add(validation_source, "reproduction_workspace/" + str(relative))
            validated = read_json(validation_source)
            test_bindings = validated.get("test_source_sha256", {})
            if isinstance(test_bindings, str) and amendment.get("version") == "invalid_backend_analysis_v3":
                test_bindings = {"tests/test_pressure_invalid_analysis_v3.py": test_bindings}
            if not isinstance(test_bindings, dict):
                raise ValueError("Named amendment test-source binding has an unsupported schema")
            for test_name, expected in test_bindings.items():
                test_relative = Path(test_name)
                if not test_relative.parts or test_relative.parts[0] != "tests" or test_relative.suffix != ".py":
                    raise ValueError("Amendment test source is outside the Python-test scope")
                add(checked_file(ROOT, test_relative, expected), "reproduction_workspace/" + test_name)
    for name in RUNTIME_SCRIPTS:
        add(checked_file(ROOT / "scripts", name), "reproduction_workspace/scripts/" + name)
    # Closed test-source directories contain the offline workflow and imported
    # fixture helpers. Only Python test files enter this source allowlist.
    for directory in (Path("tests"), Path("experiments") / REVISION / "tests"):
        for source in sorted((ROOT / directory).glob("test*.py")):
            relative = directory / source.name
            add(checked_file(ROOT, relative), "reproduction_workspace/" + str(relative))
    for name in DOCS:
        add(checked_file(ROOT / "docs", name), "reproduction_workspace/docs/" + name)
    for name in ("pyproject.toml", "requirements.txt", "README.md", "AGENTS.md", "CODEX_CLOUD_HANDOFF.md"):
        add(checked_file(ROOT, name), "reproduction_workspace/" + name)
    add(checked_file(ROOT, "configs/canonical.json"), "reproduction_workspace/configs/canonical.json")
    add(checked_file(ROOT, "runs/main/freeze.json"), "reproduction_metadata/original_source_freeze.json")
    add(checked_file(ROOT, "runs/main/execution_notes/official_template_read.json"),
        "reproduction_metadata/official_template_read.json")
    for name in NEW_METADATA:
        path = ROOT / "experiments" / REVISION / name
        if path.is_file():
            add(checked_file(path.parent, path.name), "reproduction_workspace/experiments/" + REVISION + "/" + name)
    for name in VALIDATION:
        path = ROOT / name
        if path.is_file():
            add(checked_file(ROOT, name), "reproduction_workspace/" + name)
    for name in RESEARCHER_EVIDENCE:
        relative = "research_plans/pressure_interview_v1_evidence/" + name
        if (ROOT / relative).is_file():
            add(checked_file(ROOT, relative), "reproduction_workspace/" + relative)
    for name in ("render_paper.py", "augment_export.py", "test_augment_export.py", "test_render_monitoring.py", "README.md", "REQUIREMENTS.json", "PAPER_SCAFFOLD.md"):
        add(checked_file(HERE, name), "reproduction_workspace/research_plans/pressure_interview_v1_writeup/" + name)
    for name in ("PREPARATION_VALIDATION.json", "FINAL_ARTIFACT_CHECKS.json"):
        if (HERE / name).is_file():
            add(checked_file(HERE, name), "reproduction_workspace/research_plans/pressure_interview_v1_writeup/" + name)
    for name in MANUSCRIPT_FILES:
        path = study / name
        if path.is_file():
            add(checked_file(study, name), name)
    return files, report, freeze


README = """# Private researcher archive and reproduction sources

The archive root is the exported newest study, pressure_interview_v1. Its
`implementation/` directory is the core export's frozen revised source. The
additional `reproduction_workspace/` directory includes the original Python
package and the exact imported infrastructure modules needed to interpret and
reproduce the revised experiment. `reproduction_sources_manifest.json` binds
every added file by SHA-256; the input core archive is retained separately.

Scientific outcomes from older runs are not pooled into the newest results.
The consistent `analysis/aggregate_budget_snapshot.sqlite3` includes old and new
costs plus unknown reservations; the newest namespace is already a subset.
Never add that subset to aggregate spending again. Codex is excluded from the
user's OpenRouter cash ceiling. No credential, process environment dump, or
provider runtime secret is included by this allowlisted augmentation.

To restore a local review layout, use reproduction_workspace as the project
root, place the exported study files under runs/main/pressure_interview_live,
and place the aggregate snapshot at runs/main/budget.sqlite3. The source package
requires Python >=3.11 and Pillow==12.3.0. Preserve originals and work from copies.
Some archival bindings contain original absolute paths; relocation is not a
claim that every runtime verifier is path-independent. Review those bindings
against their saved hashes, or restore the original /workspace project layout.
Do not resume paid work merely to inspect this completed or blocked archive.

The report and paper are unsubmitted research drafts. Unknown author identity,
direct-source literature review, limited sample size and reviewer provenance
remain stated limitations. Instruction-scoped Codex contexts are not an OS
sandbox. A private researcher archive is not automatically a public release.
"""


def augment(input_archive, output_archive, *, study=STUDY):
    source, destination = Path(input_archive).resolve(), Path(output_archive).resolve()
    study = Path(study).resolve()
    if (source == destination or destination == study or study in destination.parents
            or source.is_symlink() or not source.is_file()):
        raise ValueError("Use distinct regular input/output archives outside the study")
    extra, report, freeze = additions(study)
    input_sha = digest_file(source)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "revision": REVISION, "evidence_origin": "live",
        "run_status": report["run_status"], "input_core_archive_sha256": input_sha,
        "operator_terminal_confirmation_required": True,
        "newest_freeze_sha256": digest_file(study / "freeze.json"),
        "scope": "Allowlisted source/dependency/manuscript augmentation; no new experimental observation or allowance",
        "private_researcher_archive": True, "external_publication_performed": False,
        "files": {name: digest_file(path) for name, path in sorted(extra.items())},
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=destination.parent, prefix=".reproduction-export-", suffix=".zip")
    os.close(handle)
    try:
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(temp_name, "w", zipfile.ZIP_DEFLATED) as result:
            names = original.namelist()
            if len(names) != len(set(names)):
                raise ValueError("Core archive contains duplicate member names")
            for name in ("config.json", "plan.json", "freeze.json", "analysis/report.json"):
                if hashlib.sha256(original.read(name)).hexdigest() != digest_file(study / name):
                    raise ValueError("Core archive does not match the current frozen study/report")
            for info in original.infolist():
                with original.open(info) as old_stream, result.open(info, "w") as new_stream:
                    shutil.copyfileobj(old_stream, new_stream, length=1024 * 1024)
            for name, path in sorted(extra.items()):
                if name in names:
                    if hashlib.sha256(original.read(name)).hexdigest() != manifest["files"][name]:
                        raise ValueError("Existing member differs from the saved manuscript/source")
                else:
                    result.write(path, name)
            result.writestr("REPRODUCTION_README.md", README)
            result.writestr("reproduction_sources_manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        os.replace(temp_name, destination)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return {"status": "augmented_private_export", "path": str(destination),
            "sha256": digest_file(destination), "added_file_count": len(extra),
            "original_archive_retained": str(source), "new_paid_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-archive", type=Path, required=True)
    parser.add_argument("--output-archive", type=Path, required=True)
    parser.add_argument("--terminal-confirmed", action="store_true")
    args = parser.parse_args()
    if not args.terminal_confirmed:
        parser.error("Root operator terminal confirmation is required before final export augmentation")
    print(json.dumps(augment(args.input_archive, args.output_archive), indent=2))


if __name__ == "__main__":
    main()
