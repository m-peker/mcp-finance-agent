"""
MCP Agent Tools Server — LLM-powered agent operations
======================================================
Exposes all LLM-backed agent operations as MCP tools.
Agents call these tools through the MCP layer instead of
making direct OpenAI API calls.

Tools exposed:
    - check_scope:        Validate whether a question is within financial scope
    - generate_sql:       Convert natural language to SQLite query
    - validate_sql:       Check for fan-out risk and rewrite using CTE
    - fix_sql_error:      Analyze and correct failed SQL queries
    - check_sanity:       Verify query results are reasonable
    - analyze_results:    Convert raw results to human-readable text
    - decide_graph_need:  Determine if a chart is appropriate
    - generate_plotly:    Produce executable Plotly visualization code

Usage (standalone):
    python mcp_servers/agent_server.py

Usage (embedded):
    from mcp_servers.agent_server import AgentToolsServer
    tools = AgentToolsServer()
    result = tools.handle_tool("generate_sql", {"question": "...", "feedback": ""})
"""

import json
import os
from typing import Any
from openai import OpenAI
from config import SCHEMA_INFO, AGENT_SYSTEM_PROMPTS, settings


class AgentToolsServer:
    """
    MCP-compatible agent tools handler.

    Each tool wraps an LLM call with a specialized system prompt
    and structured output format. Tools are stateless — all context
    is passed via arguments.
    """

    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # ── Tool Definitions (MCP-compatible metadata) ──────────────────────────

    @staticmethod
    def list_tools() -> list[dict]:
        """Return tool metadata in MCP format."""
        return [
            {
                "name": "check_scope",
                "description": "Determine whether a user question is within the financial data scope, a greeting, or out of scope.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The user's question in natural language."},
                    },
                    "required": ["question"],
                },
            },
            {
                "name": "generate_sql",
                "description": "Convert a natural language question into a valid SQLite query using the finance database schema.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The user's financial question."},
                        "feedback": {"type": "string", "description": "Optional feedback from sanity check for regeneration.", "default": ""},
                    },
                    "required": ["question"],
                },
            },
            {
                "name": "validate_sql",
                "description": "Check a SQL query for fan-out (cartesian product) risk and rewrite using CTE if needed.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "The SQL query to validate."},
                        "question": {"type": "string", "description": "The original user question for context."},
                    },
                    "required": ["sql", "question"],
                },
            },
            {
                "name": "fix_sql_error",
                "description": "Analyze a failed SQL query and its error message to produce a corrected query.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "The SQL query that failed."},
                        "error": {"type": "string", "description": "The error message from the database."},
                        "question": {"type": "string", "description": "The original user question."},
                    },
                    "required": ["sql", "error", "question"],
                },
            },
            {
                "name": "check_sanity",
                "description": "Evaluate whether query results are reasonable and consistent with the question.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The original user question."},
                        "sql": {"type": "string", "description": "The SQL query that produced the results."},
                        "results": {"type": "string", "description": "The query results (JSON string, first 1000 chars)."},
                    },
                    "required": ["question", "sql", "results"],
                },
            },
            {
                "name": "analyze_results",
                "description": "Convert raw SQL query results into a natural language answer.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The original user question."},
                        "sql": {"type": "string", "description": "The SQL query used."},
                        "results": {"type": "string", "description": "The query results (JSON string)."},
                    },
                    "required": ["question", "sql", "results"],
                },
            },
            {
                "name": "decide_graph_need",
                "description": "Determine if query results would benefit from a chart and which type.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The original user question."},
                        "results": {"type": "string", "description": "The query results (JSON string, first 500 chars)."},
                    },
                    "required": ["question", "results"],
                },
            },
            {
                "name": "generate_plotly",
                "description": "Generate executable Plotly Python code for a chart based on query results.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The original user question."},
                        "graph_type": {"type": "string", "description": "Chart type: bar, line, pie, or scatter."},
                        "columns": {"type": "string", "description": "JSON array of column names."},
                        "sample": {"type": "string", "description": "JSON array of sample rows (first 5)."},
                        "row_count": {"type": "integer", "description": "Total number of rows in the result."},
                    },
                    "required": ["question", "graph_type", "columns", "sample", "row_count"],
                },
            },
        ]

    # ── Tool Handlers ───────────────────────────────────────────────────────

    def handle_tool(self, name: str, arguments: dict) -> str:
        """Dispatch tool call to the appropriate handler."""
        handlers = {
            "check_scope": self._check_scope,
            "generate_sql": self._generate_sql,
            "validate_sql": self._validate_sql,
            "fix_sql_error": self._fix_sql_error,
            "check_sanity": self._check_sanity,
            "analyze_results": self._analyze_results,
            "decide_graph_need": self._decide_graph_need,
            "generate_plotly": self._generate_plotly,
        }
        handler = handlers.get(name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {name}"})
        return handler(arguments)

    # ── LLM Call Helper ─────────────────────────────────────────────────────

    def _llm_call(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0,
        response_format: str | None = None,
        timeout: int = 30,
    ) -> str:
        """Unified LLM call wrapper."""
        kwargs: dict[str, Any] = {
            "model": settings.OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "timeout": timeout,
        }
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}

        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content.strip()

    def _clean_sql(self, sql: str) -> str:
        """Remove markdown code fences from LLM-generated SQL."""
        return sql.replace("```sql", "").replace("```", "").strip()

    # ── Tool Implementations ────────────────────────────────────────────────

    def _check_scope(self, args: dict) -> str:
        """Check if the question is in scope, a greeting, or out of scope."""
        question = args["question"]

        prompt = f"""You are a scope checker for a personal finance database assistant.
Determine whether the user's question relates to financial data, is a greeting,
or is completely out of scope.

The database contains (2024–2025 period):
- Bank accounts and balances
- Income / expense transactions
- Category-based spending records (groceries, rent, salary, etc.)
- Monthly budget limits and actual spending
- Invoices and payment statuses

GREETING examples:
- "Hello", "Hi", "How are you?", "Good morning"

IN-SCOPE examples:
- "What is my total spending this month?"
- "Which categories exceeded budget?"
- "Do I have any unpaid invoices?"
- "What is my highest expense category?"
- "How much is my credit card debt?"

OUT-OF-SCOPE examples:
- Investment advice ("Should I buy Bitcoin?")
- General knowledge ("What is the capital of Turkey?")
- Weather, sports scores, politics, etc.

User question: {question}

Respond in JSON format:
{{
    "is_in_scope": true/false,
    "is_greeting": true/false,
    "reason": "brief explanation"
}}

Ambiguous questions that could relate to finance should be considered in-scope."""

        return self._llm_call(
            AGENT_SYSTEM_PROMPTS["guardrails"],
            prompt,
            temperature=0,
            response_format="json",
        )

    def _generate_sql(self, args: dict) -> str:
        """Generate SQL from natural language."""
        question = args["question"]
        feedback = args.get("feedback", "")

        feedback_section = ""
        if feedback:
            feedback_section = (
                f"\nPREVIOUS ATTEMPT ISSUE (do not repeat this error):\n{feedback}\n"
            )

        prompt = f"""Convert the following question into a valid SQLite query.

{SCHEMA_INFO}
{feedback_section}
Question: {question}

Rules:
1. Use ONLY tables and columns defined in the schema.
2. Use appropriate JOINs when multiple tables are needed.
3. Return ONLY the SQL query — no explanations or markdown.
4. If the question has multiple sub-questions, separate queries with semicolons.
5. Use COUNT, SUM, AVG aggregation functions appropriately.
6. Add DEFAULT LIMIT 10 to multi-row queries unless the user specifies otherwise.
7. Use ISO date format for comparisons (e.g., '2024-01-01').
8. Text comparisons are case-sensitive; match the database values exactly.
9. MANDATORY — Every column reference in a JOIN query MUST be qualified with a table alias:
   CORRECT:   SELECT t.amount, b.limit_amount FROM transactions t JOIN budgets b ON t.category_id = b.category_id
   INCORRECT: SELECT amount, limit_amount FROM transactions JOIN budgets ON category_id = category_id
   This prevents "ambiguous column name" errors when tables share column names like category_id.
10. MANDATORY — CTE column scope: Aliases defined inside a CTE (WITH block) CANNOT be
    used in the outer SELECT. Reference the CTE name instead:
    CORRECT:   WITH cte AS (SELECT t.id, b.year FROM ...) SELECT cte.year FROM cte
    INCORRECT: WITH cte AS (SELECT t.id, b.year FROM ...) SELECT b.year FROM cte
11. CRITICAL — budgets vs transactions relationship:
    budgets.spent_amount is PRE-COMPUTED monthly spending. Prefer using it directly
    for budget-vs-actual comparisons. Do NOT join transactions with budgets to
    calculate spending — transactions has transaction_date (YYYY-MM-DD) but budgets
    has separate month (INTEGER) and year (INTEGER) columns, causing fan-out.
    If you MUST extract month/year from transactions:
      CAST(strftime('%m', t.transaction_date) AS INTEGER) AS tx_month
      CAST(strftime('%Y', t.transaction_date) AS INTEGER) AS tx_year
12. CRITICAL — Cartesian product (fan-out) avoidance:
    When computing SUM, COUNT, or AVG across multiple tables,
    calculate EACH aggregation in a separate CTE (WITH block), then join.
    WRONG: JOIN transactions JOIN invoices → SUM(amount), COUNT(invoice_id)
    RIGHT: WITH tx AS (SELECT category_id, SUM(amount) FROM transactions GROUP BY category_id)
           WITH inv AS (SELECT category_id, COUNT(*) FROM invoices GROUP BY category_id)
           Then JOIN the CTEs in the final query.
13. Always JOIN with categories table to show category_name instead of raw IDs.
14. CRITICAL DATE FORMAT: budgets table stores month (INTEGER 1-12) and year (INTEGER).
    To convert to date: printf('%04d-%02d', year, month) → '2024-01'
    Do NOT use: year || '-' || month || '-01' (leads to NULL in SQLite!)

Write the SQL query:"""

        sql = self._llm_call(
            AGENT_SYSTEM_PROMPTS["sql_generator"],
            prompt,
            temperature=0,
        )
        return json.dumps({"sql": self._clean_sql(sql)})

    @staticmethod
    def _has_fanout_risk(sql: str) -> bool:
        """
        Deterministic fan-out risk detection (no LLM call).

        Returns True when ALL three conditions are met:
        1. At least one JOIN is present
        2. At least one aggregation function is used
        3. No CTE (WITH block) is already wrapping aggregations
        """
        sql_upper = sql.upper().strip()
        has_join = " JOIN " in sql_upper
        has_agg = any(f in sql_upper for f in ["SUM(", "COUNT(", "AVG(", "MAX(", "MIN("])
        has_cte = sql_upper.startswith("WITH ")
        return has_join and has_agg and not has_cte

    def _validate_sql(self, args: dict) -> str:
        """Validate SQL for fan-out risk and rewrite if needed."""
        sql = args["sql"]
        question = args["question"]

        # Deterministic pre-check — skip LLM if no risk
        if not self._has_fanout_risk(sql):
            return json.dumps({"sql": sql, "modified": False})

        # LLM-based CTE rewrite for risky queries
        prompt = f"""A fan-out (cartesian product) risk was detected in the SQL below.
Rewrite it using the safe CTE pattern where each aggregation is isolated.

PROBLEM:
Multiple tables are JOINed while computing SUM/COUNT/AVG in the same query.
This causes row multiplication and incorrect inflated results.

SOLUTION — Isolate each aggregation in its own CTE:
    WITH tx AS (
        SELECT category_id, SUM(amount) AS total
        FROM transactions GROUP BY category_id
    ),
    inv AS (
        SELECT category_id, COUNT(*) AS cnt
        FROM invoices WHERE status = 'overdue' GROUP BY category_id
    )
    SELECT c.category_name, tx.total, COALESCE(inv.cnt, 0)
    FROM categories c
    JOIN tx ON tx.category_id = c.category_id
    LEFT JOIN inv ON inv.category_id = c.category_id

Original Question: {question}

SQL to rewrite:
{sql}

Produce ONLY the rewritten SQL — no markdown or explanations."""

        validated_sql = self._llm_call(
            AGENT_SYSTEM_PROMPTS["sql_validator"],
            prompt,
            temperature=0,
        )
        return json.dumps({"sql": self._clean_sql(validated_sql), "modified": True})

    def _fix_sql_error(self, args: dict) -> str:
        """Fix a failed SQL query based on the error message."""
        sql = args["sql"]
        error = args["error"]
        question = args["question"]

        prompt = f"""The following SQL query produced an error. Please fix it.

{SCHEMA_INFO}

Original Question: {question}

Failed SQL:
{sql}

Error Message:
{error}

Important: If computing SUM/COUNT/AVG across multiple tables,
isolate each aggregation in its own CTE, then join the CTEs.
Otherwise JOIN fan-out will cause incorrect inflated results.

Write the corrected SQL query (SQL only, no explanations):"""

        corrected = self._llm_call(
            AGENT_SYSTEM_PROMPTS["error_handler"],
            prompt,
            temperature=0,
        )
        return json.dumps({"sql": self._clean_sql(corrected)})

    def _check_sanity(self, args: dict) -> str:
        """Check if query results are reasonable."""
        question = args["question"]
        sql = args["sql"]
        results = args["results"][:1000]  # Truncate for context limits

        if not results or results == "No results found.":
            return json.dumps({"is_reasonable": True, "issue": ""})

        prompt = f"""Evaluate whether SQL query results are reasonable.

Database context:
- Personal finance database (2024–2025 synthetic data, 2 years)
- ~510 transactions over 2 years, 200 invoices, 5 accounts, 15 categories
- Monthly salary: ~18,000–22,000 TRY → annual: 216,000–264,000 TRY
- Annual total income (salary + freelance + rental + interest + other): 280,000–450,000 TRY
- Monthly rent: ~8,000–9,000 TRY → annual rent: 96,000–108,000 TRY
- Annual total expenses across ALL categories: 180,000–300,000 TRY
- A single expense category's annual total can reach up to 250,000 TRY
- Per-month values: income 18K–35K, expenses 10K–30K

User Question: {question}

SQL Executed:
{sql}

Query Results (first 1000 chars):
{results}

Check:
1. Are monetary values within realistic ranges? Annual totals can reach 450K. Single category up to 250K.
2. Are counts realistic? (200 invoices means max ~200 overdue; 510 transactions total)
3. Does the result structure match the question?
4. Are duplicate month/category entries expected? (if question asks "which months", multiple categories per month is NORMAL — do NOT flag this)

Respond in JSON format:
{{
    "is_reasonable": true/false,
    "issue": "brief description of the problem, or empty string if fine"
}}"""

        return self._llm_call(
            AGENT_SYSTEM_PROMPTS["sanity_checker"],
            prompt,
            temperature=0,
            response_format="json",
        )

    def _analyze_results(self, args: dict) -> str:
        """Convert raw results to natural language."""
        question = args["question"]
        sql = args["sql"]
        results = args["results"]

        prompt = f"""Explain the following database query results in clear, plain language.

User Question: {question}

SQL Used:
{sql}

Query Results:
{results}

Guidelines:
- Answer the question directly.
- Format monetary values readably (e.g., 12,450.00 TRY).
- Use bullet lists for multiple results.
- For multi-part questions, address each part separately.
- Keep it concise and focused; avoid unnecessary repetition.

Answer:"""

        answer = self._llm_call(
            AGENT_SYSTEM_PROMPTS["analyst"],
            prompt,
            temperature=0.7,  # Medium temperature for natural, fluent output
        )
        return json.dumps({"answer": answer})

    def _decide_graph_need(self, args: dict) -> str:
        """Decide if a graph is needed and what type."""
        question = args["question"]
        results = args.get("results", "")

        if not results or results == "No results found.":
            return json.dumps({"needs_graph": False, "graph_type": "none", "reason": "No data to visualize"})

        prompt = f"""Based on the question and query results, decide if a chart is needed and which type.

Question: {question}

Query Results (first 500 chars):
{results[:500]}

Decision criteria:
- Time trend → line chart
- Category comparison → bar chart
- Proportion / percentage → pie chart
- Correlation / scatter → scatter chart
- Single value or short text → no chart needed

Respond in JSON format:
{{"needs_graph": true/false, "graph_type": "bar/line/pie/scatter/none", "reason": "brief explanation"}}"""

        return self._llm_call(
            AGENT_SYSTEM_PROMPTS["graph_decider"],
            prompt,
            temperature=0,
            response_format="json",
        )

    def _generate_plotly(self, args: dict) -> str:
        """Generate Plotly visualization code."""
        question = args["question"]
        graph_type = args["graph_type"]
        columns = args["columns"]
        sample = args["sample"]
        row_count = args["row_count"]

        prompt = f"""Write Plotly code to visualize the following financial data.

User Question: {question}
Chart Type: {graph_type}
Columns: {columns}
Sample Data (first 5 rows): {sample}
Total Rows: {row_count}

CRITICAL — DataFrame structure rules:
A) If row_count == 1 and you're comparing multiple numeric columns (e.g. income vs expenses):
   The DataFrame has ONE row with columns like [total_income, total_expenses].
   You MUST reshape it before plotting:
   ```
   values = [df[col].iloc[0] for col in columns]
   fig = go.Figure(data=[go.Bar(x=columns, y=values, ...)])
   ```
   Do NOT pass multi-element lists to px.bar() when df has only 1 row.

B) If row_count > 1 with a clear category column + value column:
   ```
   df_subset = df.head(20) if len(df) > 20 else df
   fig = px.bar(df_subset, x='category_column', y='value_column', ...)
   ```

Requirements:
1. Use plotly.graph_objects (go) OR plotly.express (px) — choose based on data shape.
2. The data is already available in a pandas DataFrame named 'df'.
3. Create a {graph_type} chart.
4. If more than 20 rows, use only the first 20.
5. Add descriptive title and axis labels.
6. The figure variable MUST be named 'fig'.
7. Do NOT include import statements, fig.show(), or markdown — only Python code.
8. Enhance visual quality with colors, hover information, and responsive sizing.
9. The code MUST execute without errors. Test your logic mentally before writing.
10. Currency is TRY (Turkish Lira) — use 'TRY' not '$' in labels and formatting.

Write the Plotly code:"""

        code = self._llm_call(
            AGENT_SYSTEM_PROMPTS["visualizer"],
            prompt,
            temperature=0.3,
            timeout=30,
        )
        code = code.replace("```python", "").replace("```", "").strip()
        return json.dumps({"code": code})


# ── Standalone MCP Server Entry Point ───────────────────────────────────────
# To run as a proper MCP server over stdio (production mode):
#   python -m mcp_servers.agent_server

if __name__ == "__main__":
    import asyncio
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent

    tools_handler = AgentToolsServer()
    server = Server("finance-agent-tools-server")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        meta = tools_handler.list_tools()
        return [Tool(**t) for t in meta]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        result = tools_handler.handle_tool(name, arguments)
        return [TextContent(type="text", text=result)]

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(main())
