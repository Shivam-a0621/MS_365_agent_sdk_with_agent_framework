"""Task launchers — what actually STARTS a long-running job for a given task_type.

A launcher returns IMMEDIATELY (it must not block the conversation turn) after kicking off the job;
when the job finishes it drives the task-completion callback. A real external engine would POST
/api/task-callback; this `demo_sleep` launcher simulates one IN-PROCESS: it spawns a detached asyncio
task that sleeps (default 120s, override with params={"seconds": N}) and then resumes the task with
some result data via ``resume_external_task``.

It is wired as the DEFAULT launcher (see the bottom of this module), so ANY backgrounded task runs the
2-minute demo job until real per-task_type launchers are registered.

Caveat: because the demo job lives in-process, a process restart before it wakes loses it (the
aistudio_agent_task row stays 'pending' until the TTL reaper expires it). A real HTTP-callback engine
does not have this limitation.

Activated by importing this module (app.py does so at startup).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.task_service import set_default_launcher

logger = logging.getLogger("app.launchers")

_DEFAULT_SECONDS = 120
_JOBS: set[asyncio.Task] = set()  # strong refs so detached jobs aren't garbage-collected


async def _demo_job(correlation_id: str, params: dict[str, Any]) -> None:
    seconds = int(params.get("seconds", _DEFAULT_SECONDS))
    label = params.get("label") or params.get("workflow") or params.get("name") or "demo job"
    logger.info("demo task %s: sleeping %ss (%s)", correlation_id, seconds, label)

    from services.conversation_service import resume_external_task  # lazy: avoids an import cycle

    try:
        await asyncio.sleep(seconds)
        output = {
            "label": label,
            "slept_seconds": seconds,
            "rows": 128,
            "url": f"https://example.invalid/report/{correlation_id[:8]}",
            "summary": f"'{label}' finished after {seconds}s and produced 128 rows.",
        }
        await resume_external_task(correlation_id=correlation_id, status="ok", output=output)
        logger.info("demo task %s: delivered", correlation_id)
    except asyncio.CancelledError:  # process shutting down — leave the row pending for the reaper
        logger.warning("demo task %s: cancelled before completion", correlation_id)
        raise
    except Exception:  # noqa: BLE001
        logger.exception("demo task %s: failed", correlation_id)
        await resume_external_task(correlation_id=correlation_id, status="failed", error="demo job crashed")


async def demo_sleep_launcher(*, correlation_id: str, params: dict[str, Any]) -> None:
    """Start the demo job detached and return immediately (does NOT block the turn)."""
    task = asyncio.create_task(_demo_job(correlation_id, params))
    _JOBS.add(task)
    task.add_done_callback(_JOBS.discard)


# Wire demo_sleep as the default launcher: every backgrounded task runs the 2-minute demo job.
set_default_launcher(demo_sleep_launcher)
