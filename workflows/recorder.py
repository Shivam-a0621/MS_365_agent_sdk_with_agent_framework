"""Map a workflow run result -> aistudiobot_chathistory + aistudiobot_agent_actions rows.

Processes the ``output`` events (each carries an AgentResponse whose messages hold
the typed contents). The service handles the user message (before the run), the
approval *decision* (it knows the approved bool on resume), and aistudiobot_agent_human_input.

Assistant `aistudiobot_chathistory` rows and `aistudiobot_agent_actions` rows are
both written this turn. Actions soft-ref the user inbound row via
`user_message_id`. Transcript rows have no parent_id — assistant rows sit
between user rows in the shared `aistudiobot_event_seq` order; that order is the
implicit lineage.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agents.handoff_agents import WORKFLOW_ANALYZER
from db import repositories as repo

_HANDOFF_PREFIX = "handoff_to_"


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool, dict, list)):
        return v
    return str(v)


def _is_assistant(role: Any) -> bool:
    return "assistant" in str(role).lower()


def _assistant_activity(agent_name: str | None, text: str) -> dict:
    """Synthesize a small outbound Activity dict for an assistant chathistory row.
    Good-enough fidelity for Teams (text replies); a perfect capture would need a
    send_activity_handler interceptor on the SDK side."""
    return {
        "type": "message",
        "from": {"name": agent_name} if agent_name else None,
        "text": text,
    }


async def record_run(
    session: AsyncSession,
    result: Any,
    *,
    chat_session_id: int,
    chat_conversation_id: int,
    user_message_id: int,
) -> dict[str, str]:
    """Persist assistant transcript rows + aistudiobot_agent_actions rows from the run events.

    Returns a {call_id -> agent_name} map for the approvals seen this run, so the
    caller can stamp the owning agent onto the aistudiobot_agent_human_input row.
    """
    handoff_call_ids: set = set()
    call_to_tool: dict = {}  # call_id -> tool name (to label tool_result rows)
    approval_agents: dict = {}  # call_id -> agent that requested the approval
    recorded_approvals: set = set()  # approval call_ids already written this run

    # An approval-required tool arrives as a function_call AND a function_approval_request with the SAME
    # call_id on the PAUSE turn. We pre-scan the approval requests so we can record the approval_request
    # ONLY (skipping its tool_call) on this turn; the service stamps the decision onto that same row and
    # appends tool_call + tool_result on the RESUME turn. Final actions order:
    # approval_request(status=approved) -> tool_call -> tool_result.
    approvals_by_call: dict = {}  # call_id -> (approval content, agent_name)
    for ev in result:
        if getattr(ev, "type", None) != "output":
            continue
        data = getattr(ev, "data", None)
        agent_from_event = getattr(ev, "executor_id", None)
        agent_response = getattr(data, "agent_response", None) or data
        for msg in getattr(agent_response, "messages", None) or []:
            agent_name = getattr(msg, "author_name", None) or agent_from_event
            for content in getattr(msg, "contents", None) or []:
                if getattr(content, "type", "") == "function_approval_request":
                    approvals_by_call[getattr(content, "id", None)] = (content, agent_name)

    async def _emit_approval(content: Any, agent_name: Any) -> None:
        fc = getattr(content, "function_call", None)
        call_id = getattr(content, "id", None)
        approval_agents[call_id] = agent_name  # remember the owning agent
        recorded_approvals.add(call_id)
        await repo.add_action(
            session,
            chat_conversation_id=chat_conversation_id,
            user_message_id=user_message_id,
            event_type="approval_request",
            agent_name=agent_name,
            tool_name=getattr(fc, "name", None),
            call_id=call_id,
            payload={"arguments": _jsonable(getattr(fc, "arguments", None))},
        )

    for ev in result:
        if getattr(ev, "type", None) != "output":
            continue
        data = getattr(ev, "data", None)
        agent_from_event = getattr(ev, "executor_id", None)
        agent_response = getattr(data, "agent_response", None) or data
        messages = getattr(agent_response, "messages", None)
        if not messages:
            continue

        for msg in messages:
            role = getattr(msg, "role", None)
            agent_name = getattr(msg, "author_name", None) or agent_from_event
            for content in getattr(msg, "contents", None) or []:
                ctype = getattr(content, "type", "")
                # Event -> row mapping (per content within a message):
                #   text                       -> aistudiobot_chathistory (role=assistant)
                #   function_call (handoff_*)  -> aistudiobot_agent_actions (event_type=handoff)
                #   function_call (other)      -> aistudiobot_agent_actions (event_type=tool_call)
                #                                 (approval_request emitted FIRST when the call needs it)
                #   function_result            -> aistudiobot_agent_actions (event_type=tool_result)
                #                                 (handoff acks skipped via handoff_call_ids)
                #   function_approval_request  -> aistudiobot_agent_actions (event_type=approval_request)

                if ctype == "text":
                    text = (getattr(content, "text", "") or "").strip()
                    if text and _is_assistant(role):
                        await repo.add_chat_message(
                            session,
                            chat_session_id=chat_session_id,
                            chat_conversation_id=chat_conversation_id,
                            role="assistant",
                            text=text,
                            activity=_assistant_activity(agent_name, text),
                            agent_name=agent_name,
                        )

                elif ctype == "function_call":
                    name = getattr(content, "name", "") or ""
                    call_id = getattr(content, "call_id", None) or getattr(
                        content, "id", None
                    )
                    args = _jsonable(getattr(content, "arguments", None))
                    if name.startswith(_HANDOFF_PREFIX):
                        handoff_call_ids.add(call_id)
                        await repo.add_action(
                            session,
                            chat_conversation_id=chat_conversation_id,
                            user_message_id=user_message_id,
                            event_type="handoff",
                            agent_name=agent_name,
                            tool_name=name,
                            tool_kind="handoff",
                            call_id=call_id,
                            payload={
                                "source": agent_name,
                                "target": name[len(_HANDOFF_PREFIX) :],
                            },
                        )
                    elif call_id in approvals_by_call:
                        # Approval-gated call on the PAUSE turn: record the approval_request ONLY. The
                        # tool has not run yet — the service stamps this row's status (approved/denied) and
                        # appends tool_call + tool_result on the RESUME turn, so the audit reads
                        # approval_request(status=approved) -> tool_call -> tool_result.
                        if call_id not in recorded_approvals:
                            ac, an = approvals_by_call[call_id]
                            await _emit_approval(ac, an)
                    else:
                        call_to_tool[call_id] = name  # remember the name for the tool_result
                        await repo.add_action(
                            session,
                            chat_conversation_id=chat_conversation_id,
                            user_message_id=user_message_id,
                            event_type="tool_call",
                            agent_name=agent_name,
                            tool_name=name,
                            tool_kind=(
                                "mcp" if agent_name == WORKFLOW_ANALYZER else "function"
                            ),
                            call_id=call_id,
                            payload={"arguments": args},
                        )

                elif ctype == "function_result":
                    call_id = getattr(content, "call_id", None)
                    if call_id in handoff_call_ids:
                        continue  # the handoff ack, already recorded as a handoff action
                    await repo.add_action(
                        session,
                        chat_conversation_id=chat_conversation_id,
                        user_message_id=user_message_id,
                        event_type="tool_result",
                        agent_name=agent_name,
                        tool_name=call_to_tool.get(call_id),  # look up the name by call_id
                        call_id=call_id,
                        status="ok",
                        payload={"result": _jsonable(getattr(content, "result", None))},
                    )

                elif ctype == "function_approval_request":
                    # Usually already emitted just before its tool_call (above); emit here only if
                    # the approval arrived without a preceding function_call this run.
                    call_id = getattr(content, "id", None)
                    if call_id not in recorded_approvals:
                        await _emit_approval(content, agent_name)

    return approval_agents
