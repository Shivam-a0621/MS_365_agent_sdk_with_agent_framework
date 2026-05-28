"""De-risk spike for durable human-in-the-loop (each step is a SEPARATE process,
sharing ONLY Postgres — simulating restart / different worker).

State between steps is passed via /tmp/spike_state.json (conv_id, latest checkpoint_id,
pending request_id + kind + approval primitives).

  pause          -> fresh run until first pause; persist state.
  say "<text>"   -> resume a pending HandoffAgentUserRequest with text; persist state.
  approve        -> resume a pending function_approval, reconstructing the response from
                    stored primitives (no live event); persist state.
  deny           -> resume a pending function_approval with approved=False (expect it to raise).
  show           -> print persisted state.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from agent_framework import Content, Message

from db.checkpoint_store import PostgresCheckpointStorage
from workflows.handoff import build_handoff_workflow, workflow_name
from workflows.outcome import extract_replies, is_function_approval

CONV_ID = 999_001
STATE_FILE = Path("/tmp/spike_state.json")
PROMPT = (
    "Read the log file /var/log/db.log, then create an IT support ticket for the "
    "error you find. Title 'DB timeout', department IT."
)


def _save_state(d: dict) -> None:
    STATE_FILE.write_text(json.dumps(d, indent=2))


def _load_state() -> dict:
    return json.loads(STATE_FILE.read_text())


def _pending(result) -> dict | None:
    """Return {request_id, kind, is_approval, primitives?} for the first pending request."""
    events = result.get_request_info_events()
    if not events:
        return None
    e = events[0]
    out = {"request_id": e.request_id, "kind": type(e.data).__name__, "is_approval": is_function_approval(e)}
    if is_function_approval(e):
        fc = e.data.function_call
        out["primitives"] = {
            "request_id": e.request_id,
            "call_id": getattr(fc, "call_id", None) or getattr(fc, "id", None),
            "function_name": getattr(fc, "name", None),
            "arguments": getattr(fc, "arguments", None),
        }
    return out


def _describe(result) -> None:
    print("final_state:", result.get_final_state())
    for r in extract_replies(result):
        print("reply:", r)
    for e in result.get_request_info_events():
        print(f"  pending: id={e.request_id} kind={type(e.data).__name__} approval={is_function_approval(e)}")


async def _ensure_conv() -> None:
    """Clean prior test rows and insert the aistudiobot_bot -> ... -> aistudiobot_chatconversation
    chain (CONV_ID is the aistudiobot_chatconversation id that aistudiobot_agent_checkpoint and
    aistudiobot_agent_human_input soft-reference; no FK)."""
    from sqlalchemy import text

    from db.session import SessionLocal

    async with SessionLocal() as s:
        for stmt, params in [
            ("DELETE FROM aistudiobot_agent_checkpoint WHERE workflow_name = :n", {"n": workflow_name(CONV_ID)}),
            ("DELETE FROM aistudiobot_agent_human_input WHERE chat_conversation_id = :i", {"i": CONV_ID}),
            ("DELETE FROM aistudiobot_chathistory WHERE chat_conversation_id = :i", {"i": CONV_ID}),
            ("DELETE FROM aistudiobot_chatconversation WHERE id = :i", {"i": CONV_ID}),
            ("DELETE FROM aistudiobot_chatsession WHERE id = 1", {}),
            ("DELETE FROM aistudiobot_botchannelmapping WHERE id = 1", {}),
            ("DELETE FROM aistudiobot_bot WHERE id = 1", {}),
            ("DELETE FROM aistudiobot_channel WHERE id = 1", {}),
            ("INSERT INTO aistudiobot_bot (id, name, bot_id) VALUES (1, 'spike-bot', 'spike')", {}),
            ("INSERT INTO aistudiobot_channel (id, name) VALUES (1, 'spike')", {}),
            ("INSERT INTO aistudiobot_botchannelmapping (id, bot_id, channel_id) VALUES (1, 1, 1)", {}),
            (
                "INSERT INTO aistudiobot_chatsession (id, user_id, username, start_time, "
                "agent_conversation_id, agent_channel_mapping_id) "
                "VALUES (1, 'u1', 'spike user', now(), 'spike-conv', 1)",
                {},
            ),
            (
                "INSERT INTO aistudiobot_chatconversation (id, start_time, intent, is_ka, chat_session_id, skill_name) "
                "VALUES (:i, now(), '', false, 1, 'handoff')",
                {"i": CONV_ID},
            ),
        ]:
            await s.execute(text(stmt), params)
        await s.commit()


async def _persist_after(result, storage) -> None:
    latest = await storage.get_latest(workflow_name=workflow_name(CONV_ID))
    pending = _pending(result)
    state = {"conv_id": CONV_ID, "checkpoint_id": latest.checkpoint_id if latest else None, "pending": pending}
    _save_state(state)
    print("\npersisted state:", json.dumps(state.get("pending"), indent=2), "checkpoint:", state["checkpoint_id"])


async def pause() -> None:
    await _ensure_conv()
    storage = PostgresCheckpointStorage(chat_conversation_id=CONV_ID)
    wf = build_handoff_workflow(conversation_id=CONV_ID, checkpoint_storage=storage)
    print("=== fresh run ===")
    result = await wf.run(PROMPT, checkpoint_storage=storage)
    _describe(result)
    await _persist_after(result, storage)


async def say(user_text: str) -> None:
    st = _load_state()
    storage = PostgresCheckpointStorage(chat_conversation_id=st["conv_id"])
    wf = build_handoff_workflow(conversation_id=st["conv_id"], checkpoint_storage=storage)
    rid = st["pending"]["request_id"]
    print(f"=== resume (say) in fresh process; checkpoint={st['checkpoint_id']} request={rid} ===")
    result = await wf.run(
        responses={rid: [Message("user", [user_text])]},
        checkpoint_id=st["checkpoint_id"],
        checkpoint_storage=storage,
    )
    _describe(result)
    await _persist_after(result, storage)


async def _resume_approval(approved: bool) -> None:
    st = _load_state()
    p = st["pending"]
    if not p or not p.get("is_approval"):
        print("Pending request is not a function approval; use 'say' instead. Pending:", p)
        return
    prim = p["primitives"]
    storage = PostgresCheckpointStorage(chat_conversation_id=st["conv_id"])
    wf = build_handoff_workflow(conversation_id=st["conv_id"], checkpoint_storage=storage)
    function_call = Content.from_function_call(
        call_id=prim["call_id"], name=prim["function_name"], arguments=prim["arguments"]
    )
    response = Content.from_function_approval_response(
        approved=approved, id=prim["request_id"], function_call=function_call
    )
    print(f"=== resume (approved={approved}) in fresh process; checkpoint={st['checkpoint_id']} ===")
    try:
        result = await wf.run(
            responses={prim["request_id"]: response},
            checkpoint_id=st["checkpoint_id"],
            checkpoint_storage=storage,
        )
        _describe(result)
        await _persist_after(result, storage)
    except Exception as exc:  # noqa: BLE001
        print(f"RESUME RAISED: {type(exc).__name__}: {exc}")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "show"
    if mode == "pause":
        asyncio.run(pause())
    elif mode == "say":
        asyncio.run(say(sys.argv[2]))
    elif mode == "approve":
        asyncio.run(_resume_approval(True))
    elif mode == "deny":
        asyncio.run(_resume_approval(False))
    elif mode == "show":
        print(STATE_FILE.read_text() if STATE_FILE.exists() else "(no state)")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
