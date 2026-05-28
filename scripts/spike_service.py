"""End-to-end verification of the runtime via ConversationService (channel='rest',
no Teams tunnel). Drives a full greet -> log read -> ticket -> approval -> approve
flow, then dumps the rows written to every table.

Run:  python -m scripts.spike_service
"""

from __future__ import annotations

import asyncio
import time

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from db.session import SessionLocal  # noqa: E402
from services.conversation_service import handle_message  # noqa: E402

REF = f"svc-{int(time.time())}"


def _show(label, r) -> None:
    print(
        f"\n== {label} ==\n  replies={r.replies}\n  approvals="
        f"{[(a.function_name, a.request_id) for a in r.approvals]}\n  prompts={r.prompts}\n  error={r.error}"
    )


async def drive() -> None:
    r = await handle_message(
        channel="rest", conversation_ref=REF, user_id="u1", user_name="Tester",
        text="Read the log file /var/log/db.log, then raise an IT support ticket for the "
             "error you find. Title 'DB timeout', department IT.",
    )
    _show("turn 1 (user)", r)
    for i in range(5):
        if r.approvals:
            ap = r.approvals[0]
            r = await handle_message(
                channel="rest", conversation_ref=REF, text="approve",
                value={"approval_id": ap.request_id, "approved": True},
            )
            _show("APPROVE", r)
            break
        r = await handle_message(
            channel="rest", conversation_ref=REF,
            text="Yes, actually call the create_ticket tool now.",
        )
        _show(f"turn {i + 2} (nudge)", r)
    await dump_db()


async def dump_db() -> None:
    async with SessionLocal() as s:
        conv = (
            await s.execute(
                text(
                    "select c.id from aistudiobot_chatconversation c "
                    "join aistudiobot_chatsession ss on c.chat_session_id = ss.id "
                    "where ss.agent_conversation_id = :r"
                ),
                {"r": REF},
            )
        ).scalar()
        print(f"\n################ DB (conversation {conv}) ################")
        for tbl in (
            "aistudiobot_chathistory",
            "aistudiobot_agent_actions",
            "aistudiobot_agent_llm_call",
            "aistudiobot_agent_human_input",
            "aistudiobot_agent_checkpoint",
        ):
            n = (
                await s.execute(text(f"select count(*) from {tbl} where chat_conversation_id=:c"), {"c": conv})
            ).scalar()
            print(f"  {tbl}: {n}")

        print("\n  transcript (aistudiobot_chathistory):")
        for row in await s.execute(
            text(
                "select seq, role, agent_name, left(text, 70) "
                "from aistudiobot_chathistory where chat_conversation_id=:c order by seq"
            ),
            {"c": conv},
        ):
            print("   ", tuple(row))
        print("\n  actions (aistudiobot_agent_actions):")
        for row in await s.execute(
            text(
                "select seq, user_message_id, event_type, agent_name, tool_name, tool_kind, status "
                "from aistudiobot_agent_actions where chat_conversation_id=:c order by seq"
            ),
            {"c": conv},
        ):
            print("   ", tuple(row))
        print("\n  aistudiobot_agent_human_input:")
        for row in await s.execute(
            text(
                "select kind, function_name, status from aistudiobot_agent_human_input "
                "where chat_conversation_id=:c order by id"
            ),
            {"c": conv},
        ):
            print("   ", tuple(row))
        print("\n  aistudiobot_agent_llm_call:")
        for row in await s.execute(
            text(
                "select agent_name, model, prompt_tokens, completion_tokens, total_tokens "
                "from aistudiobot_agent_llm_call where chat_conversation_id=:c order by id"
            ),
            {"c": conv},
        ):
            print("   ", tuple(row))


if __name__ == "__main__":
    asyncio.run(drive())
