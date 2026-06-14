"""
Finance Assistant — Chainlit Web Interface
===========================================
Receives user messages, streams the LangGraph agent workflow
in real-time with step-by-step visibility, and delivers the
final response (text + optional chart) to the user.

Startup:
    chainlit run app.py

Dependencies:
    stream.process_question_stream     → agent workflow events
    stream.generate_graph_visualization → workflow diagram (optional)
"""

import json
import chainlit as cl
from stream import process_question_stream, generate_graph_visualization

# ── Generate workflow diagram on startup (optional) ─────────────────────────
# Attempts to create a PNG visualization of the LangGraph architecture.
# Silently skipped if pygraphviz/grandalf are not installed.
try:
    diagram_path = generate_graph_visualization("finance_workflow.png")
    if diagram_path:
        print(f"✅ Workflow diagram generated: {diagram_path}")
except Exception as e:
    print(f"⚠️  Diagram could not be generated: {e}")


# ── Node Display Names ──────────────────────────────────────────────────────
# Maps internal LangGraph node names to human-readable labels
# shown in the Chainlit step panel.
NODE_DISPLAY_NAMES = {
    "guardrails_agent":    "🛡️  Scope Check",
    "sql_agent":           "📝 SQL Generation",
    "sql_validator_agent": "🔍 SQL Validation",
    "execute_sql":         "⚙️  Query Execution",
    "sanity_check_agent":  "🧠 Sanity Check",
    "analysis_agent":      "💬 Response Generation",
    "error_agent":         "🔧 Error Recovery",
    "decide_graph_need":   "📊 Graph Decision",
    "viz_agent":           "📈 Graph Generation",
}


# ══════════════════════════════════════════════════════════════════════════════
# CHAINLIT EVENT HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@cl.on_chat_start
async def start():
    """
    Triggered when a new chat session begins.
    Sends a welcome message with sample questions to the user.
    """
    await cl.Message(
        content=(
            "👋 **Welcome to the Finance Assistant!**\n\n"
            "Query your 2024–2025 financial data using natural language.\n\n"
            "**Sample questions:**\n"
            "- What is my total income and expenses this year?\n"
            "- What are my top 5 spending categories?\n"
            "- Which months did I exceed my budget?\n"
            "- Do I have any unpaid or overdue invoices?\n"
            "- What is the total spent via credit card?\n"
            "- What's my average monthly grocery spending?\n"
            "- What are my income sources besides salary?\n\n"
            "Type your question and I'll handle the rest! 🚀"
        )
    ).send()


@cl.on_message
async def main(message: cl.Message):
    """
    Triggered on every user message.
    Starts the agent workflow, streams each step live to the Chainlit panel,
    and sends the final response (text + optional chart) when complete.
    """
    user_question = message.content
    final_result = None
    node_steps = {}  # Track open Chainlit sub-steps by node name

    # Group all agent steps under a single parent step
    async with cl.Step(name="🤖 Agent Workflow", type="llm") as workflow_step:
        try:
            async for event in process_question_stream(user_question):
                event_type = event.get("type")

                # ── Node started ────────────────────────────────────────
                if event_type == "node_start":
                    node_name = event["node"]
                    display_name = NODE_DISPLAY_NAMES.get(node_name, node_name)

                    # Create a nested Chainlit step for this node
                    node_step = cl.Step(
                        name=display_name,
                        type="tool",
                        parent_id=workflow_step.id,
                    )
                    await node_step.send()
                    node_steps[node_name] = node_step

                # ── Node completed ───────────────────────────────────────
                elif event_type == "node_end":
                    node_name = event["node"]
                    output = event.get("output", {})

                    if node_name not in node_steps:
                        continue

                    # Format node output and update the step
                    node_steps[node_name].output = _format_node_output(node_name, output)
                    await node_steps[node_name].update()

                # ── Workflow complete ────────────────────────────────────
                elif event_type == "final":
                    final_result = event["result"]

                # ── Unexpected error ─────────────────────────────────────
                elif event_type == "error":
                    workflow_step.output = f"❌ **Error:** {event['error']}"
                    await workflow_step.update()
                    return

            workflow_step.output = "✅ Workflow completed."
            await workflow_step.update()

        except Exception as e:
            workflow_step.output = f"❌ **Unexpected error:** {str(e)}"
            await workflow_step.update()
            raise

    # ── Send final response ──────────────────────────────────────────────────
    if not final_result:
        return

    # Show generated SQL if available (not present for greetings/out-of-scope)
    if final_result.get("sql_query") and final_result["sql_query"].strip():
        response_text = (
            f"**Generated SQL:**\n"
            f"```sql\n{final_result['sql_query']}\n```\n\n"
            f"**Answer:**\n{final_result['final_answer']}"
        )
    else:
        # Greeting or out-of-scope — text only
        response_text = final_result["final_answer"]

    # Notify user if error persisted after 3 retries
    if final_result.get("error"):
        response_text += f"\n\n⚠️ **Note:** {final_result['error']}"

    await cl.Message(content=response_text).send()

    # ── Send chart (if available) ─────────────────────────────────────────────
    if final_result.get("needs_graph") and final_result.get("graph_json"):
        import plotly.graph_objects as go

        # Restore Plotly figure from JSON and send as Chainlit element
        fig = go.Figure(json.loads(final_result["graph_json"]))
        chart_type = final_result.get("graph_type", "chart").title()

        graph_element = cl.Plotly(
            name=f"{final_result.get('graph_type', 'chart')}_visualization",
            figure=fig,
            display="inline",
        )

        await cl.Message(
            content=(
                f"📊 **Interactive {chart_type} Chart**\n\n"
                "*Hover, zoom, or drag to explore!*"
            ),
            elements=[graph_element],
        ).send()


@cl.on_chat_end
async def end():
    """Sends a farewell message when the chat session ends."""
    await cl.Message(
        content="Goodbye! Thanks for using the Finance Assistant. 👋"
    ).send()


# ══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _format_node_output(node_name: str, output: dict) -> str:
    """
    Format each node's output for display in the Chainlit step panel.
    Selects relevant fields per node type; truncates long content.

    Args:
        node_name: Internal LangGraph node name
        output:    Node's state update dict

    Returns:
        Markdown-formatted output string
    """
    if node_name == "guardrails_agent":
        is_in_scope = output.get("is_in_scope", True)
        if is_in_scope:
            return "✅ Question is within financial scope, proceeding."
        return "⛔ Question is out of scope; skipping SQL generation."

    elif node_name == "sanity_check_agent":
        passed = output.get("sanity_passed", True)
        issue = output.get("sanity_issue", "")
        if passed:
            return "✅ Results are reasonable, proceeding."
        return f"⚠️ **Suspicious result detected, regenerating SQL:**\n{issue}"

    elif node_name == "sql_validator_agent":
        validated_sql = output.get("sql_query", "")
        return (
            f"**Validated SQL:**\n```sql\n{validated_sql}\n```"
            if validated_sql
            else "ℹ️ No fan-out risk detected, SQL unchanged."
        )

    elif node_name == "sql_agent":
        sql = output.get("sql_query", "")
        return f"**Generated SQL:**\n```sql\n{sql}\n```"

    elif node_name == "execute_sql":
        if output.get("error"):
            return f"❌ **Error:**\n```\n{output['error']}\n```"
        result = output.get("query_result", "")
        # Truncate long results for UI readability
        if len(result) > 500:
            result = result[:500] + "\n... (truncated)"
        return f"**Query Results:**\n```json\n{result}\n```"

    elif node_name == "error_agent":
        corrected = output.get("sql_query", "")
        iteration = output.get("iteration", 0)
        return f"**Corrected SQL (Attempt {iteration}):**\n```sql\n{corrected}\n```"

    elif node_name == "analysis_agent":
        answer = output.get("final_answer", "")
        return f"**Response:**\n{answer}"

    elif node_name == "decide_graph_need":
        needs_graph = output.get("needs_graph", False)
        graph_type = output.get("graph_type", "")
        if needs_graph:
            return f"✅ **Chart needed:** {graph_type.upper()} type selected."
        return "ℹ️ **No chart needed for this query.**"

    elif node_name == "viz_agent":
        return (
            "✅ Chart generated successfully."
            if output.get("graph_json")
            else "⚠️ Chart could not be generated, text response is sufficient."
        )

    # Unknown node — show raw output
    return str(output)


if __name__ == "__main__":
    # This file is not run directly; it's launched via Chainlit CLI:
    #   chainlit run app.py
    pass
