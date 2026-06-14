"""
MCP Client Wrapper — agent communication layer
===============================================
Provides a clean interface for LangGraph agents to call MCP tools.
In production, this would use stdio/sse transport to connect to
separate MCP server processes. For this project, we use direct
in-process calls to the tool handler classes for simplicity and
lower latency during development.

Usage:
    from agents.mcp_client import MCPClient

    client = MCPClient()
    result = client.call_tool("db", "get_schema", {})
    result = client.call_tool("agent", "generate_sql", {"question": "..."})
"""

import json
from mcp_servers.db_server import DBServerTools
from mcp_servers.agent_server import AgentToolsServer


class MCPClient:
    """
    Unified MCP client for database and agent tools.

    Architecture:
        Agents (LangGraph nodes)
              │
              ▼
        MCPClient.call_tool(server, tool_name, args)
              │
              ├── "db"    → DBServerTools.handle_tool()
              └── "agent" → AgentToolsServer.handle_tool()

    In a distributed deployment, this layer would be replaced by
    actual MCP stdio/sse client connections. The interface remains
    identical — only the transport changes.
    """

    def __init__(self):
        """Initialize MCP server tool handlers (in-process)."""
        self._db_tools = DBServerTools()
        self._agent_tools = AgentToolsServer()
        self._servers = {
            "db": self._db_tools,
            "agent": self._agent_tools,
        }

    def call_tool(self, server: str, tool_name: str, arguments: dict) -> str:
        """
        Call an MCP tool on a specific server.

        Args:
            server:    Server name — "db" or "agent"
            tool_name: Tool name to invoke
            arguments: Tool arguments as a dict

        Returns:
            Tool response as a string (usually JSON)
        """
        handler = self._servers.get(server)
        if not handler:
            raise ValueError(f"Unknown MCP server: {server}. Use 'db' or 'agent'.")
        return handler.handle_tool(tool_name, arguments)

    def call_tool_json(self, server: str, tool_name: str, arguments: dict) -> dict:
        """
        Call an MCP tool and parse the JSON response.

        Args:
            server:    Server name — "db" or "agent"
            tool_name: Tool name to invoke
            arguments: Tool arguments as a dict

        Returns:
            Parsed JSON response as a dict
        """
        raw = self.call_tool(server, tool_name, arguments)
        return json.loads(raw)

    def call_db(self, tool_name: str, arguments: dict | None = None) -> str:
        """Shorthand for database server tool calls."""
        return self.call_tool("db", tool_name, arguments or {})

    def call_agent(self, tool_name: str, arguments: dict | None = None) -> str:
        """Shorthand for agent server tool calls."""
        return self.call_tool("agent", tool_name, arguments or {})
