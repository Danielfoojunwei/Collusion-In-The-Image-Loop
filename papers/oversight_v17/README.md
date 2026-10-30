# Beyond Detection manuscript and explanation

**Beyond Detection: Evaluating Collusion Oversight in Multi-Turn Software Engineering Agents**

Prospective research protocol, version 0.5, 30 October 2026.

- [Read the compiled paper](manuscript.pdf).
- [Understand the setup step by step](UNDERSTANDING_THE_PAPER.md).
- [Edit the LaTeX source](manuscript.tex).
- [Read searchable extracted text](manuscript.txt).
- [Inspect the build checks](BUILD_VALIDATION.json).
- [See the underlying research design](../../research_plans/oversight_v17/README.md).

The manuscript connects recruitment, information transfer, knowing participation,
actual patch approval, monitor attribution, intervention and adaptation. Four
studies isolate receiver incentives, evidence access, live policy effects and
held-out transfer. The candidate task source is SWE-Bench Pro V2 Hard, with actual
tool-using implementation and independent review across up to three repair cycles.

No new SWE-bench run, monitor evaluation or intervention result is reported.
Published prior work motivates the design; it is not pooled into new findings.
The illustrative timestamp issue in the appendix and guide is invented for
explanation and is not a selected benchmark instance.

## Rebuild offline

Use Python with PyMuPDF (`fitz`) and a TeX Live installation including `pdflatex`,
Latin Modern, TikZ/PGF, `microtype`, `booktabs`, `tabularx`, `xurl`, `hyperref` and
`fancyhdr`. In Debian/Ubuntu these typically come from `texlive-latex-extra`,
`texlive-pictures` and `lmodern`; package splits vary by platform.

From the repository root:

```bash
python -m unittest discover -s papers/oversight_v17 -p 'test_build.py' -v
python papers/oversight_v17/build.py
```

The builder imports no experiment modules and makes no network/API calls. It
checks the machine-readable study arithmetic, compiles twice, rejects unresolved
references and overfull boxes, extracts readable text and verifies page boundaries
and protocol-status footers. `BUILD_VALIDATION.json` records hashes of the source,
study plan and PDF. It does not certify experimental reliability, reference-paper
truth, novelty, statistical power or future results.

## Decisions to bind before data collection

Exact task/model revisions, actor/monitor prompts, resource and command limits,
sanctions and grant cancellation semantics, correctness-reward rules, acceptance
and false-sanction tolerances, sample sizes and the current total budget remain
release gates. Task difficulty must be checked on disjoint development tasks.
Hidden evaluation outcomes and oracle-dependent running reward balances remain
outside actor feedback until cohort completion.

The old synthetic-task cost estimate is superseded by the long coding workflow.
No new spending or release of a historical stop marker is authorized by this paper.

## Source-grounded implementation update

The26-paper [registry and build status](../../research_plans/oversight_v17/RESEARCH_BUILD_STATUS.md) document the new development work,38 passing harness tests and one real OpenRouter critique at$0.0012077648. These are separate from empirical findings. The benchmark image installation failed for storage capacity; local gold/empty gates and full runtime integration have not passed. Collective fines are restored as the V15/V16 reference; individual fines remain a prospective comparison.

## Frontier API reviewer amendment

The primary reviewer is now GPT-6 Astra Pro through OpenRouter, max effort/Pro mode, with actual image input and scoped evidence retrieval. No Codex/Claude Code reviewer is used. [The access contract](../../research_plans/oversight_v17/REVIEWER_ACCESS.md) documents context, real-world observation conditions and API billing.53 harness tests and five paper-contract tests pass; no paid reviewer call has run. The static monitor plan is480 jobs at highest effort, replacing the former960-job effort factorial.
