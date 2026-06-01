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
    """User-visible replies = the ``send_reply_to_user`` outputs ONLY, in emission order, de-duplicated.

    The framework also yields each agent's implicit ``AgentResponse`` as an ``output`` event; we
    deliberately skip those — ``send_reply_to_user`` is the sole user-reply channel — so there are no
    interleaved replies. (Lazy import to avoid any import-order coupling.)

    De-dup safety net: in a handoff mesh a receiving agent sometimes RESTATES a result another agent
    already reported (it sees the "[other_agent] …" note and parrots it as plain text, which the
    executor's fallback then surfaces). Identical replies are collapsed by whitespace-normalized text
    so the user never sees the same answer twice, regardless of how the models route.
    """
    from workflows.handoff_orchestrator import UserReply

    replies: list[str] = []
    seen: set[str] = set()
    for output in result.get_outputs():
        if isinstance(output, UserReply) and output.text:
            key = " ".join(output.text.split())  # normalize whitespace for the comparison only
            if key in seen:
                continue
            seen.add(key)
            replies.append(output.text)
    return replies


def find_tool_result(result: Any, tool_name: str | None) -> str | None:
    """Return the captured result of the executed tool named ``tool_name`` from this run's
    ``ToolExecuted`` outputs, or None. Used by the service to write the ``tool_result`` audit row on an
    approval RESUME (the framework runs the approved tool but does not re-emit its function_result)."""
    if not tool_name:
        return None
    from workflows.handoff_orchestrator import ToolExecuted

    for output in result.get_outputs():
        if isinstance(output, ToolExecuted) and output.tool == tool_name:
            return output.result
    return None


def is_function_approval(event: Any) -> bool:
    """True if a request_info event is a tool/function approval request."""
    return getattr(getattr(event, "data", None), "type", None) == "function_approval_request"
