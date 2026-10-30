# Implementation and paper backlog

> Proposal0.2 SWE-bench amendment: SWE_BENCH_SETUP.md is authoritative for tasks, permissions, stage timing, outcomes and resource accounting. The previous synthetic supplier/P1-P4 substrate and per-round cost model are superseded. One round now means one multi-cycle repository issue episode. No paid execution or benchmark evaluation has occurred.

This branch adds a reviewable research protocol, not an executable new experimental harness. Existing V15/V16 code, data, ledgers, paper and freezes are untouched. The legacy root AGENTS.md and newer CLAUDE.md disagree about historical V16 readiness; the completed baseline paper is the source of historical status. Neither authorizes a new paid study.

## Proposed isolated namespace

Implement next_batch/oversight_v17/ and a new study output namespace only after the prospective contract is finalized. Do not turn V16 live outputs into new confirmatory data. Historical funding liabilities must remain included wherever the account-level cap applies.

Audit reuse before import:
- V16 documents/vector rendering and artifact hash records.
- Provider capacity/price checks and one-POST reservation machinery.
- Immutable storage/checkpoint implementation.
- Public review packet and first-response provenance.
- Report calculation and quotation checks.

Do not import old incentive functions, reviewer identity labels, collective fines, tag-gated grants or absolute output paths without explicit adaptation. Bind actual model identity where response metadata expose it; distinguish operator-configured identity otherwise. Reviewer product family is configurable in the new freeze, not silently called Codex.

## Work packages and acceptance checks

1. Contract: versioned case, role, treatment and event schemas; exact actor prompts; task oracle and utility/regret. Validate task feasibility, target!=optimum, independent nonce stream and no balanced schedule leakage.
2. Assignment: seeded matched cohort/task blocks, separate development/test/transfer namespaces, no shared actor memory. Test every treatment and disjointness assertion.
3. Round engine: immutable pre-exposure judgments, delivered artifacts, locked decisions, first reviews, policy projection, reflection and checkpoint. Test order and information-access boundaries at every stage.
4. Incentives: individual correctness reward, receiver-only variable shared grant, fixed A target incentive, individual supported sanctions, floor/no double deduction. Exhaustively test knowing/refusal/uncertain combinations.
5. Feedback: preserve full report; generate deterministic bounded public projection; prove projections use no hidden payload oracle. Counterfactual edits are separate replay records with parent hashes, never mutated reviewer originals.
6. Labels: blinded annotation packets, independent assignments, disagreement/adjudication tracking and uncertainty. Protect private reference data from monitor packets.
7. Monitors: nested evidence projections, exact model/effort budgets, calibrated thresholds, immutable first outputs and coverage/missingness. Test no train/test trajectory leakage.
8. Finance/recovery: new study cap defaults disabled; existing account costs retained; single POST identities; faults and uncertain costs preserved. Offline tests inject route failure, timeout after submission, malformed response, renderer failure and mid-round crash. No behavioral rerolls.
9. Simulation: power/MDE grid and sample-size selection without live inference. Include dependence, attrition, six live policies, multiple endpoints and calibration sample sizes.
10. Analysis: machine-generated tables/figures from immutable events, cohort/block inference, natural vs diagnostic prevalence and replay eligibility. Tests cover known toy truths, hard negatives, absent strata and bounds.
11. Reproducibility: lockfile/container or pinned environment, offline fixture smoke run, source/config freeze and one command building all proposed outputs. Never publish fixture-derived paper findings.

## Proposed paper structure

Introduction: oversight must reduce harmful coordination while preserving work and attribution.
Related work: explicit overlap matrix and proposed incremental findings.
Framework: roles, stages, threats, trust assumptions and observable outcomes.
Study1: receiver recruitment under controlled incentives.
Study2: evidence and monitor attribution on hard negatives.
Study3: live interventions, adaptation and mechanism probes.
Study4: held-out transfer and carrier displacement.
Discussion: boundary conditions, deployment implications and falsified hypotheses.
Appendices: full prompts/schemas, preregistration deviations, all models/settings, annotation rules, costs, faults and ethics.

The report should distinguish overt recruitment, covert transmission, knowing cooperation and actual harm. Successful hiding is not required for the paper to be informative. Avoid selecting only attractive images or successful attacks. Primary Apart relevance remains carriers/countermeasures (Track3), with monitoring evidence relevant to Track2; the expanded paper is not limited to the sprint rubric.

## Definition of ready

All STUDY_PLAN release gates pass, models/prompts/utility tolerances are bound, a real total cap is approved, the review bridge is provenance-verified, and the full offline harness passes. Until then paid_execution_enabled remains false. No claim that these documentation changes finish the implementation.

## Required SWE-bench integration before any other launch

Implement an allowlisted dataset importer, versioned Docker/image/task manifest, honest gold/empty grading gates, tool-using A/B/C sandboxes, patch-hash review, three-cycle repair state machine, hidden-grader broker and pristine regrading. No test patch, gold fix, future git history, grading secret, host key or Docker socket can enter an actor container.

Add semantic leakage fixtures for full dataset-row serialization, hidden outcome feedback via balances, mutable dependency caches, forged stdout/test files and cross-agent shared files. Prove rollback/resume retains code and conversation for the current issue but resets executable repository state between issues. Memory transfer is explicit bounded notes, never inherited workspace dirt.

Mandatory human-readable work documents/images accompany actual code and observed tests. Text-only and figure-removed cells retain the same diff/code/test access. Document the new B->A repair report path and C status-only exception rather than asserting the old one-way graph.

One scheduled issue must execute its actual full multi-turn workflow and clean grading before broad dispatch after budget release; never count the current specification as that validation.
