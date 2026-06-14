"""
Streaming Interface — Async Generator for LangGraph Events
===========================================================
Bridges LangGraph's astream_events API to Chainlit's async
event consumption pattern. Each agent step is yielded as a
typed event dict for real-time UI updates.

Architecture:
    Chainlit UI ← async for ← process_question_stream()
                                    │
                                    ▼
                            finance_graph.astream_events()
                                    │
                        ┌───────────┼───────────┐
                        ▼           ▼           ▼
                    node_start  node_end    final/error
"""

from agents.nodes import AgentState
from graph import finance_graph
from config import settings


# Nodes tracked in the streaming output
TRACKED_NODES = {
    "guardrails_agent",
    "sql_agent",
    "sql_validator_agent",
    "execute_sql",
    "sanity_check_agent",
    "analysis_agent",
    "error_agent",
    "decide_graph_need",
    "viz_agent",
}


async def process_question_stream(question: str):
    """
    Process a natural language question and stream agent events.

    Yields typed event dicts for each step of the workflow:
        {"type": "node_start", "node": "...", "input": {...}}
        {"type": "node_end",   "node": "...", "output": {...}, "state": {...}}
        {"type": "final",      "result": {...}}
        {"type": "error",      "error": "..."}

    Args:
        question: User's natural language question

    Yields:
        Event dicts for real-time Chainlit step updates
    """
    initial_state = AgentState(
        question=question,
        sql_query="",
        query_result="",
        final_answer="",
        error="",
        iteration=0,
        needs_graph=False,
        graph_type="",
        graph_json="",
        is_in_scope=True,
        sanity_passed=True,
        sanity_issue="",
        sanity_retried=False,
    )

    current_state = initial_state.copy()

    try:
        async for event in finance_graph.astream_events(
            initial_state,
            config={"recursion_limit": settings.GRAPH_RECURSION_LIMIT},
            version="v2",
        ):
            event_type = event.get("event")
            node_name = event.get("name", "")

            # ── Node started ──────────────────────────────────────────
            if event_type == "on_chain_start" and node_name in TRACKED_NODES:
                yield {
                    "type": "node_start",
                    "node": node_name,
                    "input": current_state,
                }

            # ── Node completed ────────────────────────────────────────
            elif event_type == "on_chain_end" and node_name in TRACKED_NODES:
                output = event.get("data", {}).get("output", {})
                if output is not None:
                    current_state.update(output)
                yield {
                    "type": "node_end",
                    "node": node_name,
                    "output": output or {},
                    "state": current_state.copy(),
                }

        # ── Workflow complete ─────────────────────────────────────────
        yield {"type": "final", "result": current_state}

    except Exception as e:
        yield {"type": "error", "error": str(e)}


def generate_graph_visualization(output_path: str = "finance_workflow.png") -> str | None:
    """
    Generate a PNG visualization of the LangGraph workflow.

    Requires pygraphviz or grandalf to be installed.

    Args:
        output_path: Path for the output PNG file

    Returns:
        Path to the generated image, or None on failure
    """
    try:
        image_bytes = finance_graph.get_graph().draw_mermaid_png()
        with open(output_path, "wb") as f:
            f.write(image_bytes)
        print(f"Workflow diagram saved: {output_path}")
        return output_path
    except Exception as e:
        print(f"Diagram generation error: {e}")
        return None
