"""Workflow registry — the seam for adding more workflows later.

For now only the handoff workflow is wired. Add new builders to ``_BUILDERS``.
"""

from __future__ import annotations

from agent_framework import CheckpointStorage, Workflow

from workflows.handoff import build_handoff_workflow

_BUILDERS = {
    "handoff": build_handoff_workflow,
}


def build_workflow(
    kind: str = "handoff",
    *,
    conversation_id: str | int = "default",
    workflow_version: int = 1,
    checkpoint_storage: CheckpointStorage | None = None,
    user_message_id: int | None = None,
) -> Workflow:
    try:
        builder = _BUILDERS[kind]
    except KeyError:
        raise ValueError(
            f"Unknown workflow kind {kind!r}. Available: {', '.join(_BUILDERS)}."
        ) from None
    return builder(
        conversation_id=conversation_id,
        workflow_version=workflow_version,
        checkpoint_storage=checkpoint_storage,
        user_message_id=user_message_id,
    )
