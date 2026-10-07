"""SENTINEL's domain layer — the business concepts, free of any framework.

This package imports nothing but the standard library. No SQLAlchemy, no
LangChain, no LangGraph, no FastAPI, no pydantic. That constraint is the
dependency rule from the brief (§10.6) made concrete, and `tests/unit/
test_architecture.py` enforces it automatically rather than trusting review.

The practical consequence: the persistence layer, the orchestrator and the web
framework are all replaceable details. If every other package were deleted,
this one would still compile and still describe what SENTINEL is about.
"""

from sentinel.domain.analysis import (
    Finding,
    RiskAssessment,
    RiskBand,
    RiskFactor,
    Severity,
    compose,
)
from sentinel.domain.evidence import (
    Confidence,
    Evidence,
    EvidenceKind,
    EvidencePool,
    new_id,
    utc_now,
)
from sentinel.domain.intelligence import (
    EntityHistory,
    FraudAssessment,
    FraudPattern,
    FraudSignal,
    GraphEdge,
    GraphNode,
    HistoryTrend,
    KnowledgeGraph,
    PriorReview,
    SignalCategory,
)
from sentinel.domain.planning import InvestigationPlan, RoutingDecision
from sentinel.domain.review import (
    Challenge,
    ChallengeKind,
    ChallengeSeverity,
    CriticReview,
    ReviewVerdict,
    decide_verdict,
)
from sentinel.domain.tooling import (
    ToolAuditTrail,
    ToolInvocation,
    ToolOutcome,
    redact_arguments,
)
from sentinel.domain.investigation import (
    HumanDecision,
    Investigation,
    InvestigationStatus,
    Subject,
    Verdict,
)

__all__ = [
    "Challenge",
    "ChallengeKind",
    "ChallengeSeverity",
    "Confidence",
    "CriticReview",
    "Evidence",
    "EvidenceKind",
    "EvidencePool",
    "Finding",
    "HumanDecision",
    "Investigation",
    "InvestigationPlan",
    "InvestigationStatus",
    "RiskAssessment",
    "RiskBand",
    "RiskFactor",
    "ReviewVerdict",
    "RoutingDecision",
    "Severity",
    "EntityHistory",
    "FraudAssessment",
    "FraudPattern",
    "FraudSignal",
    "GraphEdge",
    "GraphNode",
    "HistoryTrend",
    "KnowledgeGraph",
    "PriorReview",
    "SignalCategory",
    "Subject",
    "ToolAuditTrail",
    "ToolInvocation",
    "ToolOutcome",
    "Verdict",
    "compose",
    "decide_verdict",
    "new_id",
    "redact_arguments",
    "utc_now",
]
