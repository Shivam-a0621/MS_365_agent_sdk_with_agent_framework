"""The `start_background_task` tool — kick off a long-running external job and return immediately.

The agent calls this for any task that takes a while (rather than blocking on a synchronous tool).
It does NOT run the job itself: it mints a correlation id and returns a parseable breadcrumb. The
conversation service detects that breadcrumb after the turn, spawns the task's own checkpointed
workflow (so the user keeps chatting), and launches the external job. When the job finishes it POSTs
the result to /api/task-callback, which resumes the task and notifies the user.

`task_type` keeps this engine-agnostic — a specific integration (e.g. an automation engine) is just
one value.
"""

import json
import uuid
from typing import Annotated, Any

from agent_framework import tool

# Prefix on the tool's return string so the conversation service can recognize + parse it from the
# captured tool result (a ToolExecuted output) without guessing.
BREADCRUMB_PREFIX = "__BACKGROUND_TASK__"


@tool(approval_mode="never_require")
def start_background_task(
    task_type: Annotated[str, "The kind of long-running job to start (e.g. a workflow or report id)."],
    summary: Annotated[str, "One short human-readable line describing what is being started."] = "",
    params: Annotated[dict[str, Any] | None, "Parameters to pass to the job."] = None,
) -> str:
    """Start a long-running background job and return immediately WITHOUT waiting for it. The job runs
    asynchronously; the user will be messaged with the result when it finishes. Use this for anything
    that takes more than a few seconds. After calling this, tell the user it has started via
    send_reply_to_user."""
    correlation_id = uuid.uuid4().hex
    payload = {
        "correlation_id": correlation_id,
        "task_type": task_type,
        "summary": summary,
        "params": params or {},
    }
    return f"{BREADCRUMB_PREFIX}{json.dumps(payload)}"


def parse_background_task(result_text: Any) -> dict[str, Any] | None:
    """Parse a `start_background_task` breadcrumb out of a captured tool-result string, or None."""
    if not isinstance(result_text, str) or not result_text.startswith(BREADCRUMB_PREFIX):
        return None
    try:
        data = json.loads(result_text[len(BREADCRUMB_PREFIX):])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) and data.get("correlation_id") else None
