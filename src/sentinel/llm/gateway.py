"""The LLM gateway — the only door to a language model.

Everything the brief asks of this layer (§4.2) lives here and nowhere else:
provider selection by configuration, retries with backoff, provider fallback,
timeout control, token and cost accounting, and response caching.

The ordering of concerns is deliberate:

    cache  →  primary provider (retry × N)  →  fallback provider (retry × N)

A cache hit costs nothing and cannot fail, so it is checked first. Retries come
before fallback because a 429 from a fast provider is usually cheaper to wait
out than a cold local model. And a non-retryable error — a bad key, an unknown
model — skips straight to the fallback instead of sleeping three times first.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from sentinel.llm.cache import NullCache, ResponseCache, cache_key
from sentinel.llm.contracts import (
    AllProvidersFailed,
    LLMRequest,
    LLMResponse,
    ModelProfile,
    ProviderAdapter,
    ProviderError,
)

logger = logging.getLogger(__name__)


@dataclass
class UsageLedger:
    """Running totals per caller, so the console can attribute spend to agents.

    Guarded by a lock because the four specialists run on parallel threads and
    would otherwise interleave their read-modify-writes.
    """

    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    fallbacks: int = 0
    by_caller: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Per-provider totals. This is the evidence for M-01: it shows which
    # provider actually served the traffic, rather than which was configured.
    by_provider: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, response: LLMResponse) -> None:
        with self._lock:
            self.calls += 1
            self.cache_hits += 1 if response.cached else 0
            self.fallbacks += 1 if response.fallback_used else 0
            self.prompt_tokens += response.usage.prompt_tokens
            self.completion_tokens += response.usage.completion_tokens
            self.cost_usd = round(self.cost_usd + response.usage.cost_usd, 6)

            entry = self.by_caller.setdefault(
                response.caller,
                {"calls": 0, "cache_hits": 0, "tokens": 0, "cost_usd": 0.0,
                 "latency_ms": 0, "provider": response.provider, "model": response.model},
            )
            entry["calls"] += 1
            entry["cache_hits"] += 1 if response.cached else 0
            entry["tokens"] += response.usage.total_tokens
            entry["cost_usd"] = round(entry["cost_usd"] + response.usage.cost_usd, 6)
            entry["latency_ms"] += response.latency_ms
            entry["provider"] = response.provider
            entry["model"] = response.model

            # A cached reply is attributed to the provider that originally
            # produced it, but counted separately so the split stays honest.
            served = self.by_provider.setdefault(
                response.provider,
                {"calls": 0, "cache_hits": 0, "tokens": 0, "cost_usd": 0.0, "models": []},
            )
            served["calls"] += 1
            served["cache_hits"] += 1 if response.cached else 0
            served["tokens"] += response.usage.total_tokens
            served["cost_usd"] = round(served["cost_usd"] + response.usage.cost_usd, 6)
            if response.model not in served["models"]:
                served["models"].append(response.model)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "calls": self.calls,
                "cache_hits": self.cache_hits,
                "cache_hit_rate": round(self.cache_hits / self.calls, 3) if self.calls else 0.0,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens,
                "cost_usd": round(self.cost_usd, 6),
                "fallbacks": self.fallbacks,
                "by_caller": {name: dict(entry) for name, entry in self.by_caller.items()},
                "by_provider": {name: dict(entry) for name, entry in self.by_provider.items()},
            }


class LLMGateway:
    """Provider-agnostic access to language models."""

    def __init__(
        self,
        adapters: Sequence[ProviderAdapter],
        *,
        cache: Optional[ResponseCache] = None,
        ledger: Optional[UsageLedger] = None,
        max_attempts: int = 3,
        timeout: float = 60.0,
        backoff_base: float = 0.6,
        cache_ttl_seconds: int = 6 * 60 * 60,
    ) -> None:
        if not adapters:
            raise ValueError("The gateway needs at least one provider adapter.")
        self._adapters = list(adapters)
        self._cache = cache or NullCache()
        self.ledger = ledger or UsageLedger()
        self._max_attempts = max(1, max_attempts)
        self._timeout = timeout
        self._backoff_base = backoff_base
        self._cache_ttl = cache_ttl_seconds

    # -- introspection, for /system and the console -----------------------

    @property
    def providers(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": adapter.name,
                "available": adapter.is_available(),
                "role": "primary" if index == 0 else "fallback",
                "models": {
                    profile.value: adapter.model_for(profile) for profile in ModelProfile
                },
            }
            for index, adapter in enumerate(self._adapters)
        ]

    @property
    def cache_enabled(self) -> bool:
        return bool(getattr(self._cache, "enabled", False))

    # -- the one operation ------------------------------------------------

    def complete(
        self,
        prompt: str,
        *,
        profile: ModelProfile = ModelProfile.REASONING,
        temperature: float = 0.1,
        system: str = "",
        caller: str = "unknown",
        max_tokens: Optional[int] = None,
        use_cache: bool = True,
    ) -> LLMResponse:
        """Completes a prompt, trying each provider in turn."""
        request = LLMRequest(
            prompt=prompt,
            profile=profile,
            temperature=temperature,
            system=system,
            caller=caller,
            max_tokens=max_tokens,
        )

        usable = [adapter for adapter in self._adapters if adapter.is_available()]
        if not usable:
            raise AllProvidersFailed({adapter.name: "not configured" for adapter in self._adapters})

        # The key includes the model, so a fallback provider never serves a
        # cached answer that the primary produced under a different model.
        key = ""
        if use_cache and self._cache.enabled:
            key = cache_key(request.cache_key_material(usable[0].model_for(profile)))
            hit = self._cache.get(key)
            if hit is not None:
                response = LLMResponse(
                    text=hit.text, provider=hit.provider, model=hit.model,
                    usage=hit.usage, latency_ms=0, cached=True, caller=caller,
                )
                self.ledger.record(response)
                return response

        errors: Dict[str, str] = {}

        for index, adapter in enumerate(usable):
            model = adapter.model_for(profile)
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = adapter.complete(request, model, self._timeout)
                except ProviderError as exc:
                    errors[adapter.name] = str(exc)
                    if not exc.retryable:
                        logger.warning(
                            "%s rejected the request permanently (%s); moving on.",
                            adapter.name, exc,
                        )
                        break
                    if attempt < self._max_attempts:
                        self._sleep(attempt)
                        continue
                    logger.warning("%s exhausted %d attempts.", adapter.name, self._max_attempts)
                    break

                settled = LLMResponse(
                    text=response.text,
                    provider=response.provider,
                    model=response.model,
                    usage=response.usage,
                    latency_ms=response.latency_ms,
                    cached=False,
                    attempts=attempt,
                    # Measured against the *configured* primary, not against the
                    # surviving list. A primary dropped for being unavailable is
                    # still a primary that did not serve the request.
                    fallback_used=adapter.name != self._adapters[0].name,
                    caller=caller,
                )
                if key:
                    self._cache.set(key, settled, self._cache_ttl)
                self.ledger.record(settled)
                return settled

        raise AllProvidersFailed(errors)

    def _sleep(self, attempt: int) -> None:
        """Exponential backoff with jitter, so parallel agents do not resynchronise.

        Four specialists retrying in lockstep would hit the rate limit together
        on every attempt; the random component spreads them out.
        """
        delay = self._backoff_base * (2 ** (attempt - 1))
        time.sleep(delay + random.uniform(0, delay * 0.3))
