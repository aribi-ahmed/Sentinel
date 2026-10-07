# ADR-010: An anchored critic with a bounded revision loop

**Status:** Accepted
**Date:** 2026-08-22
**Objectives:** brief phase 5 (critic); M-07 (explainability); rubric — traceability, agentic design

## Context

Every component built so far exists to *produce* a verdict. Nothing existed to
**doubt** one. That asymmetry has a predictable consequence: the system will
eventually produce a confident wrong answer, and give the officer reading it no
reason to look twice.

The gap was not hypothetical. Three defects had already been found by hand, each
one after it had shipped:

* the model cited a source it had only seen mentioned in a footnote (ADR-007);
* every entity scored ELEVATED, because a binary prompt had no middle (ADR-006);
* a `CLEAR` sanctions status was produced by a screening that never ran, because
  `search_company_news` and friends report their own failures as ordinary data
  (ADR-009).

All three are the same shape: **a verdict that is internally coherent and
externally unsupported.** A reviewer who read the verdict alongside the evidence
would have caught each one in seconds.

## The problem with the obvious implementation

The obvious critic is one model call: *"here is an assessment, is it good?"*

That does not work, and it fails in the way that is hardest to notice. A model
asked to review another model's output produces fluent, plausible, unfalsifiable
commentary — and it produces it whether or not anything is wrong. It would
endorse the ADR-007 verdict, because the fabricated citation *reads* correctly.
It is the same mistake as asking a model to certify its own provenance, which
this project has now made and corrected twice.

## Options considered

1. **One model call reviewing the whole assessment.** Rejected above.
2. **Deterministic checks only.** Safe and cheap, but blind to the class of
   defect that matters most: a narrative claiming more than its evidence shows.
   No amount of schema validation catches "going-concern doubts in filings" when
   the filings say no such thing.
3. **Deterministic checks, plus one narrow model question.**

## Decision

**Option 3.** Seven checks, six of them deterministic:

| Check | Severity | Catches |
| --- | --- | --- |
| `dangling_citation` | blocking | A factor citing evidence that is not in the pool (the ADR-007 regression) |
| `band_mismatch` | blocking | A band that does not follow arithmetically from its score |
| `unverified_scope` | blocking / advisory | A **reassuring** score produced by a tool that never ran |
| `ungrounded_factor` | material / advisory | A scored factor resting on judgement alone |
| `thin_evidence` | material | An ELEVATED-or-worse verdict with no high-confidence evidence behind it |
| `overconfident` | material / advisory | Confidence outrunning the work that was actually done |
| `unsupported_driver` | material | *(model-judged)* A stated driver the cited evidence cannot support |

Three things make this trustworthy rather than decorative.

**The critic reads the record, not the author's memory.** `critic_node`
reconstructs the verdict through `RiskAssessment.from_dict` from graph state
rather than receiving the supervisor's objects. A reviewer sharing the author's
working memory is not independent, and the round trip also catches a verdict
that holds together in memory but not in what was persisted.

**The model gets one narrow question, and its answer is anchored.** It is asked
only *which of these stated drivers does this evidence fail to support*, and
every driver it objects to is matched back against the assessment's own
`key_drivers` list. It cannot invent an objection to a claim the verdict never
made. If the provider is unreachable the review still stands on the other six
checks, with `model_ok: false` recorded.

**`unverified_scope` is the check that only exists because of
[ADR-009](ADR-009-tool-registry-and-audit-trail.md).** A sanctions dimension
scoring 10/100 because the list was screened and came back clean, and one
scoring 10/100 because no list was ever consulted, are *identical* in the
assessment. They differ only in the tool audit trail. This check reads the trail,
finds tools that ended `UNAVAILABLE` or `FAILED`, and blocks when the dimension
they feed scored reassuringly — absence of evidence being read as evidence of
absence. It is the single most dangerous verdict shape the system can produce,
and until the audit trail existed nothing could see it.

### The loop

`supervisor → critic → {revise → supervisor | human_approval}`, bounded at
**one** revision by `MAX_REVISIONS`.

Three deliberate choices:

* **Only `rejected` triggers a revision.** A `qualified` verdict is reported, not
  re-run; re-scoring over an advisory would burn tokens to change nothing.
* **The counter is incremented in its own `revise` node**, not as an edge
  side-effect, so the increment is a state write the checkpointer records. A
  resume after a crash otherwise restarts the loop with a counter that never
  moved — which is exactly how a bounded loop becomes unbounded.
* **An unresolved rejection still reaches the human.** The gate is the backstop.
  Suppressing a verdict the system could not fix would hide the disagreement
  rather than surface it, and SENTINEL is advisory by design.

On a revision the critique is put in front of the model, framed to *answer* the
objections rather than to lower the score:

> Do not change a score you can defend — agreeing with the reviewer is not the
> goal, being supportable is.

A critic that reliably pushes scores downward is a bias, not a control.

### Confidence

The critic may lower confidence and never raise it — enforced in
`CriticReview.__post_init__`, which rejects a negative penalty. Blocking
challenges deduct 0.15, material 0.05, advisories nothing, capped at 0.40 so a
pile of minor objections cannot zero a verdict. Anything short of `endorsed`
also forces `requires_human_review`, whatever the score says.

## Consequences

* **Positive.** On the first live run against Wolfspeed the critic raised three
  material objections, all correct. The sharpest: the verdict claimed
  *"high leverage and going-concern doubts in filings"* when the evidence
  established only that debt exceeds market cap — no filing language about going
  concern was ever retrieved. That is precisely the unsupported embellishment
  this ADR exists to catch, and it had been shipping unremarked.
* **Positive.** The same run exposed a second problem the critic was not built
  for: the supervisor reported **confidence 1.0**. No evidence base assembled
  from filings, market data and open-source reporting earns certainty. A
  `CERTAINTY_CEILING` advisory was added as a result.
* **Positive.** Every objection carries a `remedy`, so the panel tells the
  officer what would resolve it rather than only what is wrong.
* **Negative.** One extra model call per investigation, and up to two supervisor
  calls when a revision fires. Measured at roughly +25% tokens on a full run.
  Accepted: the cost of the check is small against the cost of a wrong verdict
  reaching a compliance decision.
* **Negative.** The critic cannot detect a defect that is *consistent* across
  both the verdict and the evidence — if a specialist filed a wrong fact, the
  critic will confirm the verdict rests on it. It checks support, not truth.
* **Note.** `use_model=False` runs the entire critic offline, which is what lets
  the 39 unit tests cover every check exhaustively without a network.
