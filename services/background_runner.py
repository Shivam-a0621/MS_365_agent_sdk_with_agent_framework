"""Run a long-running job IN-PROCESS and resume the conversation DIRECTLY when it finishes.

No HTTP callback, no manual step: when the agent starts a background task, the conversation service
fires ``run_background_task`` detached on the event loop. It does the work, then calls
``resume_external_task`` in-process — which loads the conversation's checkpoint, lets the same agent
report the result, and notifies the user.

To integrate a REAL long operation, replace the body of ``_do_work``: call your engine / poll it /
compute, and return a result dict. Everything else (resume + notify) stays the same.

Caveat: the job lives in this process, so a restart before it finishes loses it (the
aistudiobot_agent_task row stays 'pending'). A pull/poll-based ``_do_work`` that re-checks on startup, or
a durable job queue, would remove that limitation.

This module also owns ``proactive_push`` — the out-of-band notification that delivers a finished task's
result into the user's chat (the run+notify story in one place). It needs real bot credentials; a send
failure is logged, never raised (the result is already merged into the conversation).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("app.task")

_JOBS: set[asyncio.Task] = set()  # strong refs so detached jobs aren't garbage-collected

# Demo work duration when params don't specify "seconds". Override with env TASK_DEMO_SECONDS.
_DEFAULT_SECONDS = int(os.getenv("TASK_DEMO_SECONDS", "90"))


async def _do_work(summary: str | None, params: dict[str, Any]) -> dict[str, Any]:
    """The actual long-running work. PLACEHOLDER: sleeps, then returns demo data. Replace with the
    real operation (e.g. trigger an engine workflow and poll it until done)."""
    seconds = int(params.get("seconds", _DEFAULT_SECONDS))
    logger.info("background task: working %ss (%s)", seconds, summary)
    await asyncio.sleep(seconds)
    return {
        "rows": 12800,
        "url": "https://example.invalid/report/subbu",
        "summary": f"'{summary or 'task'}' finished after {seconds}s and produced 128 rows.",
    }


async def run_background_task(correlation_id: str, summary: str | None, params: dict[str, Any] | None) -> None:
    """Do the work, then resume the conversation directly. Lazy-imports resume_external_task to avoid
    an import cycle (conversation_service fires this)."""
    from services.conversation_service import resume_external_task

    # 1) Do the work. A failure HERE means the task itself failed -> deliver a failure notice.
    try:
        output: dict[str, Any] | None = await _do_work(summary, params or {})
    except asyncio.CancelledError:  # process shutting down — leave the row pending
        logger.warning("background task %s cancelled before completion", correlation_id)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("background task %s failed", correlation_id)
        status, output, error = "failed", None, str(exc)
    else:
        status, error = "ok", None

    # 2) Deliver the outcome exactly once. A failure of the DELIVERY itself must NOT re-run delivery:
    #    that was the crash-cascade (a non-converging resume would retry and crash again). Log + stop.
    #    (resume_external_task already falls back to raw delivery on a looped/broken resume.)
    try:
        await resume_external_task(
            correlation_id=correlation_id, status=status, output=output, error=error
        )
    except Exception:  # noqa: BLE001
        logger.exception("delivery of background task %s failed; not retrying", correlation_id)


def fire(correlation_id: str, summary: str | None, params: dict[str, Any] | None) -> None:
    """Schedule run_background_task detached on the running event loop; returns immediately."""
    task = asyncio.create_task(run_background_task(correlation_id, summary, params))
    _JOBS.add(task)
    task.add_done_callback(_JOBS.discard)


_proactive_logger = logging.getLogger("app.proactive")


async def proactive_push(conversation_ref: str, replies: list[str]) -> None:
    """Notify a conversation OUTSIDE a user turn — message the user when a long-running task completes.
    Sends each reply into the conversation via the SDK's proactive API. Sending from outside a turn needs
    the bot's outbound Bot Connector token, so this delivers only when the bot has real credentials
    configured (a registered/deployed bot). A send failure is logged, never raised — the task result is
    already merged into the conversation, so the user still sees it on their next message. No-op on empty."""
    if not replies:
        return
    # Lazy import: avoids constructing the SDK stack (and the bot import cycle) unless we actually deliver.
    from microsoft_agents.activity import Activity
    from bot import ADAPTER, AGENT_APP

    for reply in replies:
        try:
            await AGENT_APP.proactive.send_activity(
                ADAPTER, conversation_ref, Activity(type="message", text=reply)
            )
        except Exception:  # noqa: BLE001 - needs real bot creds; the result is already in the conversation
            _proactive_logger.exception(
                "Proactive notify failed for %s (bot credentials required); result not pushed to chat: %r",
                conversation_ref,
                reply,
            )
