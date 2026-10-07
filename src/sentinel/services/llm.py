"""Deprecated — superseded by `sentinel.llm`.

The original single-function factory returned a LangChain chat model directly,
which meant every caller held a provider-shaped object and the "provider-agnostic
gateway" of M-01 existed in name only: there was no retry policy, no fallback,
no token accounting and no cache, because there was nowhere to put them.

`sentinel.llm.get_gateway()` replaces it. This shim stays only so that any
stale import fails with an explanation instead of an ImportError.
"""

from typing import Any


def get_llm(model_name: str | None = None, temperature: float = 0.1) -> Any:
    raise RuntimeError(
        "sentinel.services.llm.get_llm() has been replaced by the LLM gateway. "
        "Use: from sentinel.llm import get_gateway, ModelProfile; "
        "get_gateway().complete(prompt, profile=ModelProfile.REASONING, caller='<agent>')"
    )
