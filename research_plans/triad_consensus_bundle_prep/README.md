# Reproducing the first triad run offline

This is a preparation manifest for the final private researcher archive. It was written while the first run was live. Capture the final run tree and a consistent aggregate SQLite snapshot only after the run is terminal. This document does not certify completion or final findings.

The inherited exporter already includes the current run tree and the 64 frozen source files under `reproduction_workspace/`. `REPRODUCTION_FILES.json` lists the bounded additions: the five v3/v4/v5 runtime sidecars, their five test modules, five current workflow test modules, two imported fixture helpers, configurations, dependency declarations, the gallery renderer, and the inherited task-author data needed by the loader. Validation records and ten original public task-author packets are listed separately.

## Restore the original paths

Use a separate reproduction environment with a real directory at `/workspace/visual_collusion_stress_v4`. Several immutable requests, amendment manifests and image descriptors contain absolute paths. Symlink aliases are rejected by the evidence checks. A bind mount at the original path is suitable. Rewriting historical paths would change their hashes.

1. Restore `reproduction_workspace/` to `/workspace/visual_collusion_stress_v4/`, retaining the exact relative paths.
2. Restore the scientific run tree from the archive root to `/workspace/visual_collusion_stress_v4/runs/main/triad_consensus_live/`. Archive metadata and `reproduction_workspace/` are separate from that scientific tree.
3. Copy the final consistent `analysis/aggregate_budget_snapshot.sqlite3` from that run tree to `/workspace/visual_collusion_stress_v4/runs/main/budget.sqlite3` in the reproduction environment. Keep all original aggregate rows and settings. Old spending and unresolved reservations remain part of the same capped history; a new empty ledger or a namespace-only subset will not reproduce the checks.
4. Retain the small inherited task dependency at `runs/main/pressure_interview_live/plan.json` plus the twenty original task-author `job.json`/`result.json` files named in the manifest. These are inherited task data, not current triad results. The constructor reloads them from their original paths even though the new run also contains byte-identical copies under `inherited_task_authors/`. Optional original public packets complete their packet-hash provenance.

## Environment and offline commands

Use Python 3.11 or newer and `Pillow==12.3.0` with WebP support. `pyproject.toml` declares the package and `setuptools>=68` build backend; `requirements.txt` contains the runtime pin. Supply installed dependencies or an offline wheelhouse. Package wheels, the operating system and a complete Python environment are not bundled. Exact re-encoding can depend on the Pillow/libwebp build; the retained source PNG and wire artifacts remain authoritative.

From the restored workspace, with that Python environment active:

```bash
cd /workspace/visual_collusion_stress_v4
PYTHONPATH="$PWD/src:$PWD" python scripts/run_triad_format_v5.py verify
PYTHONPATH="$PWD/src:$PWD" python -m unittest discover -s experiments/triad_consensus_v2/tests -v
PYTHONPATH="$PWD/src:$PWD" python -m unittest tests.test_triad_gate_v3 tests.test_triad_response_format_v4 tests.test_triad_format_v4_runner tests.test_triad_response_format_v5 tests.test_triad_format_v5_runner -v
```

These commands require no inference credential and do not issue provider requests. Software tests use temporary ledgers and scripted provider fixtures; the retained-observation tests read the restored aggregate and run records. One gate-establishment test is explicitly skipped after round two has begun because its original precondition no longer applies. Final evidence verification uses the existing declared amendments.

The canonical configuration digest is `0b14219f2b0a24d24d808c912a03cb6a1009a61876e55b1f686f8cc75219892f`. The frozen run digest is `b78bc1c3d7bba9b459ac92c20b4b07d75f5b06a3923ac10f892bd8d19c61943f`. The manifest records byte hashes separately from canonical JSON digests.

The gallery is portable inside the run tree because it links the exact PNGs by relative path. To regenerate its HTML on a separate working copy:

```bash
PYTHONPATH="$PWD/src:$PWD" python scripts/render_triad_first_run_gallery.py --out runs/main/triad_consensus_live
```

This writes `analysis/FIRST_RUN_GALLERY.html`; it preserves the original images. Existing gallery HTML is already part of the exported run tree.

## Scope of the additions

The five external runtime files are `run_triad_gate_v3.py`, `triad_response_format_v4.py`, `run_triad_format_v4.py`, `triad_response_format_v5.py`, and `run_triad_format_v5.py`. The renderer is a sixth additional script, outside the runtime count. Their sources are required because the original 64-file freeze deliberately remains unchanged across declared amendments.

The two imported fixture helpers are `experiments/pressure_interview_v1/tests/test_live_provider.py` and `tests/test_provider_budget.py`. The historical pressure configuration is needed by those adapter fixtures. The current tests do not require the earlier study's scientific outcomes, image tree or other test suites.

The file manifest is not an export and contains no live-run snapshot. Include this README and `REPRODUCTION_FILES.json` in the final archive. The manifest cannot carry its own hash without a circular reference; the final archive builder should hash it after writing. Final reports, completed-run checks and the final database snapshot must be captured separately by the root host.
