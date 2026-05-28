"""File/log tools for the file_master agent (mock bodies kept verbatim from the POC)."""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool


@tool(approval_mode="always_require")
def mock_write_file(
    path: Annotated[str, "Path to write"],
    content: Annotated[str, "Content"],
) -> str:
    """Write content to a file"""
    return f"Pretended to write {len(content)} bytes to {path}"


@tool(approval_mode="never_require")
def mock_read_file(path: Annotated[str, "Path to read"]) -> str:
    """Read a file. For *.log files we return a canned error so the demo
    consistently exercises the duplicate-ticket path."""
    if path.endswith(".log"):
        return f"{path} contents: [ERROR] ConnectionTimeout to db1 (x3 in last hour)"
    return f"Pretend contents of {path}"
