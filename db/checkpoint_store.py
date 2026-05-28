"""Postgres-backed CheckpointStorage for agent_framework workflows.

Mirrors the framework's FileCheckpointStorage exactly (same encode/decode helpers),
but persists into the `aistudiobot_agent_checkpoint` table so paused human-in-the-loop
runs survive restarts and are resumable from any worker.

The private `_checkpoint_encoding` import is isolated to this one file so a framework
rename only touches here.
"""

from __future__ import annotations

import logging
from typing import Any

from agent_framework import WorkflowCheckpoint
from agent_framework._workflows._checkpoint_encoding import (
    decode_checkpoint_value,
    encode_checkpoint_value,
)
from agent_framework.exceptions import WorkflowCheckpointException
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import WorkflowCheckpointRow
from db.session import SessionLocal

logger = logging.getLogger("app.checkpoint")

# Types pickled into handoff-workflow checkpoints that live OUTSIDE the `agent_framework`
# package (only agent_framework's own types are trusted by default), so the decoder must
# be told they're allowed. Extend this if decode raises "deserialization blocked for type".
ALLOWED_CHECKPOINT_TYPES: frozenset[str] = frozenset(
    {
        "agent_framework_orchestrations._handoff:HandoffAgentUserRequest",
        "types:GenericAlias",  # parameterized response_type, e.g. list[Message]
    }
)


class PostgresCheckpointStorage:
    """Implements the agent_framework CheckpointStorage Protocol over Postgres.

    Construct one per conversation so `conversation_id` is stamped on saved rows;
    isolation across conversations comes from a unique `workflow_name` per conversation.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession] = SessionLocal,
        *,
        chat_conversation_id: int | None = None,
        allowed_types: frozenset[str] | None = None,
    ) -> None:
        self._sm = sessionmaker
        self._chat_conversation_id = chat_conversation_id
        self._allowed_types = ALLOWED_CHECKPOINT_TYPES | (allowed_types or frozenset())

    def _decode(self, data: Any) -> WorkflowCheckpoint:
        """Reverse `save`'s encode step.

        `decode_checkpoint_value` rejects non-`agent_framework` classes by
        default for safety. Handoff blobs carry `HandoffAgentUserRequest` and
        `types:GenericAlias` (the parameterized `list[Message]` response_type),
        so we allowlist those in `ALLOWED_CHECKPOINT_TYPES`. If decode raises
        `deserialization blocked for type X`, add that X to the allowlist.
        """
        return WorkflowCheckpoint.from_dict(
            decode_checkpoint_value(data, allowed_types=self._allowed_types)
        )

    async def save(self, checkpoint: WorkflowCheckpoint) -> str:
        """Persist one checkpoint (the framework calls this once per superstep).

        Uses its own DB session and commits immediately — the framework can save
        mid-run without coupling to caller transaction boundaries. `session.merge`
        upserts by `checkpoint_id`, so re-saving the same id is safe.
        """
        encoded = encode_checkpoint_value(checkpoint.to_dict())
        async with self._sm() as session:
            await session.merge(
                WorkflowCheckpointRow(
                    checkpoint_id=checkpoint.checkpoint_id,
                    workflow_name=checkpoint.workflow_name,
                    graph_signature_hash=checkpoint.graph_signature_hash,
                    previous_checkpoint_id=checkpoint.previous_checkpoint_id,
                    chat_conversation_id=self._chat_conversation_id,
                    iteration_count=checkpoint.iteration_count,
                    version=checkpoint.version,
                    timestamp=checkpoint.timestamp,
                    data=encoded,
                )
            )
            await session.commit()
        return checkpoint.checkpoint_id

    async def load(self, checkpoint_id: str) -> WorkflowCheckpoint:
        """Fetch and decode one checkpoint by id (called by the framework on
        resume). Raises `WorkflowCheckpointException` if missing — the caller
        treats that as "needs to restart this conversation"."""
        async with self._sm() as session:
            row = await session.get(WorkflowCheckpointRow, checkpoint_id)
            if row is None:
                raise WorkflowCheckpointException(f"No checkpoint found with ID {checkpoint_id}")
            data = row.data
        return self._decode(data)

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        async with self._sm() as session:
            rows = (
                await session.execute(
                    select(WorkflowCheckpointRow).where(
                        WorkflowCheckpointRow.workflow_name == workflow_name
                    )
                )
            ).scalars().all()
        checkpoints: list[WorkflowCheckpoint] = []
        for row in rows:
            try:
                checkpoints.append(self._decode(row.data))
            except Exception as exc:  # noqa: BLE001 - skip a corrupt row, don't fail the listing
                logger.warning("Failed to decode checkpoint %s: %s", row.checkpoint_id, exc)
        return checkpoints

    async def delete(self, checkpoint_id: str) -> bool:
        async with self._sm() as session:
            result = await session.execute(
                sa_delete(WorkflowCheckpointRow).where(
                    WorkflowCheckpointRow.checkpoint_id == checkpoint_id
                )
            )
            await session.commit()
            return bool(getattr(result, "rowcount", 0))

    async def mark_floor(self, *, workflow_name: str, checkpoint_id: str | None) -> None:
        """Mark `checkpoint_id` as the rollback floor for this workflow, clearing any
        previous floor. Called when a turn closes cleanly (no pending approval)."""
        if not checkpoint_id:
            return
        async with self._sm() as session:
            await session.execute(
                sa_update(WorkflowCheckpointRow)
                .where(
                    WorkflowCheckpointRow.workflow_name == workflow_name,
                    WorkflowCheckpointRow.is_floor.is_(True),
                )
                .values(is_floor=False)
            )
            await session.execute(
                sa_update(WorkflowCheckpointRow)
                .where(WorkflowCheckpointRow.checkpoint_id == checkpoint_id)
                .values(is_floor=True)
            )
            await session.commit()

    async def prune(self, *, workflow_name: str, latest_checkpoint_id: str | None) -> int:
        """Keep only the rollback floor (`is_floor`) and the latest checkpoint for this
        workflow; delete the rest. Returns the number of rows deleted.

        Safe because each checkpoint is a full self-contained snapshot and `load`
        reads one row (never the chain), so deleting others can't corrupt a kept one.
        """
        async with self._sm() as session:
            stmt = sa_delete(WorkflowCheckpointRow).where(
                WorkflowCheckpointRow.workflow_name == workflow_name,
                WorkflowCheckpointRow.is_floor.is_(False),
            )
            if latest_checkpoint_id:  # never delete the resume target
                stmt = stmt.where(
                    WorkflowCheckpointRow.checkpoint_id != latest_checkpoint_id
                )
            result = await session.execute(stmt)
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0)

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        """The most-recent checkpoint for this conversation's workflow_name.
        `manager.run_turn` calls this after a run to find the new pause-point id
        to stamp onto the next `aistudiobot_agent_human_input` row."""
        async with self._sm() as session:
            row = (
                await session.execute(
                    select(WorkflowCheckpointRow)
                    .where(WorkflowCheckpointRow.workflow_name == workflow_name)
                    .order_by(
                        WorkflowCheckpointRow.timestamp.desc(),
                        WorkflowCheckpointRow.created_at.desc(),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            data = row.data
        return self._decode(data)

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        async with self._sm() as session:
            rows = (
                await session.execute(
                    select(WorkflowCheckpointRow.checkpoint_id).where(
                        WorkflowCheckpointRow.workflow_name == workflow_name
                    )
                )
            ).scalars().all()
        return list(rows)
