# ADR-001: Initial technology stack

**Status:** Accepted — partially superseded by [ADR-002](ADR-002-durable-checkpointing.md), [ADR-004](ADR-004-provider-agnostic-llm-gateway.md) and [ADR-008](ADR-008-retire-prototype-entry-point.md)
**Date:** August 2026

## Context

We needed to choose the orchestration framework, persistence and model access for
SENTINEL before any agent existed.

## Decision

* **Orchestration:** LangGraph — cyclic graphs, shared state, and first-class
  interrupts for human-in-the-loop.
* **LLM access:** LangChain chat models, Groq primary and Ollama fallback.
* **Persistence:** PostgreSQL for structured findings; Redis for caching.
* **API / UI:** FastAPI for the REST surface; Streamlit for a rapid prototype UI.

## Consequences

* Positive: LangGraph natively supports the supervisor/specialist topology the
  brief specifies, and its checkpointer is the mechanism behind objective M-10.
* Negative: requires defining the shared `GraphState` schema early and
  deliberately, because every agent depends on it.

## What actually happened

Recorded here rather than edited away, because the gap between this decision and
the code is the more instructive half of the record.

| Decided | Reality until 2026-08-20 | Corrected by |
| --- | --- | --- |
| PostgreSQL as system of record | SQLite file; Postgres declared in compose but unused | [ADR-002](ADR-002-durable-checkpointing.md) |
| Redis for caching | Never imported anywhere in the codebase | [ADR-004](ADR-004-provider-agnostic-llm-gateway.md) |
| "LLM gateway" | A factory function returning a LangChain object — no retries, fallback, accounting or cache | [ADR-004](ADR-004-provider-agnostic-llm-gateway.md) |
| Streamlit prototype UI | Replaced by a React console; the Streamlit-era file lingered and broke the provider boundary | [ADR-008](ADR-008-retire-prototype-entry-point.md) |

The lesson carried into later ADRs: a decision recorded but not enforced decays
into a comment. Where a later ADR states a boundary, it also names the test that
fails when the boundary is crossed.
