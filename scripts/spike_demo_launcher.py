"""Verify the REAL demo_sleep launcher: spawn a task, let the launcher's detached job sleep and call
back on its own, then confirm the conversation learned the result. Uses a short sleep by default.

Run:  PROACTIVE_MODE=log DEMO_SECONDS=5 python -m scripts.spike_demo_launcher
(Set DEMO_SECONDS=120 to exercise the real 2-minute job.)
"""

from __future__ import annotations

import asyncio
import os
import uuid

from dotenv import load_dotenv

load_dotenv()

import services.launchers  # noqa: E402,F401  - registers demo_sleep as the default launcher
from sqlalchemy import select  # noqa: E402

from db.models import AgentTask, ChatConversation, ChatSession  # noqa: E402
from db.session import SessionLocal  # noqa: E402
from services import task_service  # noqa: E402
from services.conversation_service import handle_message  # noqa: E402

REF = f"demo-{uuid.uuid4().hex[:8]}"
CH = "rest"
SECONDS = int(os.getenv("DEMO_SECONDS", "5"))


async def _conv_id() -> int:
    async with SessionLocal() as s:
        sess = (await s.execute(select(ChatSession).where(ChatSession.agent_conversation_id == REF))).scalar_one()
        return (await s.execute(select(ChatConversation).where(ChatConversation.chat_session_id == sess.id))).scalar_one().id


async def _status(corr: str) -> str | None:
    async with SessionLocal() as s:
        t = (await s.execute(select(AgentTask).where(AgentTask.correlation_id == corr))).scalar_one_or_none()
        return t.status if t else None


async def main() -> None:
    print(f"conversation_ref={REF}  sleep={SECONDS}s")
    await handle_message(channel=CH, conversation_ref=REF, user_id="u", user_name="T", text="hi")
    conv_id = await _conv_id()

    corr = uuid.uuid4().hex
    crumb = {"correlation_id": corr, "task_type": "demo_sleep", "summary": "Quick demo report",
             "params": {"seconds": SECONDS, "label": "Quick demo report"}}
    async with SessionLocal() as s:
        await task_service.spawn_task(s, chat_conversation_id=conv_id, channel=CH, conversation_ref=REF, breadcrumb=crumb)
        await s.commit()
    print("spawned; status =", await _status(corr), "(expect pending)")

    # wait for the detached demo job to sleep, call back, resume, merge (an LLM turn) + deliver.
    await asyncio.sleep(SECONDS + 12)
    print("after wait; status =", await _status(corr), "(expect delivered)")

    r = await handle_message(channel=CH, conversation_ref=REF, user_id="u", user_name="T",
                             text="what did my demo report task return?")
    print("later-turn reply:", r.replies)
    assert await _status(corr) == "delivered", "demo launcher did not deliver"
    print("DEMO LAUNCHER OK — real job slept, called back, merged, delivered.")


if __name__ == "__main__":
    asyncio.run(main())
