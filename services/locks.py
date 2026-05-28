"""Per-conversation serialization via a Redis distributed lock.

Safe across multiple uvicorn workers / processes: only one worker holds a given
conversation's lock at a time, so concurrent turns (rapid messages, card double-tap)
can't double-resume the same checkpoint. Same ``conversation_lock`` interface as
before, so callers are unchanged.

Behaviour:
  * blocks up to ``_BLOCKING_TIMEOUT`` for the current holder to finish (turns are
    serialized, not rejected);
  * the lock auto-expires after ``_TTL`` so a crashed worker can't deadlock a
    conversation forever (set well above the longest expected turn);
  * if Redis is unreachable, fails OPEN (logs a warning and proceeds) so a Redis
    blip never takes the bot down — at the cost of the cross-worker guarantee during
    the outage.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis
from redis.exceptions import RedisError

logger = logging.getLogger("app.locks")

_TTL = 180  # seconds the lock is held before auto-expiry (must exceed max turn time)
_BLOCKING_TIMEOUT = 120  # seconds a waiting turn will block for the holder to finish

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    return _client


@asynccontextmanager
async def conversation_lock(conversation_key: str) -> AsyncIterator[None]:
    lock = _redis().lock(
        f"lock:conv:{conversation_key}",
        timeout=_TTL,
        blocking=True,
        blocking_timeout=_BLOCKING_TIMEOUT,
    )
    acquired = False
    # State machine:
    #   acquired starts False; we always yield exactly once.
    #   - lock.acquire() returns True  -> we hold the lock; release in `finally`.
    #   - RedisError                   -> Redis unreachable; fail open (log + yield).
    #   - blocking_timeout exceeded    -> warn + yield anyway (so a stuck lock
    #                                     can't bring the bot down for that user).
    try:
        try:
            acquired = await lock.acquire()
        except RedisError as exc:
            logger.warning(
                "Redis unavailable for conversation lock (%s); proceeding without it.", exc
            )
            yield
            return
        if not acquired:
            # Held longer than the blocking timeout — proceed but warn (very rare).
            logger.warning(
                "Could not acquire conversation lock %s within %ss; proceeding.",
                conversation_key,
                _BLOCKING_TIMEOUT,
            )
        yield
    finally:
        if acquired:
            try:
                await lock.release()
            except Exception:  # noqa: BLE001 - lock may have expired; release is best-effort
                pass
