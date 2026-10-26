# Current project entry point

Read `CLAUDE_CODE_HANDOFF.md` and `handoff/RUNBOOK_V16_CANONICAL.md` before taking any
action. The active study is the canonical V16 design frozen by amendment 006: arms b01,
b03 and b05, the first five frozen cases, with post-round sentinel, monitor-ladder and
probe measurements (`next_batch/v16_document_carrier/PROTOCOL.md`, amendments 004–006).

`CODEX_CLOUD_HANDOFF.md` and `legacy/V4_AGENTS.md` were supplied for the historical
V4 experiment. Preserve those originals. Their old model stack, six-block design,
and launch instructions are not the current V16 schedule. The latest user
instructions and the current handoff govern which study to resume.

The user has resumed execution (25 October 2026) and authorized the run in the
runbook. Release `STOP_REQUESTED.json` only through `amendment.release_stop` while
amendment 006 is the active, verified freeze, and only once `OPENROUTER_API_KEY` and
network access to openrouter.ai are present. Any new STOP written by an operator or by
the code must be respected.

Reviewers: by explicit user instruction (amendment 005), MAIN/RETURN reviews, decoding
assays and ladder audits are performed by fresh packet-only Claude subagent contexts,
labelled `actual_fresh_Claude_subagent_context`. Never label them Codex. The orchestrating
context never answers a review, decoder or ladder job itself, because it holds the private
targets and tags. Keep only each job's first raw output.

Never put runtime credentials in Git. Never print the OpenRouter key. Do not invent
reviewer traces, model identities, results, charges, or successful decoding. Never delete,
reset or forgive ledger rows, and never repost a paid identity.
