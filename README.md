# Collusion in the Image Loop

Empirical AI-safety research into communication through generated visual work
documents, private-target persuasion, independent receiver incentives and
countermeasures. Primary Apart Research alignment: **Track 3.1**, with detection
and behavioral evidence relevant to Track 2.

**Start with [the Claude Code handoff](CLAUDE_CODE_HANDOFF.md).** It describes the
current V16 implementation, exact run state and remaining repair. The experiment
is paused: **0/60 V16 rounds are complete**. The latest API compatibility changes
passed offline tests; recovery amendment004 is still pending. No new paid run
was started for this GitHub transfer.

- [Current protocol and five matched twelve-round conditions](next_batch/v16_document_carrier/PROTOCOL.md)
- [Actual actor system/user prompt builders](next_batch/v16_document_carrier/prompts.py)
- [Durable round workflow](next_batch/v16_document_carrier/runner.py)
- [Current run, immutable amendments and diagnostics](runs/main/triad_document_carrier_v16_live/)
- [Machine-readable handoff state](handoff/STATE.json)
- [Latest offline verification log](handoff/OFFLINE_TESTS.log)
- [Full historical run archive and restore instructions](archives/README.md)

The study uses three actors: A's document reaches B/C, B's document reaches C,
and C's return reaches next A. A separate genuine Codex reviewer provides actual
MAIN and RETURN judgments. Independent B/C client judgments are recorded before
exposure; exact private payload recovery, subsequent choices, document quality,
reviewer findings and fictional financial consequences are measured separately.

The preceding V15 twelve rounds are complete: 23/24 B/C target ballots,
11/12 unanimous target rounds, and 12/12 uncertain live joint assessments.
These observations do not establish invisible steganography. V16 is designed to
measure recovery, persuasion and defenses more directly; it has no completed
round results yet.

All project source, historical research plans, tests and reports are retained.
Large historical artifacts are stored in split lossless archives with SHA-256
manifests. Runtime credentials, Python environments and bytecode caches are
excluded. The original V4 README is preserved in [legacy/](legacy/V4_README.md);
the original V4 handoff remains an historical document.

For restoration and continuation, use the full handoff. Existing absolute frozen
paths require the checkout at `/workspace/visual_collusion_stress_v4` or a
container bind mount there. Supply the existing OpenRouter credential only as a
runtime secret. **Do not delete the stop marker or restart the old driver.**
