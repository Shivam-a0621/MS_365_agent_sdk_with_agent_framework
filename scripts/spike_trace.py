"""Discovery spike: trace the FULL handoff flow and dump the MAXIMUM data the
framework exposes per turn — so we know exactly what we can persist into
agent_chathistory (and friends).

In-process, InMemoryCheckpointStorage (no DB needed). Drives:
  user msg -> handoff(s) -> tool call (read log) -> create_ticket approval card -> approve

For every WorkflowEvent it prints: type, executor_id, request_id, data type, and for
every Message: role, author_name, and each Content (text / function_call name+args+call_id
/ function_result / function_approval_request).

Run:  python -m scripts.spike_trace
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

load_dotenv()  # load AZURE_OPENAI_* / AE_MCP_URL from .env before building the workflow

from agent_framework import InMemoryCheckpointStorage, Message  # noqa: E402

from workflows.handoff import build_handoff_workflow  # noqa: E402
from workflows.outcome import extract_replies, is_function_approval  # noqa: E402


def _short(v: object, n: int = 240) -> str:
    s = v if isinstance(v, str) else repr(v)
    return s if len(s) <= n else s[:n] + "…"


def dump_content(c: object, prefix: str = "        ") -> None:
    t = getattr(c, "type", type(c).__name__)
    fields: dict[str, str] = {}
    for attr in ("text", "name", "call_id", "id", "arguments", "result", "approved", "exception"):
        val = getattr(c, attr, None)
        if val is not None and val != "":
            fields[attr] = _short(val, 200)
    fc = getattr(c, "function_call", None)
    if fc is not None:
        fields["function_call"] = (
            f"{getattr(fc, 'name', None)}"
            f"(args={_short(getattr(fc, 'arguments', None), 120)}) "
            f"call_id={getattr(fc, 'call_id', None) or getattr(fc, 'id', None)}"
        )
    print(f"{prefix}· content[{t}] {fields}")


def dump_message(m: object, prefix: str = "      ") -> None:
    print(
        f"{prefix}msg role={getattr(m, 'role', '?')} "
        f"author={getattr(m, 'author_name', None)} "
        f"text={_short(getattr(m, 'text', '') or '')}"
    )
    for c in getattr(m, "contents", None) or []:
        dump_content(c, prefix + "  ")


def dump_event(ev: object, i: int) -> None:
    et = getattr(ev, "type", "?")
    ex = getattr(ev, "executor_id", None)
    rid = ev.request_id if et == "request_info" else None
    print(f"\n[{i:02d}] {et}  executor={ex}  request_id={rid}")
    data = getattr(ev, "data", None)
    if data is None:
        return
    print(f"     data={type(data).__name__}")
    ar = getattr(data, "agent_response", None)
    msgs = getattr(ar, "messages", None) if ar is not None else getattr(data, "messages", None)
    if msgs:
        for m in msgs:
            dump_message(m)
    elif isinstance(data, Message):
        dump_message(data)
    elif getattr(data, "function_call", None) is not None or getattr(data, "type", None):
        dump_content(data, "     ")
    else:
        print(f"     value={_short(data)}")


async def turn(workflow, storage, label, *, text=None, responses=None):
    print(f"\n########################## {label} ##########################")
    if responses is not None:
        result = await workflow.run(responses=responses, include_status_events=True)
    else:
        result = await workflow.run(text, include_status_events=True, checkpoint_storage=storage)
    for i, ev in enumerate(result):
        dump_event(ev, i)
    print("\n  >> replies:", extract_replies(result))
    reqs = result.get_request_info_events()
    print("  >> pending:", [(e.request_id, type(e.data).__name__, is_function_approval(e)) for e in reqs])
    print("  >> final_state:", result.get_final_state())
    return result


async def main() -> None:
    storage = InMemoryCheckpointStorage()
    wf = build_handoff_workflow(conversation_id="trace", checkpoint_storage=storage)

    r = await turn(
        wf, storage, "TURN 1 — user",
        text="Read the log file /var/log/db.log, then raise an IT support ticket for the "
             "error you find. Title 'DB timeout', department IT.",
    )
    # Drive up to 3 follow-up turns to reach the create_ticket approval.
    for n in range(3):
        reqs = r.get_request_info_events()
        if not reqs:
            break
        ev = reqs[0]
        if is_function_approval(ev):
            r = await turn(
                wf, storage, f"TURN {n + 2} — APPROVE create_ticket",
                responses={ev.request_id: ev.data.to_function_approval_response(approved=True)},
            )
            break
        r = await turn(
            wf, storage, f"TURN {n + 2} — user pushes the tool",
            responses={ev.request_id: [Message("user", ["Yes, actually call create_ticket now."])]},
        )


if __name__ == "__main__":
    asyncio.run(main())
