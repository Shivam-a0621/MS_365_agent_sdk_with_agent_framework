"""The ``show_workflow_picker`` tool — render a typed-search card so the user picks an exact workflow.

The ``ae_workflow_analyzer`` calls this when an engine tool needs an exact ``workflowName`` the user has
not given. The AGENT first fetches the names (via the ``list_workflows`` MCP tool) and passes them here;
like ``start_background_task`` (tools/background_task.py), this returns a parseable breadcrumb carrying
the list. After the turn, the conversation service recognizes it (a ``ToolExecuted`` output) and ``bot.py``
builds a static typed-search Adaptive Card embedding those names. The user's pick comes back as the next
message and the paused conversation resumes with that exact name.

(Tools are framework-agnostic and have no ``TurnContext``, so a tool cannot send a card itself — it can
only return data. The breadcrumb is how that data reaches the bot layer, which does the rendering.)
"""

import json
from typing import Annotated, Any

from agent_framework import tool

# Prefix so the conversation service can recognize + parse this from the captured tool result.
SHOW_WORKFLOW_PICKER_PREFIX = "__SHOW_WORKFLOW_PICKER__"


@tool(approval_mode="never_require")
def show_workflow_picker(
    workflow_names: Annotated[
        list[str],
        "The exact workflow names to offer the user, copied verbatim from a list_workflows result.",
    ],
) -> str:
    """Show the user a typed-search card to pick ONE workflow — use this whenever you need an exact
    workflowName the user has NOT given precisely. FIRST call list_workflows to get the names (use the
    user's hint as nameContains if they gave one, otherwise list them all), THEN call this with that list
    of exact names. After calling this, STOP: do not guess a name, do not hand off, do not call other
    tools; the user picks from the card and your next turn continues with the exact name they choose."""
    return f"{SHOW_WORKFLOW_PICKER_PREFIX}{json.dumps({'workflows': [str(n) for n in workflow_names]})}"


def parse_workflow_picker(result_text: Any) -> list[str] | None:
    """Parse a `show_workflow_picker` breadcrumb out of a captured tool-result string -> the list of
    workflow names, or None if this isn't a picker breadcrumb."""
    if not isinstance(result_text, str) or not result_text.startswith(SHOW_WORKFLOW_PICKER_PREFIX):
        return None
    try:
        data = json.loads(result_text[len(SHOW_WORKFLOW_PICKER_PREFIX):])
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("workflows"), list):
        return [str(n) for n in data["workflows"] if n]
    return None
