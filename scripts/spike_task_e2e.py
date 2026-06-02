"""End-to-end spike for the (simplified) long-running-task feature. No Teams needed.

The flow it exercises:
  1. a turn parks the conversation on its normal between-turns (handoff_user) checkpoint;
  2. a background task is recorded for this conversation (what the agent's start_background_task does);
  3. the user can keep chatting (the task stays pending);
  4. after the job "runs" (a short sleep here), the completion callback (resume_external_task) loads the
     conversation's checkpoint, the SAME agent reports the result, and it's delivered (PROACTIVE_MODE=log
     prints it); the task row flips to delivered; a duplicate callback is ignored;
  5. a later turn references the result (it was merged into the conversation).

Prereqs: `alembic upgrade head`; DATABASE_URL + AZURE_OPENAI_* set; AE MCP reachable.
Run:  PROACTIVE_MODE=log TASK_SLEEP=3 python -m scripts.spike_task_e2e
(Set TASK_SLEEP=120 to feel the real 2-minute wait.)
"""

from __future__ import annotations

import asyncio
import os
import uuid

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from db import repositories as repo  # noqa: E402
from db.models import AgentTask, ChatConversation, ChatSession  # noqa: E402
from db.session import SessionLocal  # noqa: E402
from services.conversation_service import handle_message, resume_external_task  # noqa: E402

REF = f"spike-task-{uuid.uuid4().hex[:8]}"
CH = "rest"
SLEEP = int(os.getenv("TASK_SLEEP", "3"))


async def _conv_id() -> int:
    async with SessionLocal() as s:
        sess = (await s.execute(select(ChatSession).where(ChatSession.agent_conversation_id == REF))).scalar_one()
        return (await s.execute(select(ChatConversation).where(ChatConversation.chat_session_id == sess.id))).scalar_one().id


async def _status(corr: str) -> str | None:
    async with SessionLocal() as s:
        t = (await s.execute(select(AgentTask).where(AgentTask.correlation_id == corr))).scalar_one_or_none()
        return t.status if t else None


async def main() -> None:
    print(f"conversation_ref={REF}  sleep={SLEEP}s")

    # 1. greeting parks the conversation on a handoff_user checkpoint.
    await handle_message(channel=CH, conversation_ref=REF, user_id="u", user_name="T", text="hi")
    conv_id = await _conv_id()

    # 2. record a background task for this conversation (what start_background_task's breadcrumb does).
    corr = uuid.uuid4().hex
    async with SessionLocal() as s:
        await repo.create_agent_task(s, correlation_id=corr, chat_conversation_id=conv_id, channel=CH,
                                     conversation_ref=REF, summary="Nightly Report (region=APAC)")
        await s.commit()
    print("recorded; status =", await _status(corr), "(expect pending)")

    # 3. user keeps chatting — the task stays pending.
    r2 = await handle_message(channel=CH, conversation_ref=REF, user_id="u", user_name="T",
                              text="Thanks! In one sentence, what is an automation workflow schedule?")
    print("mid-wait turn reply:", r2.replies)
    assert await _status(corr) == "pending", "task should still be pending while the user chats"

    # 4. the job runs for a while, then calls back.
    await asyncio.sleep(SLEEP)
    delivered = await resume_external_task(correlation_id=corr, status="ok",
                                           output={"rows": 128, "url": "https://example/report/42"})
    print("callback delivered:", delivered, "| status =", await _status(corr), "(expect delivered)")
    assert delivered and await _status(corr) == "delivered"

    dup = await resume_external_task(correlation_id=corr, status="ok", output={})
    print("duplicate callback delivered:", dup, "(expect False)")
    assert dup is False

    # 5. a later turn references the merged result.
    r3 = await handle_message(channel=CH, conversation_ref=REF, user_id="u", user_name="T",
                              text="what did my Nightly Report task return?")
    print("later-turn reply:", r3.replies)
    print("\nOK — recorded, waited (user kept chatting), callback resumed the conversation, merged, delivered.")


if __name__ == "__main__":
    asyncio.run(main())
