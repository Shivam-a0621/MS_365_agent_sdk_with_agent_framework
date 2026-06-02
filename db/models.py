"""ORM models for agent-fastapi — two decoupled families in one schema.

Persistence follows the project's stability seam (mirrors how chatbot39 split its
`aistudiobot_*` platform tables from its `custom_*` app tables):

  PLATFORM / SDK FAMILY (fixed)            prefix: aistudiobot_
    Identity, conversation hierarchy, transcript, SDK Storage.
    aistudiobot_bot, aistudiobot_channel, aistudiobot_botchannelmapping,
    aistudiobot_chatsession -> aistudiobot_chatconversation, aistudiobot_chathistory,
    aistudiobot_store.

  AGENT-FRAMEWORK FAMILY (changeable)      prefix: aistudiobot_agent_
    Framework-bound event log + framework-internal state — the agent action
    log (handoffs, tool calls, approvals), LLM telemetry, the HITL pause
    record, durable workflow checkpoint blobs. Swap the framework and only
    these change.
    aistudiobot_agent_actions, aistudiobot_agent_llm_call,
    aistudiobot_agent_human_input, aistudiobot_agent_checkpoint.

Anchoring: every user inbound (text OR card submit) writes an
`aistudiobot_chathistory` row (role=user) that is the per-query anchor.
`aistudiobot_agent_actions` / `aistudiobot_agent_llm_call` /
`aistudiobot_agent_human_input` rows soft-ref it via `user_message_id`.
Transcript rows have no explicit parent link — assistant rows simply sit
between user rows in the shared `seq` order, and that order IS the lineage.
There is no separate "turn" table — the user message IS the unit of work.

Dependency is ONE-WAY: an `aistudiobot_agent_*` row carries a platform key
(`chat_conversation_id` / `user_message_id`) as a plain INDEXED column with NO
foreign key. No FK crosses the boundary either way; no `aistudiobot_*` (non-agent)
table references an `aistudiobot_agent_*` table.

Ordering across `aistudiobot_chathistory` and `aistudiobot_agent_actions` uses a SHARED
Postgres sequence `aistudiobot_event_seq` (cheaper than MAX+1, no race), filterable
by `chat_conversation_id` for per-conversation order.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


# Shared sequence used to order events across aistudiobot_chathistory +
# aistudiobot_agent_actions within a conversation. Global counter; numbers are gappy and
# not contiguous per conversation, but `ORDER BY seq` filtered by
# chat_conversation_id gives the exact interleaved order with no MAX+1 race.
# Attached to MetaData so alembic autogenerate emits CREATE/DROP SEQUENCE.
event_seq = Sequence("aistudiobot_event_seq", metadata=Base.metadata)


# ============================================================================
# PLATFORM / SDK FAMILY (fixed) — prefix aistudiobot_
# Identity, conversation hierarchy, transcript, agent actions, SDK Storage.
# Hard FKs WITHIN this family. Never references the aistudiobot_agent_ family.
# ============================================================================

# --- identity (mirrors chatbot39 aistudiobot_bot/channel/botchannelmapping) ---

class Bot(Base):
    """One row per bot (the AAD app behind the agent). Stable identity for
    tying conversations to a specific bot deployment."""

    __tablename__ = "aistudiobot_bot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    bot_id: Mapped[str] = mapped_column(String(255))


class Channel(Base):
    """One row per channel the bot speaks on (msteams, web, rest, …)."""

    __tablename__ = "aistudiobot_channel"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))


class BotChannelMapping(Base):
    """Cross product of bots × channels — the per-deployment identity each
    `ChatSession` keys off (a bot in Teams ≠ same bot on the web channel)."""

    __tablename__ = "aistudiobot_botchannelmapping"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("aistudiobot_bot.id"))
    channel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("aistudiobot_channel.id"))


# --- conversation hierarchy (mirrors chatbot39 chatsession/chatconversation) ---

class ChatSession(Base):
    """One row per Teams conversation reference (`agent_conversation_id`).
    Tied to one user. Currently 1:1 with `ChatConversation` (one open conversation
    per session at a time)."""

    __tablename__ = "aistudiobot_chatsession"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_conversation_id: Mapped[str] = mapped_column(String(150))
    agent_channel_mapping_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("aistudiobot_botchannelmapping.id")
    )


class ChatConversation(Base):
    """The conversation; everything else hangs off `chat_conversation_id`
    (transcript rows, framework actions/checkpoints/llm-calls/HITL)."""

    __tablename__ = "aistudiobot_chatconversation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    intent: Mapped[str] = mapped_column(String(64))
    is_ka: Mapped[bool] = mapped_column(Boolean)
    chat_session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("aistudiobot_chatsession.id")
    )
    skill_name: Mapped[str] = mapped_column(String(64))


# --- channel transcript (user + assistant messages) ----------------------------

class ChatHistory(Base):
    """The transcript — and the per-query anchor.

    A user row is written for EVERY inbound Activity (text or card submit) and
    becomes the anchor that `aistudiobot_agent_actions` / `aistudiobot_agent_*`
    rows point at via `user_message_id`. Assistant rows live in this same table;
    lineage between user and assistant rows is implicit (sorted by ``seq``,
    assistant rows are the ones between two user rows). There is no separate
    turn row.
    """

    __tablename__ = "aistudiobot_chathistory"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(64))  # user | assistant
    message_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    activity: Mapped[dict] = mapped_column(JSONB)  # real channel Activity (in/out)
    text: Mapped[str] = mapped_column(Text)  # visible text (may be "" on card submits)
    chat_conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("aistudiobot_chatconversation.id"), index=True
    )
    chat_session_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("aistudiobot_chatsession.id"))
    agent_name: Mapped[str | None] = mapped_column(String(128))  # label on assistant rows
    seq: Mapped[int] = mapped_column(
        BigInteger,
        event_seq,
        server_default=event_seq.next_value(),
        nullable=False,
    )  # shared with aistudiobot_agent_actions via aistudiobot_event_seq


# --- SDK key-value Storage (mirrors chatbot39 aistudiobot_store) ----------------

class StoreRow(Base):
    """Backs PostgresStorage — the MS 365 Agents SDK's own key-value state
    (TurnState etc.). Key is the SDK storage key, data is StoreItem JSON."""

    __tablename__ = "aistudiobot_store"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)  # e.g. {channel}/conversations/{id}
    data: Mapped[dict] = mapped_column(JSONB)  # StoreItem.store_item_to_json()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ============================================================================
# AGENT-FRAMEWORK FAMILY (changeable) — prefix aistudiobot_agent_
# Reasoning event log + framework-internal state. user_message_id /
# chat_conversation_id are SOFT refs to the platform family: plain INDEXED
# columns, NO foreign key.
# ============================================================================

# --- agent action log (handoffs, tool/MCP calls, approvals) --------------------

class AgentAction(Base):
    """One row per action an agent took (handoff / tool call / tool result /
    approval request / approval decision). Anchored to the user message that
    triggered the work via `user_message_id` (soft ref, no FK)."""

    __tablename__ = "aistudiobot_agent_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_conversation_id: Mapped[int] = mapped_column(BigInteger, index=True)  # soft ref -> aistudiobot_chatconversation.id
    user_message_id: Mapped[int] = mapped_column(BigInteger, index=True)  # soft ref -> aistudiobot_chathistory.id
    seq: Mapped[int] = mapped_column(
        BigInteger,
        event_seq,
        server_default=event_seq.next_value(),
        nullable=False,
    )  # shared with aistudiobot_chathistory via aistudiobot_event_seq
    event_type: Mapped[str] = mapped_column(
        String(32)
    )  # handoff | tool_call | tool_result | approval_request (its status carries the decision)
    agent_name: Mapped[str | None] = mapped_column(String(128))
    tool_name: Mapped[str | None] = mapped_column(String(128))  # create_ticket, handoff_to_x, mcp tool…
    tool_kind: Mapped[str | None] = mapped_column(String(32))  # function | mcp | handoff
    call_id: Mapped[str | None] = mapped_column(String(128))  # correlate call/result/approval
    # status on approval_request: null=pending -> approved|denied (stamped in place on resume); ok|failed on results
    status: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict | None] = mapped_column(JSONB)  # args / result / approval / {source,target}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- LLM-call telemetry ---------------------------------------------------------

class AgentLlmCall(Base):
    """One row per LLM model invocation (model id, token usage, latency).
    Telemetry only — not load-bearing for resume/replay."""

    __tablename__ = "aistudiobot_agent_llm_call"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_conversation_id: Mapped[int] = mapped_column(BigInteger, index=True)  # soft ref -> aistudiobot_chatconversation.id
    user_message_id: Mapped[int | None] = mapped_column(BigInteger, index=True)  # soft ref -> aistudiobot_chathistory.id
    agent_name: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    finish_reason: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    request: Mapped[dict | None] = mapped_column(JSONB)  # optional full audit
    response: Mapped[dict | None] = mapped_column(JSONB)  # optional full audit
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- durable workflow checkpoints (backs PostgresCheckpointStorage) -------------

class WorkflowCheckpointRow(Base):
    """One row per agent_framework superstep checkpoint; isolated per conversation
    via a unique workflow_name. Pruned to {floor, latest} per conversation:
    `is_floor` marks the last completed-turn checkpoint (the rollback floor)."""

    __tablename__ = "aistudiobot_agent_checkpoint"

    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_name: Mapped[str] = mapped_column(String(256), index=True)
    graph_signature_hash: Mapped[str] = mapped_column(String(128))
    previous_checkpoint_id: Mapped[str | None] = mapped_column(String(64))
    chat_conversation_id: Mapped[int | None] = mapped_column(BigInteger, index=True)  # soft ref -> aistudiobot_chatconversation.id
    iteration_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    version: Mapped[str] = mapped_column(String(16), default="1.0", server_default=text("'1.0'"))
    timestamp: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB)
    is_floor: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )  # the last completed-turn checkpoint kept as a rollback floor when pruning
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_aistudiobot_agent_checkpoint_name_ts", "workflow_name", "timestamp"),
    )


# --- HITL approval requests + decisions (durable resume source) -----------------

class HumanInput(Base):
    """One row per durable HITL pause — either a `function_approval` (tool
    that needs the user to OK it) or a `handoff_user` (workflow asking for the
    user's next message). Resume reads this row to know what's pending and
    which `af_checkpoint` to load."""

    __tablename__ = "aistudiobot_agent_human_input"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_conversation_id: Mapped[int] = mapped_column(BigInteger, index=True)  # soft ref -> aistudiobot_chatconversation.id
    user_message_id: Mapped[int | None] = mapped_column(BigInteger, index=True)  # soft ref -> aistudiobot_chathistory.id
    request_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))  # function_approval | handoff_user | external_input
    agent_name: Mapped[str | None] = mapped_column(String(128))  # the agent that owns this request
    function_name: Mapped[str | None] = mapped_column(String(128))
    call_id: Mapped[str | None] = mapped_column(String(128))
    arguments: Mapped[dict | None] = mapped_column(JSONB)
    response_type: Mapped[str | None] = mapped_column(String(256))
    request_text: Mapped[str | None] = mapped_column(Text)  # text shown to the user for this request
    status: Mapped[str] = mapped_column(String(32), default="open", server_default=text("'open'"))
    checkpoint_id: Mapped[str | None] = mapped_column(String(64))  # soft ref -> aistudiobot_agent_checkpoint.checkpoint_id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("chat_conversation_id", "request_id", name="uq_aistudiobot_agent_human_input_conv_request"),
        Index(
            "ix_aistudiobot_agent_human_input_open",
            "chat_conversation_id",
            postgresql_where=text("status = 'open'"),
        ),
    )


# --- long-running background tasks (own checkpoint lineage, decoupled from the chat) ---

class AgentTask(Base):
    """One row per long-running background task started mid-conversation (a generic
    external async job; the specific engine is just one `task_type`).

    The task runs in its OWN checkpoint lineage (`task_workflow_name`), DECOUPLED from
    the conversation: this row is the ONLY record of the pause — it is deliberately NOT
    written as an `aistudiobot_agent_human_input` row — so the conversation's
    `get_open_human_input` never returns it and the user can keep chatting while it runs.
    The external job's completion callback looks this row up by `correlation_id` and
    resumes the task workflow from `checkpoint_id`.
    """

    __tablename__ = "aistudio_agent_task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)  # external callback key + idempotency anchor
    chat_conversation_id: Mapped[int] = mapped_column(BigInteger, index=True)  # soft ref -> aistudiobot_chatconversation.id
    channel: Mapped[str] = mapped_column(String(64))  # with conversation_ref -> conversation_lock key
    conversation_ref: Mapped[str] = mapped_column(String(150))  # agent_conversation_id (proactive push target)
    task_workflow_name: Mapped[str] = mapped_column(String(256))  # the task's own checkpoint lineage
    request_id: Mapped[str] = mapped_column(String(128))  # request_info id to resume against
    checkpoint_id: Mapped[str | None] = mapped_column(String(64))  # soft ref -> aistudiobot_agent_checkpoint.checkpoint_id
    task_type: Mapped[str] = mapped_column(String(64))  # generic kind; the engine integration is one value
    params: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default=text("'pending'")
    )  # pending | delivered | failed | expired
    result: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(  # reaper hot-path: find still-pending tasks past their TTL
            "ix_aistudio_agent_task_pending",
            "status",
            postgresql_where=text("status = 'pending'"),
        ),
    )
