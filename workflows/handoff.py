"""Build the IT-support handoff workflow (port of the POC handoff_workflow.py).

triage_agent (start) hands off to file_master / ticket_raiser / ae_workflow_analyzer.

The workflow NAME is unique per conversation (`it_support__{conversation_id}__v{version}`)
because agent_framework keys checkpoints by workflow_name only — this isolates each
conversation's checkpoints. The `version` lets us intentionally invalidate stale
checkpoints when the agent graph changes.
"""

from __future__ import annotations

from agent_framework import CheckpointStorage, Workflow
from agent_framework.orchestrations import HandoffBuilder

from agents.client import build_chat_client
from agents.handoff_agents import TRIAGE, build_handoff_agents
from mcps.sse_tool import build_ae_mcp

WORKFLOW_KIND = "handoff"


def workflow_name(conversation_id: str | int, workflow_version: int = 1) -> str:
    return f"it_support__{conversation_id}__v{workflow_version}"


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

    builder = HandoffBuilder(
        name=workflow_name(conversation_id, workflow_version), participants=agents
    ).with_start_agent(by_name[TRIAGE])
    if checkpoint_storage is not None:
        builder = builder.with_checkpointing(checkpoint_storage)
    return builder.build()
