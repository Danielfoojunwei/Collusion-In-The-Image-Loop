# Recovery of the first completed API audit — 9 November 2026

The original paid audit request completed through OpenRouter with model `openai/gpt-6-astra-pro`, explicit max/Pro/current-turn reasoning echoes, 16 actual images and an actual charge of $2.145485. The strict local broker stopped before parsing the verdict because its top-level provider lookup returned null. The response instead identifies one selected OpenAI endpoint inside `openrouter_metadata.endpoints.available`.

Before reading the verdict for the manuscript, a free generation lookup independently confirmed the same generation ID, canonical Pro model, OpenAI provider, completed stop and identical actual cost. The repair verifies those fields, the original request and response hashes, the frozen evidence-packet hash and the explicit reasoning echoes. It then parses the same first output. There is no replacement API call, changed prompt, changed evidence or participant rerun.

The original request, raw response, receipt (including its missing provider field), failure record and settled charge remain unchanged. A new append-only identity-reconciliation row preserves the old `route_hold` status and records its evidence before releasing that resolved hold. Historical ledgers are untouched. The original receipt's broad identity-evidence label should be read with this correction: route confirmation comes from nested selected-endpoint metadata plus the independent generation lookup.

This was a broker-schema defect after participant collection. It is not an agent behavior, collusion event, or participant-trial failure. The pre-participant source freeze remains unchanged. The recovery utility is explicitly post-collection code and makes no paid calls.
