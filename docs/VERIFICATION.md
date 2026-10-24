# Release verification — v4

**Verified offline on 22 October 2026. No paid experimental inference was performed.**

## What passed

- All **84 unit and regression tests** passed in the final run, using Python 3.12.14 and Pillow 12.3.0. See `verification_unittest.txt` for the actual output and `VERIFICATION.json` for the machine-readable record.
- The complete canonical scripted rehearsal produced **48 reviewed A/B rounds, 16 final trajectories, all 12 primary receptions, and 58 mock Codex task/review jobs**. It used 356 synthetic chat responses and 108 synthetic image responses; these are software fixtures, not live model observations.
- Resuming the exact completed run made **zero fresh requests**. Its ledger retained zero actual API cost and reports retained zero live observations.
- The evidence checker reconstructed exact archived model inputs, tool contexts and image requests. It checked model outputs, neutral replays, actual fixture pixels, latest-draft delivery, reviewer packets and complete trajectory histories against the durable ledger.
- A separate copy extracted from the release archive was installed as a wheel in a new virtual environment. CLI help, packaged prompt loading, initialization and an empty live-plan report passed. That installed copy also resumed and verified the complete fixture and exported it using a consistent SQLite backup. The export passed ZIP CRC validation.
- An initialized but unexecuted live-plan report correctly had **zero live observations**.

## Concrete failure risks covered

The tests reject input changes even when someone recomputes a local input hash; substituted or relabeled generated images; changed neutral decisions; missing required trajectory rounds; peer-private data leaks; unchanged peer handles during a neutral intervention; schema/role mistakes; rewritten accepted reviewer traces; wrong routes or prices; unbounded keys; changed configuration; and unresolved paid reservations.

A replay interruption preserves the valid original reception and marks the replay missing. The interrupted run persists a stopped status. An invalid round-three reviewer response is retained as a terminal failure while unrelated cases continue: the fixture retains all 12 primary decisions and completes the other 15 trajectories. Resuming neither replaces that review nor makes new provider calls.

The maximum legal multilingual tool history fits the locked message, image and byte limits. The maximum scheduled reservation is below the $40 inference ceiling.

These checks establish consistency with saved responses and artifacts. They are not independent attestation of a remote provider or protection against someone rewriting the entire archive.

## What was not tested

No real OpenRouter POST, paid compatibility pilot, live GLM response, real Qwen image, Sonnet judgment, account credential, or experimental Codex review was used in these checks. Actual inference compatibility, willingness to cooperate, image fidelity, provider availability, live billing, Codex allowance and reviewer isolation remain prerequisites or observations for the main run. Public model metadata was checked with free GET requests during preparation; that is advertised compatibility, not a behavioral test.

The first paid request belongs to the frozen study. Preserve its result even if it is a refusal, schema failure or unusable artifact. Do not present any scripted fixture as a research finding.

## Release hygiene

The delivered ZIP contains source, canonical prompts/configuration, offline tests, documentation, the prospective paper blueprint and conceptual graphic. It does not contain credentials, paid-run data, simulated results presented as findings, virtual environments or build caches. `MANIFEST.sha256` records the packaged file bytes; runtime independently freezes source/prompt/configuration hashes at initialization.
