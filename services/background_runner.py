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
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("app.task")

_JOBS: set[asyncio.Task] = set()  # strong refs so detached jobs aren't garbage-collected

# Demo work duration when params don't specify "seconds". Override with env TASK_DEMO_SECONDS.
_DEFAULT_SECONDS = int(os.getenv("TASK_DEMO_SECONDS", "20"))


async def _do_work(summary: str | None, params: dict[str, Any]) -> dict[str, Any]:
    """The actual long-running work. PLACEHOLDER: sleeps, then returns demo data. Replace with the
    real operation (e.g. trigger an engine workflow and poll it until done)."""
    seconds = int(params.get("seconds", _DEFAULT_SECONDS))
    logger.info("background task: working %ss (%s)", seconds, summary)
    await asyncio.sleep(seconds)
    return {
        "rows": 128,
        "url": "https://example.invalid/report",
        "summary": f"'{summary or 'task'}' finished after {seconds}s and produced 128 rows.",
    }


async def run_background_task(correlation_id: str, summary: str | None, params: dict[str, Any] | None) -> None:
    """Do the work, then resume the conversation directly. Lazy-imports resume_external_task to avoid
    an import cycle (conversation_service fires this)."""
    from services.conversation_service import resume_external_task

    try:
        output = await _do_work(summary, params or {})
        await resume_external_task(correlation_id=correlation_id, status="ok", output=output)
    except asyncio.CancelledError:  # process shutting down — leave the row pending
        logger.warning("background task %s cancelled before completion", correlation_id)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("background task %s failed", correlation_id)
        await resume_external_task(correlation_id=correlation_id, status="failed", error=str(exc))


def fire(correlation_id: str, summary: str | None, params: dict[str, Any] | None) -> None:
    """Schedule run_background_task detached on the running event loop; returns immediately."""
    task = asyncio.create_task(run_background_task(correlation_id, summary, params))
    _JOBS.add(task)
    task.add_done_callback(_JOBS.discard)
