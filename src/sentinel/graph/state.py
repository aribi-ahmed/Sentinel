import operator
from typing import TypedDict, Optional, List, Dict, Any, Annotated

class GraphState(TypedDict, total=False):
    investigation_id: str
    subject_name: str
    ticker: str
    # Which specialists the supervisor engaged, and why it skipped the rest.
    plan: Dict[str, Any]
    research_data: List[Dict[str, Any]]
    financial_data: Dict[str, Any]
    news_data: List[Dict[str, Any]]
    # Synthesised control set from the policy frameworks: obligations plus the
    # passages they were drawn from.
    compliance_data: Dict[str, Any]
    # Merged OFAC SDN and OpenSanctions screening result.
    sanctions_data: Dict[str, Any]
    # SEC EDGAR registrant profile and supervisory filing flags.
    edgar_data: Dict[str, Any]
    risk_level: str
    # Full scored verdict: composite, band, per-dimension breakdown, drivers.
    risk_assessment: Dict[str, Any]
    supervisor_reasoning: str
    confidence: float
    requires_human_review: bool
    human_approved: Optional[bool]
    final_report: str
    # Written concurrently by all 4 parallel specialist nodes each superstep,
    # so these need a reducer to merge writes instead of the default
    # last-value-wins channel (which raises on concurrent updates).
    logs: Annotated[List[str], operator.add]
    # The evidence pool: every sourced fact any specialist collected, serialised.
    # Append semantics are what make the parallel fan-in safe.
    evidence: Annotated[List[Dict[str, Any]], operator.add]
    # M-04: every tool call, with its arguments, duration and outcome. Written
    # by the same four parallel nodes, so it needs the same append reducer.
    tool_calls: Annotated[List[Dict[str, Any]], operator.add]
    # Prior reviews of the same entity, read from the audit ledger.
    memory_data: Dict[str, Any]
    # Entities connected to the subject, derived from collected evidence.
    graph_data: Dict[str, Any]
    # Fraud indicators and the convergence patterns they form.
    fraud_data: Dict[str, Any]
    # The critic's most recent reading of the verdict. Written by a single node
    # after the fan-in, so last-value-wins is correct here - on a revision pass
    # the newer review is the one that matters.
    critic_review: Dict[str, Any]
    # How many times the critic has sent the verdict back. Bounded in the graph:
    # an unbounded critic loop is a way to burn a rate limit, not a quality gate.
    revision_count: int
