"""
LangGraph Agent Nodes — MCP-backed agent implementations
=========================================================
Each agent is a LangGraph node function that receives AgentState,
calls one or more MCP tools through the MCP client, updates the
state, and returns it for the next node in the workflow.

All agents are stateless; the shared AgentState carries context
across the workflow. Agents never make direct function calls —
all operations go through the MCP client layer.

Architecture:
    Agent Node → MCPClient.call_tool("db"|"agent", tool, args)
                      │
                      ├── DBServerTools    (get_schema, execute_query, get_table_info)
                      └── AgentToolsServer (check_scope, generate_sql, validate_sql, ...)
"""

import json
import pandas as pd
from typing import TypedDict

from agents.mcp_client import MCPClient


# ══════════════════════════════════════════════════════════════════════════════
# Shared State Definition
# ══════════════════════════════════════════════════════════════════════════════

class AgentState(TypedDict):
    """State object passed between LangGraph nodes."""
    question:         str     # User's natural language question
    sql_query:        str     # Generated (or corrected) SQL query
    query_result:     str     # Query results in JSON format
    final_answer:     str     # Final natural language answer for the user
    error:            str     # Error message — empty if no error
    iteration:        int     # Current retry attempt counter
    needs_graph:      bool    # Should a visualization be generated?
    graph_type:       str     # Chart type: bar | line | pie | scatter
    graph_json:       str     # Plotly figure JSON for Chainlit
    is_in_scope:      bool    # Is the question about financial data?
    # ── Sanity Check fields ─────────────────────────────────────────────
    sanity_passed:    bool    # Are the results reasonable?
    sanity_issue:     str     # Description of detected issue
    sanity_retried:   bool    # Has SQL already been regenerated? (loop guard)


# ══════════════════════════════════════════════════════════════════════════════
# Agent Node Functions
# ══════════════════════════════════════════════════════════════════════════════

# Shared MCP client instance — reused across all agent calls
_mcp = MCPClient()


def guardrails_agent(state: AgentState) -> AgentState:
    """
    Scope & greeting filter agent.

    Calls MCP tool 'check_scope' to classify the user's question as:
    - In-scope (financial data) → proceed to SQL generation
    - Greeting → respond with welcome message, skip SQL
    - Out-of-scope → respond with redirection message, skip SQL
    """
    question = state["question"]

    # Call MCP agent server: check_scope tool
    result = _mcp.call_tool_json("agent", "check_scope", {"question": question})

    state["is_in_scope"] = result.get("is_in_scope", False)
    is_greeting = result.get("is_greeting", False)

    if is_greeting:
        state["final_answer"] = (
            "Hello! I'm your Finance Assistant. 💰\n\n"
            "You can ask me questions about your financial data for the 2024–2025 period:\n"
            "- Account balances and transaction history\n"
            "- Category-based income / expense analysis\n"
            "- Budget tracking and overruns\n"
            "- Invoice and payment statuses\n\n"
            "How can I help you today?"
        )
        return state

    if not state["is_in_scope"]:
        state["final_answer"] = (
            "I'm sorry, but this question is outside the scope of your financial data. "
            "I can help with:\n\n"
            "- 💳 Accounts and balances\n"
            "- 📊 Income / expense analysis\n"
            "- 🎯 Budget tracking\n"
            "- 🧾 Invoice and payment statuses\n\n"
            "Please ask a question related to your financial data."
        )
        return state

    return state


def sql_agent(state: AgentState) -> AgentState:
    """
    SQL generation agent.

    Calls MCP tool 'generate_sql' to convert the natural language question
    into a valid SQLite query. Includes sanity check feedback when a previous
    attempt produced unreasonable results.
    """
    question = state["question"]
    iteration = state.get("iteration", 0)

    # Pass sanity check feedback if available (for regeneration)
    feedback = state.get("sanity_issue", "")

    # Call MCP agent server: generate_sql tool
    result = _mcp.call_tool_json("agent", "generate_sql", {
        "question": question,
        "feedback": feedback,
    })

    state["sql_query"] = result.get("sql", "")
    state["iteration"] = iteration + 1
    return state


def sql_validator_agent(state: AgentState) -> AgentState:
    """
    SQL validation agent — fan-out detection & CTE rewrite.

    First performs a deterministic Python-side fan-out risk check.
    Only calls the MCP LLM tool if risk is detected — saving cost
    and latency for safe queries.
    """
    sql_query = state["sql_query"]
    question = state["question"]

    # Call MCP agent server: validate_sql tool
    # The server itself does deterministic pre-check internally
    result = _mcp.call_tool_json("agent", "validate_sql", {
        "sql": sql_query,
        "question": question,
    })

    state["sql_query"] = result.get("sql", sql_query)
    return state


def execute_sql(state: AgentState) -> AgentState:
    """
    SQL execution node.

    Calls MCP db server 'execute_query' tool to run the SQL against
    the SQLite database. On success, stores the result. On failure,
    stores the error for the error_agent to handle.
    """
    sql_query = state["sql_query"]

    # Call MCP database server: execute_query tool
    result = _mcp.call_tool("db", "execute_query", {
        "sql": sql_query,
        "max_rows": 100,
    })

    # Check if the result contains an error
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and "error" in parsed:
            state["error"] = parsed["error"]
            state["query_result"] = ""
        else:
            state["query_result"] = result
            state["error"] = ""
    except json.JSONDecodeError:
        # Plain text result (e.g., "No results found.")
        state["query_result"] = result
        state["error"] = ""

    return state


def error_agent(state: AgentState) -> AgentState:
    """
    Error recovery agent.

    Calls MCP tool 'fix_sql_error' to analyze the failed SQL and error
    message, then produce a corrected query. Gives up after 3 attempts.
    """
    error = state["error"]
    sql_query = state["sql_query"]
    question = state["question"]
    iteration = state.get("iteration", 0)

    # Max retry limit — gracefully give up
    if iteration > 3:
        state["final_answer"] = (
            f"I'm sorry, I couldn't generate a correct SQL query for this question. "
            f"Error: {error}\n\nPlease try rephrasing your question."
        )
        return state

    # Call MCP agent server: fix_sql_error tool
    result = _mcp.call_tool_json("agent", "fix_sql_error", {
        "sql": sql_query,
        "error": error,
        "question": question,
    })

    state["sql_query"] = result.get("sql", "")
    state["error"] = ""           # Clear error for re-execution
    state["iteration"] = iteration + 1
    return state


def sanity_check_agent(state: AgentState) -> AgentState:
    """
    Result sanity checker agent.

    Calls MCP tool 'check_sanity' to verify that query results are
    reasonable and consistent with the original question. If results
    are suspicious, flags them for SQL regeneration (one retry only).
    """
    question = state["question"]
    sql_query = state["sql_query"]
    query_result = state["query_result"]

    # Skip check if there are no results
    if not query_result or query_result == "No results found.":
        state["sanity_passed"] = True
        state["sanity_issue"] = ""
        return state

    # Loop guard: skip if already retried once
    if state.get("sanity_retried", False):
        state["sanity_passed"] = True
        state["sanity_issue"] = ""
        return state

    # Call MCP agent server: check_sanity tool
    result = _mcp.call_tool_json("agent", "check_sanity", {
        "question": question,
        "sql": sql_query,
        "results": query_result[:1000],
    })

    if result.get("is_reasonable", True):
        state["sanity_passed"] = True
        state["sanity_issue"] = ""
    else:
        # Issue detected — flag for SQL regeneration with loop guard
        state["sanity_passed"] = False
        state["sanity_issue"] = result.get("issue", "Results don't seem reasonable.")
        state["sanity_retried"] = True

    return state


def analysis_agent(state: AgentState) -> AgentState:
    """
    Results analysis agent.

    Calls MCP tool 'analyze_results' to convert raw JSON query results
    into a natural language answer for the user.
    """
    question = state["question"]
    sql_query = state["sql_query"]
    query_result = state["query_result"]

    # Call MCP agent server: analyze_results tool
    result = _mcp.call_tool_json("agent", "analyze_results", {
        "question": question,
        "sql": sql_query,
        "results": query_result,
    })

    state["final_answer"] = result.get("answer", "")
    return state


def decide_graph_need(state: AgentState) -> AgentState:
    """
    Graph decision agent.

    Calls MCP tool 'decide_graph_need' to determine whether query
    results would benefit from a visualization and which chart type
    is most appropriate.
    """
    question = state["question"]
    query_result = state["query_result"]

    # Skip if no results or there's a pending error
    if not query_result or query_result == "No results found." or state.get("error"):
        state["needs_graph"] = False
        state["graph_type"] = ""
        return state

    # Call MCP agent server: decide_graph_need tool
    result = _mcp.call_tool_json("agent", "decide_graph_need", {
        "question": question,
        "results": query_result[:500],
    })

    state["needs_graph"] = result.get("needs_graph", False)
    state["graph_type"] = result.get("graph_type", "none")
    return state


def viz_agent(state: AgentState) -> AgentState:
    """
    Visualization agent.

    Calls MCP tool 'generate_plotly' to produce Plotly code, then
    executes it with exec() to create a figure. The figure JSON is
    stored for Chainlit to render.

    Note: exec() has security implications. In production, use a
    sandboxed execution environment.
    """
    query_result = state["query_result"]
    graph_type = state["graph_type"]
    question = state["question"]
    plotly_code = ""

    try:
        results = json.loads(query_result)
        if not results:
            state["graph_json"] = ""
            return state

        df = pd.DataFrame(results)
        columns = df.columns.tolist()
        sample = df.head(5).to_dict("records")

        # Call MCP agent server: generate_plotly tool
        result = _mcp.call_tool_json("agent", "generate_plotly", {
            "question": question,
            "graph_type": graph_type,
            "columns": json.dumps(columns),
            "sample": json.dumps(sample, indent=2, ensure_ascii=False),
            "row_count": len(df),
        })

        plotly_code = result.get("code", "")

        # Execute LLM-generated Plotly code
        import plotly.graph_objects as go
        import plotly.express as px

        exec_env = {"df": df, "pd": pd, "json": json, "go": go, "px": px}
        exec(plotly_code, exec_env)

        fig = exec_env.get("fig")
        if fig is None:
            raise ValueError("Generated code did not create a 'fig' variable.")

        state["graph_json"] = fig.to_json()

    except Exception as e:
        print(f"Graph generation error: {e}")
        print(f"Generated code:\n{plotly_code}")
        state["graph_json"] = ""

    return state
