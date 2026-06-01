"""AutomationEdge engine MCP tool over the streamable-HTTP transport.

agent_framework's ``MCPStreamableHTTPTool`` speaks MCP's streamable-HTTP transport natively,
so we use it directly — no custom client override needed. The endpoint is configured via the
``AE_MCP_URL`` env var (streamable-HTTP servers expose ``/mcp``, not the legacy ``/sse``).
"""

from __future__ import annotations

from os import environ

from agent_framework import MCPStreamableHTTPTool


def build_ae_mcp() -> MCPStreamableHTTPTool:
    return MCPStreamableHTTPTool(
        name="ae_mcp",
        url=environ.get("AE_MCP_URL", "http://localhost:8050/mcp"),
        description="AutomationEdge engine MCP server.",
    )
