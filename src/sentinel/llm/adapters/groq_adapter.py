"""Groq adapter.

One of only two modules in the project permitted to import a provider SDK —
`tests/unit/test_architecture.py` fails the build if that spreads.

Groq is the hosted provider: fast, free-tier, rate-limited. The rate limit is a
design constraint rather than an obstacle (Appendix B), which is why the gateway
above this treats 429 as retryable and the adapter says so explicitly.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from sentinel.llm.contracts import (
    LLMRequest,
    LLMResponse,
    ModelProfile,
    ProviderError,
    Usage,
)

# Indicative list prices in USD per million tokens. On the free tier the real
# cost is zero; these are carried so the console can show what the same traffic
# *would* cost, which is the number that matters when sizing a real deployment.
PRICES_PER_MTOK: Dict[str, tuple[float, float]] = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
    "openai/gpt-oss-safeguard-20b": (0.075, 0.30),
    "qwen/qwen3.6-27b": (0.10, 0.30),
    "groq/compound-mini": (0.075, 0.30),
}
_DEFAULT_PRICE = (0.15, 0.60)

# Errors that no amount of retrying will fix.
_TERMINAL = ("api key", "unauthorized", "401", "403", "not found", "404", "invalid_request")


class GroqAdapter:
    """Hosted inference via Groq."""

    name = "groq"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        models: Optional[Dict[ModelProfile, str]] = None,
    ) -> None:
        """Configuration arrives through the constructor, never from `os.getenv`.

        §10.5 puts environment reading in one place; an adapter that consults the
        environment itself silently ignores anything loaded from `.env`.
        """
        from sentinel.config.settings import settings

        self._api_key = api_key if api_key is not None else settings.GROQ_API_KEY
        self._models = models or {
            ModelProfile.REASONING: settings.LLM_MODEL,
            ModelProfile.EXTRACTION: settings.extraction_model,
            ModelProfile.FAST: settings.LLM_MODEL_FAST,
        }

    def is_available(self) -> bool:
        return bool(self._api_key)

    def model_for(self, profile: ModelProfile) -> str:
        return self._models.get(profile, self._models[ModelProfile.REASONING])

    def complete(self, request: LLMRequest, model: str, timeout: float) -> LLMResponse:
        if not self._api_key:
            raise ProviderError("GROQ_API_KEY is not set", provider=self.name, retryable=False)

        client = ChatGroq(
            model_name=model,
            temperature=request.temperature,
            api_key=self._api_key,
            timeout=timeout,
            max_retries=0,  # the gateway owns retry policy, not the SDK
            max_tokens=request.max_tokens,
        )

        messages: list[Any] = []
        if request.system:
            messages.append(SystemMessage(content=request.system))
        messages.append(HumanMessage(content=request.prompt))

        started = time.perf_counter()
        try:
            reply = client.invoke(messages)
        except Exception as exc:
            text = str(exc).lower()
            raise ProviderError(
                str(exc),
                provider=self.name,
                retryable=not any(marker in text for marker in _TERMINAL),
            ) from exc

        return LLMResponse(
            text=str(getattr(reply, "content", "") or ""),
            provider=self.name,
            model=model,
            usage=self._usage(reply, model),
            latency_ms=int((time.perf_counter() - started) * 1000),
            caller=request.caller,
        )

    def _usage(self, reply: Any, model: str) -> Usage:
        """Reads token counts off the reply, falling back to an estimate."""
        meta = getattr(reply, "usage_metadata", None) or {}
        prompt_tokens = int(meta.get("input_tokens", 0) or 0)
        completion_tokens = int(meta.get("output_tokens", 0) or 0)
        measured = bool(prompt_tokens or completion_tokens)

        if not measured:
            # Roughly four characters per token — good enough to keep a budget
            # honest when a provider omits counts, and flagged as unmeasured.
            text = str(getattr(reply, "content", "") or "")
            completion_tokens = max(1, len(text) // 4)

        prompt_price, completion_price = PRICES_PER_MTOK.get(model, _DEFAULT_PRICE)
        cost = (prompt_tokens * prompt_price + completion_tokens * completion_price) / 1_000_000

        return Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=round(cost, 6),
            measured=measured,
        )
