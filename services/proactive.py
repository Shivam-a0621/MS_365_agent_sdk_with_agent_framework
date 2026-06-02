"""Deliver a message to a conversation OUTSIDE a user turn (for async task completion).

PROACTIVE_MODE controls behaviour so the feature is testable locally without Bot Connector creds:
  - ``log``  (default): just log the message — no SDK, no creds. Used for Stages 1-2.
  - ``live``: actually push to Teams via the SDK proactive API (lazy-imports bot; needs real creds).
  - ``echo``: like log, but also buffers into ``ECHO_SINK`` so a test/dev route can assert delivery.

In ``live`` mode a send failure is logged (never raised) — but note the workflow checkpoint has
already been consumed, so a failed push means the result is NOT redelivered. Operationally this should
alert / write to a delivery-retry table; for now it is logged loudly.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("app.proactive")

# In-memory sink for PROACTIVE_MODE=echo (dev/test only).
ECHO_SINK: list[tuple[str, str]] = []


def _mode() -> str:
    return os.getenv("PROACTIVE_MODE", "log").lower()


async def proactive_push(channel: str, conversation_ref: str, replies: list[str]) -> None:
    """Send each reply to the conversation. No-op on empty."""
    if not replies:
        return
    mode = _mode()

    if mode == "live":
        from microsoft_agents.activity import Activity  # lazy: only needed for a real send
        from bot import ADAPTER, AGENT_APP  # lazy: avoids constructing the SDK in log/echo mode

        for reply in replies:
            try:
                await AGENT_APP.proactive.send_activity(
                    ADAPTER, conversation_ref, Activity(type="message", text=reply)
                )
            except Exception:  # noqa: BLE001 - checkpoint already consumed; surface loudly, don't crash
                logger.exception(
                    "PROACTIVE PUSH FAILED for %s — result NOT delivered: %r", conversation_ref, reply
                )
        return

    # log / echo
    for reply in replies:
        logger.info("[proactive:%s] %s -> %s", mode, conversation_ref, reply)
        if mode == "echo":
            ECHO_SINK.append((conversation_ref, reply))
