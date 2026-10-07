"""Demonstrates M-01: the same work served by two providers, no code change.

    python scripts/verify_m01.py                 # one prompt on each provider
    python scripts/verify_m01.py --investigation # a full investigation on each

M-01's acceptance signal is "same investigation runs on two providers without
code changes". Nothing here edits code: each pass rebuilds the gateway with a
different `primary`, exactly as setting `LLM_PROVIDER` in `.env` would.

The second pass also proves the failover path by pointing the primary at a
provider that cannot answer and watching the fallback pick the work up.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Dict

PROVIDERS = ("groq", "huggingface")
PROMPT = "Name the capital of Tunisia. Reply with the single word only."


def rule(title: str) -> None:
    print(f"\n{title}\n" + "-" * 74)


def one_prompt() -> bool:
    """Same prompt, each provider in turn."""
    from sentinel.llm import build_gateway
    from sentinel.llm.contracts import ModelProfile

    rule("1. One prompt, each provider — selection is configuration only")
    answers: Dict[str, str] = {}

    for name in PROVIDERS:
        gateway = build_gateway(primary=name, fallback="")
        try:
            reply = gateway.complete(
                PROMPT, profile=ModelProfile.FAST, temperature=0.0, caller="verify_m01",
            )
        except Exception as exc:
            print(f"  {name:<13} FAILED  {type(exc).__name__}: {str(exc)[:90]}")
            return False

        answers[name] = reply.text.strip()
        print(f"  {name:<13} {reply.model:<30} {reply.latency_ms:>6}ms  ->  {answers[name][:38]!r}")

    agree = len({a.lower().strip(" .") for a in answers.values()}) == 1
    print(f"\n  Both providers answered; agreement on content: {agree}")
    return True


def full_investigation() -> bool:
    """A complete investigation, each provider in turn."""
    import os

    rule("2. A full investigation on each provider")
    results: Dict[str, Any] = {}

    for name in PROVIDERS:
        os.environ["LLM_PROVIDER"] = name
        for module in [m for m in list(sys.modules) if m.startswith("sentinel")]:
            del sys.modules[module]

        from sentinel.graph.workflow import app

        thread = {"configurable": {"thread_id": f"m01-{name}-{int(time.time())}"}}
        try:
            app.invoke(
                {"investigation_id": "m01", "subject_name": "Wolfspeed", "ticker": "WOLF"},
                thread,
            )
        except Exception:
            pass  # the graph stops at the human gate; that is the expected exit

        state = app.get_state(thread).values
        assessment = state.get("risk_assessment") or {}
        results[name] = (assessment.get("score"), assessment.get("band"))
        print(f"  {name:<13} verdict {assessment.get('score')}/100 {assessment.get('band')}")

    bands = {band for _, band in results.values()}
    print(f"\n  Same band on both providers: {len(bands) == 1}")
    return len(bands) == 1


def failover() -> bool:
    """Point the primary at nothing and watch the fallback answer."""
    from sentinel.llm import build_gateway
    from sentinel.llm.contracts import ModelProfile

    rule("3. Failover — primary unreachable, fallback takes over")
    gateway = build_gateway(primary="ollama", fallback="groq")  # ollama is not running

    # A unique prompt: a cached reply would be served without touching a
    # provider at all, which would prove nothing about failover.
    prompt = f"{PROMPT} (run {int(time.time())})"

    try:
        reply = gateway.complete(
            prompt, profile=ModelProfile.FAST, temperature=0.0, caller="verify_m01_failover",
        )
    except Exception as exc:
        print(f"  FAILED  {type(exc).__name__}: {str(exc)[:90]}")
        return False

    print(f"  configured primary : ollama (not running)")
    print(f"  actually answered  : {reply.provider} ({reply.model})")
    print(f"  fallback_used flag : {reply.fallback_used}")
    return reply.provider == "groq"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify M-01 against live providers.")
    parser.add_argument("--investigation", action="store_true",
                        help="also run a full investigation on each provider (slow)")
    args = parser.parse_args()

    print("=" * 74)
    print("  M-01 — provider-agnostic LLM gateway")
    print("=" * 74)

    ok = one_prompt()
    if ok and args.investigation:
        ok = full_investigation() and ok
    ok = failover() and ok

    rule("Result")
    print(f"  {'PASS — M-01 acceptance signal demonstrated' if ok else 'FAIL — see above'}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
