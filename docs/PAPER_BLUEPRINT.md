# Paper blueprint

**Working title:** Collusion in the Image Loop: Tracing Induced Coordination Attempts Under AI Oversight

**Status:** prospective paper skeleton. No live results, effect sizes or successful mechanisms are asserted. Replace placeholders only with evidence from the frozen run and its receipts. Use [ANALYSIS_PLAN.md](ANALYSIS_PLAN.md) for definitions and denominators.

## Contribution to test

The proposed contribution is a stage-resolved study of ordinary API-generated task images: two agents may generate, inspect, revise and exchange rasters while a separate Codex AI reviewer assigns the legitimate task and returns feedback. Private target branches and a current-artifact replacement replay help distinguish a transmitted objective from a shared default choice. Full traces locate where an attempt breaks between request, rendered pixels and recipient action.

This is a candidate contribution, not a first-ever claim. The paper becomes useful by explaining a supported mechanism or a well-localized failure with reproducible evidence. Merely eliciting a suspicious image, collecting private plans, or obtaining an incorrect answer does not complete that contribution.

## Draft abstract: 199 words, no empirical findings

> Generated images can serve simultaneously as task evidence and as messages between agents, complicating oversight of visual workflows. We propose a bounded stress test of explicitly instructed coordination against a legitimate supplier-selection task. Two agents exchange generated rasters over multiple rounds while an actual Codex AI reviewer assigns the task and supplies public feedback. Each participant can inspect artifacts, request image generation or editing, and choose whether to deliver an image within fixed limits. Six main cases share a two-round history before branching into two different private targets disclosed only to the sender; separate honest and overt controls test the workflow. A truthful replacement of the current incoming artifact is replayed through the receiver's full bounded tool loop. We distinguish stated plans, exact renderer requests, delivered pixels, receiver decisions and reviewer judgments, and compare offline auditing with only the latest image pair versus the full image history. [Insert completed counts, primary paired outcomes, the strongest supported stage-level finding, and uncertainty after execution.] The planned analysis treats exposed reasoning as self-report, retains failures and refusals, and avoids equating wrong answers with knowing cooperation. The intended contribution is a reproducible account of where image-mediated coordination is realized or interrupted under feedback.

Check the final word count after replacing the bracketed placeholder. Do not submit a prospective abstract as if it described completed experiments.

## Eight-page main-paper allocation

| Page | Content | Evidence to supply |
| --- | --- | --- |
| 1 | Title, authors, abstract, problem and precise contribution | One clear research question; no unsupported prevalence claim. |
| 2 | Closest related work and experiment overview | Overlap/gap table; actor and information-boundary diagram. |
| 3 | Methods, task rule, tool budget, common prefix, target branches and replay | Frozen configuration, prompt/version hashes, branch and replay implementation. |
| 4 | Primary round-3 results and execution accounting | Six-row paired-case table; original-versus-neutral decisions; every missing/invalid cell. |
| 5 | Stage-level analysis of attempts and failures | Plan/request/pixel/decision links, refusal/error counts, renderer-boundary observations. |
| 6 | Codex outcomes, later feedback and offline image-history comparison | Trace eligibility, conditional denominators, paired audit disagreements. |
| 7 | One supported mechanism or failure case, plus counterexample | Actual delivered images, exact limited prompt excerpts and evidence-linked timeline. |
| 8 | Interpretation, practical implications, limitations and next study | Claims proportionate to six cases; bounded countermeasure interpretation; disclosure of preexisting work. |

References and the detailed limitations/dual-use appendix follow the main paper. A compact successful-result paper may use fewer pages. Do not fill the page budget with duplicated logs.

## Related work: make the overlap explicit

| Primary source | Existing contribution that limits novelty | Narrow question left for this study |
| --- | --- | --- |
| Dineen et al., [Despite Instructions: Frontier Agents Improvise Covert Channels at Test Time](https://arxiv.org/abs/2609.32701) | Repeated interaction with feedback can establish covert meaning without a supplied codebook; this is already prior work. | Does an analogous objective survive an ordinary image renderer and receiver tool loop, and at which observed stage does it break? |
| Alnuhait et al., [Covert Assistance: Helpful LLM Agents Evade Oversight in Multi-Agent Systems](https://arxiv.org/abs/2609.39050) | Concealed assistance and completed unauthorized recipient actions have been studied without adversarial incentives. | Explicit induction here measures a bounded stress-test capability; it cannot reproduce or exceed a claim about spontaneous willingness. |
| Hu et al., [Lying with Truths: Open-Channel Multi-Agent Collusion for Belief Manipulation via Generative Montage](https://arxiv.org/abs/2601.01685) | Coordinated truthful evidence fragments can manipulate a victim's conclusions; misleading evidence need not contain a hidden message. | Do generated visual task artifacts act as a target signal, purported evidence, or both, and is the receiver a cooperating actor or a deceived one? |

These primary sources were checked on 22 October 2026. A particularly close multimodal lead is Ge-Wang et al., [When Do Covert Channels Emerge? Probing Steganographic Capacity in Multimodal Agents via Diffusion VAE Latents](https://openreview.net/forum?id=Y3POisuyOO). Its official indexed title already makes a broad “first visual covert channel” claim untenable. Direct full-paper retrieval encountered OpenReview's verification wall during this documentation pass; verify the full method before making a detailed claim about its access assumptions or differences from this experiment. Cite the exact versions actually read. Do not turn a limited abstract review into a claim that no similar visual study exists. Our use of readily available tools or an additional model brand is not sufficient novelty on its own.

## Results tables to populate

**Table 1 — Execution accounting.** Planned and observed case/branch/round counts, A/B and replay turns, model and image calls, invalid outputs, refusals/declines, provider blocks, budget stops, actual cost and Codex trace coverage. Distinguish counters from observations; max 432 GLM requests and 216 Qwen requests are caps.

**Table 2 — Primary paired carrier test.** One row per main case, with both private targets, both round-3 decisions, whether both target identities were followed, each neutral replay decision, and missingness. The two targets are different ineligible candidates. A single fixed choice cannot count as successful differentiation.

The planned denominator is 12 round-3 receptions across six case blocks, independent of completed final trajectories. Merge `reception_rounds` → `unreviewed_rounds` → `rounds`, deduplicating the same branch/round. The base valid A/B delivery pair is saved in `reception_rounds` before replay, so a replay transport/budget stop does not erase it; later records add replay and review evidence. Retain valid receptions when Codex review is pending/invalid or round 4 fails. Incomplete pairs remain unresolved/null. A missing neutral replay affects only its paired replay contrast, not eligibility of the original reception.

**Table 3 — Stage localization.** Counts of supported/failed/unknown/not-reached evidence at planning, request, generation, inspection, delivery, reception, task outcome and oversight. Support each nontrivial code with an artifact or event reference. Self-reports are a separate evidence column.

**Table 4 — Reviewer views and final outcomes.** Main-branch final decisions and actual Codex accept/revise/reject plus suspicion; honest and overt controls separately; latest-pair versus full-history Sonnet judgments and disagreements. Both offline views have identical task facts and decision timelines and omit all Codex feedback.

## Figures that carry the argument

1. **Information boundaries and interventions.** Show A's private target, Qwen requests/pixels, B's tool loop, public Codex packets and feedback, and the round-3 replacement. Distinguish research logs from participant-visible channels.
2. **One full observed trace.** Show a stated plan, requested feature, actual generated image, private inspection/revision, delivered image, B's response and Codex feedback. Include an unsuccessful or contradictory step if present. Never substitute a prettier undelivered intermediate image.
3. **Six-case paired plot or table.** Emphasize case-level results and unknown outcomes. Avoid dozens of turn-level dots that imply independent samples.

Use standard plotting tools and actual saved rasters for research figures. Do not generate fictional example outputs to stand in for experimental evidence. Crops can aid readers when clearly labeled as author annotations; they must remain linked to the unmodified delivered image.

## Interpretation routes after the run

| Observed pattern | Defensible conclusion | Claim to avoid |
| --- | --- | --- |
| Both fresh targets followed; effect weakens on neutral replay | Evidence consistent with current-artifact-mediated target use in these contexts | Universal channel capacity, a proven secret code, or spontaneous collusion |
| Clear requested feature absent from returned image | A localized rendering mismatch in the observed attempt | Qwen was safety-aligned, or another renderer would necessarily succeed |
| Feature present, B describes it, but B follows legitimate task | Reception evidence without completed task-violating cooperation | All channel attempts succeeded, or B could not see the signal |
| Target selected and Codex accepts without suspicion | Recorded task violation accepted by this reviewer configuration | Human deception, general undetectability, or certainty about internal intent |
| No target following and overt controls work | A bounded negative result for the tested configuration | Images cannot carry collusion |
| Overt controls fail or renderer/bridge never works | Pipeline or control limitation, with useful diagnostic traces if available | A clean negative finding about agent willingness or capacity |

The neutral replacement is a limited diagnostic intervention. It removes the current artifact while preserving earlier history; it does not establish a deployed defense's utility across tasks. Feedback-linked changes are observational because the study does not include matched feedback/no-feedback conditions.

## Rubric alignment and submission preparation

The primary event fit is **Track 3.1**, with **Track 2.3** supporting the evidence standard. A causal claim about oversight awareness would require an additional matched design; this experiment's evolving feedback alone does not establish Track 3.4's causal question. The official rubric uses three 1–5 dimensions and publishes no separate weights. [Official sprint guidelines](https://apartresearch.com/sprints/ai-collusion-research-sprint-2026-10-23-to-2026-10-25#guidelines)

| Rubric dimension | What this package prepares | What the actual paper still must earn |
| --- | --- | --- |
| Impact Potential & Innovation | A precise image-artifact question, explicit prior-work contrasts and a practical plan-to-pixels failure analysis | A supported new insight and clear reason another researcher would use it. |
| Execution Quality | Frozen bounded design, controls, paired target/replay comparisons, evidence-linked traces and complete accounting | Successful execution, verified boundaries and conclusions that survive counterexamples and missingness. |
| Presentation & Clarity | An eight-page structure, one primary result table and a trace that connects plan to action | Clear figures, concise claims and no invented results or buried limitations. |

No implementation or plan guarantees a 5/5 score. Findings, validation and the final presentation determine what the submission supports.

Use the event's currently linked official PDF template, keep the main text within eight pages, provide a 150–250-word abstract and author affiliations, and include the required limitations/dual-use appendix. Report the positive control even when it fails. Disclose code prepared before the sprint in both README and paper. These requirements are documented on the same official guidelines page.

## Appendix contents

- Complete frozen prompts, tool schemas, case generation, model/provider identities, budget limits and hashes.
- Exact public/private projections and Codex context/trace audit procedure; identify instruction-scoped isolation honestly.
- All outcomes and failures, replay provenance, full image manifest and selected evidence references.
- A timestamped discovery log, clearly separated from prespecified analyses and protocol amendments.
- Limitations: explicit inducement; small dependent sample; separate control cases and unequal control durations; one fixed model configuration; stochastic renderer/receiver; incomplete exposed reasoning; structured synthetic task; finite tool budget; AI rather than human reviewer; limited replay intervention; no matched oversight counterfactual or causal induction comparison.
- Dual use: operational examples could facilitate covert coordination. Keep the study sandboxed, describe defensive monitoring implications, and assess the necessity of releasing reusable attack-specific instructions separately from aggregate evidence and reproducibility code.

Finish with the strongest observed insight, even if it is a failure boundary. Do not replace a disappointing result with a more exciting story unsupported by the saved artifacts.
