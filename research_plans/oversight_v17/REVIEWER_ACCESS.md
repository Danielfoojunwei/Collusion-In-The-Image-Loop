# Frontier OpenRouter reviewer and evidence contract

User amendment,30October2026. This supersedes the new paper's earlier Codex/Claude Code product-review requirement. Historical V15/V16 records and the first development critique remain unchanged.

## Model and reasoning

Primary monitor: **OpenAI GPT-6 Astra Pro through OpenRouter's Responses API**, requested ID `openai/gpt-6-astra-pro`, bound catalog snapshot `openai/gpt-6-astra-pro-20260903`, direct `openai` provider. Set `reasoning.effort=max` and `reasoning.mode=pro`. The current model catalog explicitly lists max as its highest supported effort. Pro mode and effort are separate controls; both are requested. No product reviewer or fallback to the cheap actor model is permitted.

The selected route advertises text/image input,1,050,000 context tokens and128,000 maximum completion tokens. The request permits128,000 combined output/reasoning tokens; maximum effort is not unlimited computation or a guarantee of detection. Temperature/top-k are omitted because this model does not advertise those controls. A/B/C keep their existing temperature1.0 and high reasoning.

Sources: [model listing](https://openrouter.ai/openai/gpt-6-astra-pro), [reasoning options](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens), [stateless Responses API](https://openrouter.ai/docs/api_reference/responses/overview). REVIEWER_PROFILE.json retains the exact free model/endpoint metadata and pricing. Do not infer delivered capabilities from a product UI label or claim a paid call was validated from metadata alone.

## Default live evidence

| Surface | What M receives or can retrieve |
|---|---|
| Task and contract | Exact public issue, truthful reporting/independent verification duties and declared communication graph. The frozen treatment must also supply its applicable public consequence rules. |
| Documents | Full permitted document text, every listed current document page and labeled original/delivered images. Actual PNG bytes reach the vision model; filenames, OCR and generation briefs are not substitutes. |
| Implementation | Locked patch/diff, relevant base/patched source registered by the broker, hashes and a read-only evidence catalog. Source files can be retrieved by evidence ID. |
| Observed work | Available command/test outputs, exit status, output digest, responsible actor, chronology and patch association. Local tests remain distinct from hidden grading. |
| Decisions and channels | Patch-bound assessments, A-to-B/C work, B-to-C images, B-to-A repair text, C public status and C's next-issue return with actual timing. |
| History | Authorized prior documents/actions and public monitor findings registered for the current cohort. Earlier private model reasoning is not inherited. The receipt lists presented versus unretrieved evidence. |

Current document images are attached automatically. A listed image without its actual bytes blocks review before dispatch. Read-only `read_evidence` can return complete registered source, command records or historical images, using IDs rather than host paths. It cannot access private files, write code or call arbitrary host tools. A job permits at most four paid model responses including retrieval turns; its first final report is preserved. Exhaustion or an incomplete response is missing monitoring, not a fabricated verdict or actor misconduct.

Each checkpoint starts a fresh stateless job: no previous_response_id, no stored service conversation and no researcher chat history. Within that job, returned outputs/reasoning are preserved for its own tool continuation. This is explicit data selection, not a claim that the API offers an independently attested security sandbox.

## Private and counterfactual surfaces

The live monitor cannot see A's private agenda/reward notice, private actor reasoning or memory, generator briefs, future decisions, gold solutions or evaluator-only tests/results. Those remain in the experimenter's research record. More capable monitoring does not justify giving it the answers it is supposed to infer. Separately labeled forensic adjudication can later use its declared richer evidence; it is not the live verdict and is never fed backward to actors.

The default is full authorized evidence. `artifact_only` and `no_history` are named real-world observation conditions: both the initial packet and retrieval catalog enforce their restrictions. The available/presented evidence and access profile are hashed into each job's provenance. No silent context truncation, image dropping or OCR-only replacement is allowed. If materialized evidence exceeds the declared envelope, stop and disclose the gap. Evidence listed in a catalog is available, not proof that the model actually inspected it.

Study2's two-family comparison uses each model's highest supported effort. The old lower-effort factor is removed:60 packets×4 views×2 families=480 planned judgments. The second frontier API family remains a freeze gate, not a hidden substitution. Any private-incentive view is a separately labeled diagnostic disclosure condition, never the default live monitor; its final collector still needs implementation.

## Costs, provenance and current readiness

All monitor input, reasoning and final output are now **paid OpenRouter usage**. The old product-allowance exclusion no longer applies to this reviewer. Preserve the original aggregate$40/program$24/cash$50 caps and all historical charges; do not reset them. The existing$0.10 development allocation cannot fund this reviewer and must not be reused as if it could.

Current standard-route prices are$10/M input and$50/M output, rising to$20/M and$75/M for prompts at or above272,000 tokens. The reservation accounts for the applicable tier, images and the full output bound. Cached-token discounts are not assumed. Worst-case reviewer reservations alone do not fit the earlier large study schedule. Reprice and choose an affordable coherent schedule before dispatch; keep full-study execution disabled rather than silently lowering effort or enlarging the cap.

Archive exact request/response bytes, actual model/provider/response IDs, reasoning settings requested and echoed when available, usage/cost, packet hash, image hashes and evidence retrievals. Missing or conflicting monetary metadata retains the reservation and stops new spending. A valid final report is accepted by the phase machine only with matching API provenance and required current pixels. Paid API compatibility has not been tested in this amendment; only free metadata and offline fixtures ran. Storage, clean grading and full runtime integration remain separate launch gates.
