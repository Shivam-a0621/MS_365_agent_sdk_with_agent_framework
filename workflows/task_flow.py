"""Lightweight per-task workflow for a long-running external job.

This is the OWN checkpoint lineage of a backgrounded task (the conversation workflow is a
separate lineage). The flow is intentionally tiny and has NO LLM:

    TaskStart ──▶ TaskExecutor.start ──▶ ctx.request_info(TaskPending, TaskResult)  [PAUSE + checkpoint]
                                              │  (waits for the external job's callback)
    callback ─────────────────────────────▶ TaskExecutor.on_result ──▶ yield TaskCompleted

It pauses durably awaiting the external job's completion callback, and on resume yields the
result. The user-facing wording is produced later by the conversation agent when the result is
merged back into the conversation (see services/task_service.deliver_result), not here.

The workflow NAME is keyed by ``correlation_id`` so each task is an isolated checkpoint lineage
(the checkpoint store keys purely by ``workflow_name``); multiple tasks can be in flight per
conversation at once. ``_GRAPH_VERSION`` is independent of the conversation graph's version.

NOTE: this module intentionally does NOT use ``from __future__ import annotations`` — the
``@response_handler``/``@handler`` validators inspect the real ``WorkflowContext[...]`` annotation
via ``get_origin`` at decoration time, and stringized annotations would fail that check.
"""

from dataclasses import dataclass, field
from typing import Any

from agent_framework import (
    CheckpointStorage,
    Executor,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    response_handler,
)

# Bump if the task graph / message types change (invalidates in-flight task checkpoints by name).
_GRAPH_VERSION = 1


def task_workflow_name(conversation_id: str | int, correlation_id: str) -> str:
    return f"task__{conversation_id}__{correlation_id}__v{_GRAPH_VERSION}"


# Dotted paths to allowlist in the checkpoint store so the pickled pending-request decodes.
CHECKPOINT_TYPES: tuple[str, ...] = (
    "workflows.task_flow:TaskPending",
    "workflows.task_flow:TaskResult",
)


@dataclass
class TaskStart:
    """Input that kicks the task workflow to its pause. The external job is launched by the
    caller (services/task_service) BEFORE/around running this, so the executor only pauses."""

    correlation_id: str
    task_type: str
    summary: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskPending:
    """The paused request payload (also what the callback resumes against, by request_id)."""

    correlation_id: str
    task_type: str
    summary: str = ""


@dataclass
class TaskResult:
    """The completion payload delivered by the external job's callback. The callback may send a
    plain JSON dict; the framework coerces it to this dataclass against the declared response_type."""

    correlation_id: str
    status: str = "ok"  # ok | failed | timeout
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class TaskCompleted:
    """The workflow output the service reads after resume."""

    correlation_id: str
    result: TaskResult


class TaskExecutor(Executor):
    """Single-node task flow: pause awaiting the external callback, then yield the result."""

    def __init__(self, id: str = "task_executor") -> None:
        super().__init__(id=id)

    @handler
    async def start(self, message: TaskStart, ctx: WorkflowContext[Any, TaskCompleted]) -> None:
        # Pause + checkpoint. response_type=TaskResult locks the resume payload to TaskResult
        # (a JSON dict from the callback auto-coerces). The external job is already running.
        await ctx.request_info(
            TaskPending(message.correlation_id, message.task_type, message.summary),
            TaskResult,
        )

    @response_handler(request=TaskPending, response=TaskResult)
    async def on_result(
        self, request: TaskPending, response: TaskResult, ctx: WorkflowContext[Any, TaskCompleted]
    ) -> None:
        await ctx.yield_output(TaskCompleted(request.correlation_id, response))


def build_task_workflow(
    *,
    conversation_id: str | int,
    correlation_id: str,
    checkpoint_storage: CheckpointStorage | None = None,
) -> Workflow:
    """Build the (deterministic, single-node) task workflow. The graph is identical at spawn and at
    resume — only the name (per correlation_id) differs — so a rehydrated instance loads the checkpoint."""
    executor = TaskExecutor()
    return WorkflowBuilder(
        name=task_workflow_name(conversation_id, correlation_id),
        start_executor=executor,
        checkpoint_storage=checkpoint_storage,
        output_from=[executor],
    ).build()
