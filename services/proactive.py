"""Notify a conversation OUTSIDE a user turn — used to message the user when a long-running task
completes.

One flow: push the message(s) into the conversation via the SDK's proactive API. Sending from outside
a turn needs the bot's outbound Bot Connector token, so this delivers only when the bot has real
credentials configured (a registered/deployed bot). A send failure is logged, never raised — the task
result is already merged into the conversation, so the user still sees it on their next message.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("app.proactive")


async def proactive_push(conversation_ref: str, replies: list[str]) -> None:
    """Send each reply into the conversation proactively. No-op on empty."""
    if not replies:
        return
    # Lazy import: avoids constructing the SDK stack unless we actually deliver.
    from microsoft_agents.activity import Activity
    from bot import ADAPTER, AGENT_APP

    for reply in replies:
        try:
            await AGENT_APP.proactive.send_activity(
                ADAPTER, conversation_ref, Activity(type="message", text=reply)
            )
        except Exception:  # noqa: BLE001 - needs real bot creds; the result is already in the conversation
            logger.exception(
                "Proactive notify failed for %s (bot credentials required); result not pushed to chat: %r",
                conversation_ref,
                reply,
            )
