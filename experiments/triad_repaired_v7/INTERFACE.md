# V7 continuation interface

The source/output namespace is separate to preserve original frozen records while applying the requested prospective consequence change at round9. All v6 schemas and directed routes remain.

Offline sequence: preserve stopped v6 eight-round prefix; record source/ledger baseline; run scripts/check_repaired_v7_offline.py; initialize experiments.triad_repaired_v7.cli; run scripts/migrate_repaired_v7_prefix.py; verify the unpaid continuation gate. Only then resume four remaining rounds through the original aggregate ledger and runtime secret.

The server accepts JSON-line commands as before. Advance optionally accepts through_round, integer1–12, stopping before the next round is prepared. Unknown paid dispatch remains a hard stop. Actual fresh reviews use scripts/service_repaired_review_v7.py; preserve one first final, including malformed/refused answers, without replacement judgments.

Imported observations, requests, claims, pixels and provenance are copied exactly and bound by continuation_import.json. The original case ID and assigned targets remain. New requests use the new payoff policy but preserve old scores and role-local history. A new namespace creates no new allowance. Round12 C image is archived only.
