"""Ollama adapter — local models.

The second provider M-01 requires, and the one that makes the requirement
meaningful: Groq and Ollama differ in every way that matters (hosted vs local,
metered vs free, rate-limited vs bounded by your GPU), so a system that runs
unchanged on both has genuinely abstracted the provider rather than renamed it.

It is also the fallback that keeps an investigation alive when the free tier
throttles, and it costs nothing — hence the zero price.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from sentinel.llm.contracts import (
    LLMRequest,
    LLMResponse,
    ModelProfile,
    ProviderError,
    Usage,
)

_TERMINAL = ("not found", "no such model", "404")


class OllamaAdapter:
    """Local inference via an Ollama daemon."""

    name = "ollama"

    def __init__(
        self,
        *,
        host: Optional[str] = None,
        models: Optional[Dict[ModelProfile, str]] = None,
    ) -> None:
        from sentinel.config.settings import settings

        self._host = host or settings.OLLAMA_HOST
        default = settings.OLLAMA_MODEL
        self._models = models or {
            ModelProfile.REASONING: default,
            ModelProfile.EXTRACTION: default,
            ModelProfile.FAST: default,
        }

    def is_available(self) -> bool:
        """A reachable daemon, checked cheaply.

        Import and connection are both probed: the package may be absent, or
        present with nothing listening, and neither should raise from here.
        """
        try:
            import httpx

            response = httpx.get(f"{self._host}/api/tags", timeout=1.5)
            return response.status_code == 200
        except Exception:
            return False

    def model_for(self, profile: ModelProfile) -> str:
        return self._models.get(profile, self._models[ModelProfile.REASONING])

    def complete(self, request: LLMRequest, model: str, timeout: float) -> LLMResponse:
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:
            raise ProviderError(
                "langchain-ollama is not installed", provider=self.name, retryable=False
            ) from exc

        from langchain_core.messages import HumanMessage, SystemMessage

        client = ChatOllama(
            model=model,
            temperature=request.temperature,
            base_url=self._host,
            num_predict=request.max_tokens,
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

        meta = getattr(reply, "usage_metadata", None) or {}
        prompt_tokens = int(meta.get("input_tokens", 0) or 0)
        completion_tokens = int(meta.get("output_tokens", 0) or 0)
        measured = bool(prompt_tokens or completion_tokens)
        if not measured:
            completion_tokens = max(1, len(str(getattr(reply, "content", "") or "")) // 4)

        return LLMResponse(
            text=str(getattr(reply, "content", "") or ""),
            provider=self.name,
            model=model,
            # Local inference has no marginal cost, so the number is truthfully zero.
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=0.0,
                measured=measured,
            ),
            latency_ms=int((time.perf_counter() - started) * 1000),
            caller=request.caller,
        )
