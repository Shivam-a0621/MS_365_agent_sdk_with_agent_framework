"""Simulate the external engine finishing a long-running task: POST a completion to the running app's
/api/task-callback. Use this to test the feature via a NORMAL chat query — start a task by chatting,
then run this to deliver the result.

Usage:
  python -m scripts.complete_task                 # completes the LATEST pending task with demo data
  python -m scripts.complete_task <correlation_id>
  python -m scripts.complete_task <correlation_id> '{"rows": 7, "note": "hi"}'   # custom output JSON
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from db.models import AgentTask  # noqa: E402
from db.session import SessionLocal  # noqa: E402

CALLBACK_URL = os.getenv("TASK_CALLBACK_URL", "http://localhost:3978/api/task-callback")


async def _latest_pending() -> AgentTask | None:
    async with SessionLocal() as s:
        return (
            await s.execute(select(AgentTask).where(AgentTask.status == "pending").order_by(AgentTask.id.desc()).limit(1))
        ).scalar_one_or_none()


def main() -> None:
    corr = sys.argv[1] if len(sys.argv) > 1 else None
    output = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {"rows": 128, "url": "https://example/report/42"}

    if not corr:
        task = asyncio.run(_latest_pending())
        if task is None:
            print("No pending task found. Start one by chatting first.")
            return
        corr = task.correlation_id
        print(f"Completing latest pending task: {corr}  ({task.summary})")

    headers = {"Content-Type": "application/json"}
    if os.getenv("TASK_CALLBACK_SECRET"):
        headers["X-Task-Secret"] = os.environ["TASK_CALLBACK_SECRET"]
    body = json.dumps({"correlation_id": corr, "status": "ok", "output": output}).encode()
    req = urllib.request.Request(CALLBACK_URL, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        print(f"POST {CALLBACK_URL} -> {resp.status} {resp.read().decode()}")


if __name__ == "__main__":
    main()
