# ADR-006: Composite risk scoring with deterministic anchors

**Status:** Accepted
**Date:** 2026-08-20
**Objectives:** M-07 (explainable, weighted, decomposed scoring)

## Context

The supervisor asked the model for a verdict in one prompt:

> Classify risk strictly as "LOW" or "ELEVATED".

Every target came back **ELEVATED**. A verdict that never varies carries no
information, and the cause was in the framing rather than the model:

* **No middle.** A large profitable firm with ordinary antitrust litigation had
  nowhere to land but the top of a two-point scale.
* **Tone leaks into judgement.** Adverse press pushes a language model toward
  caution, so critical coverage scored like a regulatory finding.
* **Nothing was anchored.** Alphabet's balance sheet — P/E 17, debt at 2.9 % of
  market cap, 55 % margins — could not pull the verdict down, because no part of
  the score was computed from it.

## Options considered

1. **Better prompt, same binary output.** Cheapest, and it would help. But the
   verdict would still rest entirely on model judgement, with nothing to anchor it.
2. **Fully deterministic rules.** Auditable and stable, but unable to weigh a
   litigation narrative, which is most of what an analyst actually reads.
3. **A weighted composite: hard numbers computed, judgement scored to a rubric.**

## Decision

**Option 3.** Five weighted dimensions, banded into five levels:

| Dimension | Weight | Source |
| --- | ---: | --- |
| Sanctions & watchlists | 30 % | computed — OFAC/OpenSanctions screening |
| Legal & regulatory | 25 % | model judgement, blended with SEC filings |
| Financial health | 20 % | computed — market metrics |
| Reputational | 15 % | model judgement |
| Governance & controls | 10 % | model judgement |

Bands: `MINIMAL <20 · LOW <40 · MODERATE <60 · ELEVATED <80 · SEVERE ≥80`.

Three mechanisms keep it honest:

* **Computed dimensions cannot drift with tone.** Financial and sanctions scores
  come from arithmetic and lookups. Sanctions weighs most because a designation
  is a legal bar to dealing at all, not a matter of degree.
* **A written rubric with worked anchors** for the judgement dimensions —
  explicitly: *"antitrust suits and privacy fines are ORDINARY for a large,
  profitable technology firm; on their own they belong in the 25–45 range, not
  above 60"*, and mitigating factors must be named, not only aggravating ones.
* **Filings outrank press.** SEC disclosures blend into the regulatory dimension
  at 60/40, and the worst filing sets a **floor** — a disclosed restatement is
  not a matter of opinion, so no narrative can score beneath it.

**The escalation rule.** Weighting alone produced a wrong answer we caught in
testing: Wolfspeed — three Form 8-K Item 1.03 bankruptcy filings and a delisting
notice — scored **56.4 MODERATE**, because a clean 30 %-weighted sanctions score
diluted it. A company in Chapter 11 must not read MODERATE. So when hard evidence
reaches 70+, the composite is lifted to 75 % of it rather than averaged away:

```
composite = max(weighted_average, 0.75 × hard_evidence)   when hard_evidence ≥ 70
```

Wolfspeed now reads **67.5 ELEVATED**, and the panel states why. Both figures are
kept — `weighted_average` alongside `score` — so the lift is visible, not hidden.

## Consequences

* **Positive.** The scale discriminates. Verified live: Alphabet 24.5 LOW,
  Lucid 40.7 MODERATE, Wolfspeed 67.5 ELEVATED, Rosneft 72.8 ELEVATED.
* **Positive.** Confidence is derived from how many dimensions could actually be
  assessed, so thin evidence lowers certainty rather than silently guessing.
* **Positive.** `compose()` is a pure function, tested exhaustively at every band
  boundary.
* **Negative.** Weights and the escalation constants are judgement calls with no
  empirical backing. They are configuration, and Phase 5's golden dataset is the
  intended way to calibrate them.
* **Negative.** More prompt engineering to maintain, and the JSON contract with
  the model can fail — handled by falling back to computed dimensions only and
  reducing confidence, never by inventing a score.
