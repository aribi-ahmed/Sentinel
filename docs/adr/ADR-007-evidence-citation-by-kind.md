# ADR-007: Factors cite evidence by kind, not by model self-report

**Status:** Accepted
**Date:** 2026-08-21
**Objectives:** M-07 (evidence references), M-04 (tool results in the audit trail)

## Context

[ADR-003](ADR-003-framework-free-domain.md) gave evidence an identity. The
remaining question was how a `RiskFactor` comes to hold the ids of the evidence
it rests on.

§11 asks that "every score [is] traceable to factors, findings and cited
evidence". A citation that is merely *plausible* fails that test as surely as no
citation at all — arguably worse, because it looks like provenance.

## Options considered

1. **Ask the model which evidence it used.** Natural, and it reads well. But the
   model can cite evidence it did not use, or invent an id. We had already been
   bitten by exactly this in retrieval: asked to name its source, the model cited
   a document title it had seen inside a footnote rather than the passage it read
   ([ADR-005](ADR-005-compliance-corpus-and-embeddings.md)). A citation that
   *might* be true is not provenance.
2. **A separate model pass to link factors to evidence.** More reliable than (1),
   but it spends a model call to reconstruct a relationship the system already
   knows, and can still hallucinate.
3. **Map factors to evidence deterministically by kind.**

## Decision

**Option 3.** Each factor cites the evidence that, by its nature, bears on that
dimension:

| Factor | Cites evidence of kind |
| --- | --- |
| `sanctions` | `watchlist` |
| `legal_regulatory` | `filing`, `policy` |
| `financial` | `market_data` |
| `reputational` | `open_source` |
| `governance` | `policy`, `baseline` |

The link is therefore **always true**: the financial score is computed from market
data, so citing the market-data records is a statement of fact rather than a
claim about the model's reasoning.

Evidence is created by `services/evidence_builder.py`, which normalises each
tool's output and — importantly — assigns confidence by *provenance*:

* an SEC filing is `HIGH`, because the entity said it about itself;
* a press article is `MEDIUM` at best, because coverage is not a finding;
* the model's own baseline summary is `LOW`, because it is interpretation.

Without that distinction the scorer would weigh rumour and disclosure equally.

## Consequences

* **Positive.** Every factor in a live run is grounded — verified at 15 evidence
  records for Alphabet, 20 for Wolfspeed, zero ungrounded factors.
* **Positive.** `RiskAssessment.ungrounded_factors()` reports honestly when a
  dimension had nothing to stand on, which is the hook a critic agent uses in
  Phase 5. The UI shows an amber `ungrounded` tag when it happens.
* **Positive.** No extra model call, and no way for a citation to point at
  evidence that was never collected.
* **Negative.** Coarser than a true per-claim citation: the factor cites *all*
  evidence of the relevant kinds, not the specific records that moved the number.
  A finer link belongs with the `Finding` layer, which is modelled but not yet
  populated by the agents.
* **Negative.** The mapping is a design assertion. If a new evidence kind is added
  without a factor mapping, its records are collected but cited by nobody — worth
  a test in a later iteration.
