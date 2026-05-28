"""Turn-output and request-info helpers (channel-neutral).

These functions only know about `agent_framework` run results — no Teams/SDK
specifics. The service layer uses them to turn a workflow run into display
strings and to detect approval requests.

NOTE: resume-value reconstruction lives in `workflows/manager.py` (`_resume_value`)
because it operates on a stored `HumanInput` row (cross-process resume), not on a
live `RequestInfoEvent`. Keeping them separate avoids confusing the two paths.
"""

from __future__ import annotations

from typing import Any


def reply_text(data: Any) -> str | None:
    """Turn one workflow output into display text, whatever shape it is."""
    if data is None:
        return None
    if isinstance(data, str):
        return data or None
    if isinstance(data, list):  # e.g. list[Message]; use the last
        return reply_text(data[-1]) if data else None
    text = getattr(data, "text", None)  # AgentResponse / AgentResponseUpdate
    if isinstance(text, str) and text:
        return text
    messages = getattr(data, "messages", None)
    if messages:
        last = getattr(messages[-1], "text", None)
        if isinstance(last, str) and last:
            return last
    coerced = str(data)
    return coerced or None


def extract_replies(result: Any) -> list[str]:
    """Final outputs of a run as display strings."""
    replies = []
    for output in result.get_outputs():
        text = reply_text(output)
        if text:
            replies.append(text)
    return replies


def is_function_approval(event: Any) -> bool:
    """True if a request_info event is a tool/function approval request."""
    return getattr(getattr(event, "data", None), "type", None) == "function_approval_request"
