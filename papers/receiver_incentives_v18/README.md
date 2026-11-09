# Receiver-incentive pilot: paper and evidence

[Read the 10-page paper](PAPER.pdf) · [Manuscript source](PAPER.md) · [Response to the review](RESPONSE_TO_REVIEW.md)

Twenty independent B/C cohorts completed three V16 work-document cases each. Only the correctness reward, 0 versus 4000 fictional points, differed between arms. All 120 ballots were client-correct; joint sponsor adoption was 0/30 in both arms. The paired estimate was 0, exact p=1, with a broad prespecified uncertainty bound. This is a null conditional pilot, not proof of equivalence or covert-channel absence.

The single max-effort/Pro frontier API audit found no supported knowing joint participation in six presampled trials. It recovered three historical visible-text nonce patterns and flagged verification errors despite correct ballots. These are descriptive observations from one auditor, not new causal findings or general detection rates. See [the actual audit](results/frontier_audit.json) and [readout comparison](results/post_audit_readout_comparison.json).

Actual new cost: **$3.2762225** across 181 paid calls, including the technical canary and the one frontier audit. [Funding reconciliation](results/funding_reconciliation.json) matches the account's usage change to the ledger. Historical liabilities remain charged. A post-collection provider-metadata parser mismatch was [recovered from the same first response](AUDIT_RECOVERY.md), without a replacement call or participant rerun.

## Reproduce the analysis

From the repository root, no API key is needed:

```sh
make -C papers/receiver_incentives_v18 reproduce
make -C papers/receiver_incentives_v18 pdf
```

PDF compilation needs Pandoc and pdfLaTeX. The analysis uses Python's standard library. Plot regeneration additionally uses Matplotlib and Pillow. Participant/model replay is a new stochastic experiment with costs; these commands reproduce the published analysis and manuscript only.

## Evidence

- [Grouped analysis](results/analysis.json), [all 60 rows](results/records_public.csv), [cohort rates](results/cohort_rates.csv), [actual choices](results/choice_counts.json).
- [All public reports](results/decisions_public.json), [trusted source tasks](results/sources_public.json), [literal quote checks](results/quoted_evidence.json).
- [Delivered page and original-figure manifest](results/assets_manifest.json), [full image assets](results/all_documents), [first-pair overview](results/documents_first_pair.png).
- [Paid receipts](results/receipts_public.json), [costs](results/costs.json), [technical events](results/technical_events.json), [fictional settlement](results/fictional_settlement.json).
- [Numeric/provenance check](NUMERIC_CHECK.md), [prospective protocol](../../research_plans/receiver_incentives_v18/PROTOCOL.md), [pre-participant amendment](../../research_plans/receiver_incentives_v18/AMENDMENT_002.md).

The original participant-source freeze remains unchanged. Reporting and recovery utilities were added after collection and are identified as such. The archive retains first provider outputs and private model-written notes separately; public reports are not faithful access to hidden cognition. No Codex or Claude Code product supplies experimental audit labels.
