"""The contract every model provider is held to.

Objective M-01 is that the same investigation runs on two providers with no code
change. That only holds if callers never see a provider-shaped object — so they
ask for a *profile* ("give me the fast model") and get back an `LLMResponse`,
never a LangChain message, never a Groq client.

Nothing here imports a provider SDK. The adapters do, and only they.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Protocol, runtime_checkable


# Joins the fields of a cache key. A control character cannot occur in a prompt
# or a model name, so no combination of field values can collide with another.
_FIELD_SEPARATOR = "\x1f"


class ModelProfile(str, Enum):
    """What a caller needs from a model, not which model it wants.

    §5.1: *"Extraction does not need the model that reasoning needs."* Mapping a
    profile onto a concrete model is the gateway's job and lives in config, so
    swapping models is a settings change rather than a code change.
    """

    FAST = "fast"              # cheap, short, schema-shaped work
    REASONING = "reasoning"    # the supervisor's judgement calls
    EXTRACTION = "extraction"  # structured pulls from retrieved text


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts and the cost they imply."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    # False when the provider gave no counts and these are estimates.
    measured: bool = True

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cost_usd=round(self.cost_usd + other.cost_usd, 6),
            measured=self.measured and other.measured,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "measured": self.measured,
        }


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """One completion request, in provider-neutral terms."""

    prompt: str
    profile: ModelProfile = ModelProfile.REASONING
    temperature: float = 0.1
    max_tokens: Optional[int] = None
    system: str = ""
    # Which agent asked, so usage can be attributed in the console.
    caller: str = "unknown"

    def cache_key_material(self, model: str) -> str:
        """Everything that must match for a cached reply to be reusable."""
        return _FIELD_SEPARATOR.join(
            [self.system, self.prompt, model, f"{self.temperature:.3f}", str(self.max_tokens)]
        )


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """A completion plus everything needed to audit how it was obtained."""

    text: str
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    cached: bool = False
    attempts: int = 1
    # True when the primary provider failed and a fallback answered.
    fallback_used: bool = False
    caller: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "cached": self.cached,
            "attempts": self.attempts,
            "fallback_used": self.fallback_used,
            "caller": self.caller,
            "usage": self.usage.to_dict(),
        }


class ProviderError(RuntimeError):
    """A provider call failed. Carries whether retrying could plausibly help."""

    def __init__(self, message: str, *, provider: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class AllProvidersFailed(RuntimeError):
    """Every configured provider refused the request."""

    def __init__(self, errors: Dict[str, str]) -> None:
        detail = "; ".join(f"{name}: {message}" for name, message in errors.items())
        super().__init__(f"No provider could serve the request ({detail})")
        self.errors = errors


@runtime_checkable
class ProviderAdapter(Protocol):
    """What the gateway needs from any provider.

    Deliberately narrow (§10.1, Interface Segregation): one operation, plus
    enough identity to log and cost the call. Adding OpenRouter or Cerebras
    later means writing one of these, not editing the gateway.
    """

    name: str

    def is_available(self) -> bool:
        """Whether this adapter is configured well enough to be tried."""
        ...

    def model_for(self, profile: ModelProfile) -> str:
        """The concrete model this adapter uses for a profile."""
        ...

    def complete(self, request: LLMRequest, model: str, timeout: float) -> LLMResponse:
        """Performs one call. Raises `ProviderError` on failure."""
        ...
