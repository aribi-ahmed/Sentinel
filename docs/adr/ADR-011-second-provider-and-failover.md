# ADR-011: Hugging Face as the second provider, and failover measured against the configured primary

**Status:** Accepted
**Date:** 2026-08-24
**Objectives:** M-01 (provider-agnostic gateway); M-11 (containerisation); rubric — architecture quality

## Context

M-01's acceptance signal is precise: *"Same investigation runs on two providers
without code changes."* Everything needed for that appeared to exist —
`groq_adapter.py`, `ollama_adapter.py`, a factory keyed on configuration, and an
architecture test proving no other module imports a provider SDK.

None of it had ever been exercised. Three separate defects were hiding behind
the appearance of completeness:

1. **Ollama was never installed.** `OllamaAdapter.is_available()` returned
   `False`, so the adapter was filtered out before every call. `GET /system`
   listed one provider and the usage ledger recorded `fallbacks: 0` across the
   entire project history.
2. **`HF_TOKEN` was set in `.env` but absent from `Settings`.** A variable the
   typed settings object does not declare is read by nothing — the same
   silent-configuration failure recorded in
   [ADR-004](ADR-004-provider-agnostic-llm-gateway.md), recurring in a new place.
3. **The container had no second provider at all.** `docker-compose.yml`
   hardcoded `LLM_FALLBACK_PROVIDER: ""` and never passed `HF_TOKEN` through, so
   in the deployment a reviewer actually runs, M-01 was not demonstrable.

The objective was therefore *structurally* satisfied and *operationally* untrue —
a distinction the brief's wording anticipates by specifying an acceptance signal
rather than a feature.

## Options considered

1. **Install Ollama and pull a local model.** Closest to the brief's example
   (§2.1 names "Groq + Ollama"), and the only option giving genuine offline
   capability. Rejected for now on cost of setup: a multi-gigabyte model download
   for a machine that already spent twenty hours computing embeddings, and a
   provider that cannot be reached from inside the container network anyway.
2. **Add OpenRouter or Cerebras.** Both named in §12.3. Rejected because each
   needs a new account and key, and a second hosted provider was already
   available.
3. **Hugging Face serverless inference.** Named in §4.2 and §12.3, and the token
   was already present in `.env` — it had simply never been wired to anything.

## Decision

**Option 3.** `huggingface_adapter.py` joins the two existing adapters, and the
architecture test's exemption list grows from two files to three — named
individually, so adding a file to `llm/` still cannot widen the hole silently.

Two subsidiary decisions matter more than the adapter itself.

**`fallback_used` is measured against the *configured* primary, not against the
surviving adapter list.** The gateway filters unavailable providers before
dispatch, so a primary dropped for being unreachable used to leave the fallback
sitting at index 0 and reporting `fallback_used=False`. That reads as "no
failover occurred" when the configured primary served nothing at all. A primary
that did not answer is a primary that was fallen back from, whatever the reason.

**Usage is now attributed per provider as well as per caller.** This is the
evidence for M-01: `by_provider` in `GET /system` shows which provider actually
served the traffic. Configuration says what *should* happen; the ledger says
what *did*.

Hugging Face routes through partner providers, so model availability depends on
the account rather than on the Hub. `Qwen/Qwen2.5-7B-Instruct` — the obvious
default — returns a 400 for this account, while `Qwen/Qwen2.5-72B-Instruct`
works. `_TERMINAL` therefore treats *"not supported by any provider"* as final:
retrying it three times only delays the fallback.

## Consequences

* **Positive.** The acceptance signal is demonstrable and reproducible.
  `scripts/verify_m01.py` runs one prompt on each provider, optionally a full
  investigation on each, then kills the primary and watches the fallback take
  over. Wolfspeed scores **67.5 ELEVATED on both providers**.
* **Positive.** `docker compose up` now brings up a stack where both providers
  are live, so M-01 holds in the deployment a reviewer uses rather than only on
  the developer's machine.
* **Positive.** Fourteen tests in `test_provider_failover.py` cover the mechanism
  against fake adapters: selection by configuration, retry-then-fall-back,
  terminal errors skipping the retry loop, and every provider failing raising
  rather than inventing an answer.
* **Negative.** Hugging Face is slower than Groq — roughly 3.8s against 0.6s on
  the same prompt — so it is the fallback rather than the primary.
* **Negative.** No offline capability. Ollama remains registered and would work
  if installed, but the system still requires a network.
* **Note.** This is the third time a silent configuration gap has been found by
  going looking for evidence rather than by anything failing. The first two are
  ADR-004 and ADR-005. The pattern is consistent enough to state plainly: in this
  system, "it is implemented" and "it works" are independent claims, and only the
  second one is worth making.
