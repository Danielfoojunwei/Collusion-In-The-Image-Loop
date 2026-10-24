# Technical repairs before the fresh twelve-round run

The fresh main run started only after 87 offline tests, two real technical rounds (16 valid stages), an independent engineering audit, an independent receipt/budget audit, and the sealed real-evidence gate passed. The technical rounds are separate from the main scientific observations.

| Observed issue | Repair and verification |
|---|---|
| Private-log schemas admitted legal JSON text that the client rejected, including escaped control characters. | Preserve valid returned strings exactly and escape them when serializing; no normalization or retrospective repair of old outcomes. |
| API `maxLength` counted Unicode characters while local validators counted UTF-8 bytes. | Match character units across ballots, image prompts, notes, reflections and reviewer fields. Keep explicitly byte-based whole-message limits separate. |
| Image-prompt and context allowances disagreed across adapters and layers. | Use a local image adapter with the advertised character bound; size bounded own-history/schema inputs consistently, while reserving against the original full wire input. |
| Creative sampling produced very long or incomplete private records. | Force the native two-string `record_log` tool; cap notes at 1,000 and reflections at 4,000 characters. Prospectively use temperature 0.3 for recordkeeping. All A/B/C image and ballot stages stay at 1.3; every actor call requests high reasoning and an 8,192-token ceiling. |
| C image delivery could be entangled with a malformed private note. | Separate C's image delivery and private log. A valid delivered image survives a later invalid log. |
| A response-less HTTP 429 left a charge unknown. | Preserve its full reservation; use bounded, separately identified 429 attempts on identical frozen input. Other ambiguous failures stop instead of blindly replaying. |
| Offline fixtures alone could appear to establish readiness. | Block paid main inference until two actual complete rounds bind source/config hashes, physical provider receipts, fresh actual reviewer answers and image/memory routes. |

The actual validation verified eight full reviewer-feedback deliveries, six native private logs (all first-attempt valid), exact prompt/pixel evidence for reflection, own-role memory, and C1's exact image in A2's input with no extra A reply. C's second creative image stage produced two truncated instructions; both consumed its original action allowance, remained archived, and were followed by valid generation and delivery. No extra actions were granted.

Technical candidate04 made 34 completed paid calls and cost **$0.155260268** in the integer accounting ledger. All four technical attempts together cost **$0.426316916** under the original $2 technical subset cap. At main start, aggregate known costs were **$2.177250782**, with **$0.167885** still reserved for 14 older uncertain charges. The original $40 inference cap and $50 OpenRouter cash ceiling remain; Codex is excluded.

Earlier failed candidates, their original invalid statuses, raw outputs and costs remain preserved. Passing this gate establishes observed execution of the tested workflow; it cannot guarantee that a future provider response will be well formed or available. Future missing/invalid observations must remain visible, without scientific-outcome retries.

Evidence: `CANDIDATE04_BACKEND_ACTUAL_AUDIT.json`, `CANDIDATE04_ACTUAL_BUDGET_AUDIT.json`, `candidate04/OFFLINE_SUITE.log`, and `../../experiments/triad_reliable_v4/REAL_LIVE_GATE.json`.
