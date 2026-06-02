"""POST /api/task-callback — the endpoint an external job hits when a long-running task finishes.

Auth is a shared secret (NOT the Bot Connector JWT — the engine is not a Bot caller). The body carries
``{correlation_id, status, output?, error?}``; we resume the task's checkpoint, merge the result into
the conversation, and proactively notify the user. Returns 200 on handled cases (idempotent — duplicate
/ late / unknown callbacks are no-ops) so the engine does not retry-storm.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from services.conversation_service import resume_external_task

logger = logging.getLogger("app.task_callback")
router = APIRouter(tags=["task"])


@router.post("/api/task-callback")
async def task_callback(
    request: Request, x_task_secret: str | None = Header(default=None)
) -> JSONResponse:
    secret = os.getenv("TASK_CALLBACK_SECRET")
    if secret and x_task_secret != secret:
        raise HTTPException(status_code=401, detail="invalid task callback secret")

    body: dict[str, Any] = await request.json()
    correlation_id = body.get("correlation_id")
    if not correlation_id:
        return JSONResponse({"error": "missing correlation_id"}, status_code=400)

    try:
        delivered = await resume_external_task(
            correlation_id=correlation_id,
            status=body.get("status", "ok"),
            output=body.get("output"),
            error=body.get("error"),
        )
    except Exception:  # noqa: BLE001 - never 500 a callback in a way that triggers retry storms
        logger.exception("task-callback failed for %s", correlation_id)
        return JSONResponse({"status": "error", "correlation_id": correlation_id}, status_code=200)

    return JSONResponse({"status": "delivered" if delivered else "ignored", "correlation_id": correlation_id})
