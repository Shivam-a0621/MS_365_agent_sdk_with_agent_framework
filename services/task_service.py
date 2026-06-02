"""Spawn and resume long-running background tasks (the per-task checkpoint lineage).

`spawn_task` is called from the conversation service after a turn in which the agent invoked
`start_background_task`: it runs the lightweight task workflow to its pause (a durable checkpoint in
its OWN lineage), records an `aistudio_agent_task` row, and launches the external job. The
conversation is untouched, so the user keeps chatting.

`resume_task` is called from the /api/task-callback path: it rehydrates the task workflow from its
checkpoint, resumes it with the callback payload (coerced into TaskResult), and prunes the lineage.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from db import repositories as repo
from db.checkpoint_store import PostgresCheckpointStorage
from db.models import AgentTask
from workflows.task_flow import (
    CHECKPOINT_TYPES,
    TaskCompleted,
    TaskResult,
    TaskStart,
    build_task_workflow,
    task_workflow_name,
)

logger = logging.getLogger("app.task")

_TTL_SECONDS = int(os.getenv("TASK_TTL_SECONDS", "600"))  # how long before a never-callback task expires
_ALLOWED = frozenset(CHECKPOINT_TYPES)


# --- external-job launchers (pluggable per task_type; stubbed until a real integration lands) ------

async def _noop_launcher(*, correlation_id: str, params: dict[str, Any]) -> None:
    logger.info("No launcher registered; task %s is inert until /api/task-callback is hit.", correlation_id)


_LAUNCHERS: dict[str, Callable[..., Awaitable[None]]] = {}
_default_launcher: Callable[..., Awaitable[None]] = _noop_launcher


def register_launcher(task_type: str, launcher: Callable[..., Awaitable[None]]) -> None:
    """Register the function that actually kicks off the external job for a given task_type. The
    launcher MUST pass `correlation_id` to the engine so the engine echoes it back in its callback."""
    _LAUNCHERS[task_type] = launcher


def set_default_launcher(launcher: Callable[..., Awaitable[None]]) -> None:
    """Set the launcher used when no task_type-specific one is registered (default: no-op)."""
    global _default_launcher
    _default_launcher = launcher


def _storage(chat_conversation_id: int) -> PostgresCheckpointStorage:
    return PostgresCheckpointStorage(chat_conversation_id=chat_conversation_id, allowed_types=_ALLOWED)


async def spawn_task(
    session: AsyncSession,
    *,
    chat_conversation_id: int,
    channel: str,
    conversation_ref: str,
    breadcrumb: dict[str, Any],
) -> AgentTask:
    """Run the task workflow to its pause (own checkpoint lineage), record the AgentTask row in the
    caller's session, and launch the external job. Returns the AgentTask row."""
    correlation_id = breadcrumb["correlation_id"]
    task_type = breadcrumb.get("task_type", "")
    summary = breadcrumb.get("summary", "")
    params = breadcrumb.get("params") or {}

    storage = _storage(chat_conversation_id)
    workflow = build_task_workflow(
        conversation_id=chat_conversation_id, correlation_id=correlation_id, checkpoint_storage=storage
    )
    run = await workflow.run(
        TaskStart(correlation_id=correlation_id, task_type=task_type, summary=summary, params=params),
        include_status_events=True,
        checkpoint_storage=storage,
    )
    reqs = list(run.get_request_info_events())
    if not reqs:
        raise RuntimeError(f"task workflow {correlation_id} did not pause as expected")
    request_id = reqs[0].request_id

    name = task_workflow_name(chat_conversation_id, correlation_id)
    latest = await storage.get_latest(workflow_name=name)
    checkpoint_id = latest.checkpoint_id if latest else None

    row = await repo.create_agent_task(
        session,
        correlation_id=correlation_id,
        chat_conversation_id=chat_conversation_id,
        channel=channel,
        conversation_ref=conversation_ref,
        task_workflow_name=name,
        request_id=request_id,
        checkpoint_id=checkpoint_id,
        task_type=task_type,
        params=params,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=_TTL_SECONDS),
    )

    launcher = _LAUNCHERS.get(task_type, _default_launcher)
    try:
        await launcher(correlation_id=correlation_id, params=params)
    except Exception:  # noqa: BLE001 - a launch failure shouldn't break the conversation turn
        logger.exception("Launcher for task_type=%s (corr=%s) failed", task_type, correlation_id)
    logger.info("Spawned background task corr=%s type=%s checkpoint=%s", correlation_id, task_type, checkpoint_id)
    return row


async def resume_task(*, task: AgentTask, result_payload: dict[str, Any]) -> TaskResult | None:
    """Rehydrate the task workflow from its checkpoint, resume it with the callback payload (coerced
    to TaskResult), prune the lineage, and return the TaskResult (or None if it didn't complete)."""
    storage = _storage(task.chat_conversation_id)
    workflow = build_task_workflow(
        conversation_id=task.chat_conversation_id,
        correlation_id=task.correlation_id,
        checkpoint_storage=storage,
    )
    run = await workflow.run(
        responses={task.request_id: result_payload},
        checkpoint_id=task.checkpoint_id,
        checkpoint_storage=storage,
        include_status_events=True,
    )
    completed = [o for o in run.get_outputs() if isinstance(o, TaskCompleted)]
    # The task is resolved; drop its checkpoint lineage (nothing to keep).
    try:
        await storage.prune(workflow_name=task.task_workflow_name, latest_checkpoint_id=None)
    except Exception:  # noqa: BLE001 - pruning is best-effort
        logger.warning("Failed to prune task lineage %s", task.task_workflow_name)
    return completed[0].result if completed else None
