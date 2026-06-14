"""
MCP Database Server — finance.db access layer
==============================================
Exposes database operations as MCP tools.
Agents communicate with the database exclusively through this server.

Tools exposed:
    - get_schema:      Return the full database schema documentation
    - execute_query:   Execute a SQLite query and return formatted results
    - get_table_info:  Return row counts and sample data for a table

Usage (standalone):
    python mcp_servers/db_server.py

Usage (embedded via MCP client):
    from mcp_servers.db_server import DBServerTools
    tools = DBServerTools(db_path="finance.db")
    result = tools.handle_tool("get_schema", {})
"""

import sqlite3
import json
import os
from typing import Any
from config import SCHEMA_INFO, settings


class DBServerTools:
    """
    MCP-compatible database tool handler.

    In a full production deployment, this would be wrapped by
    mcp.server.Server and exposed via stdio/sse transport.
    For this project, we implement the tool interface directly
    and use it via MCPClientWrapper for in-process communication.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.DB_PATH
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(
                f"Database not found at {self.db_path}. Run 'python db_init.py' first."
            )

    # ── Tool Definitions (MCP-compatible metadata) ──────────────────────────

    @staticmethod
    def list_tools() -> list[dict]:
        """Return tool metadata in MCP format."""
        return [
            {
                "name": "get_schema",
                "description": "Return the complete database schema documentation including all tables, columns, types, and relationships.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "execute_query",
                "description": "Execute a SQLite query against the finance database and return results as JSON.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "The SQL query to execute (SQLite dialect). Multiple statements separated by semicolons are supported.",
                        },
                        "max_rows": {
                            "type": "integer",
                            "description": "Maximum number of rows to return (default: 100).",
                            "default": 100,
                        },
                    },
                    "required": ["sql"],
                },
            },
            {
                "name": "get_table_info",
                "description": "Return row count and sample data for a specific table.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "table_name": {
                            "type": "string",
                            "description": "Name of the table to inspect (accounts, categories, transactions, budgets, invoices).",
                        },
                    },
                    "required": ["table_name"],
                },
            },
        ]

    # ── Tool Handlers ───────────────────────────────────────────────────────

    def handle_tool(self, name: str, arguments: dict) -> str:
        """Dispatch tool call to the appropriate handler."""
        handlers = {
            "get_schema": self._get_schema,
            "execute_query": self._execute_query,
            "get_table_info": self._get_table_info,
        }
        handler = handlers.get(name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {name}"})
        return handler(arguments)

    def _get_schema(self, _arguments: dict) -> str:
        """Return the full database schema."""
        return SCHEMA_INFO

    def _execute_query(self, arguments: dict) -> str:
        """Execute a SQL query and return results."""
        sql = arguments.get("sql", "")
        max_rows = arguments.get("max_rows", 100)

        if not sql.strip():
            return json.dumps({"error": "Empty SQL query provided."})

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Split multiple statements
            statements = [s.strip() for s in sql.split(";") if s.strip()]
            all_results = []

            for i, statement in enumerate(statements):
                cursor.execute(statement)
                rows = cursor.fetchall()

                if rows:
                    col_names = [d[0] for d in cursor.description]
                    formatted = [
                        dict(zip(col_names, row)) for row in rows[:max_rows]
                    ]

                    if len(statements) > 1:
                        all_results.append({
                            f"query_{i + 1}": formatted,
                            f"query_{i + 1}_sql": statement,
                        })
                    else:
                        all_results = formatted

            conn.close()

            if not all_results:
                return "No results found."

            return json.dumps(all_results, indent=2, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": f"SQL Error: {str(e)}"})

    def _get_table_info(self, arguments: dict) -> str:
        """Return row count and sample data for a table."""
        table_name = arguments.get("table_name", "")

        allowed_tables = {"accounts", "categories", "transactions", "budgets", "invoices"}
        if table_name not in allowed_tables:
            return json.dumps({
                "error": f"Unknown table: {table_name}. Allowed: {', '.join(sorted(allowed_tables))}"
            })

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Row count
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            row_count = cursor.fetchone()[0]

            # Sample data (first 3 rows)
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 3")
            col_names = [d[0] for d in cursor.description]
            sample = [dict(zip(col_names, row)) for row in cursor.fetchall()]

            conn.close()

            return json.dumps({
                "table": table_name,
                "row_count": row_count,
                "columns": col_names,
                "sample": sample,
            }, indent=2, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)})


# ── Standalone MCP Server Entry Point ───────────────────────────────────────
# To run as a proper MCP server over stdio (production mode):
#   python -m mcp_servers.db_server

if __name__ == "__main__":
    import asyncio
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent

    tools_handler = DBServerTools()
    server = Server("finance-db-server")

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
