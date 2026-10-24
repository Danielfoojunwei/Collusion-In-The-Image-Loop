# Budget: at most US$50 new cash

The fixed plan reserves at most **$39.3675776** across all its maximum allowed requests. The local ledger and dedicated OpenRouter key each cap inference at **$40**. Actual charges may be much lower; the cap is not a spending target.

| Request type | Maximum | Maximum reservation each | Maximum reservation sum |
|---|---:|---:|---:|
| GLM decisions, including full neutral B replays | 432 | $0.0472608 | $20.4166656 |
| Sonnet audits | 32 | $0.403216 | $12.902912 |
| Qwen image outputs, including edits and discarded drafts | 216 | $0.028 | $6.048 |
| Total | 680 | | $39.3675776 |

Each turn allows four GLM calls and two image outputs; these are ceilings. A new image returned to an agent can be inspected and delivered in fewer calls. Every paid draft and counterfactual counts, even if it is never sent to the peer. The 96 peer-delivery slots are not the same as the 216 maximum image outputs.

At the inspected price, 216 Qwen outputs would cost $3.9744, before agent/auditor token usage. Do not quote that as total run cost. Exact pinned routes and rate ceilings are in MODELS.md. No extra image generation is needed to make figures: use the actual saved artifacts in the paper.

## Account setup

Use one dedicated paid OpenRouter inference key with a non-resetting lifetime limit no higher than $40. Do not attach BYOK providers or enable automatic credit purchases for this experiment. Keep the same key for every resume. Runtime preflight checks key metadata and pinned public model/price metadata with GET requests only; it cannot verify every wallet or checkout setting.

For reference, OpenRouter's published standard credit-purchase fee is 5.5% with a minimum fee. A $40 credit purchase at 5.5% is $42.20 before applicable tax, leaving $7.80 below the user's total ceiling. Check the actual checkout total: it must be no more than $50. Do not purchase more to compensate for errors or incomplete results. Source: https://openrouter.ai/pricing .

Codex task authoring and reviews consume **existing included product allowance**. There are at most 58 experimental Codex jobs, in addition to the host's orchestration work. No new subscription, credit purchase, metered fallback, or API-key Codex billing is budgeted. If existing included access is unavailable, preserve the pending packets; the $40 OpenRouter key cannot cap Codex product billing.

## Stop behavior

Every paid request reserves funds in SQLite before POST. Exact completed requests are reused on resume. Unknown network/billing outcomes retain reservations and stop further paid work; do not release or retry them automatically. Known charged invalid model responses are retained as failures, not replaced. A price, route, snapshot or source/config change stops the run. A local reserve is an estimate, so the independent provider key cap is required as well.

No live calls have yet verified the account or actual inference behavior. Metadata and offline contracts are the preparation-time checks; the first paid call belongs to the main study.
