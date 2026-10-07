# src/sentinel/graph/workflow.py
from langgraph.graph import StateGraph, START, END
from sentinel.graph.checkpointer import CheckpointBackend, build_checkpointer
from sentinel.graph.state import GraphState
from sentinel.services.planning import MEMORY, SPECIALISTS, route_from_plan
from sentinel.graph.nodes import (
    intake_node,
    research_analyst_node,
    financial_analyst_node, 
    news_analyst_node,
    compliance_analyst_node,
    memory_node,
    graph_analyst_node,
    fraud_analyst_node,
    supervisor_node,
    critic_node,
    revise_node,
    human_approval_node,
    summary_node,
    cancelled_node
)

def route_specialists(state: GraphState):
    """Fans out to the specialists the intake step selected.

    Returning a *list* from a conditional edge is what makes LangGraph run the
    targets as parallel branches; returning one name would serialise them.
    """
    return route_from_plan(state.get("plan") or {})


# One revision, not more. An unbounded critic loop is a way to exhaust a rate
# limit rather than a quality gate, and a verdict the critic rejects twice is a
# verdict a human should see - not one the graph should keep grinding on.
MAX_REVISIONS = 1


def route_after_critic(state: GraphState) -> str:
    """Sends a rejected verdict back once, then hands it to the human either way.

    The human gate is the backstop: an unresolved rejection does not block
    release, it arrives at the officer's desk carrying the critic's objections.
    That is the honest behaviour - the system is advisory, and suppressing a
    verdict it could not fix would hide the disagreement rather than surface it.
    """
    review = state.get("critic_review") or {}
    if review.get("verdict") != "rejected":
        return "human_approval"
    if int(state.get("revision_count", 0) or 0) >= MAX_REVISIONS:
        return "human_approval"
    return "revise"


def route_after_approval(state: GraphState) -> str:
    if state.get("human_approved") is True:
        return "summary"
    return "cancelled"

def build_graph():
    builder = StateGraph(GraphState)

    # 1. Add Nodes
    builder.add_node("intake", intake_node)
    builder.add_node("research_analyst", research_analyst_node)
    builder.add_node("financial_analyst", financial_analyst_node)
    builder.add_node("news_analyst", news_analyst_node)
    builder.add_node("compliance_analyst", compliance_analyst_node)
    builder.add_node("memory", memory_node)
    builder.add_node("graph_analyst", graph_analyst_node)
    builder.add_node("fraud_analyst", fraud_analyst_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("critic", critic_node)
    builder.add_node("revise", revise_node)
    builder.add_node("human_approval", human_approval_node)
    builder.add_node("summary", summary_node)
    builder.add_node("cancelled", cancelled_node)

    # 2. Intake plans, then fans out only to the specialists it engaged.
    #    The map lets a specialist be skipped entirely; "supervisor" is the
    #    escape hatch for a request so empty that no specialist qualifies.
    builder.add_edge(START, "intake")
    builder.add_conditional_edges(
        "intake",
        route_specialists,
        {name: name for name in (*SPECIALISTS, MEMORY, "supervisor")},
    )

    # Second tier: the analytical agents depend on what the collectors gathered,
    # so they fan in from all of them and then run in parallel with each other.
    for collector in (*SPECIALISTS, MEMORY):
        builder.add_edge(collector, "fraud_analyst")
        builder.add_edge(collector, "graph_analyst")

    builder.add_edge("fraud_analyst", "supervisor")
    builder.add_edge("graph_analyst", "supervisor")
    
    # The supervisor's verdict is reviewed before anyone sees it. A rejected
    # verdict goes back once through `revise`, which only increments the counter
    # - the re-scoring itself is the supervisor's job, and doing it in its own
    # node is what keeps the loop bounded and visible in the execution trace.
    builder.add_edge("supervisor", "critic")
    builder.add_conditional_edges(
        "critic",
        route_after_critic,
        {"revise": "revise", "human_approval": "human_approval"},
    )
    builder.add_edge("revise", "supervisor")

    # 3. Conditional Routing
    builder.add_conditional_edges(
        "human_approval",
        route_after_approval,
        {
            "summary": "summary",
            "cancelled": "cancelled"
        }
    )

    builder.add_edge("summary", END)
    builder.add_edge("cancelled", END)

    return builder.compile(
        checkpointer=CHECKPOINT_BACKEND.saver,
        interrupt_before=["human_approval"]
    )


# Built once at import: the pool is long-lived and the tables are created on
# first use. Exposed so the API can report which backend is actually in play.
CHECKPOINT_BACKEND: CheckpointBackend = build_checkpointer()

app = build_graph()