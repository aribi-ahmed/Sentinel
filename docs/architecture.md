# SENTINEL — architecture

A living document. When it disagrees with the code, the code is right and this
file is a bug.

Decisions and their reasoning live in [`adr/`](adr/); this file describes the
shape that resulted.

---

## 1. The problem the shape solves

An investigation is not a pipeline. It is a **stateful, cyclic, long-running
process**: work fans out to independent specialists, converges for scoring,
pauses indefinitely for a human, and must survive a restart while it waits.

Three properties follow, and everything else is a consequence of them:

| Property | Mechanism |
| --- | --- |
| Independent work runs concurrently | LangGraph parallel branches with reducer-merged state |
| A pause can outlive the process | Checkpoints written to PostgreSQL after every super-step |
| Every claim is auditable | Typed `Evidence` records with stable ids, cited by each risk factor |

## 2. Layers

Dependencies point inward. Nothing in an inner layer knows an outer one exists.

```
┌──────────────────────────────────────────────────────────────┐
│ INTERFACE          api/            FastAPI · SSE · OpenAPI   │
│                    sentinel-ui/    React review console      │
├──────────────────────────────────────────────────────────────┤
│ APPLICATION        graph/          LangGraph topology, state │
│                    services/       scoring · evidence · report│
├──────────────────────────────────────────────────────────────┤
│ DOMAIN             domain/         Evidence · Finding        │
│                                    RiskFactor · Investigation│
│                                    (standard library only)   │
├──────────────────────────────────────────────────────────────┤
│ INFRASTRUCTURE     llm/            gateway + provider adapters│
│                    repositories/   persistence behind Protocol│
│                    tools/          EDGAR · sanctions · RAG   │
│                    database/       SQLAlchemy models, session │
└──────────────────────────────────────────────────────────────┘
```

**Why the domain is framework-free.** So the scoring rules can be tested without
a database, and so storage and delivery stay replaceable. The practical test —
*"swap FastAPI or the vector store and `domain/` does not change"* — is run on
every commit by `tests/unit/test_architecture.py`, which parses the source with
`ast` and fails on a forbidden import.

## 3. The graph

```
                            START
                              │
        ┌──────────┬──────────┼──────────┬──────────┐
        ▼          ▼          ▼          ▼          │  parallel
   research   financial    OSINT    compliance      │  branches
        │          │          │          │          │
        └──────────┴────┬─────┴──────────┘          │
                        ▼   fan-in: reducers merge evidence + logs
                   supervisor      scores 5 weighted dimensions
                        │
                        ▼
              ‖ human_approval ‖   ← interrupt_before; state persisted
                        │
              approved ─┴─ rejected
                 │              │
              summary       cancelled
                 └──────┬───────┘
                        ▼
                       END
```

**State** (`graph/state.py`) is one typed `TypedDict`. Two channels are written
concurrently by all four specialists and so carry an `operator.add` reducer:

```python
logs:     Annotated[List[str], operator.add]
evidence: Annotated[List[Dict[str, Any]], operator.add]
```

Without the reducer, LangGraph raises on concurrent updates to the same channel.
Everything else is written by exactly one node, so last-value-wins is correct.

**The interrupt** is static: `interrupt_before=["human_approval"]`. The graph
stops, the API returns, and the checkpoint sits in Postgres until a decision
arrives on the same thread.

## 4. Agents

| Agent | Gathers | Model profile |
| --- | --- | --- |
| Research | Corporate baseline; SEC EDGAR filing history | `FAST` |
| Financial | Market cap, P/E, debt, free cash flow, margins | none (deterministic) |
| OSINT | Open-web coverage, classified and scored | none (deterministic) |
| Compliance | Policy obligations via RAG; OFAC/OpenSanctions screening | `EXTRACTION` |
| Supervisor | — scores the evidence into a verdict | `REASONING` |

Agents never call each other. They communicate only through shared state, which
is what makes parallelism and checkpointing safe.

Two agents use no model at all: their value is deterministic lookup, and an LLM
would only add latency and a hallucination surface.

## 5. Scoring

Five weighted dimensions produce a 0–100 composite in one of five bands.

```
sanctions 30%  ─┐   computed from screening
legal     25%  ─┤   model judgement, floored by SEC filings
financial 20%  ─┼─► weighted sum ─► escalation check ─► band
reputation15%  ─┤   model judgement
governance10%  ─┘   model judgement
```

Two mechanisms stop a categorical finding from being averaged away:

* **A filing floor.** The worst disclosed event sets a minimum for the regulatory
  dimension — a restatement is not a matter of opinion.
* **Escalation.** When hard evidence reaches 70+, the composite is lifted to 75 %
  of it. Without this, a company in Chapter 11 scored MODERATE because it was not
  also sanctioned. Both the lifted score and the plain weighted average are kept,
  so the adjustment is visible.

See [ADR-006](adr/ADR-006-composite-risk-scoring.md).

## 6. The evidence chain

```
Evidence            atomic sourced fact: summary, source, collector,
                    confidence, id (ev_9f3a1c2b)
   ▲
   │ cited by kind (ADR-007)
   │
RiskFactor          named, weighted contributor; carries evidence_ids
   ▲
   │
RiskAssessment      composite score, band, drivers, mitigants
```

Confidence reflects **provenance, not severity**: an SEC filing is `HIGH` because
the entity said it about itself; a press article is `MEDIUM` at best; the model's
own summary is `LOW`. Without that distinction the scorer would weigh rumour and
disclosure equally.

`RiskAssessment.ungrounded_factors()` names any factor resting on judgement
alone — the hook a critic agent will use, and an amber tag in the console today.

## 7. The LLM gateway

The only door to a model.

```
complete(prompt, profile, caller)
    │
    ├─ cache lookup (Redis)                        hit → return, 0 ms, no cost
    │
    ├─ primary provider    ── retry ×N, jittered backoff
    │     └─ non-retryable? skip straight to fallback
    │
    └─ fallback provider   ── retry ×N
              └─ all failed → AllProvidersFailed(per-provider reasons)
```

Callers ask for a **profile** (`FAST` / `REASONING` / `EXTRACTION`), never a model
name. Every call is recorded in a usage ledger keyed by calling agent, which is
what lets the console attribute tokens and cost per agent.

Only `llm/adapters/*_adapter.py` may import a provider SDK — enforced by test.

### Providers

Two are wired: **Groq** (hosted, free tier) and **Hugging Face** (serverless).
Switching is `LLM_PROVIDER` and nothing else — `scripts/verify_m01.py` runs the
same investigation on each and compares the verdict. Only three files import a
provider SDK, enforced by `test_only_the_gateway_imports_a_provider_sdk`.

`fallback_used` is measured against the *configured* primary rather than the
surviving adapter list: a primary dropped for being unreachable is still a
primary that did not serve the request. Usage is attributed per provider as well
as per caller, so `GET /system` shows which provider actually answered rather
than which was configured. See [ADR-011](adr/ADR-011-second-provider-and-failover.md).

## 8. Persistence

| Store | Holds | Why there |
| --- | --- | --- |
| PostgreSQL | Investigations, reports, human decisions, **graph checkpoints** | Relational integrity; checkpoints share the durability story of the records they describe |
| Redis | LLM response cache | Ephemeral by nature; free tiers are rate-limited |
| Chroma (`chroma_compliance/`) | 500 policy passages + embeddings | Similarity search with citable source metadata |
| `datasets/` | OFAC SDN lists | Screened by exact/fuzzy name match, not embeddings |

Both Postgres and Redis **degrade gracefully**: absent Postgres, checkpointing
falls back to memory; absent Redis, the cache becomes a no-op. Both report their
real state at `GET /system`, so degradation is never silent.

## 9. API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/investigations` | Start; runs to the human gate |
| `POST` | `/investigations/stream` | Same, streaming per-agent telemetry (SSE) |
| `GET` | `/investigations` | Audit ledger |
| `GET` | `/investigations/{id}` | One investigation with its full assessment |
| `POST` | `/investigations/{id}/approve` | Officer decision; resumes the graph |
| `POST` | `/investigations/{id}/approve/stream` | Same, streaming |
| `GET` | `/investigations/{id}/report?format=md\|pdf` | Stored report, Markdown or typeset PDF |
| `GET` | `/system` | Live durability, provider and usage telemetry |
| `GET` | `/tools` | Tool catalogue with live per-tool availability |
| `GET` | `/assets/files`, `/assets/{category}/{filename}` | Reference corpus browser |

The SSE endpoints exist because LangGraph's `stream_mode="tasks"` emits a task
event when a node starts and another when it settles — which is what lets the
console show four agents genuinely running at once rather than faking a sequence.

## 10. The tool layer

Five tools reach outside the process: sanctions screening, SEC EDGAR, market
data, open-web search, and retrieval over the policy corpus. All five are called
through one registry (`tools/registry.py`), and never imported directly - a rule
`tests/unit/test_architecture.py` enforces by parsing every shipped module.

The registry exists for a reason that is easy to miss: **a tool's failure used to
look exactly like its data.** `search_company_news` returns a string in every
case, its own errors included, so an investigation run with no search key
reported no adverse media - not because none existed, but because nothing was
searched. Each tool now declares a `classify` function that maps its return value
onto four outcomes:

| Outcome | Meaning | Example |
| --- | --- | --- |
| `OK` | Ran, returned usable data | EDGAR resolved a CIK |
| `EMPTY` | Ran, found nothing - a real answer | The entity is not an SEC registrant |
| `FAILED` | Ran and raised | Upstream returned 503 |
| `UNAVAILABLE` | Never ran | No search key; corpus not indexed |

`EMPTY` versus `UNAVAILABLE` is the distinction that carries the weight. A
screening that ran and found nothing supports a clean verdict; a screening that
never ran does not, and a system that conflates the two reports confidence it has
not earned.

Every call appends a `ToolInvocation` to the investigation's `tool_calls` channel
- the tool, the caller, the **arguments**, the duration, the outcome, and the ids
of the evidence it produced. Arguments are recorded because "the entity was
screened" is unfalsifiable, while "`Bank Melli Iran` was screened at 14:32:07
against 1 of 2 providers, returning 3 matches" can be checked and disputed.
Secret-looking argument names are masked by substring, so a key cannot reach the
database through a tool whose author forgot to declare it.

`ToolAuditTrail.attribute` links each call to the evidence it produced, which is
what closes the chain end to end:

```
verdict -> risk factor -> evidence record -> the tool call and arguments behind it
```

`GET /tools` reports the catalogue with live availability, because a deployment
gap is otherwise invisible: an investigation missing a data source produces a
result that looks identical to one that had it.

See [ADR-009](adr/ADR-009-tool-registry-and-audit-trail.md).

## 11. The critic

The supervisor produces a verdict. The critic tries to break it, between the
supervisor and the human gate:

```
supervisor -> critic -> { revise -> supervisor | human_approval }
```

Six deterministic checks plus one narrow model question. The deterministic six
are what make it trustworthy: they read the assessment, the evidence pool and the
tool audit trail, and either find a defect or do not - no model is ever asked
whether the verdict is *good*, because a model asked that produces fluent
commentary whether or not anything is wrong.

| Check | Severity | Catches |
| --- | --- | --- |
| `dangling_citation` | blocking | A factor citing evidence not in the pool |
| `band_mismatch` | blocking | A band that does not follow from its score |
| `unverified_scope` | blocking | A **reassuring** score from a tool that never ran |
| `ungrounded_factor` | material | A factor scored on judgement alone |
| `thin_evidence` | material | A serious verdict with no high-confidence evidence |
| `overconfident` | material | Confidence outrunning the work actually done |
| `unsupported_driver` | material | *(model)* A driver the cited evidence cannot support |

`unverified_scope` exists only because of the tool audit trail: a sanctions
dimension scoring 10/100 after a clean screening, and one scoring 10/100 because
no list was ever consulted, are identical in the assessment and differ only in
the trail.

The loop is bounded at one revision, and the counter is incremented in its own
node so the checkpointer records it - otherwise a resume after a crash restarts
the loop with a counter that never moved. An unresolved rejection still reaches
the officer, carrying the objections: SENTINEL is advisory, and suppressing a
verdict it could not fix would hide the disagreement rather than surface it.

The critic found two real defects on its first two live runs: a verdict claiming
*"going-concern doubts in filings"* that no retrieved filing supported, and a
confidence figure of 1.0 produced by a formula that measured coverage rather than
certainty. Both are fixed. See [ADR-010](adr/ADR-010-critic-with-bounded-revision.md).

## 12. Known gaps

Named rather than implied:

* **Memory and knowledge graph.** Not built (brief phases 3-5). There is no recall
  from one investigation to the next, and no model of the links between entities.
* **Joint fraud-signal reasoning.** The red flags are collected and each sets a
  floor under the regulatory dimension, but nothing scores their *convergence* -
  an auditor change plus a late filing plus an officer exodus in one quarter is
  currently worth no more than the strongest of the three.
* **The critic checks support, not truth.** If a specialist files a wrong fact,
  the critic will confirm the verdict rests on it.
* **Cross-investigation tool analytics.** The audit trail is persisted through the
  checkpointer rather than as its own table, so it is queryable per investigation
  but not across them.
* **OpenSanctions coverage.** The screening tool reports `1/2 providers` unless
  `OPENSANCTIONS_API_KEY` is set: the OFAC SDN list is held locally and always
  available, while EU, UN, UK and PEP coverage requires the optional key.
  Screening still runs, and the audit digest records the reduced coverage on
  every call — which is how the gap became visible in the first place.
