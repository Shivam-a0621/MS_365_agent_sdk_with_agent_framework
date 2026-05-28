"""Clear per-conversation framework state so the next inbound starts fresh.

Run between dev tests, or as a "start over" knob in prod for one Teams conversation.

Usage:
    uv run python -m scripts.reset_session <agent_conversation_id>
    uv run python -m scripts.reset_session --all              # nuke every conversation's framework state

Removes:
  - aistudiobot_agent_checkpoint rows for the conversation's workflow_name
  - Open aistudiobot_agent_human_input rows for the conversation

Keeps:
  - aistudiobot_chathistory (channel transcript stays)
  - aistudiobot_agent_actions / aistudiobot_agent_llm_call (audit log stays)
  - aistudiobot_store (SDK state)
"""

from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from db.session import SessionLocal  # noqa: E402
from workflows.handoff import workflow_name  # noqa: E402


async def reset(ref: str | None) -> None:
    """Clear framework state. `ref=None` means nuke everything (dev only)."""
    async with SessionLocal() as session:
        if ref is None:
            ck = await session.execute(text("DELETE FROM aistudiobot_agent_checkpoint"))
            hi = await session.execute(text("DELETE FROM aistudiobot_agent_human_input"))
            await session.commit()
            print(f"reset --all done. checkpoints deleted={ck.rowcount}, human_inputs deleted={hi.rowcount}")
            return

        row = (
            await session.execute(
                text(
                    "SELECT c.id FROM aistudiobot_chatconversation c "
                    "JOIN aistudiobot_chatsession ss ON ss.id = c.chat_session_id "
                    "WHERE ss.agent_conversation_id = :r "
                    "ORDER BY c.id DESC LIMIT 1"
                ),
                {"r": ref},
            )
        ).first()
        if row is None:
            print(f"No conversation found for agent_conversation_id={ref!r}")
            return
        conv_id = row[0]
        ck = await session.execute(
            text("DELETE FROM aistudiobot_agent_checkpoint WHERE workflow_name = :n"),
            {"n": workflow_name(conv_id)},
        )
        hi = await session.execute(
            text("DELETE FROM aistudiobot_agent_human_input WHERE chat_conversation_id = :i"),
            {"i": conv_id},
        )
        await session.commit()
        print(
            f"reset done for conv={conv_id} (ref={ref!r}). "
            f"checkpoints deleted={ck.rowcount}, human_inputs deleted={hi.rowcount}"
        )


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    arg = sys.argv[1]
    asyncio.run(reset(None if arg == "--all" else arg))


if __name__ == "__main__":
    main()
