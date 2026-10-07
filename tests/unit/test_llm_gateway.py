"""Gateway tests — retry, fallback, caching and accounting.

The point of the gateway is behaviour under failure, and failure is exactly what
is hard to observe against a real provider. So these run against fake adapters
that fail on demand: §10.3's "unit tests with fake LLMs", and the reason the
whole file finishes in well under a second without a network.

Note what is asserted: never the text a model produced, always the structure and
the behaviour — which provider answered, how many attempts it took, whether the
cache was consulted.
"""

from __future__ import annotations

import pytest

from sentinel.llm.cache import NullCache, cache_key
from sentinel.llm.contracts import (
    AllProvidersFailed,
    LLMRequest,
    LLMResponse,
    ModelProfile,
    ProviderAdapter,
    ProviderError,
    Usage,
)
from sentinel.llm.gateway import LLMGateway, UsageLedger


class FakeAdapter:
    """A provider that behaves exactly as a test needs it to."""

    def __init__(
        self,
        name: str,
        *,
        available: bool = True,
        fail_times: int = 0,
        retryable: bool = True,
        always_fail: bool = False,
        text: str = "ok",
    ) -> None:
        self.name = name
        self._available = available
        self._fail_times = fail_times
        self._retryable = retryable
        self._always_fail = always_fail
        self._text = text
        self.calls = 0

    def is_available(self) -> bool:
        return self._available

    def model_for(self, profile: ModelProfile) -> str:
        return f"{self.name}-{profile.value}"

    def complete(self, request: LLMRequest, model: str, timeout: float) -> LLMResponse:
        self.calls += 1
        if self._always_fail or self.calls <= self._fail_times:
            raise ProviderError(f"{self.name} failed", provider=self.name, retryable=self._retryable)
        return LLMResponse(
            text=self._text,
            provider=self.name,
            model=model,
            usage=Usage(prompt_tokens=100, completion_tokens=50, cost_usd=0.0001),
            latency_ms=12,
            caller=request.caller,
        )


def gateway(*adapters, **kwargs) -> LLMGateway:
    kwargs.setdefault("backoff_base", 0.0)  # no real sleeping in tests
    return LLMGateway(list(adapters), **kwargs)


class TestProviderSelection:
    def test_the_first_available_adapter_answers(self) -> None:
        primary, fallback = FakeAdapter("groq"), FakeAdapter("ollama")

        response = gateway(primary, fallback).complete("hi")

        assert response.provider == "groq"
        assert not response.fallback_used
        assert fallback.calls == 0

    def test_an_unavailable_primary_is_skipped_entirely(self) -> None:
        """M-01: swapping providers is configuration, and an unconfigured one is inert."""
        primary, fallback = FakeAdapter("groq", available=False), FakeAdapter("ollama")

        response = gateway(primary, fallback).complete("hi")

        assert response.provider == "ollama"
        assert primary.calls == 0

    def test_no_configured_provider_raises_a_clear_error(self) -> None:
        with pytest.raises(AllProvidersFailed, match="not configured"):
            gateway(FakeAdapter("groq", available=False)).complete("hi")

    def test_a_gateway_needs_at_least_one_adapter(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            LLMGateway([])

    def test_profiles_map_to_different_models(self) -> None:
        adapter = FakeAdapter("groq")

        fast = gateway(adapter).complete("hi", profile=ModelProfile.FAST)
        reasoning = gateway(adapter).complete("hi", profile=ModelProfile.REASONING)

        assert fast.model == "groq-fast"
        assert reasoning.model == "groq-reasoning"


class TestRetry:
    def test_a_transient_failure_is_retried_then_succeeds(self) -> None:
        adapter = FakeAdapter("groq", fail_times=2)

        response = gateway(adapter, max_attempts=3).complete("hi")

        assert response.attempts == 3
        assert adapter.calls == 3

    def test_retries_stop_at_the_configured_limit(self) -> None:
        adapter = FakeAdapter("groq", always_fail=True)

        with pytest.raises(AllProvidersFailed):
            gateway(adapter, max_attempts=2).complete("hi")

        assert adapter.calls == 2

    def test_a_permanent_failure_is_not_retried(self) -> None:
        """A bad API key will not fix itself; sleeping three times only wastes time."""
        adapter = FakeAdapter("groq", always_fail=True, retryable=False)

        with pytest.raises(AllProvidersFailed):
            gateway(adapter, max_attempts=3).complete("hi")

        assert adapter.calls == 1


class TestFallback:
    def test_the_fallback_answers_when_the_primary_is_exhausted(self) -> None:
        primary, fallback = FakeAdapter("groq", always_fail=True), FakeAdapter("ollama")

        response = gateway(primary, fallback, max_attempts=2).complete("hi")

        assert response.provider == "ollama"
        assert response.fallback_used
        assert primary.calls == 2

    def test_a_permanent_primary_failure_moves_on_immediately(self) -> None:
        primary = FakeAdapter("groq", always_fail=True, retryable=False)
        fallback = FakeAdapter("ollama")

        response = gateway(primary, fallback, max_attempts=3).complete("hi")

        assert response.provider == "ollama"
        assert primary.calls == 1

    def test_every_provider_failing_reports_each_reason(self) -> None:
        with pytest.raises(AllProvidersFailed) as caught:
            gateway(
                FakeAdapter("groq", always_fail=True),
                FakeAdapter("ollama", always_fail=True),
                max_attempts=1,
            ).complete("hi")

        assert set(caught.value.errors) == {"groq", "ollama"}


class DictCache:
    """A cache with the real semantics and none of the infrastructure."""

    enabled = True

    def __init__(self) -> None:
        self.store: dict[str, LLMResponse] = {}
        self.reads = 0

    def get(self, key: str):
        self.reads += 1
        hit = self.store.get(key)
        if hit is None:
            return None
        return LLMResponse(
            text=hit.text, provider=hit.provider, model=hit.model,
            usage=Usage(hit.usage.prompt_tokens, hit.usage.completion_tokens, 0.0),
            cached=True,
        )

    def set(self, key: str, response: LLMResponse, ttl_seconds: int) -> None:
        self.store[key] = response


class TestCaching:
    def test_an_identical_call_is_served_from_cache(self) -> None:
        """M-09's signal: repeated identical calls hit cache."""
        adapter, cache = FakeAdapter("groq"), DictCache()
        gw = gateway(adapter, cache=cache)

        first = gw.complete("same prompt", caller="supervisor")
        second = gw.complete("same prompt", caller="supervisor")

        assert not first.cached
        assert second.cached
        assert adapter.calls == 1

    def test_a_cache_hit_costs_nothing(self) -> None:
        adapter, cache = FakeAdapter("groq"), DictCache()
        gw = gateway(adapter, cache=cache)
        gw.complete("p")

        assert gw.complete("p").usage.cost_usd == 0.0

    def test_a_different_prompt_is_a_miss(self) -> None:
        adapter, cache = FakeAdapter("groq"), DictCache()
        gw = gateway(adapter, cache=cache)

        gw.complete("first")
        gw.complete("second")

        assert adapter.calls == 2

    def test_temperature_is_part_of_the_key(self) -> None:
        adapter, cache = FakeAdapter("groq"), DictCache()
        gw = gateway(adapter, cache=cache)

        gw.complete("p", temperature=0.1)
        gw.complete("p", temperature=0.9)

        assert adapter.calls == 2

    def test_caching_can_be_bypassed_per_call(self) -> None:
        adapter, cache = FakeAdapter("groq"), DictCache()
        gw = gateway(adapter, cache=cache)

        gw.complete("p")
        gw.complete("p", use_cache=False)

        assert adapter.calls == 2

    def test_the_null_cache_never_hits(self) -> None:
        adapter = FakeAdapter("groq")
        gw = gateway(adapter, cache=NullCache())

        gw.complete("p")
        gw.complete("p")

        assert adapter.calls == 2

    def test_keys_are_hashed_so_prompts_never_reach_redis(self) -> None:
        key = cache_key("a confidential prompt about an entity")

        assert "confidential" not in key
        assert key.startswith("sentinel:llm:")


class TestUsageLedger:
    def test_totals_accumulate_across_calls(self) -> None:
        gw = gateway(FakeAdapter("groq"))

        gw.complete("a", caller="supervisor")
        gw.complete("b", caller="compliance_analyst")

        snapshot = gw.ledger.snapshot()
        assert snapshot["calls"] == 2
        assert snapshot["total_tokens"] == 300
        assert snapshot["cost_usd"] == pytest.approx(0.0002)

    def test_usage_is_attributed_to_the_calling_agent(self) -> None:
        """This is what lets the console show spend per agent."""
        gw = gateway(FakeAdapter("groq"))

        gw.complete("a", caller="supervisor")
        gw.complete("b", caller="supervisor")
        gw.complete("c", caller="research_analyst")

        by_caller = gw.ledger.snapshot()["by_caller"]
        assert by_caller["supervisor"]["calls"] == 2
        assert by_caller["research_analyst"]["calls"] == 1

    def test_cache_hit_rate_is_reported(self) -> None:
        gw = gateway(FakeAdapter("groq"), cache=DictCache())

        gw.complete("p")
        gw.complete("p")

        assert gw.ledger.snapshot()["cache_hit_rate"] == 0.5

    def test_fallbacks_are_counted(self) -> None:
        gw = gateway(
            FakeAdapter("groq", always_fail=True), FakeAdapter("ollama"), max_attempts=1
        )
        gw.complete("p")

        assert gw.ledger.snapshot()["fallbacks"] == 1

    def test_usage_addition_combines_counts_and_flags(self) -> None:
        combined = Usage(10, 5, 0.001) + Usage(20, 10, 0.002, measured=False)

        assert combined.total_tokens == 45
        assert combined.cost_usd == pytest.approx(0.003)
        assert not combined.measured  # one estimate makes the total an estimate


class TestIntrospection:
    def test_providers_are_reported_with_their_role(self) -> None:
        gw = gateway(FakeAdapter("groq"), FakeAdapter("ollama", available=False))

        providers = gw.providers

        assert [p["role"] for p in providers] == ["primary", "fallback"]
        assert [p["available"] for p in providers] == [True, False]
        assert providers[0]["models"]["fast"] == "groq-fast"

    def test_adapters_satisfy_the_protocol(self) -> None:
        assert isinstance(FakeAdapter("groq"), ProviderAdapter)
