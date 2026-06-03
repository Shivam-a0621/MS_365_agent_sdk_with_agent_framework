"""Async data-access for the runtime.

Functions take an AsyncSession and `flush` (assign PKs) but do NOT commit — the
caller (ConversationService) owns the transaction boundaries, because identity +
the user-message row must be committed BEFORE the workflow runs (the checkpoint
store and the LLM-telemetry middleware write in their own sessions and must see
those rows).

`seq` on aistudiobot_chathistory / aistudiobot_agent_actions is auto-assigned by the shared
Postgres sequence `aistudiobot_event_seq` — we don't set it from Python.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    AgentAction,
    AgentLlmCall,
    AgentTask,
    Bot,
    BotChannelMapping,
    Channel,
    ChatConversation,
    ChatHistory,
    ChatSession,
    HumanInput,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_or_create_bot_channel_mapping(
    session: AsyncSession, *, bot_name: str, bot_app_id: str, channel_name: str
) -> BotChannelMapping:
    bot = (
        await session.execute(select(Bot).where(Bot.bot_id == bot_app_id))
    ).scalar_one_or_none()
    if bot is None:
        bot = Bot(name=bot_name, bot_id=bot_app_id)
        session.add(bot)
        await session.flush()

    channel = (
        await session.execute(select(Channel).where(Channel.name == channel_name))
    ).scalar_one_or_none()
    if channel is None:
        channel = Channel(name=channel_name)
        session.add(channel)
        await session.flush()

    mapping = (
        await session.execute(
            select(BotChannelMapping).where(
                BotChannelMapping.bot_id == bot.id,
                BotChannelMapping.channel_id == channel.id,
            )
        )
    ).scalar_one_or_none()
    if mapping is None:
        mapping = BotChannelMapping(bot_id=bot.id, channel_id=channel.id)
        session.add(mapping)
        await session.flush()
    return mapping


async def get_or_create_session(
    session: AsyncSession,
    *,
    conversation_ref: str,
    user_id: str,
    username: str,
    agent_channel_mapping_id: int,
) -> ChatSession:
    chat = (
        await session.execute(
            select(ChatSession).where(
                ChatSession.agent_conversation_id == conversation_ref
            )
        )
    ).scalar_one_or_none()
    if chat is None:
        chat = ChatSession(
            user_id=user_id,
            username=username,
            start_time=_now(),
            agent_conversation_id=conversation_ref,
            agent_channel_mapping_id=agent_channel_mapping_id,
        )
        session.add(chat)
        await session.flush()
    return chat


async def get_or_create_conversation(
    session: AsyncSession, *, chat_session_id: int, skill_name: str = "handoff"
) -> ChatConversation:
    """1:1 with the session (for now): the single open conversation for this session."""
    conv = (
        await session.execute(
            select(ChatConversation)
            .where(ChatConversation.chat_session_id == chat_session_id)
            .order_by(ChatConversation.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if conv is None:
        conv = ChatConversation(
            start_time=_now(),
            intent="",
            is_ka=False,
            chat_session_id=chat_session_id,
            skill_name=skill_name,
        )
        session.add(conv)
        await session.flush()
    return conv


async def add_chat_message(
    session: AsyncSession,
    *,
    chat_session_id: int,
    chat_conversation_id: int,
    role: str,
    text: str,
    activity: dict,
    agent_name: str | None = None,
) -> ChatHistory:
    """Insert one transcript row. `activity` is the real channel Activity dict.
    `seq` is auto-assigned by aistudiobot_event_seq; rows are ordered by seq
    alone (no explicit parent linking — assistant rows simply sit between user
    rows in the seq sequence)."""
    row = ChatHistory(
        role=role,
        message_time=_now(),
        activity=activity,
        text=text,
        chat_conversation_id=chat_conversation_id,
        chat_session_id=chat_session_id,
        agent_name=agent_name,
    )
    session.add(row)
    await session.flush()
    return row


async def add_action(
    session: AsyncSession,
    *,
    chat_conversation_id: int,
    user_message_id: int,
    event_type: str,
    agent_name: str | None = None,
    tool_name: str | None = None,
    tool_kind: str | None = None,
    call_id: str | None = None,
    status: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AgentAction:
    """Insert one aistudiobot_agent_actions row. seq is auto-assigned by aistudiobot_event_seq."""
    row = AgentAction(
        chat_conversation_id=chat_conversation_id,
        user_message_id=user_message_id,
        event_type=event_type,
        agent_name=agent_name,
        tool_name=tool_name,
        tool_kind=tool_kind,
        call_id=call_id,
        status=status,
        payload=payload,
    )
    session.add(row)
    await session.flush()
    return row


async def update_approval_status(
    session: AsyncSession,
    *,
    chat_conversation_id: int,
    call_id: str | None,
    status: str,
) -> AgentAction | None:
    """Stamp the decision onto the EXISTING approval_request action (status -> approved/denied) instead
    of writing a separate approval_decision row. The request row was created on the pause turn; the
    decision updates it in place on the resume turn (its seq stays first, now carrying the outcome)."""
    if not call_id:
        return None
    row = (
        await session.execute(
            select(AgentAction)
            .where(
                AgentAction.chat_conversation_id == chat_conversation_id,
                AgentAction.call_id == call_id,
                AgentAction.event_type == "approval_request",
            )
            .order_by(AgentAction.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        row.status = status
        await session.flush()
    return row


async def add_llm_call(
    session: AsyncSession,
    *,
    chat_conversation_id: int,
    user_message_id: int | None,
    agent_name: str | None,
    model: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    finish_reason: str | None,
    latency_ms: int | None,
    request: dict | None = None,
    response: dict | None = None,
) -> AgentLlmCall:
    row = AgentLlmCall(
        chat_conversation_id=chat_conversation_id,
        user_message_id=user_message_id,
        agent_name=agent_name,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        finish_reason=finish_reason,
        latency_ms=latency_ms,
        request=request,
        response=response,
    )
    session.add(row)
    await session.flush()
    return row


async def get_open_human_input(
    session: AsyncSession, chat_conversation_id: int
) -> HumanInput | None:
    return (
        await session.execute(
            select(HumanInput)
            .where(
                HumanInput.chat_conversation_id == chat_conversation_id,
                HumanInput.status == "open",
            )
            .order_by(HumanInput.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def upsert_human_input(
    session: AsyncSession,
    *,
    chat_conversation_id: int,
    user_message_id: int,
    request_id: str,
    kind: str,
    agent_name: str | None = None,
    function_name: str | None = None,
    call_id: str | None = None,
    arguments: dict[str, Any] | None = None,
    response_type: str | None = None,
    request_text: str | None = None,
    checkpoint_id: str | None = None,
) -> HumanInput:
    row = (
        await session.execute(
            select(HumanInput).where(
                HumanInput.chat_conversation_id == chat_conversation_id,
                HumanInput.request_id == request_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = HumanInput(
            chat_conversation_id=chat_conversation_id,
            user_message_id=user_message_id,
            request_id=request_id,
            kind=kind,
            agent_name=agent_name,
            function_name=function_name,
            call_id=call_id,
            arguments=arguments,
            response_type=response_type,
            request_text=request_text,
            status="open",
            checkpoint_id=checkpoint_id,
        )
        session.add(row)
    else:
        row.user_message_id = user_message_id
        row.checkpoint_id = checkpoint_id
        row.status = "open"
    await session.flush()
    return row


async def resolve_human_input(session: AsyncSession, row: HumanInput, status: str) -> None:
    row.status = status
    row.resolved_at = _now()
    await session.flush()


# --- long-running background tasks (aistudiobot_agent_task) ---------------------

async def create_agent_task(
    session: AsyncSession,
    *,
    correlation_id: str,
    chat_conversation_id: int,
    channel: str,
    conversation_ref: str,
    summary: str | None = None,
) -> AgentTask:
    """Record one in-flight background task: maps the external correlation_id back to the
    conversation so the completion callback knows which conversation to resume + notify."""
    row = AgentTask(
        correlation_id=correlation_id,
        chat_conversation_id=chat_conversation_id,
        channel=channel,
        conversation_ref=conversation_ref,
        summary=summary,
        status="pending",
    )
    session.add(row)
    await session.flush()
    return row


async def get_agent_task_by_correlation(
    session: AsyncSession, correlation_id: str
) -> AgentTask | None:
    """Look up a task by the external correlation id (the callback key)."""
    return (
        await session.execute(
            select(AgentTask).where(AgentTask.correlation_id == correlation_id)
        )
    ).scalar_one_or_none()


async def resolve_agent_task(
    session: AsyncSession,
    row: AgentTask,
    *,
    status: str,
    result: dict[str, Any] | None = None,
) -> None:
    """Close a task (status in delivered | failed) and stash its result payload."""
    row.status = status
    row.result = result
    row.resolved_at = _now()
    await session.flush()
