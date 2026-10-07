"""Provider failover — the M-01 acceptance signal, tested.

M-01 asks that the same investigation run on two providers with no code change,
and Phase 6 asks for a chaos test: kill a provider mid-run and watch the
fallback take over. Both are exercised here against fake adapters, so the suite
stays offline and deterministic.

The live two-provider demonstration is a separate exercise (`scripts/verify_m01.py`);
what these tests protect is the *mechanism* — that selection really is
configuration, that a dead primary is survived, and that a terminal error is not
retried into the ground.
"""

from __future__ import annotations

import pytest

from sentinel.llm import _adapter, build_gateway
from sentinel.llm.contracts import (
    AllProvidersFailed,
    LLMRequest,
    LLMResponse,
    ModelProfile,
    ProviderError,
    Usage,
)


class FakeAdapter:
    """A provider that answers, or fails in a way the test chooses."""

    def __init__(self, name: str, *, fails: int = 0, retryable: bool = True) -> None:
        self.name = name
        self._fails = fails
        self._retryable = retryable
        self.attempts = 0

    def is_available(self) -> bool:
        return True

    def model_for(self, profile: ModelProfile) -> str:
        return f"{self.name}-model"

    def complete(self, request: LLMRequest, model: str, timeout: float) -> LLMResponse:
        self.attempts += 1
        if self._fails:
            self._fails -= 1
            raise ProviderError(
                f"{self.name} is unreachable",
                provider=self.name,
                retryable=self._retryable,
            )
        return LLMResponse(
            text=f"answered by {self.name}",
            provider=self.name,
            model=model,
            usage=Usage(prompt_tokens=10, completion_tokens=5),
            latency_ms=1,
            caller=request.caller,
        )


def gateway_with(*adapters, attempts: int = 2):
    from sentinel.llm.gateway import LLMGateway

    return LLMGateway(list(adapters), cache=None, max_attempts=attempts, timeout=5.0)


class TestProviderSelection:
    """M-01: swapping providers is configuration, not code."""

    def test_both_providers_are_constructible_by_name(self) -> None:
        """The factory is what makes LLM_PROVIDER=<name> work."""
        assert _adapter("groq") is not None
        assert _adapter("huggingface") is not None

    def test_the_short_alias_resolves(self) -> None:
        assert _adapter("hf") is not None

    def test_an_unknown_provider_is_ignored_not_fatal(self) -> None:
        """A typo in configuration must not take the system down."""
        assert _adapter("nonesuch") is None

    def test_configuration_alone_selects_the_primary(self) -> None:
        groq = build_gateway(primary="groq", fallback="")
        hf = build_gateway(primary="huggingface", fallback="")

        assert groq.providers[0]["name"] == "groq"
        assert hf.providers[0]["name"] == "huggingface"

    def test_the_fallback_is_listed_second(self) -> None:
        gateway = build_gateway(primary="groq", fallback="huggingface")
        roles = {p["name"]: p["role"] for p in gateway.providers}

        assert roles["groq"] == "primary"
        assert roles["huggingface"] == "fallback"

    def test_a_provider_is_never_listed_twice(self) -> None:
        gateway = build_gateway(primary="groq", fallback="groq")
        assert len(gateway.providers) == 1


class TestFailover:
    """Phase 6: kill the primary mid-run, watch the fallback take over."""

    def test_the_fallback_answers_when_the_primary_is_dead(self) -> None:
        primary = FakeAdapter("primary", fails=99)
        fallback = FakeAdapter("fallback")

        reply = gateway_with(primary, fallback).complete("x", caller="test")

        assert reply.provider == "fallback"
        assert reply.fallback_used is True

    def test_the_primary_is_retried_before_giving_up_on_it(self) -> None:
        """A rate-limited free tier usually recovers; switching too early wastes it."""
        primary = FakeAdapter("primary", fails=1)

        reply = gateway_with(primary, FakeAdapter("fallback"), attempts=2).complete("x", caller="t")

        assert reply.provider == "primary"
        assert primary.attempts == 2

    def test_a_terminal_error_skips_straight_to_the_fallback(self) -> None:
        """A bad key will not fix itself; sleeping three times first is waste."""
        primary = FakeAdapter("primary", fails=99, retryable=False)
        fallback = FakeAdapter("fallback")

        reply = gateway_with(primary, fallback, attempts=3).complete("x", caller="t")

        assert reply.provider == "fallback"
        assert primary.attempts == 1, "a terminal failure must not be retried"

    def test_every_provider_failing_raises_rather_than_inventing_an_answer(self) -> None:
        with pytest.raises(AllProvidersFailed):
            gateway_with(FakeAdapter("a", fails=99), FakeAdapter("b", fails=99)).complete(
                "x", caller="t"
            )

    def test_a_failover_is_counted(self) -> None:
        gateway = gateway_with(FakeAdapter("primary", fails=99), FakeAdapter("fallback"))
        gateway.complete("x", caller="t")

        assert gateway.ledger.snapshot()["fallbacks"] == 1


class TestProviderAccounting:
    """The evidence for M-01: which provider actually served the traffic."""

    def test_usage_is_attributed_to_the_provider_that_answered(self) -> None:
        gateway = gateway_with(FakeAdapter("primary", fails=99), FakeAdapter("fallback"))
        gateway.complete("x", caller="t")

        by_provider = gateway.ledger.snapshot()["by_provider"]

        assert "fallback" in by_provider
        assert "primary" not in by_provider, "a provider that never answered served nothing"
        assert by_provider["fallback"]["calls"] == 1

    def test_two_providers_answering_are_both_recorded(self) -> None:
        """This is what proves an investigation ran across providers."""
        gateway = gateway_with(FakeAdapter("alpha"))
        gateway.complete("x", caller="t")

        second = gateway_with(FakeAdapter("beta"))
        second.ledger = gateway.ledger
        second.complete("y", caller="t")

        assert set(gateway.ledger.snapshot()["by_provider"]) == {"alpha", "beta"}

    def test_the_model_used_is_recorded_per_provider(self) -> None:
        gateway = gateway_with(FakeAdapter("alpha"))
        gateway.complete("x", caller="t")

        assert gateway.ledger.snapshot()["by_provider"]["alpha"]["models"] == ["alpha-model"]
