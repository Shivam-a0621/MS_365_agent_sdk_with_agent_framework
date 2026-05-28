"""Postgres-backed implementation of the MS 365 Agents SDK ``Storage`` interface.

Replaces the in-memory ``MemoryStorage`` so the SDK's own conversation/user state
(``TurnState``) survives restarts and is shared across workers. Mirrors
``MemoryStorage`` exactly — ``store_item_to_json`` / ``from_json_to_store_item``,
no etag — but persists into the ``agent_store`` key-value table on the fixed/SDK
side of the schema. Redis would be a sibling class implementing the same Protocol.
"""

from __future__ import annotations

from typing import Any

from microsoft_agents.hosting.core.storage import Storage
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import StoreRow
from db.session import SessionLocal


class PostgresStorage(Storage):
    """SDK Storage over Postgres (the ``agent_store`` table)."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession] = SessionLocal) -> None:
        self._sm = sessionmaker

    async def read(
        self, keys: list[str], *, target_cls: Any = None, **kwargs
    ) -> dict[str, Any]:
        if not keys:
            raise ValueError("Storage.read(): Keys are required when reading.")
        if not target_cls:
            raise ValueError("Storage.read(): target_cls cannot be None.")
        async with self._sm() as session:
            rows = (
                await session.execute(select(StoreRow).where(StoreRow.key.in_(keys)))
            ).scalars().all()
        # missing keys are simply omitted (same contract as MemoryStorage)
        return {row.key: target_cls.from_json_to_store_item(row.data) for row in rows}

    async def write(self, changes: dict[str, Any]) -> None:
        if not changes:
            raise ValueError("Storage.write(): Changes are required when writing.")
        async with self._sm() as session:
            for key, item in changes.items():
                # merge = upsert by primary key (key); last write wins, no etag —
                # same as MemoryStorage. A conversation's turns are serialized by the
                # per-conversation Redis lock, so concurrent writes to one key are rare.
                await session.merge(StoreRow(key=key, data=item.store_item_to_json()))
            await session.commit()

    async def delete(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("Storage.delete(): Keys are required when deleting.")
        async with self._sm() as session:
            await session.execute(sa_delete(StoreRow).where(StoreRow.key.in_(keys)))
            await session.commit()
