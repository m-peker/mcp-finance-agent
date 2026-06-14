"""
LangGraph Workflow Definition — Multi-Agent State Machine
==========================================================
Defines the complete agent workflow as a LangGraph StateGraph.
All agent nodes communicate through the MCP layer (agents/nodes.py)
rather than making direct function calls.

Workflow:
    guardrails_agent
        ├── out of scope → END
        └── in scope → sql_agent → sql_validator_agent → execute_sql
                                   ├── error → error_agent → execute_sql (max 3×)
                                   └── success → sanity_check_agent
                                       ├── unreasonable → sql_agent (retry 1×)
                                       └── reasonable → analysis_agent → decide_graph_need
                                           ├── no graph → END
                                           └── graph → viz_agent → END
"""

from langgraph.graph import StateGraph, END
from agents.nodes import (
    AgentState,
    guardrails_agent,
    sql_agent,
    sql_validator_agent,
    execute_sql,
    error_agent,
    sanity_check_agent,
    analysis_agent,
    decide_graph_need,
    viz_agent,
)
from config import settings


# ══════════════════════════════════════════════════════════════════════════════
# Routing Functions (Conditional Edges)
# ══════════════════════════════════════════════════════════════════════════════

def check_scope(state: AgentState) -> str:
    """Route based on scope check result."""
    return "in_scope" if state.get("is_in_scope", True) else "out_of_scope"


def should_retry(state: AgentState) -> str:
    """
    Route after SQL execution.
    - Error + within retry limit → retry with error_agent
    - Error + limit exceeded      → proceed to analysis (graceful failure)
    - No error                    → proceed to sanity check
    """
    if state.get("error"):
        return "retry" if state.get("iteration", 0) <= settings.MAX_ERROR_RETRIES else "end"
    return "success"


def should_regenerate_sql(state: AgentState) -> str:
    """
    Route after sanity check.
    - Results unreasonable → regenerate SQL (with feedback, one retry)
    - Results reasonable   → proceed to analysis
    """
    return "regenerate" if not state.get("sanity_passed", True) else "proceed"


def should_generate_graph(state: AgentState) -> str:
    """Route based on graph decision."""
    return "viz_agent" if state.get("needs_graph", False) else "skip_graph"


# ══════════════════════════════════════════════════════════════════════════════
# Graph Construction
# ══════════════════════════════════════════════════════════════════════════════

def create_finance_graph():
    """
    Build and compile the LangGraph state machine.

    Returns:
        Compiled LangGraph workflow ready for execution.
    """
    workflow = StateGraph(AgentState)

    # ── Register nodes ───────────────────────────────────────────────────
    workflow.add_node("guardrails_agent",    guardrails_agent)
    workflow.add_node("sql_agent",           sql_agent)
    workflow.add_node("sql_validator_agent", sql_validator_agent)
    workflow.add_node("execute_sql",         execute_sql)
    workflow.add_node("sanity_check_agent",  sanity_check_agent)
    workflow.add_node("analysis_agent",      analysis_agent)
    workflow.add_node("error_agent",         error_agent)
    workflow.add_node("decide_graph_need",   decide_graph_need)
    workflow.add_node("viz_agent",           viz_agent)

    # ── Define edges ─────────────────────────────────────────────────────
    workflow.set_entry_point("guardrails_agent")

    # Scope check → SQL generation or end
    workflow.add_conditional_edges(
        "guardrails_agent",
        check_scope,
        {"in_scope": "sql_agent", "out_of_scope": END},
    )

    # SQL generation → validation → execution
    workflow.add_edge("sql_agent",           "sql_validator_agent")
    workflow.add_edge("sql_validator_agent", "execute_sql")

    # Execution → sanity check (success), error correction (retry), or analysis (give up)
    workflow.add_conditional_edges(
        "execute_sql",
        should_retry,
        {
            "success": "sanity_check_agent",
            "retry":   "error_agent",
            "end":     "analysis_agent",
        },
    )

    # Sanity check → regenerate SQL or proceed to analysis
    workflow.add_conditional_edges(
        "sanity_check_agent",
        should_regenerate_sql,
        {"regenerate": "sql_agent", "proceed": "analysis_agent"},
    )

    # Error correction loop: retry execution
    workflow.add_edge("error_agent", "execute_sql")

    # Analysis → graph decision
    workflow.add_edge("analysis_agent", "decide_graph_need")

    # Graph decision → visualization or end
    workflow.add_conditional_edges(
        "decide_graph_need",
        should_generate_graph,
        {"viz_agent": "viz_agent", "skip_graph": END},
    )

    workflow.add_edge("viz_agent", END)

    return workflow.compile()


# Compiled once at import time; reused for all requests
finance_graph = create_finance_graph()
