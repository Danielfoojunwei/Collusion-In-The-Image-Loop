# Whole-round checkpoint restart04

This is a prospective technical amendment to the same v9 twelve-round episode.
It does not activate on import. The original six closed rounds, 48 outcomes,
first Codex answers, all 174 parent runtime sources and all existing paid ledger
rows remain unchanged. Inactive repair03 is superseded; its lost-slot
continuation is neither activated nor reused.

The latest user instruction authorizes restarting the **whole current round**
after an API or harness fault. The initial restart is Round 7 attempt 2, from
Round 6's complete saved memory. Round 7 attempt 1 retains its original unknown
response, positive full cost reserve, request, dispatch and actual transport
evidence. Restarting never means resetting the six completed rounds or the
aggregate budget.

## Round transactions

Each new attempt has its own `attempts/rNN/aNN` workspace. All physical paid
identities keep the original `triad_emergent_v9` namespace and add the attempt
identity. A paid request identity is never posted twice. The stage order stays
A image and vote, B image and vote, C vote, actual Codex review, A log, B log,
C return image, C log. Shared actual feedback precedes every private log and
C's return. A receives only the previous committed round's C image; B receives
A's image; C receives A's and B's images.

Only a verified eight-stage attempt can publish one immutable whole-round
selector in `checkpoints_restart04/commits`. Subsequent roles, scores, histories
and reviewers read committed rounds plus their own current attempt. Abandoned
images, feedback and private notes remain available to the researcher but do
not enter a restarted agent or reviewer context. Attempts are not extra rounds
or independent experimental cases.

Every `advance` and actual-review publication has a durable operation journal.
An interrupted operation causes a partial round to be archived and restarted,
including a crash between two successful stages. If all eight outcomes already
exist, the exact round is verified and published with zero new actor or
reviewer calls. A crash after atomic publication only recovers status.
Corrupt journal, source, checkpoint, request or artifact hashes cause a hold.

## Fault policy and limits

There are at most three technical attempts per round, including the original
Round 7 attempt. Typed transport/API failures, unusable provider envelopes,
invalid PNG transport and recoverable local storage failures abandon the whole
working round. Free metadata failures before dispatch preserve the same
attempt and spend no paid intent. Authentication, permanent provider/schema
incompatibility, integrity problems, concurrent producers and insufficient
budget stop for repair. Repeated eligible faults stop at the fixed attempt
limit.

Valid refusals, wrong choices, malformed agent arguments or logs within a valid
native envelope, imperfect but valid pixels, unsuccessful communication,
uncertain reviews and invalid actual first reviewer answers are observations.
They never authorize a technical restart. A missing stage outcome is not proof
that no model response was seen; its archived provider evidence must be read.

An abandoned marker is written before its exact paid-prefix funding seal.
Interrupted seal publication is idempotently completed before any new paid
dispatch. Unknown charges retain their full reservation and original status.
A genuinely interrupted pending callback is recognized only with the exact
paid-owner record, dead or replaced producer PID/start fingerprint, matching
operation proof and a demonstrated reacquired exclusive producer lock. A live
or same-process unresolved callback holds. Unrelated unresolved rows remain
blocking.

The inference cap remains $40, the v9 subset remains $24 inside it, and the
OpenRouter cash limit remains $50. Codex remains excluded as instructed by the
user. Failed attempts consume the same aggregate and subset caps. No prompt,
model, route, task, image allowance or incentive changes. Effective actor
temperature stays 1.0, high reasoning and 8192 output tokens; the retained
original temperature-2 config is historical evidence.

## Actual reviewer bridge

Each attempt has an exact job such as
`triad_emergent_v9.r07.a02.REVIEWER`, its own public packet, copied actual images,
dispatch, fresh child context, immutable first final and hash-bound provenance.
The unchanged original first-answer bridge runs inside that attempt workspace.
The researcher host never authors the judgment. Full tool coverage and OS
isolation remain unverified; fresh contexts are instruction scoped.

`reviewer_host prepare` and `envelope` record genuine caught filesystem faults
without arbitrary exception messages. Actual fresh-context tool failures can
be recorded with `record_technical_fault(..., operation='fresh_context_host_failure',
host_evidence_path=...)`, using an allowed infrastructure exception class and
the saved actual host-tool event. A caller-declared bad verdict is rejected.
The `reviewer-fault --proof PATH` host command binds the exact current job and
proof. If a captured first final exists, it first reconstructs that exact
envelope with zero new judgments. Otherwise the genuine technical fault
archives and abandons the round. Any already observed first answer remains in
the failed-attempt archive and is never replaced inside that attempt.

## Offline seal and commands

Run `.venv/bin/python -m experiments.triad_v9_checkpoint_restart04.offline_gate`
after all candidate sources and tests are stable. It runs the preserved parent
suites plus named restart fault tests with HTTP connection dispatch disabled.
It writes only a new workflow gate and validation log. A test count does not
prove future provider availability or absence of every possible defect.

The seal command is read-only by default. It directly validates the original
active174 parent snapshot, current source/test gate, exact exited producer
proof and preserved accounting. Only the root host may use the explicit
activation phrase after independent readiness review. No old freeze is
temporarily swapped, and no provider request occurs during sealing.

The new CLI provides `status`, `preflight`, `advance`, `serve`, `dispatch`,
`ingest`, `reviewer-fault`, `verify`, `report`, `render`, `shadows`, `export` and
`export-visual`. `advance --through-round N` stops after the whole round commits.
`serve` accepts the same explicit JSON commands. There is no replacement-run
initialization command.

After all twelve committed rounds, the same fixed 24 isolated counterfactual
endpoints run from the frozen main history. This control workflow also has at
most three technical attempts, with distinct `shadow_attempts/aNN` workspaces.
A technical failure restarts the complete fixed workflow; unfavorable valid
control outcomes remain observed. All failed control artifacts and charges
are retained, and no new endpoints or extra budget are introduced. Exact saved
complete workflow publication recovers with zero further provider calls.

Reports and chronological galleries follow selected committed workspaces and
the selected control-workflow commit, while listing every failed attempt and
its missingness/costs separately. Full archives retain all failed artifacts.
Completed-only collusion rates condition on technical completion; image
complexity may correlate with failures. Technical missingness is not evidence
of collusion, refusal, concealment, emotion or faithful hidden cognition.
