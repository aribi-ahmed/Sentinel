"""Provider-agnostic access to language models (objective M-01).

The composition root for this subsystem: `build_gateway()` reads configuration
once and wires the adapters, cache and ledger. Callers ask for the gateway and
get an object that can complete a prompt — they never learn which provider
answered unless they look at the response metadata.

Adding a provider means writing one adapter and naming it in `LLM_PROVIDER` /
`LLM_FALLBACK_PROVIDER`. No other file changes, which is what "swappable by
configuration" has to mean if it is to be more than a slogan.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sentinel.llm.cache import build_cache
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

logger = logging.getLogger(__name__)

__all__ = [
    "AllProvidersFailed",
    "LLMGateway",
    "LLMRequest",
    "LLMResponse",
    "ModelProfile",
    "ProviderAdapter",
    "ProviderError",
    "Usage",
    "UsageLedger",
    "build_gateway",
    "get_gateway",
    "reset_gateway",
]

_gateway: Optional[LLMGateway] = None


def _adapter(name: str) -> Optional[ProviderAdapter]:
    """Builds one adapter by name. Unknown names are logged, not fatal."""
    key = (name or "").strip().lower()

    if key == "groq":
        from sentinel.llm.adapters.groq_adapter import GroqAdapter

        return GroqAdapter()
    if key == "ollama":
        from sentinel.llm.adapters.ollama_adapter import OllamaAdapter

        return OllamaAdapter()
    if key in ("huggingface", "hf"):
        from sentinel.llm.adapters.huggingface_adapter import HuggingFaceAdapter

        return HuggingFaceAdapter()
    if key:
        logger.warning("Unknown LLM provider %r; ignoring.", name)
    return None


def build_gateway(
    *,
    primary: Optional[str] = None,
    fallback: Optional[str] = None,
    redis_url: Optional[str] = None,
) -> LLMGateway:
    """Wires the gateway from configuration.

    The fallback is opt-out rather than opt-in: a rate-limited free tier is the
    normal operating condition here, so having somewhere to go is the default.
    """
    from sentinel.config.settings import settings

    primary_name = primary or settings.LLM_PROVIDER
    fallback_name = fallback if fallback is not None else settings.LLM_FALLBACK_PROVIDER

    adapters: List[ProviderAdapter] = []
    seen: Dict[str, bool] = {}
    for name in (primary_name, fallback_name):
        if not name or name.lower() in seen:
            continue
        adapter = _adapter(name)
        if adapter is not None:
            adapters.append(adapter)
            seen[name.lower()] = True

    if not adapters:
        # Never leave the system with no door at all; Groq is the documented default.
        from sentinel.llm.adapters.groq_adapter import GroqAdapter

        adapters.append(GroqAdapter())

    cache = build_cache(redis_url if redis_url is not None else settings.REDIS_URL)

    return LLMGateway(
        adapters,
        cache=cache,
        max_attempts=settings.LLM_MAX_ATTEMPTS,
        timeout=settings.LLM_TIMEOUT_SECONDS,
        cache_ttl_seconds=settings.LLM_CACHE_TTL_SECONDS,
    )


def get_gateway() -> LLMGateway:
    """The process-wide gateway, built on first use."""
    global _gateway
    if _gateway is None:
        _gateway = build_gateway()
        names = [f"{p['name']}({p['role']})" for p in _gateway.providers if p["available"]]
        logger.info(
            "LLM gateway ready: %s; cache %s",
            ", ".join(names) or "no provider available",
            "on" if _gateway.cache_enabled else "off",
        )
    return _gateway


def reset_gateway() -> None:
    """Drops the cached gateway. Used by tests that swap configuration."""
    global _gateway
    _gateway = None
