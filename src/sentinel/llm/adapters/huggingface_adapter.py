"""Hugging Face adapter.

The second provider required by M-01. One of only three modules permitted to
import a provider SDK — `tests/unit/test_architecture.py` fails the build if
that spreads.

Hugging Face routes serverless inference through partner providers, so which
models answer depends on what the account has enabled rather than on what exists
on the Hub. A model the account cannot reach returns a 400 that no retry will
fix, which is why `_TERMINAL` treats "not supported by any provider" as final.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from huggingface_hub import InferenceClient

from sentinel.llm.contracts import (
    LLMRequest,
    LLMResponse,
    ModelProfile,
    ProviderError,
    Usage,
)

# Indicative list prices in USD per million tokens, carried for the same reason
# as the Groq table: the free tier costs nothing, but the console should be able
# to show what the same traffic would cost on a paid plan.
PRICES_PER_MTOK: Dict[str, tuple[float, float]] = {
    "Qwen/Qwen2.5-72B-Instruct": (0.13, 0.40),
    "deepseek-ai/DeepSeek-V3": (0.27, 1.10),
}
_DEFAULT_PRICE = (0.15, 0.60)

# Failures no amount of retrying will fix.
_TERMINAL = (
    "api key",
    "unauthorized",
    "401",
    "403",
    "not found",
    "404",
    "invalid_request",
    "not supported by any provider",
    "bad request",
)


class HuggingFaceAdapter:
    """Serverless inference via the Hugging Face router."""

    name = "huggingface"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        models: Optional[Dict[ModelProfile, str]] = None,
    ) -> None:
        """Configuration arrives through the constructor, never from `os.getenv`.

        §10.5 keeps environment reading in one place; an adapter that consults
        the environment itself silently ignores anything loaded from `.env`.
        """
        from sentinel.config.settings import settings

        self._api_key = api_key if api_key is not None else settings.HF_TOKEN
        self._models = models or {
            ModelProfile.REASONING: settings.HF_MODEL,
            ModelProfile.EXTRACTION: settings.HF_MODEL,
            ModelProfile.FAST: settings.HF_MODEL_FAST,
        }

    def is_available(self) -> bool:
        return bool(self._api_key)

    def model_for(self, profile: ModelProfile) -> str:
        return self._models.get(profile, self._models[ModelProfile.REASONING])

    def complete(self, request: LLMRequest, model: str, timeout: float) -> LLMResponse:
        if not self._api_key:
            raise ProviderError("HF_TOKEN is not set", provider=self.name, retryable=False)

        client = InferenceClient(api_key=self._api_key, timeout=timeout)

        messages: list[Dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        started = time.perf_counter()
        try:
            reply = client.chat_completion(
                messages=messages,
                model=model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
        except Exception as exc:
            text = str(exc).lower()
            raise ProviderError(
                str(exc),
                provider=self.name,
                retryable=not any(marker in text for marker in _TERMINAL),
            ) from exc

        choices = getattr(reply, "choices", None) or []
        content = ""
        if choices:
            content = str(getattr(choices[0].message, "content", "") or "")

        return LLMResponse(
            text=content,
            provider=self.name,
            model=model,
            usage=self._usage(reply, content, model),
            latency_ms=int((time.perf_counter() - started) * 1000),
            caller=request.caller,
        )

    def _usage(self, reply: Any, content: str, model: str) -> Usage:
        """Reads token counts off the reply, falling back to an estimate."""
        meta = getattr(reply, "usage", None)
        prompt_tokens = int(getattr(meta, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(meta, "completion_tokens", 0) or 0)
        measured = bool(prompt_tokens or completion_tokens)

        if not measured:
            # Roughly four characters per token — enough to keep a budget honest
            # when a provider omits counts, and flagged as unmeasured.
            completion_tokens = max(1, len(content) // 4)

        prompt_price, completion_price = PRICES_PER_MTOK.get(model, _DEFAULT_PRICE)
        cost = (prompt_tokens * prompt_price + completion_tokens * completion_price) / 1_000_000

        return Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=round(cost, 6),
            measured=measured,
        )
