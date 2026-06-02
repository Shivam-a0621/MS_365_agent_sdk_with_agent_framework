"""Build the IT-support handoff workflow on the context-aware handoff orchestrator.

triage_agent (start) hands off to file_master / ticket_raiser / ae_workflow_analyzer via
``ContextAwareHandoffBuilder`` (``workflows/handoff_orchestrator.py``), which shares each agent's
tool RESULTS (as text) across handoffs so downstream agents see completed work — and, because it
reuses the framework's ``HandoffAgentExecutor``, preserves the shared conversation across an approval
pause+resume (the bug the from-scratch mesh had).

The workflow NAME is unique per conversation (and per graph version) because agent_framework keys
checkpoints by ``workflow_name``; bumping ``_GRAPH_VERSION`` invalidates stale checkpoints when the
agent graph / executor changes.
"""

from __future__ import annotations

from agent_framework import CheckpointStorage, Message, Workflow

from agents.client import build_chat_client
from agents.handoff_agents import TRIAGE, build_handoff_agents
from mcps.sse_tool import build_ae_mcp
from workflows.handoff_orchestrator import ContextAwareHandoffBuilder

WORKFLOW_KIND = "handoff"

# Bumped when the executor/graph changes so stale checkpoints (a different graph signature) are never
# matched by name. The framework would also reject them via WorkflowCheckpointException, but a fresh
# name avoids the exception path entirely.
_GRAPH_VERSION = 4  # bumped: added start_background_task to ae_workflow_analyzer (tool set is part of the signature)

# Safety net against unbounded agent->agent ping-pong WITHIN a single turn (HITL handoff has no
# built-in hop limit). This is PER-TURN, not a cap on total conversation length — _full_conversation
# grows across turns, so a total-length cap would eventually terminate a healthy long conversation
# (no handoff_user pause gets created -> the next message starts a fresh turn and history is lost).
MAX_HOPS_PER_TURN = 40


def workflow_name(conversation_id: str | int, workflow_version: int = 1) -> str:
    return f"it_support__{conversation_id}__v{workflow_version}_g{_GRAPH_VERSION}"


def _conversation_cap_reached(conversation: list[Message]) -> bool:
    """Termination: stop the turn if agents have handed off too many times SINCE the last user
    message (a within-turn loop). Counts trailing non-user messages, so total conversation length
    across turns does not trip it."""
    hops = 0
    for msg in reversed(conversation):
        if "user" in str(getattr(msg, "role", "")).lower():
            break  # reached the start of this turn
        hops += 1
    return hops > MAX_HOPS_PER_TURN


def build_handoff_workflow(
    *,
    conversation_id: str | int = "default",
    workflow_version: int = 1,
    checkpoint_storage: CheckpointStorage | None = None,
    user_message_id: int | None = None,
) -> Workflow:
    client = build_chat_client()
    ae_mcp = build_ae_mcp()
    # When running a real turn, pass the ids so each agent gets LLM-telemetry middleware.
    chat_conversation_id = conversation_id if isinstance(conversation_id, int) else None
    agents = build_handoff_agents(
        client, ae_mcp, chat_conversation_id=chat_conversation_id, user_message_id=user_message_id
    )
    by_name = {a.name: a for a in agents}

    builder = (
        ContextAwareHandoffBuilder(
            name=workflow_name(conversation_id, workflow_version), participants=agents
        )
        .with_start_agent(by_name[TRIAGE])
        .with_termination_condition(_conversation_cap_reached)
    )
    if checkpoint_storage is not None:
        builder = builder.with_checkpointing(checkpoint_storage)
    return builder.build()
