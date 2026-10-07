"""Response cache for the gateway.

M-09's acceptance signal is *"repeated identical LLM/tool calls hit cache"*.
Two reasons it matters here beyond speed: the free tiers this project runs on
are rate-limited, and re-running the same investigation while developing should
not spend quota re-deriving an answer that has not changed.

Redis when it is there, a no-op when it is not — a missing cache must never be
the reason an investigation fails.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional, Protocol

from sentinel.llm.contracts import LLMResponse, Usage

logger = logging.getLogger(__name__)

KEY_PREFIX = "sentinel:llm:"


def cache_key(material: str) -> str:
    """A stable key for a request. SHA-256 so prompts never land in Redis keys."""
    return KEY_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class ResponseCache(Protocol):
    def get(self, key: str) -> Optional[LLMResponse]: ...
    def set(self, key: str, response: LLMResponse, ttl_seconds: int) -> None: ...

    @property
    def enabled(self) -> bool: ...


class NullCache:
    """Used when Redis is absent. Every lookup misses; nothing breaks."""

    enabled = False

    def get(self, key: str) -> Optional[LLMResponse]:
        return None

    def set(self, key: str, response: LLMResponse, ttl_seconds: int) -> None:
        return None


class RedisResponseCache:
    """Caches completions in Redis, keyed by prompt, model and parameters."""

    enabled = True

    __slots__ = ("_client",)

    def __init__(self, client) -> None:
        self._client = client

    def get(self, key: str) -> Optional[LLMResponse]:
        try:
            raw = self._client.get(key)
        except Exception as exc:  # a cache outage must not fail the run
            logger.debug("Cache read failed: %s", exc)
            return None
        if not raw:
            return None

        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None

        usage = payload.get("usage") or {}
        return LLMResponse(
            text=payload.get("text", ""),
            provider=payload.get("provider", "unknown"),
            model=payload.get("model", "unknown"),
            # A cache hit costs nothing, so the recorded cost is zeroed while the
            # token counts are kept — that is what makes "tokens saved" reportable.
            usage=Usage(
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                cost_usd=0.0,
                measured=bool(usage.get("measured", True)),
            ),
            latency_ms=0,
            cached=True,
        )

    def set(self, key: str, response: LLMResponse, ttl_seconds: int) -> None:
        if response.cached or not response.text.strip():
            return
        payload = json.dumps({
            "text": response.text,
            "provider": response.provider,
            "model": response.model,
            "usage": response.usage.to_dict(),
        })
        try:
            self._client.setex(key, ttl_seconds, payload)
        except Exception as exc:
            logger.debug("Cache write failed: %s", exc)


def build_cache(redis_url: str = "") -> ResponseCache:
    """Returns a Redis cache when one is reachable, otherwise a no-op."""
    if not redis_url:
        return NullCache()

    try:
        import redis

        client = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
    except Exception as exc:
        logger.info("LLM response cache disabled (%s); calls will not be cached.", exc)
        return NullCache()

    logger.info("LLM response cache active on Redis.")
    return RedisResponseCache(client)
