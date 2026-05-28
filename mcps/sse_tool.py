"""AutomationEdge MCP tool over the legacy SSE transport (port of the POC mcp_sse_tool.py).

agent_framework ships MCPStreamableHTTPTool (streamable-HTTP) and MCPWebsocketTool but
no class for the legacy MCP SSE transport, which is all the aeengine-mcp Spring AI
server exposes (/sse), so we plug mcp.client.sse.sse_client into the same base class.
"""

from __future__ import annotations

from os import environ
from typing import Any

from agent_framework import MCPStreamableHTTPTool

_SSE_IDLE_TIMEOUT_SECONDS = 60
_SSE_CONNECT_TIMEOUT_SECONDS = 30


class MCPSSETool(MCPStreamableHTTPTool):
    """MCP tool over the legacy SSE transport."""

    def get_mcp_client(self) -> Any:
        from mcp.client.sse import sse_client

        return sse_client(
            url=self.url,
            timeout=_SSE_CONNECT_TIMEOUT_SECONDS,
            sse_read_timeout=_SSE_IDLE_TIMEOUT_SECONDS,
        )


def build_ae_mcp() -> MCPSSETool:
    return MCPSSETool(
        name="ae_mcp",
        url=environ.get("AE_MCP_URL", "http://localhost:8090/sse"),
        description="AutomationEdge engine MCP server.",
    )
