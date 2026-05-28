"""Async SQLAlchemy engine + session, built from the AISTUDIOBOT_DB_* env vars."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from dotenv import load_dotenv
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

load_dotenv()


def get_database_url() -> URL:
    """Build the async (asyncpg) SQLAlchemy URL from env.

    We use asyncpg because FastAPI runs on asyncio and the whole app stack is
    async — a sync DB driver would block the event loop. `URL.create` handles
    special characters in the password (e.g. the '@' in 'Admin@123'), so no
    manual URL-encoding is needed.

    The engine below also sets `pool_pre_ping=True` so a stale socket (e.g.
    after a Postgres restart) is detected and replaced silently. With N
    uvicorn workers the total open connections is roughly
    N × (DB_POOL_SIZE + DB_MAX_OVERFLOW); keep that under Postgres'
    `max_connections`.
    """
    return URL.create(
        "postgresql+asyncpg",
        username=os.getenv("AISTUDIOBOT_DB_USER", "postgres"),
        password=os.getenv("AISTUDIOBOT_DB_PASS"),
        host=os.getenv("AISTUDIOBOT_DB_HOST", "localhost"),
        port=int(os.getenv("AISTUDIOBOT_DB_PORT", "5432")),
        database=os.getenv("AISTUDIOBOT_DB_NAME", "agent-fastapi"),
    )


# Each worker process gets its own pool, so keep it modest: with N workers the total
# connections ≈ N * (pool_size + max_overflow). pool_pre_ping avoids stale connections.
engine = create_async_engine(
    get_database_url(),
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session, closing it after the request."""
    async with SessionLocal() as session:
        yield session
