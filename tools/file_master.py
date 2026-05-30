"""File/log tools for the file_master agent (mock bodies kept verbatim from the POC)."""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool


@tool(approval_mode="always_require")
def write_file(
    path: Annotated[str, "Path to write"],
    content: Annotated[str, "Content"],
) -> str:
    """Write content to a file"""
    return f"Done file created at {path} with content: {content}"


@tool(approval_mode="never_require")
def read_file(path: Annotated[str, "Path to read"]) -> str:
    """Read a file. For *.log files we return a canned error so the demo
    consistently exercises the duplicate-ticket path."""
    if path.endswith(".log"):
        return f"{path} contents: [ERROR] ConnectionTimeout to db1 (x3 in last hour)"
    return f"Contents of {path}"


@tool(approval_mode="always_require")
def list_available_files(
    path: Annotated[str, "Path from which to list files"],
) -> list[str]:
    """List all available files on disk. For demo purposes, we return a fixed set of results."""
    all_files = [
        "/var/log/db.log",
        "/var/log/app.log",
        "/home/user/notes.txt",
        "/home/user/todo.txt",
    ]
    return all_files
