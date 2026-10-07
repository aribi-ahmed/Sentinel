# ADR-009: Tool registry with a per-call audit trail

**Status:** Accepted
**Date:** 2026-08-22
**Objectives:** M-04 (typed tool registry); M-12 (tests); rubric — traceability, code quality

## Context

SENTINEL calls five external tools: OFAC/OpenSanctions screening, SEC EDGAR,
market data, open-web search, and retrieval over the policy corpus. Until now
each specialist node imported its tools directly and called them in whatever way
that tool happened to expose:

```python
edgar   = fetch_edgar_profile(subject_name=subject, ticker=ticker)   # plain call
data    = fetch_company_financials.invoke({"ticker": ticker})        # LangChain protocol
results = search_company_news.invoke({"query": subject})             # LangChain protocol
brief   = build_compliance_brief(query=query, subject=subject, ...)  # plain call
screening = screen_entity(subject)                                   # plain call
```

Three problems followed from this, and none of them is stylistic.

**1. A tool's failure was indistinguishable from its data.** `search_company_news`
returns a *string* in every case, including its own failures:

```python
if not tavily_key:
    return f"OSINT Warning: TAVILY_API_KEY missing. Evaluated query: '{query}'."
```

The news node stored that sentence as `news_data` and passed it downstream. A run
with no search key produced an investigation that looked complete and reported no
adverse media — not because none was found, but because nothing was ever searched.
This is the same failure mode as the RAG pipeline in
[ADR-005](ADR-005-compliance-corpus-and-embeddings.md), reappearing in a different
tool.

**2. No call was recorded.** §5.1 requires every claim to trace to a source, and
the evidence pool satisfies that for *facts*. It does not cover the *act of
collection*. "The entity was screened and is clear" is unfalsifiable without a
record of which name was screened, against which lists, and when. A reviewer
disputing a verdict had nothing to inspect.

**3. Nothing enforced the boundary.** Two modules — `agents/compliance_agent.py`
and `agents/financial_agent.py` — still imported tool modules directly. Both were
dead code, and one had stopped being importable at all: it referenced
`query_compliance_policy`, which no longer exists in `tools/rag.py`. Neither was
noticed because nothing looked.

## Options considered

1. **Wrap the tools in a LangChain agent-callable toolkit and let a model choose.**
   Rejected. It would add nondeterminism to the one part of the pipeline that is
   currently reproducible, and the specialists have fixed responsibilities — there
   is no routing decision here for a model to make. The routing that *does* exist
   is the supervisor's, and it is already explicit
   ([ADR-002](ADR-002-durable-checkpointing.md) records the state it runs on).
2. **Add logging inside each tool.** Puts the audit obligation on five authors
   instead of one place, and a tool added later would silently not be audited.
3. **A registry that owns the call path**, with each tool declaring how to read
   its own result.

## Decision

**Option 3.** `tools/registry.py` is the single way a tool is invoked. Each tool
is registered as a `ToolSpec` declaring four things beyond the callable itself:

| Field | Why it is declared per tool |
| --- | --- |
| `classify` | Only the tool's author knows what its "found nothing" looks like. |
| `availability` | A missing key or dataset is knowable *before* the call, not after it fails. |
| `fallback` | The shape returned when a call cannot run, so callers never branch on `None`. |
| `data_source` | Names the upstream, for the catalogue and the report. |

`registry.invoke()` records a `ToolInvocation` for every call — arguments,
duration, outcome, and the evidence ids it produced — and appends it to the
investigation's `tool_calls` channel, which uses the same `operator.add` reducer
as `evidence` so the four parallel specialists can write concurrently.

Two decisions inside that are worth stating explicitly:

**Outcomes are four-valued, not two.** `OK`, `EMPTY`, `FAILED`, `UNAVAILABLE`.
The distinction that matters is `EMPTY` versus `UNAVAILABLE`: a screening that
ran and found nothing is a real answer, and a screening that never ran is not.
Collapsing them is exactly how a system reports confidence it has not earned.

**Arguments are redacted, not dropped.** An audit trail without its inputs cannot
answer "what did we screen?"; one that copies an API key into the database is a
liability. Secret-looking names are masked by substring, so `groq_api_key` is
caught by the `api_key` rule without the tool's author having to declare it.

The boundary is enforced by `test_only_the_registry_imports_a_tool_module` in
`tests/unit/test_architecture.py`, which parses every shipped module with `ast`
and fails if anything outside the registry imports a tool module.

## Consequences

* **Positive.** A missing search key now produces `UNAVAILABLE` with a reason on
  the record, and the node files no evidence, instead of passing a warning string
  to the model as though it were reporting.
* **Positive.** The evidence chain closes. `ToolAuditTrail.attribute` links each
  call to the evidence it produced, so a reviewer can walk
  verdict → factor → evidence → the exact call and arguments behind it.
* **Positive.** `GET /tools` reports live availability per tool. Deployment gaps
  were previously invisible from the verdict, which looked identical either way.
* **Positive.** The first run through the registry immediately surfaced something
  that had been invisible: sanctions screening reports `1/2 providers`, because
  `OPENSANCTIONS_API_KEY` is unset and only the local OFAC list is consulted.
  That is a deliberate optional dependency, not a fault — but a `CLEAR` from one
  list is a weaker statement than a `CLEAR` from two, and until now nothing in
  the output said which had happened. The digest now records it on every call.
* **Negative.** One more indirection between a node and its tool. Accepted: the
  uniform call path is what makes the trail complete rather than best-effort.
* **Note.** The architecture test found the two dead agent modules on its first
  run — the second time an architecture test has found a real defect by itself
  (the first was [ADR-008](ADR-008-retire-prototype-entry-point.md)). They are
  retired in `docs/legacy/`.
* **Open.** The trail is persisted through the LangGraph checkpointer (Postgres),
  not as its own SQL table. That is durable and survives restart, but it means
  the audit trail is queryable per investigation rather than across them. A
  dedicated table would be the next step if cross-investigation tool analytics
  were ever needed.
