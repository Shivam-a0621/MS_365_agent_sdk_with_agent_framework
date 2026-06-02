"""FastAPI application entry point."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

app = FastAPI(
    title=os.getenv("APP_NAME", "agent-fastapi"),
    version="0.1.0",
)

# Teams channel: POST /api/messages (Microsoft 365 Agents SDK echo bot).
from teams_channel import router as teams_router  # noqa: E402

app.include_router(teams_router)

# Long-running task completion callback: POST /api/task-callback (shared-secret auth).
from task_callback import router as task_router  # noqa: E402

app.include_router(task_router)

# Register task launchers (the demo_sleep launcher becomes the default — see services/launchers.py).
import services.launchers  # noqa: E402,F401


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "app": app.title}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "3978")),
        reload=os.getenv("DEBUG", "false").lower() == "true",
    )
