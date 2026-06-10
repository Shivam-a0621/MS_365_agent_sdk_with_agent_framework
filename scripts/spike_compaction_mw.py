"""Test the candidate lever: a ChatMiddleware that compacts the assembled message list right before
each LLM call (apply_compaction on context.messages). This bounds the TRUE model context regardless of
where history comes from (session/cache/provider) — and we already attach middleware to every agent.

CONTROL vs COMPACTED, same questions; prints the model context size the LLM actually received per run.
Needs the Azure env.  Run:  python -m scripts.spike_compaction_mw
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

load_dotenv()

from agent_framework import (  # noqa: E402
    Agent,
    ChatMiddleware,
    SummarizationStrategy,
    apply_compaction,
)

from agents.client import build_chat_client  # noqa: E402


class _Capture(ChatMiddleware):
    """Records the assembled message count the LLM actually receives (runs AFTER any compactor)."""

    def __init__(self) -> None:
        self.per_call: list[int] = []

    async def process(self, context, call_next):  # type: ignore[no-untyped-def]
        self.per_call.append(len(context.messages))
        await call_next()


class _Compactor(ChatMiddleware):
    """Compacts context.messages in place before the LLM call via apply_compaction + a strategy."""

    def __init__(self, strategy) -> None:  # type: ignore[no-untyped-def]
        self.strategy = strategy

    async def process(self, context, call_next):  # type: ignore[no-untyped-def]
        try:
            projected = await apply_compaction(list(context.messages), strategy=self.strategy)
            try:
                context.messages = projected  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - fall back to in-place if not settable
                context.messages[:] = projected  # type: ignore[index]
        except Exception as exc:  # noqa: BLE001
            print(f"  [compactor] error: {type(exc).__name__}: {exc}")
        await call_next()


QUESTIONS = [
    "My name is Sam and my employee id is E-4471. In one sentence, what is MFA?",
    "How do I reset my email password?",
    "My VPN keeps disconnecting on Wi-Fi — any quick tip?",
    "What's the difference between an incident and a service request?",
    "How long do password resets usually take?",
    "Can you remind me what my employee id is?",
    "What was the very first thing I asked you about?",
    "Summarize our whole chat in one short line.",
]


async def _drive(agent, capture: _Capture, label: str) -> None:
    print(f"\n===== {label} =====")
    session = agent.create_session()
    prev = 0
    for i, q in enumerate(QUESTIONS, 1):
        try:
            resp = await agent.run(q, session=session)
        except Exception as exc:  # noqa: BLE001
            print(f"  run {i}: FAILED {type(exc).__name__}: {exc}")
            return
        calls = capture.per_call[prev:]
        prev = len(capture.per_call)
        n = calls[-1] if calls else 0
        ans = (getattr(resp, "text", "") or "").replace("\n", " ")[:70]
        print(f"  run {i}: model context = {n:>3} msgs   | ans: {ans!r}")


async def main() -> None:
    client = build_chat_client()
    instr = "You are a concise IT help assistant. Answer in ONE short sentence."

    cap_a = _Capture()
    control = Agent(
        client=client, name="control", instructions=instr, middleware=[cap_a],
        require_per_service_call_history_persistence=True,
    )

    cap_b = _Capture()
    strategy = SummarizationStrategy(
        client=client,
        target_count=6,
        threshold=2,
        prompt=(
            "Summarize the earlier messages concisely, but PRESERVE every concrete detail verbatim: "
            "the user's name and any IDs (e.g. employee id), ticket subjects/numbers, file paths, exact "
            "values/settings, and the user's original request. Never drop a specific fact."
        ),
    )
    compacted = Agent(
        client=client, name="compacted", instructions=instr,
        middleware=[_Compactor(strategy), cap_b],  # compactor first, capture measures the result
        require_per_service_call_history_persistence=True,
    )

    await _drive(control, cap_a, "CONTROL (no compaction)")
    await _drive(compacted, cap_b, "COMPACTED (compacting ChatMiddleware)")

    print("\n=== READ === CONTROL should climb; COMPACTED should PLATEAU once summarization triggers,")
    print("while still answering run 6/7 (employee id / first question) correctly if the gist survived.")


if __name__ == "__main__":
    asyncio.run(main())
