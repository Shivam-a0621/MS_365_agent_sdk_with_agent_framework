"""Stateless turn runner for the handoff workflow.

No in-memory state: each turn rebuilds the workflow (deterministic graph signature)
with a Postgres-backed checkpoint store and runs fresh or resumes from the durable
checkpoint. Function-approval resume is reconstructed from the stored HumanInput so it works across restarts / workers.
"""

from __future__ import annotations

from typing import Any

from agent_framework import Content, Message

from db.checkpoint_store import PostgresCheckpointStorage
from db.models import HumanInput
from workflows.handoff import workflow_name
from workflows.registry import build_workflow


def _resume_value(req: HumanInput, *, text: str, approved: bool) -> Any:
    """Build the response a paused workflow expects, from stored primitives.

    Why from primitives, not from a live event: the original `RequestInfoEvent`
    only exists in the process where the workflow paused. On a real resume, the
    next user turn often lands on a *different* uvicorn worker (or after a
    restart), and the only durable state is what we stored on the
    `aistudiobot_agent_human_input` row + the checkpoint blob. Reconstructing the
    response from those primitives is what makes resume cross-process safe.

    Two shapes the framework accepts:
    - `function_approval` -> a `function_approval_response` Content (id + the
      original function_call + approved bool).
    - `handoff_user` / external input -> `[Message("user", [text])]` (the user's
      next message text wrapped as a Message).
    """
    if req.kind == "function_approval":
        function_call = Content.from_function_call(
            call_id=req.call_id, name=req.function_name, arguments=req.arguments
        )
        return Content.from_function_approval_response(
            approved=approved, id=req.request_id, function_call=function_call
        )
    # handoff_user / external_input -> the user's next message
    return [Message("user", [text])]


async def run_turn(
    *,
    chat_conversation_id: int,
    user_message_id: int,
    workflow_version: int = 1,
    text: str,
    approved: bool = False,
    open_request: HumanInput | None,
) -> tuple[Any, str | None]:
    """Run one turn. Fresh when there's no open request; otherwise resume it.
handle_message
    Returns (run_result, latest_checkpoint_id). May raise WorkflowCheckpointException
    if the graph changed since the checkpoint (caller handles -> restart conversation).
    """
    storage = PostgresCheckpointStorage(chat_conversation_id=chat_conversation_id)
    workflow = build_workflow(
        "handoff",
        conversation_id=chat_conversation_id,
        workflow_version=workflow_version,
        checkpoint_storage=storage,
        user_message_id=user_message_id,
    )

    if open_request is None:
        result = await workflow.run(text, include_status_events=True, checkpoint_storage=storage)
    else:
        value = _resume_value(open_request, text=text, approved=approved)
        result = await workflow.run(
            responses={open_request.request_id: value},
            checkpoint_id=open_request.checkpoint_id,
            checkpoint_storage=storage,
            include_status_events=True,
        )

    latest = await storage.get_latest(
        workflow_name=workflow_name(chat_conversation_id, workflow_version)
    )
    return result, (latest.checkpoint_id if latest else None)


async def finalize_checkpoints(
    *,
    chat_conversation_id: int,
    workflow_version: int,
    latest_checkpoint_id: str | None,
    completed: bool,
) -> int:
    """Bound this conversation's checkpoints to {floor, latest} after a turn closes.

    A turn that closed cleanly (no pending approval) makes its latest checkpoint the
    new rollback floor; an awaiting-approval turn keeps the previous floor. Then prune
    everything except {floor, latest}. Returns the number of rows deleted.
    """
    storage = PostgresCheckpointStorage(chat_conversation_id=chat_conversation_id)
    name = workflow_name(chat_conversation_id, workflow_version)
    if completed:
        await storage.mark_floor(workflow_name=name, checkpoint_id=latest_checkpoint_id)
    return await storage.prune(workflow_name=name, latest_checkpoint_id=latest_checkpoint_id)
