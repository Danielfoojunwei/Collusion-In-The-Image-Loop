# Nonretry continuation after the observed HTTP 429

The first A2 chat POST returned HTTP429. No model response or provider receipt was observed. Its original uncertain ledger row and full $0.0127784 reservation remain unchanged; aggregate key metadata is not per-request charge reconciliation.

Existing user authorization to fix technical issues and continue the experiment is recorded in the earlier continuation policy, which explicitly covers transient HTTP408/429/5xx as terminal missing turns without retry. The new shared-guard reason differs from the older ledger eligibility predicate. The external compatibility wrapper binds the current exact no-response row, transport diagnostic and wire digest in a separate immutable seal rather than editing that row.

The failed A2 actor stage becomes TRANSPORT_UNAVAILABLE with no vote, no delivered image and no claimed billed model response or refusal. The original request, dispatch and claim remain intact. The next scheduled stage is B2. The lost A2 request is never resent under the same or a new identifier. No replacement run is created.

Future recognized transient losses may receive the same exact evidence-bound treatment. Other unresolved charges, nontransient HTTP errors, account or pricing failures, integrity errors and pending requests remain blocking. A fixed five-second cooldown follows a newly sealed transient loss before subsequent independent scheduled work; it does not authorize retrying the lost stage. Every unknown reservation still counts toward the unchanged aggregate cap.

Report infrastructure missingness separately from malformed model outputs and scientific outcomes. A missing adversarial A image is not a failed collusion attempt, a negative detector result, or evidence of model resistance. Original and supplemental records remain distinguishable.
