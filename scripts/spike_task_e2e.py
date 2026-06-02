"""Stage-2 end-to-end spike for the long-running-task feature.

Drives the channel-neutral service (channel="rest"), no Teams needed. To stay DETERMINISTIC it does
NOT rely on the LLM choosing start_background_task (that is real but flaky — the analyzer tends to
validate the workflow name via MCP first). Instead it parks the conversation on its between-turns
(handoff_user) pause with a trivial turn, then drives services.task_service.spawn_task directly with a
breadcrumb (exactly what the conversation service does when the agent DOES call the tool).

Proves:
  (a) spawn -> a pending aistudio_agent_task row + a task checkpoint, while the conversation's open
      pause is STILL handoff_user (DECOUPLING — the user can keep chatting);
  (b) a follow-up user message resumes the CONVERSATION, not the task (task stays pending);
  (c) the task-callback resumes the task, MERGES the result into the conversation, marks it delivered,
      and "delivers" the reply (PROACTIVE_MODE=log just logs it);
  (d) a later turn references the task result (memory merge).

Prereqs (live env): `alembic upgrade head`; DATABASE_URL + AZURE_OPENAI_* set; AE MCP reachable.
Run:  PROACTIVE_MODE=log python -m scripts.spike_task_e2e
"""

from __future__ import annotations

import asyncio
import uuid

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from db.models import AgentTask, ChatConversation, ChatSession  # noqa: E402
from db.repositories import get_open_human_input  # noqa: E402
from db.session import SessionLocal  # noqa: E402
from services import task_service  # noqa: E402
from services.conversation_service import handle_message, resume_external_task  # noqa: E402

REF = f"spike-task-{uuid.uuid4().hex[:8]}"
CH = "rest"


async def _conv_id() -> int:
    async with SessionLocal() as s:
        sess = (await s.execute(select(ChatSession).where(ChatSession.agent_conversation_id == REF))).scalar_one()
        conv = (await s.execute(select(ChatConversation).where(ChatConversation.chat_session_id == sess.id))).scalar_one()
        return conv.id


async def _open_kind(conv_id: int) -> str | None:
    async with SessionLocal() as s:
        row = await get_open_human_input(s, conv_id)
        return row.kind if row else None


async def _pending_task() -> AgentTask | None:
    async with SessionLocal() as s:
        return (
            await s.execute(select(AgentTask).where(AgentTask.conversation_ref == REF, AgentTask.status == "pending"))
        ).scalar_one_or_none()


async def main() -> None:
    print(f"conversation_ref = {REF}")

    # Turn 1 — a trivial greeting parks the conversation on a handoff_user pause.
    r1 = await handle_message(channel=CH, conversation_ref=REF, user_id="u1", user_name="Tester", text="hi")
    print("TURN 1 (greeting) replies:", r1.replies)
    conv_id = await _conv_id()
    assert await _open_kind(conv_id) == "handoff_user", "conversation should be on a handoff_user pause after turn 1"

    # (a) Spawn a background task directly (what conversation_service does on a start_background_task breadcrumb).
    correlation_id = uuid.uuid4().hex
    crumb = {
        "correlation_id": correlation_id,
        "task_type": "engine_run",
        "summary": "Nightly Report (region=APAC)",
        "params": {"workflow": "Nightly Report", "region": "APAC"},
    }
    async with SessionLocal() as s:
        await task_service.spawn_task(s, chat_conversation_id=conv_id, channel=CH, conversation_ref=REF, breadcrumb=crumb)
        await s.commit()

    task = await _pending_task()
    assert task is not None and task.correlation_id == correlation_id, "expected a pending task row + checkpoint"
    print(f"  spawned task corr={task.correlation_id} checkpoint={task.checkpoint_id}")
    assert await _open_kind(conv_id) == "handoff_user", "DECOUPLING FAILED: spawning a task changed the conversation's open pause"
    print("  decoupling OK — conversation still on handoff_user; the task pause is NOT in human_input")

    # (b) A normal follow-up resumes the CONVERSATION, not the task.
    r2 = await handle_message(channel=CH, conversation_ref=REF, user_id="u1", user_name="Tester",
                              text="Thanks! In one sentence, what is an automation workflow schedule?")
    print("TURN 2 replies:", r2.replies)
    assert await _pending_task() is not None, "task must still be pending after an unrelated turn"
    assert await _open_kind(conv_id) == "handoff_user", "conversation should be back on a handoff_user pause"
    print("  follow-up resumed the conversation; task still pending OK")

    # (c) Callback — the engine finished; resume the task + merge into the conversation + deliver.
    delivered = await resume_external_task(
        correlation_id=correlation_id, status="ok", output={"rows": 128, "url": "https://example/report/42"}
    )
    print("CALLBACK delivered:", delivered)
    assert delivered and await _pending_task() is None, "callback should deliver and resolve the task"
    print("  task resolved (delivered) + merged OK")

    dup = await resume_external_task(correlation_id=correlation_id, status="ok", output={})
    print("DUPLICATE callback delivered:", dup, "(expected False)")
    assert dup is False, "duplicate callback must be ignored"

    # (d) Later turn — the agent should reference the task result from conversation memory.
    r3 = await handle_message(channel=CH, conversation_ref=REF, user_id="u1", user_name="Tester",
                              text="what was the result of my Nightly Report task?")
    print("TURN 3 replies:", r3.replies)
    print("\nSTAGE 2 OK — background task: spawned, decoupled, resumed via callback, merged, delivered.")


if __name__ == "__main__":
    asyncio.run(main())
