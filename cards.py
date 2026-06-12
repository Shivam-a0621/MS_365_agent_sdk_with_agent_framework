"""Teams Adaptive Cards (port of the POC app_global._approval_card)."""

from __future__ import annotations

from typing import Any

from microsoft_agents.hosting.core import CardFactory, MessageFactory


def approval_card(request_id: str, function_name: str | None, arguments: Any) -> Any:
    """Approve/Deny card for a pending function approval.

    The Submit actions post ``{"approval_id": <request_id>, "approved": bool}`` back,
    which the bot reads from ``context.activity.value`` on the next turn.
    """
    fn_name = function_name or "?"
    args_text = arguments if isinstance(arguments, str) else (str(arguments) if arguments else "(none)")

    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": "Approval required", "weight": "Bolder", "size": "Medium"},
            {"type": "TextBlock", "text": f"Run `{fn_name}`?", "wrap": True},
            {"type": "TextBlock", "text": f"Arguments: {args_text}", "wrap": True, "isSubtle": True},
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Approve",
                "style": "positive",
                "data": {"approval_id": request_id, "approved": True},
            },
            {
                "type": "Action.Submit",
                "title": "Deny",
                "data": {"approval_id": request_id, "approved": False},
            },
        ],
    }
    return MessageFactory.attachment(CardFactory.adaptive_card(card))


def workflow_search_card(workflow_names: list[str]) -> Any:
    """Typed-search card for picking an exact workflow, with the names embedded as choices.

    A ``style: "filtered"`` ``Input.ChoiceSet`` with embedded ``choices`` does **static typeahead** — the
    user types and it filters the list client-side. Unlike dynamic ``Data.Query`` search this works in
    **both Teams and Web Chat**. The agent supplies the names (via ``show_workflow_picker``). The Submit
    posts ``{"workflowChoice": <exact name>}`` back, read from ``context.activity.value`` on the next turn.

    Note: the choices ride in the card payload, so a very large tenant could approach the channel's card-
    size limit — the agent is told to narrow with the user's hint; keep this in mind for huge lists.
    """
    choices = [{"title": name, "value": name} for name in workflow_names]
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": [
            {"type": "TextBlock", "text": "Pick a workflow", "weight": "Bolder", "size": "Medium"},
            {
                "type": "Input.ChoiceSet",
                "id": "workflowChoice",
                "label": "Workflow",
                "style": "filtered",
                "placeholder": "Type to search…",
                "choices": choices,
            },
        ],
        "actions": [{"type": "Action.Submit", "title": "Select"}],
    }
    return MessageFactory.attachment(CardFactory.adaptive_card(card))
