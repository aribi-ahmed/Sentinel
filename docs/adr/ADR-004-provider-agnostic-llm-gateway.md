# ADR-004: A real provider-agnostic LLM gateway

**Status:** Accepted
**Date:** 2026-08-21
**Objectives:** M-01 (provider gateway), M-09 (caching)

## Context

[ADR-001](ADR-001-initial-technology-stack.md) recorded an "LLM gateway". What
existed was `get_llm()` — a factory returning a configured `ChatGroq` or
`ChatOllama`:

```python
def get_llm(model_name=None, temperature=0.1):
    if provider == "ollama":
        return ChatOllama(...)
    return ChatGroq(...)
```

It switched providers by environment variable, so it met M-01 in the narrowest
reading. It met none of what §4.2 actually specifies: *"retries with backoff,
provider fallback, timeout control, token/cost accounting, and response caching
in Redis."*

There was nowhere to put any of that. Every caller received a provider-shaped
object and invoked it directly, so policy could only have been duplicated at each
call site. Redis, decided in ADR-001, was never imported anywhere.

The boundary leaked too: `app.py` imported `ChatGroq` directly, so "no provider
SDK outside the gateway" was untrue of the shipped surface.

## Options considered

1. **Keep the factory, add retry decorators at call sites.** Minimal change.
   Duplicates policy three times and leaves callers holding provider objects, so
   accounting and caching remain impossible.
2. **Wrap LangChain's own retry/fallback primitives.** Less code. But it ties the
   abstraction to LangChain's runnable model, and token accounting still has no
   home.
3. **A gateway owning the whole policy, with thin per-provider adapters.**

## Decision

**Option 3.** `sentinel/llm/`:

| Module | Responsibility |
| --- | --- |
| `contracts.py` | `ModelProfile`, `LLMRequest`, `LLMResponse`, `Usage`, `ProviderAdapter` — no SDK imports |
| `adapters/groq_adapter.py`, `adapters/ollama_adapter.py` | **the only modules permitted to import a provider SDK** |
| `cache.py` | Redis-backed response cache, degrading to a no-op |
| `gateway.py` | selection, retry, fallback, timeouts, usage ledger |

Ordering is `cache → primary (retry ×N) → fallback (retry ×N)`, chosen because:

* a cache hit costs nothing and cannot fail, so it goes first;
* a 429 from a fast hosted provider is usually cheaper to wait out than a cold
  local model, so retries precede fallback;
* a **non-retryable** error — bad key, unknown model — skips straight to the
  fallback instead of sleeping three times first.

Backoff is exponential **with jitter**, because four specialists retrying in
lockstep would re-hit the rate limit together on every attempt.

Callers ask for a `ModelProfile` (`FAST`, `REASONING`, `EXTRACTION`), never a
model name — §5.1's *"extraction does not need the model that reasoning needs."*
Profile-to-model mapping lives in settings.

## Consequences

* **Positive.** M-01 is now true in substance. Adding OpenRouter or Cerebras means
  writing one adapter and naming it in configuration; no other file changes.
* **Positive.** Redis caching is live and measurable — a repeated call goes from
  ~600 ms to 0 ms, and the ledger reports the hit rate.
* **Positive.** Token and cost accounting is attributed per calling agent, which
  made the right-sizing decision visible: the research agent spends ~240 tokens
  on the small model, the supervisor ~3,500 on the large one.
* **Positive.** 25 unit tests cover retry, fallback and caching against fake
  adapters — behaviour that is nearly impossible to observe against a live
  provider.
* **Negative.** More moving parts than a factory function, and one more layer to
  read through when debugging a prompt.
* **Negative.** Prices are a static table. Real spend on the free tier is zero;
  the figures answer "what would this traffic cost at list price", which is the
  number that matters when sizing a deployment. `Usage.measured` is `False`
  whenever counts were estimated rather than reported.

## Two bugs this surfaced

* **The adapters read `os.getenv("GROQ_API_KEY")`** and reported "not configured"
  on a machine where the key was plainly set — it lives in `.env`, which only
  pydantic-settings loads. Fixed by making `Settings` the single typed source
  (§10.5) and injecting configuration into adapters. `REDIS_URL` had the same
  fault, which is why the cache was silently disabled.
* **The configured `FAST` model did not exist on the account.** The gateway
  handled it correctly — classified the 404 as non-retryable, moved on, reported
  clearly — which is exactly the behaviour the design is for.
