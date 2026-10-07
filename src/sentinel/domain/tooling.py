"""Tool invocation records for the audit trail (M-04).

An invocation stores the arguments a tool was called with, not just its name:
"the entity was screened" cannot be verified, while "Bank Melli Iran was
screened at 14:32:07 against 1 of 2 providers" can be reproduced and disputed.

Arguments are redacted rather than dropped — secret-looking names are masked,
everything else is kept. Results are digested rather than stored, since a
filings payload runs to hundreds of kilobytes; the evidence ids on each record
lead back to the full data in the evidence pool.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sentinel.domain.evidence import new_id, utc_now

# Argument values are truncated at this length before being recorded. Long
# enough to stay meaningful, short enough that no single call can bloat a row.
MAX_ARGUMENT_CHARS = 200
MAX_DIGEST_CHARS = 300

# Argument names masked wherever they appear, whatever the tool. A tool author
# forgetting to declare a secret is the normal case, so the default has to be
# safe rather than the declaration.
ALWAYS_REDACT = ("api_key", "apikey", "token", "secret", "password", "authorization")

REDACTED = "***redacted***"


class ToolOutcome(str, Enum):
    """How a call ended — four states, because "failed" hides too much.

    `EMPTY` and `UNAVAILABLE` are the two that earn their place. A screening
    that ran and found nothing is a real, useful result; a screening that never
    ran because no dataset was present is not. Treating the second as the first
    is precisely the silent degradation that left this project with a
    non-functioning RAG pipeline for two weeks (ADR-005).
    """

    OK = "ok"                      # ran, returned something usable
    EMPTY = "empty"                # ran, found nothing — a real answer
    FAILED = "failed"              # ran and raised
    UNAVAILABLE = "unavailable"    # never ran: missing key, missing dataset

    @property
    def ran(self) -> bool:
        return self in (ToolOutcome.OK, ToolOutcome.EMPTY)


def _truncate(value: Any, limit: int) -> str:
    """Truncates to `limit` characters *including* the ellipsis."""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def redact_arguments(arguments: Dict[str, Any], extra: Iterable[str] = ()) -> Dict[str, Any]:
    """Masks secret-looking arguments and truncates the rest.

    Matching is on the argument *name*, lowercased and by substring, so
    `groq_api_key` is caught by the `api_key` rule without being declared.
    """
    masked = tuple(name.lower() for name in (*ALWAYS_REDACT, *extra))
    out: Dict[str, Any] = {}

    for name, value in (arguments or {}).items():
        lowered = str(name).lower()
        if any(secret in lowered for secret in masked):
            out[name] = REDACTED
        elif isinstance(value, (int, float, bool)) or value is None:
            out[name] = value
        else:
            out[name] = _truncate(value, MAX_ARGUMENT_CHARS)
    return out


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """One call to one tool, with enough detail to reproduce and dispute it."""

    tool: str
    caller: str
    outcome: ToolOutcome
    arguments: Dict[str, Any] = field(default_factory=dict)
    # Float, not int: an in-process derivation completes in well under a
    # millisecond, and truncating those to 0 reads as though it never ran.
    duration_ms: float = 0.0
    result_digest: str = ""
    error: str = ""
    evidence_ids: Tuple[str, ...] = ()
    id: str = field(default_factory=lambda: new_id("call"))
    called_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.tool.strip():
            raise ValueError("A tool invocation must name the tool it called.")
        if self.outcome in (ToolOutcome.FAILED, ToolOutcome.UNAVAILABLE) and not self.error.strip():
            raise ValueError(
                f"Invocation of {self.tool!r} ended {self.outcome.value} with no reason "
                "recorded; an unexplained failure is what M-04 asks to be auditable."
            )

    @property
    def ok(self) -> bool:
        return self.outcome.ran

    def one_line(self) -> str:
        """The audit-log rendering."""
        args = ", ".join(f"{k}={v!r}" for k, v in self.arguments.items()) or "no arguments"
        tail = self.error or self.result_digest or "no detail recorded"
        shown = f"{self.duration_ms:.2f}ms" if self.duration_ms < 1 else f"{self.duration_ms:.0f}ms"
        return f"{self.tool}({args}) -> {self.outcome.value} in {shown} - {tail}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "caller": self.caller,
            "outcome": self.outcome.value,
            "ok": self.ok,
            "arguments": dict(self.arguments),
            "duration_ms": round(self.duration_ms, 2),
            "result_digest": self.result_digest,
            "error": self.error,
            "evidence_ids": list(self.evidence_ids),
            "called_at": self.called_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["ToolInvocation"]:
        """Rebuilds a record read back from a checkpoint or the database.

        Returns `None` rather than raising on a malformed row: one corrupted
        audit entry must not stop an investigation from being read back.
        """
        if not isinstance(data, dict) or not str(data.get("tool") or "").strip():
            return None
        try:
            outcome = ToolOutcome(str(data.get("outcome", "ok")))
        except ValueError:
            outcome = ToolOutcome.OK
        try:
            called_at = datetime.fromisoformat(str(data.get("called_at", "")))
        except ValueError:
            called_at = utc_now()

        error = str(data.get("error") or "")
        if outcome in (ToolOutcome.FAILED, ToolOutcome.UNAVAILABLE) and not error.strip():
            error = "No reason recorded."

        return cls(
            tool=str(data["tool"]),
            caller=str(data.get("caller") or "unknown"),
            outcome=outcome,
            arguments=dict(data.get("arguments") or {}),
            duration_ms=float(data.get("duration_ms") or 0.0),
            result_digest=str(data.get("result_digest") or ""),
            error=error,
            evidence_ids=tuple(data.get("evidence_ids") or ()),
            id=str(data.get("id") or new_id("call")),
            called_at=called_at,
        )


@dataclass
class ToolAuditTrail:
    """Every tool call made during one investigation, in order."""

    invocations: List[ToolInvocation] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.invocations)

    def __iter__(self):
        return iter(self.invocations)

    def add(self, invocation: ToolInvocation) -> ToolInvocation:
        self.invocations.append(invocation)
        return invocation

    def attribute(self, invocation: ToolInvocation, evidence_ids: Iterable[str]) -> ToolInvocation:
        """Links a recorded call to the evidence it produced.

        This is what closes the chain. Evidence already names its source, and a
        risk factor already cites its evidence; without this step the trail can
        say a screening ran but not which of the pool's records came out of it.
        With it, a reviewer can walk verdict -> factor -> evidence -> the exact
        call, arguments included, that produced it.
        """
        updated = replace(invocation, evidence_ids=tuple(evidence_ids))
        for index, call in enumerate(self.invocations):
            if call.id == invocation.id:
                self.invocations[index] = updated
                return updated
        return self.add(updated)

    def for_tool(self, tool: str) -> List[ToolInvocation]:
        return [call for call in self.invocations if call.tool == tool]

    @property
    def failures(self) -> List[ToolInvocation]:
        """Calls that failed or never ran — the ones that thinned the evidence."""
        return [call for call in self.invocations if not call.ok]

    @property
    def empty(self) -> List[ToolInvocation]:
        """Calls that ran and found nothing. A finding, not a fault."""
        return [call for call in self.invocations if call.outcome is ToolOutcome.EMPTY]

    @property
    def total_duration_ms(self) -> float:
        return round(sum(call.duration_ms for call in self.invocations), 2)

    @property
    def tools_used(self) -> Tuple[str, ...]:
        seen: List[str] = []
        for call in self.invocations:
            if call.tool not in seen:
                seen.append(call.tool)
        return tuple(seen)

    def summary(self) -> str:
        """One line stating what ran, what found nothing, and what did not run.

        The three counts are kept apart on purpose. "All succeeded" would be a
        false statement about a run where a ticker resolved to no market data,
        and an officer reading the trail needs to tell a thin answer from a
        missing one.
        """
        if not self.invocations:
            return "No tools were called."

        failed, empty = self.failures, self.empty
        returned = len(self.invocations) - len(failed) - len(empty)
        line = (
            f"{len(self.invocations)} tool calls across {len(self.tools_used)} tools "
            f"in {self.total_duration_ms:.0f}ms: {returned} returned data"
        )
        if empty:
            line += f", {len(empty)} found nothing"
        if failed:
            names = ", ".join(sorted({call.tool for call in failed}))
            line += f", {len(failed)} did not run ({names})"
        return line + "."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": len(self.invocations),
            "tools_used": list(self.tools_used),
            "total_duration_ms": round(self.total_duration_ms, 2),
            "failures": len(self.failures),
            "summary": self.summary(),
            "invocations": [call.to_dict() for call in self.invocations],
        }

    @classmethod
    def from_dicts(cls, rows: Any) -> "ToolAuditTrail":
        if not isinstance(rows, list):
            return cls()
        rebuilt = (ToolInvocation.from_dict(row) for row in rows)
        return cls(invocations=[call for call in rebuilt if call is not None])
