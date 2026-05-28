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
