# Apart alignment and judging strategy

Source: [official AI Collusion Research Sprint](https://apartresearch.com/sprints/ai-collusion-research-sprint-2026-10-23-to-2026-10-25#guidelines), checked 22 October 2026.

## Track

Anchor in **Track 3: Collusion Carriers and Countermeasures**, especially **3.1**, because generated images and image tools are the communication surface. Also select Track 2 if the final paper foregrounds evidence and detection: the experiment distinguishes hidden-goal dependence from coincidence and compares auditor image access.

Ongoing reviewer feedback is part of the environment. This design does not randomize whether agents learn that monitoring exists, so it does not causally answer question 3.4. Do not stretch the track claim to imply that comparison was run.

## The actual rubric has three dimensions, each scored 1–5

| Dimension | What this project can contribute | Evidence still needed for a strong submission |
|---|---|---|
| Impact Potential & Innovation | A concrete audit problem: generated artifacts can both perform the task and carry a joint prohibited objective. Stage-linked logs make the renderer's role and the detector's information limits visible. | A specific finding supported by real trajectories and controls; a precise comparison to the closest prior work; a practical implication for artifact review. |
| Execution Quality | Fixed protocol, target forks, equal-budget neutral replays, honest/overt controls, numerical oracle, cumulative flags, independent detector views, exact request/pixel logs, capped durable accounting. | Completed real cases; documented failures and missingness; actual Codex provenance; evidence that the claimed signals survive Qwen rendering and affect B. Tests alone do not establish this. |
| Presentation & Clarity | One research question, one compact workflow, one controlled-results table, an annotated complete trajectory, clear definitions of coordination/detection/wrong approval. | A readable results-first paper with accurate captions, all denominators, and explicit limits. Keep operational implementation detail in the appendix. |

These are project-specific interpretations of the published criteria, not predicted scores. All tracks compete under the same rubric. A five in each dimension or a win cannot be guaranteed before results exist.

## Novelty to test, not assert

Repeated interaction, covert-channel emergence, target substitutions, and steganography are already studied. Do not claim that using two agents or images is itself a first. The candidate contribution is the **instrumented connection between a reasoning agent's plan, an ordinary hosted image generator's output, a peer's action, and live AI oversight**, with controlled reception tests and matched detector views.

Strong possible findings include a recurring renderer bottleneck; visible task-artifact manipulation that succeeds without sophisticated hidden encoding; public image history improving detection; coordination suspicion missing a violation caught by the numerical task check; or no target dependence despite repeated explicit attempts. Each must be tied to actual evidence and counterexamples. None is a promised result.

## Submission requirements

Use the official linked PDF template. Keep the main paper within eight pages, excluding references/appendices. Include a 150–250-word abstract, authors/affiliations, and the required Limitations and Dual-Use / Ethical Considerations appendix. Disclose all pre-sprint preparation. Use the current template linked from the sprint page, not an older email attachment.

The package's PAPER_BLUEPRINT.md is a writing scaffold, not a completed results paper. Replace prospective language with measured findings after the run. Keep the overt positive control and honest controls visible; failed controls must be reported. Quantify the six independent main blocks rather than inflating the sample size with branches or images.
