# Architecture Decision Records

One page per significant decision: context, options considered, the decision, and
its consequences. Superseded records stay — the reasoning that turned out wrong is
usually more instructive than the reasoning that turned out right.

| # | Decision | Status |
| --- | --- | --- |
| [001](ADR-001-initial-technology-stack.md) | Initial technology stack | Accepted, partially superseded |
| [002](ADR-002-durable-checkpointing.md) | Durable checkpointing in PostgreSQL | Accepted |
| [003](ADR-003-framework-free-domain.md) | A framework-free domain, enforced by test | Accepted |
| [004](ADR-004-provider-agnostic-llm-gateway.md) | A real provider-agnostic LLM gateway | Accepted |
| [005](ADR-005-compliance-corpus-and-embeddings.md) | Dedicated compliance corpus with local embeddings | Accepted |
| [006](ADR-006-composite-risk-scoring.md) | Composite risk scoring with deterministic anchors | Accepted |
| [007](ADR-007-evidence-citation-by-kind.md) | Factors cite evidence by kind, not by model self-report | Accepted |
| [008](ADR-008-retire-prototype-entry-point.md) | Retire the prototype entry point | Accepted |
| [009](ADR-009-tool-registry-and-audit-trail.md) | Tool registry with a per-call audit trail | Accepted |
| [010](ADR-010-critic-with-bounded-revision.md) | An anchored critic with a bounded revision loop | Accepted |
| [011](ADR-011-second-provider-and-failover.md) | Hugging Face as the second provider, failover measured honestly | Accepted |

## Recurring themes

**A rule that is not enforced decays into a comment.** ADR-001 recorded PostgreSQL,
Redis and a provider gateway; none of the three was true in code months later.
Every boundary asserted since then names the test that fails when it is crossed.

**Do not ask a model to certify its own provenance.** Both [005](ADR-005-compliance-corpus-and-embeddings.md)
and [007](ADR-007-evidence-citation-by-kind.md) began by asking the model which
source it had used, and both were changed to resolve the citation deterministically
after it cited documents it had merely seen mentioned. [010](ADR-010-critic-with-bounded-revision.md)
applies the same rule to review: the critic is six deterministic checks plus one
narrow, anchored model question - never "is this assessment good?".

**Degrade loudly.** Postgres, Redis and the compliance corpus can all be absent.
In each case the system continues and reports the reduced guarantee at
`GET /system` — because the original failure here was a retrieval path that fell
back to web scraping without anyone noticing. [009](ADR-009-tool-registry-and-audit-trail.md)
generalises the rule: a tool that never ran and a tool that ran and found nothing
are different outcomes, and a system that conflates them reports confidence it
has not earned.
