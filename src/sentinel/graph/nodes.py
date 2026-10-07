# src/sentinel/graph/nodes.py
import ast
from typing import Any, Dict, List

from sentinel.domain import EvidencePool, InvestigationPlan
from sentinel.domain.analysis import RiskAssessment
from sentinel.domain.review import CriticReview
from sentinel.domain.tooling import ToolAuditTrail
from sentinel.graph.state import GraphState
from sentinel.services import evidence_builder
from sentinel.services.planning import plan_investigation
from sentinel.services.risk import build_assessment
# M-04: specialists no longer import tools directly. Every call goes through the
# registry, which is what makes them uniform and the audit trail complete.
from sentinel.tools.registry import get_registry
from sentinel.agents.research_agent import fetch_entity_baseline


def intake_node(state: GraphState) -> Dict[str, Any]:
    """Plans the investigation before any specialist runs.

    §5.4.1: the supervisor "chooses which specialists to engage — not every
    investigation needs every agent". Deciding here rather than inside the
    specialists means the skip and its reason are recorded once, in the state,
    where the report and the console can both read them.
    """
    subject = state.get("subject_name") or ""
    ticker = state.get("ticker") or ""

    plan = plan_investigation(subject, ticker)

    return {
        "plan": plan.to_dict(),
        "logs": [f"Supervisor planned the investigation. {plan.summary()}"],
    }


def _parse_osint(results: Any) -> List[Dict[str, Any]]:
    """Recovers structured search hits from the tool's stringified output.

    `search_company_news` returns `str(list_of_dicts)`, so the structure has to
    be read back before each hit can become its own piece of evidence. A parse
    failure is not fatal: the raw text still reaches the OSINT panel, it just
    yields no citable records.
    """
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]

    text = str(results or "").strip()
    if not text.startswith("["):
        return []
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def research_analyst_node(state: GraphState) -> Dict[str, Any]:
    """Establishes the corporate baseline and pulls the SEC filing history."""
    subject = state.get("subject_name") or "Target Entity"
    ticker = state.get("ticker") or ""

    trail = ToolAuditTrail()

    baseline = fetch_entity_baseline(subject_name=subject, ticker=ticker)
    call = get_registry().invoke(
        "fetch_sec_filings", caller="research_analyst", trail=trail,
        subject_name=subject, ticker=ticker,
    )
    edgar = call.data

    if edgar.get("matched"):
        detail = (
            f"SEC registrant CIK {edgar['cik']} resolved; {len(edgar.get('flags', []))} "
            f"supervisory event(s) across {edgar.get('filings_reviewed', 0)} filings."
        )
    else:
        # The registry already separated "not a registrant" from "the lookup did
        # not run"; say which, instead of reporting both as the same absence.
        detail = call.invocation.result_digest or call.reason or "no SEC registrant matched."

    filing_evidence = evidence_builder.from_edgar(edgar)
    evidence = evidence_builder.from_baseline(baseline, subject) + filing_evidence
    trail.attribute(call.invocation, [item.id for item in filing_evidence])

    return {
        "research_data": [baseline],
        "edgar_data": edgar,
        "evidence": [item.to_dict() for item in evidence],
        "tool_calls": [record.to_dict() for record in trail],
        "logs": [
            f"Research Agent gathered baseline profile for {subject}; {detail} "
            f"{len(evidence)} evidence record(s) filed."
        ],
    }


def financial_analyst_node(state: GraphState) -> Dict[str, Any]:
    """Node that extracts live financial metrics using stock ticker.

    The planner does not engage this specialist without a ticker, so the
    no-ticker path should be unreachable. It is handled anyway, and handled by
    declining rather than defaulting: the previous fallback substituted a fixed
    symbol, which would have filed one company's market data as evidence about
    another — the worst outcome available, because the verdict would look fully
    sourced. If routing ever changes, this says so instead.
    """
    ticker = (state.get("ticker") or "").strip()
    trail = ToolAuditTrail()

    if not ticker:
        return {
            "financial_data": {},
            "logs": [
                "Financial Analyst skipped: no ticker supplied, so there is no "
                "market data to retrieve. No market dimension was scored."
            ],
        }

    call = get_registry().invoke(
        "fetch_company_financials", caller="financial_analyst", trail=trail, ticker=ticker,
    )
    data = call.data
    evidence = evidence_builder.from_financials(data, ticker)
    trail.attribute(call.invocation, [item.id for item in evidence])

    outcome = (
        f"{len(evidence)} metric(s) filed as evidence."
        if call.ok
        else f"market data unavailable - {call.reason}"
    )

    return {
        "financial_data": data,
        "evidence": [item.to_dict() for item in evidence],
        "tool_calls": [record.to_dict() for record in trail],
        "logs": [f"Financial Analyst pulled market data for ticker: {ticker}; {outcome}"],
    }


def news_analyst_node(state: GraphState) -> Dict[str, Any]:
    """Node that searches live web OSINT."""
    subject = state.get("subject_name") or state.get("ticker") or "Target Company"
    trail = ToolAuditTrail()

    call = get_registry().invoke(
        "search_company_news", caller="news_analyst", trail=trail, query=subject,
    )
    # This tool reports its own failures as ordinary strings. Until the registry
    # classified them, the warning text emitted when no search key is configured
    # was stored as news_data and read downstream as reporting about the entity.
    results = call.data if call.ok else ""

    evidence = evidence_builder.from_osint(_parse_osint(results))
    trail.attribute(call.invocation, [item.id for item in evidence])

    outcome = (
        f"{len(evidence)} source(s) filed as evidence."
        if call.ok
        else f"no open-source reporting collected - {call.reason}"
    )

    return {
        "news_data": [{"query": subject, "results": str(results)}],
        "evidence": [item.to_dict() for item in evidence],
        "tool_calls": [record.to_dict() for record in trail],
        "logs": [f"News Analyst gathered OSINT search results for {subject}; {outcome}"],
    }


def compliance_analyst_node(state: GraphState) -> Dict[str, Any]:
    """Retrieves the applicable controls and screens the target against OFAC.

    Both halves are compliance work: the frameworks say what the entity must do,
    and the SDN list says whether dealing with it is permitted at all.
    """
    subject = state.get("subject_name") or state.get("ticker") or "Target Entity"
    query = (
        f"corporate governance oversight, financial crime and anti-money-laundering "
        f"controls, litigation and regulatory disclosure duties relevant to {subject}"
    )

    trail = ToolAuditTrail()
    registry = get_registry()

    policy_call = registry.invoke(
        "build_compliance_brief", caller="compliance_analyst", trail=trail,
        query=query, subject=subject, context=subject,
    )
    screening_call = registry.invoke(
        "screen_sanctions", caller="compliance_analyst", trail=trail, subject_name=subject,
    )
    brief, screening = policy_call.data, screening_call.data

    obligations = len(brief.get("obligations", []))
    status = screening.get("status", "UNAVAILABLE")

    policy_evidence = evidence_builder.from_compliance(brief)
    sanctions_evidence = evidence_builder.from_sanctions(screening)
    evidence = policy_evidence + sanctions_evidence
    trail.attribute(policy_call.invocation, [item.id for item in policy_evidence])
    trail.attribute(screening_call.invocation, [item.id for item in sanctions_evidence])

    return {
        "compliance_data": brief,
        "sanctions_data": screening,
        "evidence": [item.to_dict() for item in evidence],
        "tool_calls": [record.to_dict() for record in trail],
        "logs": [
            f"Compliance Analyst synthesised {obligations} obligation(s) from "
            f"{brief.get('retrieval', 'no')} sources; OFAC screening returned {status}."
        ],
    }


def memory_node(state: GraphState) -> Dict[str, Any]:
    """Reads the audit ledger for prior reviews of the same entity."""
    subject = state.get("subject_name") or ""
    ticker = state.get("ticker") or ""

    trail = ToolAuditTrail()

    call = get_registry().invoke(
        "recall_entity_history", caller="memory", trail=trail,
        subject=subject, ticker=ticker, exclude_id=state.get("investigation_id") or "",
    )
    history = call.data

    if history.get("is_first_review"):
        log = f"Memory Agent found no prior review of {subject or ticker or 'the subject'}."
    else:
        log = (
            f"Memory Agent recalled {history.get('count')} prior review(s) of "
            f"{history.get('subject')}; trend {str(history.get('trend_label', '')).lower()}."
        )

    return {
        "memory_data": history,
        "tool_calls": [record.to_dict() for record in trail],
        "logs": [log],
    }


def graph_analyst_node(state: GraphState) -> Dict[str, Any]:
    """Builds the entity relationship graph from what the specialists collected."""
    subject = state.get("subject_name") or state.get("ticker") or "Subject"
    pool = EvidencePool.from_list(state.get("evidence") or [])

    trail = ToolAuditTrail()

    call = get_registry().invoke(
        "resolve_entity_network", caller="graph_analyst", trail=trail,
        subject=subject,
        ticker=state.get("ticker") or "",
        edgar=state.get("edgar_data") or {},
        sanctions=state.get("sanctions_data") or {},
        news=state.get("news_data"),
        evidence=pool,
    )
    graph = call.data

    log = (
        f"Network Agent resolved {graph.get('node_count', 0)} connected entities across "
        f"{graph.get('edge_count', 0)} relationships."
    )
    if graph.get("flagged_count"):
        log += f" {graph['flagged_count']} carry a watchlist association."

    return {
        "graph_data": graph,
        "tool_calls": [record.to_dict() for record in trail],
        "logs": [log],
    }


def fraud_analyst_node(state: GraphState) -> Dict[str, Any]:
    """Scores the convergence of fraud indicators across the evidence."""
    pool = EvidencePool.from_list(state.get("evidence") or [])

    trail = ToolAuditTrail()

    call = get_registry().invoke(
        "assess_fraud_signals", caller="fraud_analyst", trail=trail,
        edgar=state.get("edgar_data") or {},
        financials=state.get("financial_data") or {},
        news=state.get("news_data"),
        evidence=pool,
        research=state.get("research_data"),
    )
    assessment = call.data

    return {
        "fraud_data": assessment,
        "tool_calls": [record.to_dict() for record in trail],
        "logs": [f"Fraud Agent: {assessment.get('summary', 'no assessment produced.')}"],
    }


def supervisor_node(state: GraphState) -> Dict[str, Any]:
    """Scores the gathered intelligence into a composite risk verdict.

    The scoring itself lives in `sentinel.services.risk`, which anchors the
    financial and sanctions dimensions in computed values and holds the model to
    a written rubric for the judgement dimensions.
    """
    subject = state.get("subject_name") or state.get("ticker") or "Target Company"

    # Rehydrate the pool the specialists filled, so every factor can cite it.
    pool = EvidencePool.from_list(state.get("evidence") or [])

    # On a revision pass the critic's objections are put in front of the model.
    # First pass: empty, so the prompt is unchanged.
    previous = CriticReview.from_dict(state.get("critic_review"))
    critique = previous.briefing() if previous else ""

    assessment = build_assessment(
        subject=subject,
        ticker=state.get("ticker", ""),
        research=state.get("research_data", []),
        financials=state.get("financial_data", {}) or {},
        news=state.get("news_data", []),
        sanctions=state.get("sanctions_data", {}) or {},
        obligations=(state.get("compliance_data") or {}).get("obligations", []),
        edgar=state.get("edgar_data") or {},
        evidence=pool,
        critique=critique,
        fraud=state.get("fraud_data") or {},
    )

    band = assessment.band.value
    score = assessment.score
    ungrounded = assessment.ungrounded_factors()

    # A reduced plan means thinner evidence, and the log should say so rather
    # than leave the reader to infer it from a missing dimension.
    plan = InvestigationPlan.from_dict(state.get("plan") or {})
    scope_note = "" if plan.is_complete else f" Scope was reduced: {plan.summary()}"

    return {
        "risk_level": band,
        "risk_assessment": assessment.to_dict(pool),
        "supervisor_reasoning": assessment.summary,
        "confidence": assessment.confidence,
        # Anything above MODERATE gets a human in front of it before release.
        "requires_human_review": score >= 40,
        "logs": [
            f"Supervisor scored {subject} at {score}/100 ({band}) with "
            f"{int(assessment.confidence * 100)}% confidence, citing {len(pool)} "
            f"evidence record(s)."
            + (
                f" {len(ungrounded)} factor(s) rest on judgement alone: "
                f"{', '.join(f.id for f in ungrounded)}."
                if ungrounded else ""
            )
            + scope_note
        + (
            f" Revision {state.get('revision_count', 0)} after critic review."
            if critique else ""
        )
        ],
    }


def critic_node(state: GraphState) -> Dict[str, Any]:
    """Reads the supervisor's verdict and tries to break it.

    Deliberately reconstructs the verdict from *state* rather than receiving the
    supervisor's own objects: a reviewer that shares the author's working memory
    is not independent, and reading the serialised form is also what catches a
    verdict that survives in memory but not in the record.

    The node never raises. A critic that can fail an investigation converts a
    quality control into an availability risk.
    """
    assessment = RiskAssessment.from_dict(state.get("risk_assessment") or {})
    if assessment is None:
        return {
            "logs": ["Critic skipped: no assessment was produced to review."],
            "revision_count": state.get("revision_count", 0),
        }

    pool = EvidencePool.from_list(state.get("evidence") or [])
    trail = ToolAuditTrail.from_dicts(state.get("tool_calls") or [])
    plan = InvestigationPlan.from_dict(state.get("plan") or {})
    revision = int(state.get("revision_count", 0) or 0)

    own_trail = ToolAuditTrail()
    call = get_registry().invoke(
        "review_assessment", caller="critic", trail=own_trail,
        assessment=assessment, pool=pool, audit=trail, plan=plan, revision=revision,
    )
    review = call.data

    penalty = float(review.get("confidence_penalty") or 0.0)
    adjusted = round(max(0.0, min(1.0, assessment.confidence - penalty)), 2)
    note = "" if review.get("model_ok", True) else (
        f" (narrative check unavailable: {review.get('model_error')})"
    )

    return {
        "critic_review": review,
        "confidence": adjusted,
        # Anything the critic did not endorse goes in front of a human, whatever
        # the score says.
        "requires_human_review": bool(
            state.get("requires_human_review") or review.get("verdict") != "endorsed"
        ),
        "revision_count": revision,
        "tool_calls": [record.to_dict() for record in own_trail],
        "logs": [str(review.get("summary", "Critic produced no summary.")) + note],
    }


def revise_node(state: GraphState) -> Dict[str, Any]:
    """Counts one revision and sends the verdict back to the supervisor.

    A node rather than an edge side-effect, because the increment has to be a
    state write the checkpointer records - otherwise a resume after a crash
    would restart the loop with a counter that never moved, which is exactly how
    a bounded loop becomes unbounded.
    """
    revision = int(state.get("revision_count", 0) or 0) + 1
    review = state.get("critic_review") or {}
    objections = review.get("blocking", 0)

    return {
        "revision_count": revision,
        "logs": [
            f"Critic rejected the verdict on {objections} blocking objection(s); "
            f"returning it to the supervisor for revision {revision}."
        ],
    }


def human_approval_node(state: GraphState) -> Dict[str, Any]:
    """Checkpoint node for Human-in-the-Loop review."""
    return {
        "logs": [f"Human Checkpoint reached. Approved: {state.get('human_approved')}"]
    }


def _format_dimension_table(assessment: Dict[str, Any]) -> str:
    rows = [
        f"| {dimension['label']} | {dimension['score']:.0f} | {dimension['band']} | "
        f"{int(dimension['weight'] * 100)}% |"
        for dimension in assessment.get("dimensions", [])
    ]
    if not rows:
        return ""
    header = "| Dimension | Score | Band | Weight |\n| --- | ---: | --- | ---: |"
    return header + "\n" + "\n".join(rows)


def _format_list(title: str, items) -> str:
    if not items:
        return ""
    bullets = "\n".join(f"- {item}" for item in items)
    return f"\n#### {title}\n{bullets}\n"


def summary_node(state: GraphState) -> Dict[str, Any]:
    """Generates the final approved risk report."""
    subject = state.get("subject_name") or state.get("ticker")
    ticker = state.get("ticker", "N/A")
    assessment = state.get("risk_assessment") or {}
    score = assessment.get("score", "n/a")
    band = assessment.get("band", state.get("risk_level", "UNKNOWN"))
    confidence = assessment.get("confidence")
    screening = state.get("sanctions_data") or {}
    compliance = state.get("compliance_data") or {}

    confidence_line = f"{int(confidence * 100)}%" if isinstance(confidence, (int, float)) else "n/a"
    obligations = compliance.get("obligations", [])
    controls = "\n".join(
        f"- **{item['control']}** ({item['framework']}) — {item['requirement']}"
        for item in obligations
    ) or "- No specific obligations were retrieved for this entity."

    formatted_report = f"""### SENTINEL APPROVED RISK REPORT

**Target Entity:** **{subject} ({ticker})**
**Composite Risk:** `{score}/100` — `{band}` | **Confidence:** `{confidence_line}` | **Human Approved:** `True`

---

#### Supervisor Assessment
{assessment.get('summary', state.get('supervisor_reasoning', 'Approved by supervisor.'))}

#### Risk Breakdown
{_format_dimension_table(assessment)}
{_format_list('Key Risk Drivers', assessment.get('key_drivers'))}{_format_list('Mitigating Factors', assessment.get('mitigants'))}
#### Sanctions Screening
OFAC SDN screening returned **{screening.get('status', 'UNAVAILABLE')}** against {screening.get('list_size', 0):,} designated entries.

#### Applicable Controls
{controls}
"""
    return {
        "final_report": formatted_report,
        "logs": ["Approved structured report generated successfully."]
    }


def cancelled_node(state: GraphState) -> Dict[str, Any]:
    """Handles workflow cancellation when a human rejects the assessment."""
    subject = state.get("subject_name") or state.get("ticker")
    ticker = state.get("ticker", "N/A")
    assessment = state.get("risk_assessment") or {}
    score = assessment.get("score", "n/a")
    band = assessment.get("band", state.get("risk_level", "UNKNOWN"))

    formatted_report = f"""### SENTINEL CANCELLED RISK REPORT

**Target Entity:** **{subject} ({ticker})**
**Composite Risk:** `{score}/100` — `{band}` | **Human Approved:** `False`
**Status:** Rejected by Human Compliance Officer

---

#### Supervisor Assessment (not adopted)
{assessment.get('summary', 'No assessment was recorded.')}

The officer rejected this assessment, so it was not released. The evidence and
scoring remain in the ledger for review.
"""
    return {
        "final_report": formatted_report,
        "logs": ["Workflow cancelled by human approval checkpoint."]
    }
