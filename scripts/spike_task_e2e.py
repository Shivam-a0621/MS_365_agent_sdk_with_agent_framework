"""End-to-end spike for the long-running-task feature — fully automatic, no HTTP callback, no manual step.

Mirrors what the live app does: record a task, then fire its in-process job; the job does the work
(a short sleep here) and resumes THIS conversation directly when done — the same agent reports the
result and it's merged into memory. (The proactive push needs real bot creds, so locally it logs a
failure and we verify via the later "what did it return?" turn instead.)

Prereqs: `alembic upgrade head`; DATABASE_URL + AZURE_OPENAI_* set; AE MCP reachable.
Run:  TASK_SLEEP=2 python -m scripts.spike_task_e2e
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
from services import background_runner  # noqa: E402
from services.conversation_service import handle_message  # noqa: E402

REF = f"spike-task-{uuid.uuid4().hex[:8]}"
CH = "rest"
SLEEP = int(os.getenv("TASK_SLEEP", "2"))


async def _conv_id() -> int:
    async with SessionLocal() as s:
        sess = (await s.execute(select(ChatSession).where(ChatSession.agent_conversation_id == REF))).scalar_one()
        return (await s.execute(select(ChatConversation).where(ChatConversation.chat_session_id == sess.id))).scalar_one().id


async def _status(corr: str) -> str | None:
    async with SessionLocal() as s:
        t = (await s.execute(select(AgentTask).where(AgentTask.correlation_id == corr))).scalar_one_or_none()
        return t.status if t else None


async def main() -> None:
    print(f"conversation_ref={REF}  work={SLEEP}s")

    # 1. greeting parks the conversation on its handoff_user checkpoint.
    await handle_message(channel=CH, conversation_ref=REF, user_id="u", user_name="T", text="hi")
    conv_id = await _conv_id()

    # 2. record + fire a background task (exactly what handle_message does on a start_background_task call).
    corr = uuid.uuid4().hex
    async with SessionLocal() as s:
        await repo.create_agent_task(s, correlation_id=corr, chat_conversation_id=conv_id, channel=CH,
                                     conversation_ref=REF, summary="Nightly Report")
        await s.commit()
    background_runner.fire(corr, "Nightly Report", {"seconds": SLEEP})
    print("fired; status =", await _status(corr), "(expect pending)")

    # 3. user keeps chatting while the job runs.
    r2 = await handle_message(channel=CH, conversation_ref=REF, user_id="u", user_name="T",
                              text="Thanks! In one sentence, what is an automation workflow schedule?")
    print("mid-wait turn reply:", r2.replies)

    # 4. wait for the job to finish + auto-resume + report (no manual callback).
    await asyncio.sleep(SLEEP + 18)
    print("after work; status =", await _status(corr), "(expect delivered)")
    assert await _status(corr) == "delivered", "job did not auto-deliver"

    # 5. later turn references the merged result.
    r3 = await handle_message(channel=CH, conversation_ref=REF, user_id="u", user_name="T",
                              text="what did my Nightly Report task return?")
    print("later-turn reply:", r3.replies)
    print("\nOK — task ran in-process and resumed the conversation automatically (no callback, no manual step).")


if __name__ == "__main__":
    asyncio.run(main())
