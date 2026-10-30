# Prospective oversight harness foundation

Read [the research/build status](../../research_plans/oversight_v17/RESEARCH_BUILD_STATUS.md) and [premise map](../../research_plans/oversight_v17/PREMISE_ALIGNMENT.md) first. This code does not resume historical runs. The full experimental controller, reward escrow and runtime sandbox/verifier bridge are not yet integrated; no claim of end-to-end readiness.

From the repository root, with Python3.11+ and the pinned Pillow dependency:

```bash
python -m unittest discover -s tests/oversight_v17 -v
```

The tests use labelled deterministic fixtures, including fixture images and fixture context identifiers. They are not experimental reviews or empirical findings. No test dispatches a network request or executes a model command on the host.

`finance.py`/`reconcile.py` retain historical liabilities and ambiguous reservations. `openrouter.py` archives each first response and never retries a paid identity. `preflight.py` makes free metadata requests. `develop.py` performed the one development critique recorded in the status; do not rerun it as a compatibility sweep. Its$0.10 allocation is a subcap within the old caps.

`events.py`/`workflow.py` provide hash-linked events and the phase/role graph. `actor.py` provides a stateful forced-function loop that receives a sandbox broker; it never substitutes a host shell. `benchmark.py` projects only issue ID/instruction to actors and blocks launch without the real gates. `sandbox.py` constructs restrictive local-Docker commands and a capacity probe; it does not claim that constructing commands verifies their runtime isolation.

`documents.py` typesets substantial readable body text with the genuine generator figure, recording the original and transformed pixel hashes. `images.py` rejects missing or invalid PNGs. `monitor.py` projects public evidence and deterministic bounded feedback; fresh product-context provenance still needs integration. Exact prospective strings are in `prompts.py`; compensation amounts and final experiment freeze remain to be bound.

The original database is opened read-only for reconciliation. Private runtime files, API headers, raw source-PDF passages and actor secrets belong outside Git. Public development receipts contain no authorization headers. See `research_plans/oversight_v17/development_evidence/` for the sanitized first output/cost and infrastructure failure.
