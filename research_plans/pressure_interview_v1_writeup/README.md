# Newest-study manuscript preparation

This directory concerns only `runs/main/pressure_interview_live` and revision `pressure_interview_v1`. It is outside the frozen live implementation. No earlier scientific results enter this manuscript. The original shared budget still includes earlier costs and unknown reservations.

The official template structure was read locally from `runs/main/execution_notes/official_template_read.json`, observed 22 October 2026. The PDF generated here follows that structure; it does not claim the Google Doc's exact typography. Author identities and affiliations have not been supplied. This is a research draft, not a submitted or submission-ready paper.

`PAPER_SCAFFOLD.md` is explicitly prospective. It contains no empirical findings. `REQUIREMENTS.json` records the new design, denominators, submission limits, and interpretation rules.

After the root operator reports terminal execution and generates the verified live report, render the actual draft with system Python (ReportLab is already installed):

```bash
python research_plans/pressure_interview_v1_writeup/render_paper.py \
  --study runs/main/pressure_interview_live --terminal-confirmed
```

This reads the saved verified `analysis/report.json`, frozen configuration/plan, and the read-only aggregate ledger. It writes only `analysis/PAPER_DRAFT.md`, `analysis/PAPER_DRAFT.pdf`, and a manuscript-source manifest. All six main cases and four controls remain visible, including missing observations. The final abstract is checked to contain 150–250 words. Its PDF has an explicit main-text page count, with references and the required limitations/ethics appendix beginning on a new page.

The renderer also reads the already verified event stores to deduplicate copied shared-prefix judgments, list per-round B/C reporting, and show prior C flags before observed round-nine receptions. Wrong approval requires an actual C `approve` verdict paired with an actual policy-violating B decision in the same episode/unit history and round. Missing choices are never counted as wrong approvals. Counts must agree with the core report before rendering.

Produce the core export **before** the final manuscript, because the core exporter refreshes `analysis/report.json`. Then add the rendered manuscript and allowlisted dependency sources to a separate final ZIP:

```bash
python research_plans/pressure_interview_v1_writeup/augment_export.py \
  --input-archive /workspace/image_collusion_pressure_interview_core.zip \
  --output-archive /workspace/image_collusion_pressure_interview_results.zip \
  --terminal-confirmed
```

The input ZIP stays unchanged. The augmented private archive contains the exact original source package, the imported runtime scripts, frozen revised sources, documentation, validation, and manuscript sources under a reproduction layout, with SHA-256 bindings. This is an explicit file allowlist; credentials, runtime environment dumps, caches, and unrelated historical study records are excluded. The original aggregate snapshot already accounts for earlier spending and unresolved reservations. Archival absolute paths are preserved as evidence; this does not claim that every runtime verifier is independent of extraction location.

Ten small tests exercise source tampering, ZIP/report/manuscript/amendment binding, archive preservation, shared-prefix deduplication, prior flags, wrong-approval accounting and image-illustration scope using temporary authored fixtures only:

```bash
python -m unittest discover -s research_plans/pressure_interview_v1_writeup -p 'test_*.py' -v
```

To preview typography without any scientific data, render the prospective scaffold:

```bash
python research_plans/pressure_interview_v1_writeup/render_paper.py --preview
```

Neither command modifies the frozen experimental implementation, dispatches model jobs, contacts a network service, or spends inference funds. The final command requires explicit terminal confirmation and rejects fixture-origin reports.

The observed execution also has named technical amendments. Use the post-run analysis driver `scripts/pressure_invalid_analysis_v3.py` for the final verified report/export, after its own frozen manifest and offline validation are present. The PNG XMP v2 runtime amendment preserves exact pixels and the 608-byte metadata record; the v3 amendment changes analysis of receipt-backed invalid stages only. The source allowlist follows the declared source hashes in those named amendment manifests and includes their validation and Python tests. The six researcher evidence notes are included separately from any experimental C review. No C interview or verdict occurred in this actual run.
