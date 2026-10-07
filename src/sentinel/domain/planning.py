"""The investigation plan — which specialists to engage, and why.

§5.4.1 makes the supervisor responsible for deciding *which specialists to
engage*: "not every investigation needs every agent". M-02 is then accepted on
routing decisions being **logged**, which means a decision has to be an object
with a reason attached, not an implicit branch taken silently.

Recording the skips matters as much as the engagements. An investigation that
ran without market data reached its verdict on thinner evidence, and the reader
of that verdict deserves to know that — and why.

Standard library only, like the rest of the domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Whether one specialist was engaged, and the reason either way."""

    agent: str
    engaged: bool
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(
                f"Routing decision for {self.agent!r} needs a reason; an unexplained "
                "branch is exactly what M-02 asks to be logged."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {"agent": self.agent, "engaged": self.engaged, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class InvestigationPlan:
    """The set of routing decisions taken before any specialist runs."""

    decisions: Tuple[RoutingDecision, ...]

    @property
    def engaged(self) -> Tuple[str, ...]:
        return tuple(d.agent for d in self.decisions if d.engaged)

    @property
    def skipped(self) -> Tuple[RoutingDecision, ...]:
        return tuple(d for d in self.decisions if not d.engaged)

    @property
    def is_complete(self) -> bool:
        """True when every specialist was engaged — the fullest evidence base."""
        return not self.skipped

    @property
    def strategy(self) -> str:
        return "full" if self.is_complete else "reduced"

    def decision_for(self, agent: str) -> RoutingDecision | None:
        return next((d for d in self.decisions if d.agent == agent), None)

    def summary(self) -> str:
        """One line for the audit log."""
        if self.is_complete:
            return f"Engaged all {len(self.engaged)} specialists."
        skipped = ", ".join(f"{d.agent} ({d.reason})" for d in self.skipped)
        return f"Engaged {len(self.engaged)} of {len(self.decisions)} specialists; skipped {skipped}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "engaged": list(self.engaged),
            "skipped": [d.to_dict() for d in self.skipped],
            "decisions": [d.to_dict() for d in self.decisions],
            "summary": self.summary(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "InvestigationPlan":
        if not isinstance(data, dict):
            return cls(decisions=())
        return cls(
            decisions=tuple(
                RoutingDecision(
                    agent=str(item.get("agent", "")),
                    engaged=bool(item.get("engaged")),
                    reason=str(item.get("reason") or "No reason recorded."),
                )
                for item in data.get("decisions", [])
                if isinstance(item, dict) and item.get("agent")
            )
        )
