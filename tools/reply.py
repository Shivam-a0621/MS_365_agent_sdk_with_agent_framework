"""The per-agent user-reply tool.

``send_reply_to_user`` is how a specialist emits a user-visible message for a sub-task it just
finished. The body is a no-op confirmation — the message is captured by
``UserReplyCaptureMiddleware`` (agents/middleware.py) and surfaced by the handoff executor as a
workflow output. This is the ONLY channel that reaches the user; an agent's normal reply text is
shared cross-agent but never shown to the user.
"""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool


@tool(approval_mode="never_require")
def send_reply_to_user(
    message: Annotated[
        str,
        "The exact user-facing message for the sub-task you just completed: the data the user asked "
        "for (the list of workflows, the files, …) or a one-line confirmation naming the artifact "
        "(e.g. 'Created file custom.log.').",
    ],
) -> str:
    """Send ONE user-visible reply for a sub-task you have just finished.

    Call this exactly once per completed sub-task, BEFORE you hand off or finish. This is the ONLY
    channel that reaches the user — text in your normal reply is NOT shown to the user.
    """
    return "delivered"
