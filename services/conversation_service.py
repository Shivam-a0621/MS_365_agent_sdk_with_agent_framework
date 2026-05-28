"""Channel-neutral turn orchestration.

One entry point — ``handle_message`` — drives a single user inbound for both
Teams and any future REST client: resolve identity, write the user inbound row
(text or card submit), run the handoff workflow (durable checkpoints), persist
the full flow (transcript, actions, llm telemetry, HITL), and return a
channel-neutral result (replies + pending approvals) for the caller to render.

There is no separate "turn" table — the user ``aistudiobot_chathistory`` row IS the
per-query anchor; everything else this query produces soft-refs it via
``user_message_id``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from os import environ
from typing import Any

from agent_framework.exceptions import WorkflowCheckpointException

from db import repositories as repo
from db.session import SessionLocal
from services.locks import conversation_lock
from workflows.manager import finalize_checkpoints, run_turn
from workflows.outcome import extract_replies, is_function_approval
from workflows.recorder import _jsonable, record_run

logger = logging.getLogger("app.conversation")

_APPROVE_WORDS = {"approve", "approved", "yes", "y", "ok", "accept"}
_DENY_WORDS = {"deny", "denied", "no", "n", "reject", "rejected"}


@dataclass
class PendingApproval:
    request_id: str
    function_name: str | None
    arguments: Any


@dataclass
class TurnResult:
    replies: list[str] = field(default_factory=list)
    approvals: list[PendingApproval] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)  # non-approval prompts
    error: str | None = None


def _decision(text: str, value: Any) -> bool:
    """True if the user approved the pending HITL pause.

    A card submit (``value`` carries ``{"approved": true}``) wins outright. If
    there's no card value, we fall back to checking if the typed text is one of
    the approve keywords (``yes``, ``y``, ``ok``, ``approve``, …). The card
    value always trumps the text.
    """
    if isinstance(value, dict) and "approved" in value:
        return bool(value.get("approved"))
    return text.strip().lower() in _APPROVE_WORDS


def _pending_primitives(event: Any) -> dict[str, Any]:
    """Project a RequestInfoEvent into the flat dict we persist + render.

    A ``function_approval`` event surfaces ``function_name`` / ``call_id`` /
    ``arguments`` — everything needed to reconstruct the approval Content on
    resume. A ``handoff_user`` event surfaces ``request_text`` — the prompt the
    agent wants to show the user. Both kinds share ``request_id`` and ``kind``.
    """
    data = event.data
    if is_function_approval(event):
        fc = getattr(data, "function_call", None)
        return {
            "kind": "function_approval",
            "request_id": event.request_id,
            "function_name": getattr(fc, "name", None),
            "call_id": getattr(fc, "call_id", None) or getattr(fc, "id", None),
            "arguments": _jsonable(getattr(fc, "arguments", None)),
        }
    request_text = getattr(data, "message", None)
    if request_text is None:
        msgs = getattr(data, "messages", None)
        request_text = getattr(msgs[-1], "text", None) if msgs else None
    return {"kind": "handoff_user", "request_id": event.request_id, "request_text": request_text}


def _fallback_activity(text: str, value: Any) -> dict:
    """Synthesize a minimal inbound Activity dict for callers that don't pass one.

    Real Teams turns go through ``bot.py``, which serializes ``context.activity``
    and passes it in. REST/script callers (e.g. ``scripts/spike_service.py``)
    skip that — this stub keeps ``aistudiobot_chathistory.activity`` non-null and
    meaningful for them.
    """
    return {"type": "message", "text": text, "value": _jsonable(value)}


async def _record_approval_card_outbound(
    session: Any,
    *,
    chat_session_id: int,
    chat_conversation_id: int,
    prim: dict[str, Any],
    owner: str | None,
) -> None:
    """Write a transcript row representing the outbound approval card.

    bot.py renders the actual Teams adaptive card via ``approval_card(...)``;
    this row is the transcript's record that the card was sent. The row lands
    in ``aistudiobot_chathistory`` between the user request and the user's
    approval-submit row (ordered by ``seq``) so any transcript replay shows
    the card was offered.
    """
    await repo.add_chat_message(
        session,
        chat_session_id=chat_session_id,
        chat_conversation_id=chat_conversation_id,
        role="assistant",
        text=f"(approval requested for {prim.get('function_name')})",
        activity={
            "type": "approval_request",
            "from": {"name": owner} if owner else None,
            "approval_id": prim["request_id"],
            "function_name": prim.get("function_name"),
            "arguments": prim.get("arguments"),
        },
        agent_name=owner,
    )


async def handle_message(
    *,
    channel: str,
    conversation_ref: str,
    user_id: str = "",
    user_name: str = "",
    text: str = "",
    value: Any = None,
    activity: dict | None = None,
    workflow_version: int = 1,
) -> TurnResult:
    async with conversation_lock(f"{channel}:{conversation_ref}"):
        async with SessionLocal() as session:
            # 1. identity: bot/channel mapping -> session -> conversation
            mapping = await repo.get_or_create_bot_channel_mapping(
                session,
                bot_name="it_support_bot",
                bot_app_id=environ.get(
                    "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", "local"
                ),
                channel_name=channel,
            )
            chat = await repo.get_or_create_session(
                session,
                conversation_ref=conversation_ref,
                user_id=user_id or "unknown",
                username=user_name or "unknown",
                agent_channel_mapping_id=mapping.id,
            )
            conv = await repo.get_or_create_conversation(session, chat_session_id=chat.id)

            open_request = await repo.get_open_human_input(session, conv.id)
            approved = _decision(text, value)

            # 2. ALWAYS write the user inbound row (text OR card submit). This row IS
            #    the per-query anchor; assistant rows and actions soft-ref its id.
            user_msg = await repo.add_chat_message(
                session,
                chat_session_id=chat.id,
                chat_conversation_id=conv.id,
                role="user",
                text=text,
                activity=activity or _fallback_activity(text, value),
                agent_name=None,
            )
            await session.commit()  # checkpoint store + telemetry middleware (own sessions) must see it

            # 3. run the workflow (fresh or resume)
            try:
                result, latest_checkpoint_id = await run_turn(
                    chat_conversation_id=conv.id,
                    user_message_id=user_msg.id,
                    workflow_version=workflow_version,
                    text=text,
                    approved=approved,
                    open_request=open_request,
                )
            except WorkflowCheckpointException as exc:
                logger.warning("Graph signature changed for conv %s: %s", conv.id, exc)
                if open_request is not None:
                    await repo.resolve_human_input(session, open_request, "expired")
                await session.commit()
                return TurnResult(
                    replies=["This conversation needs to restart — please send your request again."],
                    error="workflow_changed",
                )
            except Exception as exc:  # noqa: BLE001 - never crash the turn
                logger.exception("Workflow error for conv %s: %s", conv.id, exc)
                await session.commit()  # leave any open_request intact for retry
                return TurnResult(error=str(exc), replies=["Sorry, something went wrong. Please try again."])

            # 4. persist transcript + actions from the run events. Assistant
            #    aistudiobot_chathistory rows land between user rows in seq order
            #    (no explicit parent link); aistudiobot_agent_actions rows soft-ref
            #    the user inbound via user_message_id. Returns {call_id -> agent_name}
            #    for approvals raised this run.
            approval_agents = await record_run(
                session,
                result,
                chat_session_id=chat.id,
                chat_conversation_id=conv.id,
                user_message_id=user_msg.id,
            ) or {}

            # 5. resolve the previous request (if any) — record the decision as an action
            if open_request is not None:
                if open_request.kind == "function_approval":
                    await repo.add_action(
                        session,
                        chat_conversation_id=conv.id,
                        user_message_id=user_msg.id,
                        event_type="approval_decision",
                        agent_name=open_request.agent_name,
                        tool_name=open_request.function_name,
                        call_id=open_request.call_id,
                        status="approved" if approved else "denied",
                        payload={"approved": approved},
                    )
                    await repo.resolve_human_input(
                        session, open_request, "approved" if approved else "denied"
                    )
                else:
                    await repo.resolve_human_input(session, open_request, "answered")

            # 6. record any NEW pauses -> aistudiobot_agent_human_input, build channel-neutral output
            result_obj = TurnResult(replies=extract_replies(result))
            for event in result.get_request_info_events():
                prim = _pending_primitives(event)
                owner = approval_agents.get(prim.get("call_id"))
                await repo.upsert_human_input(
                    session,
                    chat_conversation_id=conv.id,
                    user_message_id=user_msg.id,
                    request_id=prim["request_id"],
                    kind=prim["kind"],
                    agent_name=owner,
                    function_name=prim.get("function_name"),
                    call_id=prim.get("call_id"),
                    arguments=prim.get("arguments"),
                    request_text=prim.get("request_text"),
                    checkpoint_id=latest_checkpoint_id,
                )
                if prim["kind"] == "function_approval":
                    await _record_approval_card_outbound(
                        session,
                        chat_session_id=chat.id,
                        chat_conversation_id=conv.id,
                        prim=prim,
                        owner=owner,
                    )
                    result_obj.approvals.append(
                        PendingApproval(
                            request_id=prim["request_id"],
                            function_name=prim.get("function_name"),
                            arguments=prim.get("arguments"),
                        )
                    )
                elif prim.get("request_text"):
                    result_obj.prompts.append(prim["request_text"])

            # 7. commit; then bound this conversation's checkpoints to {floor, latest}
            await session.commit()
            if latest_checkpoint_id:
                await finalize_checkpoints(
                    chat_conversation_id=conv.id,
                    workflow_version=workflow_version,
                    latest_checkpoint_id=latest_checkpoint_id,
                    completed=(not result_obj.approvals),
                )
            return result_obj
