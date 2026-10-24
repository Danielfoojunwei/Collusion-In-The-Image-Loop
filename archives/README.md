# Lossless historical research archive

`runs/runs.tar.zst.part-*` are ordered pieces of one Zstandard-compressed tar
stream containing the original project `runs/` tree. Each part is at most48 MiB,
below GitHub's100 MiB per-file limit. `INDEX.json` records order, byte sizes and
SHA-256 hashes; `SHA256SUMS` provides standard command-line checksum verification.

The archive preserves the shared aggregate budget ledger, all historical run
evidence, generated images, raw requests/responses, failures, analysis, decks and
the current V16 stopped run. It excludes bytecode/cache files and environment
secrets. No records were deleted to improve reported outcomes.

From the repository root, after installing `requirements-handoff.txt`:

```bash
python scripts/restore_research_archive.py
```

The helper verifies all parts, restores only manifest-listed files under `runs/`,
checks every restored SHA-256, and refuses to replace differing existing files.
It leaves the current V16 stop marker unchanged. It does not import experiment
modules, use keys or make API calls. `--verify-only` validates the full archive
without extracting it. After restoration, `--verify-restored` checks all included
original source files and run files against the transfer manifest; the original
README, gitignore and V4 AGENTS are checked through their preserved `legacy/` copies.

An equivalent bare extraction, into an empty destination, is:

```bash
cat archives/runs/runs.tar.zst.part-* | zstd --long=29 -d | tar -xf -
```

Prefer the Python helper for its conflict and file-integrity checks. Reserve
about15 GB for the expanded research records, beyond the clone itself. Frozen
records contain absolute `/workspace/visual_collusion_stress_v4` paths; follow
the [main handoff](../CLAUDE_CODE_HANDOFF.md) before trying to continue the study.
