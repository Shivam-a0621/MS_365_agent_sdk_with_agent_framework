"""Diagnostic probe (read-only): WHERE does conversation history accumulate, and WHAT does the model
actually receive — so we can aim summarization compaction at the correct lever.

It builds the REAL handoff workflow, drives a few plain conversational turns (no MCP, no approvals), and
after each turn reports, per executor instance:
  - _cache            (messages fed into agent.run)
  - _full_conversation(the executor's cross-agent history)
  - agent_session     (the agent's own thread)
and separately the EXACT messages handed to the chat client's get_response (= the real LLM context, the
ground truth for the context-window question), plus the checkpoint blob size growth.

It does NOT change app behavior. Needs the Azure LLM env (it drives real turns); each turn is one or a
few cheap chat calls. Run:  python -m scripts.spike_compaction_probe
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

load_dotenv()  # AZURE_OPENAI_* before building the client/workflow

from agent_framework import InMemoryCheckpointStorage, Message  # noqa: E402

import workflows.handoff as handoff_mod  # noqa: E402
from agents.client import build_chat_client as _real_build_chat_client  # noqa: E402
from workflows.handoff import build_handoff_workflow, workflow_name  # noqa: E402
from workflows.handoff_orchestrator import extract_replies, is_function_approval  # noqa: E402

# --- per-call LLM-request capture (the true model context) ----------------------------------------
CURRENT_TURN = [0]
LLM_CALLS: list[dict] = []


def _seq(msgs) -> list:
    if msgs is None:
        return []
    return list(msgs) if isinstance(msgs, (list, tuple)) else [msgs]


def _n(msgs) -> int:
    return len(_seq(msgs))


def _chars(msgs) -> int:
    """Rough char-size of a message list: text + any string content fields."""
    total = 0
    for m in _seq(msgs):
        if isinstance(m, str):
            total += len(m)
            continue
        t = getattr(m, "text", None)
        if isinstance(t, str):
            total += len(t)
        for c in getattr(m, "contents", None) or []:
            for attr in ("text", "arguments", "result"):
                v = getattr(c, attr, None)
                if isinstance(v, str):
                    total += len(v)
    return total


# Instrument the chat client's get_response = the final list handed to the LLM (model context).
_client = _real_build_chat_client()
_ClientCls = type(_client)
_orig_get_response = _ClientCls.get_response


def _wrapped_get_response(self, messages=None, *args, **kwargs):
    try:
        LLM_CALLS.append({"turn": CURRENT_TURN[0], "n": _n(messages), "chars": _chars(messages)})
    except Exception:  # noqa: BLE001
        pass
    return _orig_get_response(self, messages, *args, **kwargs)


_ClientCls.get_response = _wrapped_get_response
# Force the workflow to use our single instrumented client instance.
handoff_mod.build_chat_client = lambda: _client


# --- reporting ------------------------------------------------------------------------------------
def _session_chars(ex) -> int:
    sess = getattr(ex, "_session", None)
    if sess is None:
        return 0
    try:
        return len(str(sess.to_dict()))
    except Exception:  # noqa: BLE001
        try:
            return len(repr(sess))
        except Exception:  # noqa: BLE001
            return -1


async def _checkpoint_report(storage, wf_name: str) -> str:
    try:
        cps = await storage.list_checkpoints(workflow_name=wf_name)
    except Exception as exc:  # noqa: BLE001
        return f"(list_checkpoints failed: {exc})"
    if not cps:
        return "no checkpoints yet"
    sizes = []
    for cp in cps:
        try:
            sizes.append(len(str(cp)))
        except Exception:  # noqa: BLE001
            sizes.append(-1)
    return f"{len(cps)} checkpoints | largest={max(sizes)}ch total={sum(sizes)}ch"


def _report_turn(turn: int, wf, storage_report: str) -> None:
    print(f"\n===== after TURN {turn} =====")
    print("  per-executor stores (msgs / chars):")
    for eid, ex in wf.executors.items():
        cache = getattr(ex, "_cache", None)
        fc = getattr(ex, "_full_conversation", None)
        if cache is None and fc is None:
            continue  # not an agent executor
        print(
            f"    {eid:24s} cache={_n(cache):>3}m/{_chars(cache):>6}c   "
            f"full_conv={_n(fc):>3}m/{_chars(fc):>6}c   session={_session_chars(ex):>6}c"
        )
    calls = [c for c in LLM_CALLS if c["turn"] == turn]
    if calls:
        print("  LLM get_response calls this turn (n_messages / chars = the model context):")
        for j, c in enumerate(calls, 1):
            print(f"    call {j}: {c['n']:>3} messages / {c['chars']:>6} chars")
    else:
        print("  LLM get_response calls this turn: NONE captured (wrapper missed the call path?)")
    print(f"  checkpoint: {storage_report}")


def _handoff_pause(result):
    for ev in result.get_request_info_events():
        if not is_function_approval(ev):
            return ev.request_id
    return None


TURNS = [
    "Hi! In one or two sentences, what kinds of IT problems can you help me with?",
    "Got it. Can you explain, briefly, how the ticket process works here?",
    "Thanks. And how would I check the status of an existing ticket?",
    "Great — can you summarize everything you've told me so far in this chat?",
]


async def main() -> None:
    storage = InMemoryCheckpointStorage()
    wf = build_handoff_workflow(conversation_id="probe", checkpoint_storage=storage)
    wf_name = workflow_name("probe", 1)

    result = None
    for i, text in enumerate(TURNS, 1):
        CURRENT_TURN[0] = i
        print(f"\n########## TURN {i}: {text!r} ##########")
        try:
            if result is None:
                result = await wf.run(text, include_status_events=True, checkpoint_storage=storage)
            else:
                req = _handoff_pause(result)
                if req is None:
                    print("  no handoff_user pause to resume — stopping the probe.")
                    break
                result = await wf.run(
                    responses={req: [Message("user", [text])]}, include_status_events=True
                )
        except Exception as exc:  # noqa: BLE001
            print(f"  TURN {i} FAILED: {type(exc).__name__}: {exc}")
            print("  (If this is an auth/network error, run the probe where Azure OpenAI is reachable.)")
            break
        print("  replies:", [r[:120] for r in extract_replies(result)])
        _report_turn(i, wf, await _checkpoint_report(storage, wf_name))

    print("\n\n=========== READING THE RESULT ===========")
    print("- If `full_conv`/`cache` chars climb every turn while `session` stays ~flat -> history lives")
    print("  in the executor's cache/full_conversation, and the model context = the LLM `chars` column.")
    print("- If `session` chars climb -> the agent thread accumulates; CompactionProvider/HistoryProvider")
    print("  is the right lever.")
    print("- Watch whether INACTIVE specialists' `cache` grows from broadcasts (replication across executors).")
    print("- The LLM `chars` column is the real context-window cost; that's what compaction must shrink.")


if __name__ == "__main__":
    asyncio.run(main())
