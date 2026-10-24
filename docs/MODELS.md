# Models, endpoint pins, and cost assumptions

This configuration was checked against public OpenRouter metadata on **22 October 2026**. These were unauthenticated metadata GETs. No paid request, image generation, model behavior test, or account-access test was performed when building the package. A run must pass the adapter's free account and metadata preflight before any paid work.

## Locked choices

| Role | OpenRouter model ID | Exact provider endpoint | Observed public price |
| --- | --- | --- | --- |
| Both collaborating agents and neutral-image receiver replays | `z-ai/glm-5.3-flash` | `sail-research/fp4` — Sail Research, FP4 | $0.07835 per million input tokens; $0.25 per million output tokens |
| Independent final artifact auditor | `anthropic/claude-sonnet-5.5` | `anthropic` — Anthropic | $2 per million input tokens; $10 per million output tokens |
| Generated-image tool | `qwen/qwen-image-2.1-turbo` | `alibaba` — Alibaba Cloud Int. | $0.0184 per 1K output image; $0 per reference image in the inspected endpoint record |

The checked canonical chat snapshots were `z-ai/glm-5.3-flash-20260826` and `anthropic/claude-sonnet-5.5-20260928`. Preflight records the current canonical snapshot and binds it, the exact provider route, the quantization, the reasoning setting, and the supported JSON mode into the durable run ledger. Changing this configuration on resume is rejected. Provider fallback is disabled.

GLM-5.3-Flash has published weights under an MIT license on the [official Z.ai model repository](https://huggingface.co/zai-org/GLM-5.3-Flash). This experiment uses a hosted FP4 endpoint; it does not run those weights locally. We have not verified an open-weight release of Qwen Image 2.1 Turbo. Sonnet is a separately hosted proprietary auditor. Consequently, describe this as an **open-weight agent family with hosted image generation and an independent auditor**, not as an entirely open-source stack.

## Capability checks and their limits

The chat catalog advertises actual image input and text output. Both selected chat endpoints currently advertise `response_format`, `structured_outputs`, `max_tokens`, and `reasoning`. GLM advertises mandatory reasoning with supported efforts `max`, `high`, and `low`; this experiment uses **high** for the agents. Sonnet uses **low** for the independent final audit. A missing required effort stops free preflight rather than silently changing the experiment. The adapter uses strict JSON schema only when that mode is advertised by both the model and selected endpoint, otherwise JSON object mode. The selected mode is recorded and bound. The experiment still validates each returned task object locally.

Agent requests set `reasoning.exclude=false`. Any provider-exposed `reasoning` and `reasoning_details` are preserved in the raw billed response and returned separately for logging. The provider may omit, redact, or summarize them; this package does not request access to hidden internal reasoning or fabricate a trace. Such material is a model-generated record, not proof of intent, faithful internal computation, or a causal explanation. It is archived privately. Conversation history contains the agent's own visible structured actions and notes plus tool results; native reasoning fields are not replayed as messages or disclosed to the peer.

The free chat endpoint schema does not separately enumerate image input capability for every endpoint. Checking the catalog and exact endpoint is evidence about advertised compatibility; it is not a test of actual vision behavior. The first paid study request may still fail. There is no paid compatibility pilot, alternative model sweep, or automatic fallback.

| Request class | Actual inline PNG blocks | Message objects | UTF-8 text plus schema | Completion/reasoning token cap | Reasoning effort |
| --- | ---: | ---: | ---: | ---: | --- |
| GLM agents and receiver replays | 16 | 64 | 65,536 bytes | 4,096 | high |
| Sonnet final audit | 8 | 40 | 16,384 bytes | 2,048 | low |

Each PNG must be nonanimated, at most 1024 pixels on either side and at most 8 MiB. Full repeated histories, including repeated image blocks, are sent again and counted again. Exceeding a bound fails rather than silently truncating the history. Each agent turn has at most four decision calls and at most two generated images; the same loop budget applies to neutral-image receiver replays.

The Qwen image endpoint currently advertises 1K and 2K resolution, square aspect ratio, one to six outputs, and zero to four references. This package fixes one 1K square output with zero or one actual PNG reference. The payload sends `resolution="1K"`, `aspect_ratio="1:1"`, `n=1`, and the documented common `output_format="png"` field. A returned image must actually decode as one 1024 by 1024 PNG. The local `quality="standard"` setting labels this fixed configuration; it is **not** sent as an unsupported Qwen quality parameter.

## Budget protection

The local inference cap is **$40**. A dedicated OpenRouter inference key must also have a positive lifetime cap of at most $40, no periodic reset, and sufficient remaining allowance. The adapter checks key metadata without recording the credential. A Cloud network-secret placeholder is not treated as a real credential identifier. The account owner must keep the same dedicated key and ensure total checkout, tax, and any later account spending remain within the user's **$50 total cash ceiling**. Wallet funding and auto-recharge settings cannot be verified through the ordinary inference key.

Reservations are made durably before every paid POST. Completed responses can be resumed without another POST only when the stable request ID and exact request digest match. An interrupted request, unknown billing outcome, or missing finite cost receipt retains its reservation and stops further paid work. There are no automatic paid POST retries. A known billed but unusable response can be recorded as a failed study item without replacement.

The conservative input estimate counts each UTF-8 byte as one token allowance, adds message framing, and reserves 8,192 tokens for each image block. This is a budgeting allowance, not a tokenizer or vision-token claim. At all simultaneous input limits, it reserves 205,824 input tokens for GLM and 88,064 for Sonnet. Hard input-estimate boundaries are 210,000 and 90,000, respectively. It assumes no cache-hit discount, adds a cache-write allowance, reserves twice the requested completion cap for reasoning/output uncertainty, and includes a per-call margin.

GLM input is reserved at $0.20 per million allowance tokens, while its advertised prompt and any advertised cache-read/write rates must each be at most $0.15 per million. Its output rate must be at most $0.50 per million, and the per-call margin is $0.002. The inspected GLM endpoint advertises cache-read pricing of $0.0261 per million and no cache-write rate. Sonnet keeps its $4 per million input allowance, $10 per million output ceiling, and $0.01 per-call margin.

| Planned call class | Maximum calls | Maximum local reservation per call | Sum of those maxima |
| --- | ---: | ---: | ---: |
| GLM agent and replay decision calls | 432 | $0.0472608 | $20.4166656 |
| Sonnet final audits | 32 | $0.4032160 | $12.9029120 |
| Qwen image outputs, each with at most one reference | 216 | $0.0280000 | $6.0480000 |
| **Maximum fixed-schedule total** | **680** | | **$39.3675776** |

These are conservative local reservations, not expected spending or an API-enforced per-request dollar guarantee. The image reservation allows an output price up to $0.025 and reference price up to $0.003, above the inspected current endpoint rates. The chat adapter checks exact selected endpoint prices, not the catalog's cheaper aggregate minimum. Unexpected rates, insufficient advertised context, unavailable pinned endpoints, unresolved requests, and reserve overruns halt paid work. Actual reported costs and raw receipts are kept in the ledger. An actual cost above its reservation is recorded before halting.

The maximum 216 output images alone would cost $3.9744 at the inspected $0.0184 rate, before chat usage. This is a conditional arithmetic example, not a quote for the entire experiment. Models may use fewer calls and images than the allowed maximum. Model output lengths, full repeated histories, vision accounting, and reasoning usage determine the final chat bill. Codex oversight uses the user's existing allowance and is outside these OpenRouter API estimates.

## Official sources

The public APIs below are the authoritative inputs to runtime preflight. Prices and endpoint availability can change after this document was written.

- [OpenRouter chat model catalog](https://openrouter.ai/api/v1/models)
- [GLM-5.3-Flash endpoint metadata](https://openrouter.ai/api/v1/models/z-ai/glm-5.3-flash/endpoints)
- [Sonnet 5.5 endpoint metadata](https://openrouter.ai/api/v1/models/anthropic/claude-sonnet-5.5/endpoints)
- [OpenRouter image model catalog](https://openrouter.ai/api/v1/images/models)
- [Qwen Image 2.1 Turbo endpoint metadata](https://openrouter.ai/api/v1/images/models/qwen/qwen-image-2.1-turbo/endpoints)
- [Endpoint metadata schema](https://openrouter.ai/docs/api/api-reference/endpoints/list-all-endpoints-for-a-model)
- [Image understanding and actual inline image inputs](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding)
- [Image generation API request and response fields](https://openrouter.ai/docs/api/api-reference/images/generate-an-image)
- [Image generation, reference images, common output format, and provider routing](https://openrouter.ai/docs/guides/overview/multimodal/image-generation)
- [GLM-5.3-Flash official weights and license](https://huggingface.co/zai-org/GLM-5.3-Flash)
