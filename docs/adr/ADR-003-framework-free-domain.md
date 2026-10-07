# ADR-003: A framework-free domain, enforced by test

**Status:** Accepted
**Date:** 2026-08-20
**Objectives:** M-07 (explainable scoring); rubric — architecture, extensibility

## Context

Business concepts lived inside the frameworks that happened to carry them. Risk
scores were dictionaries assembled in `services/risk.py`; an investigation was a
SQLAlchemy row; evidence did not exist as a concept at all, only as prose inside
a rationale string.

Three consequences followed:

* **M-07 was structurally impossible to complete.** The objective asks for a score
  decomposed into factors, *each with evidence references*. There was nothing to
  reference — no evidence had an identity.
* **Unit tests needed a database.** Testing the scoring maths meant constructing
  ORM objects, so the cheap deterministic tests §10.3 recommends were not cheap.
* **The dependency rule (§10.6) could not be checked.** Its stated test — *"you
  could replace FastAPI, or swap pgvector for Chroma, and `domain/` would not
  change by a single line"* — cannot be run against a `domain/` that does not exist.

## Options considered

1. **Keep dictionaries, add a JSON schema.** Cheapest. Gives validation but no
   behaviour, no invariants, and no place for `RiskBand.for_score` to live.
2. **ORM-mapped domain classes.** One model instead of two. But it inverts the
   dependency rule — the domain would import SQLAlchemy, and every unit test
   would need a session.
3. **Plain dataclasses in `domain/`, mapped explicitly in `repositories/`.**
   Costs a translation layer; buys a domain that compiles on its own.

## Decision

**Option 3.**

* `sentinel/domain/` — `Evidence`, `EvidencePool`, `Finding`, `RiskFactor`,
  `RiskAssessment`, `Investigation`, `HumanDecision`. Frozen dataclasses and
  enums, **standard library only**.
* `sentinel/repositories/` — an `InvestigationRepository` Protocol in domain
  language, with a SQLAlchemy implementation and an in-memory one. `sql.py` is
  the only module that knows both the domain and the schema.
* Invariants live with the data: a `RiskFactor` cannot hold a score outside
  0–100 or a weight outside 0–1; `Evidence` without a source raises.
* Transitions return new instances, so a checkpointed snapshot cannot be
  invalidated by a later mutation.

**The rule is enforced by `tests/unit/test_architecture.py`**, which parses every
module with `ast` and fails if the domain imports a framework, if the domain
reaches into another `sentinel` package, or if the repository contract mentions
a storage engine. Nothing is imported to run it, so it is fast and cannot be
fooled by import side effects.

## Consequences

* **Positive.** M-07's chain is now navigable: `RiskAssessment → RiskFactor →
  Evidence → source`, and `ungrounded_factors()` names any factor resting on
  judgement alone — the hook a critic agent will use in Phase 5.
* **Positive.** 49 domain tests run with no database, no model and no network.
* **Positive.** The in-memory repository is not a toy: the same contract test body
  runs against it and against Postgres, so the fake cannot be more permissive
  than production.
* **Negative.** Two models to keep aligned, and an explicit mapping to maintain.
  Accepted deliberately: the alternative is a domain that cannot be tested
  without infrastructure.
* **Note.** `Protocol` rather than `ABC`, so an implementation does not inherit
  anything — a test fake is just a class with the right methods.
