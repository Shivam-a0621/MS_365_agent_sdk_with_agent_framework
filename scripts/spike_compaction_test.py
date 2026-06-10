"""Confirmation test: does the framework's CompactionProvider + SummarizationStrategy actually SHRINK
the model context (the agent's growing session) — the fix for the context-window pain we diagnosed?

Drives the SAME questions through two single agents and prints the TRUE model context (the assembled
message list handed to the LLM, measured via a ChatMiddleware) per run:
  - CONTROL : a plain agent like ours today (default session, no compaction) -> context grows.
  - COMPACTED: agent + InMemoryHistoryProvider + CompactionProvider(SummarizationStrategy) -> plateaus.

Minimal standalone agents (no tools/handoff) — the compaction mechanism is identical; this proves it
works with our Azure client before we wire it into build_handoff_agents. Needs the Azure env.
Run:  python -m scripts.spike_compaction_test
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

load_dotenv()

from agent_framework import (  # noqa: E402
    Agent,
    ChatMiddleware,
    CompactionProvider,
    InMemoryHistoryProvider,
    SummarizationStrategy,
)

from agents.client import build_chat_client  # noqa: E402


class _CaptureContext(ChatMiddleware):
    """Record the assembled message list handed to the LLM each call (the true model context)."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def process(self, context, call_next):  # type: ignore[no-untyped-def]
        msgs = list(context.messages)
        chars = sum(len(getattr(m, "text", "") or "") for m in msgs)
        self.calls.append((len(msgs), chars))
        await call_next()


QUESTIONS = [
    "My name is Sam and my employee id is E-4471. In one sentence, what is MFA?",
    "How do I reset my email password?",
    "My VPN keeps disconnecting on Wi-Fi — any quick tip?",
    "What's the difference between an incident and a service request?",
    "How long do password resets usually take?",
    "Can you remind me what my employee id is?",        # did old context survive compaction?
    "What was the very first thing I asked you about?",  # ditto
    "Summarize our whole chat in one short line.",
]


async def _drive(agent, capture: _CaptureContext, label: str) -> None:
    print(f"\n===== {label} =====")
    session = agent.create_session()
    prev = 0
    for i, q in enumerate(QUESTIONS, 1):
        try:
            resp = await agent.run(q, session=session)
        except Exception as exc:  # noqa: BLE001
            print(f"  run {i}: FAILED {type(exc).__name__}: {exc}")
            return
        run_calls = capture.calls[prev:]
        prev = len(capture.calls)
        n, chars = run_calls[-1] if run_calls else (0, 0)
        answer = (getattr(resp, "text", "") or "").replace("\n", " ")[:70]
        print(f"  run {i}: model context = {n:>3} msgs / {chars:>6} chars   | ans: {answer!r}")


async def main() -> None:
    client = build_chat_client()
    instr = "You are a concise IT help assistant. Answer in ONE short sentence."

    # CONTROL — plain agent, like ours today (default session, no compaction).
    cap_a = _CaptureContext()
    control = Agent(
        client=client,
        name="control",
        instructions=instr,
        middleware=[cap_a],
        require_per_service_call_history_persistence=True,
    )

    # COMPACTED — history provider + summarizing compaction (summarize once >6 included messages).
    cap_b = _CaptureContext()
    history = InMemoryHistoryProvider("hist", load_messages=True)
    compaction = CompactionProvider(
        before_strategy=SummarizationStrategy(client=client, target_count=4, threshold=2),
        history_source_id=history.source_id,
    )
    compacted = Agent(
        client=client,
        name="compacted",
        instructions=instr,
        middleware=[cap_b],
        context_providers=[history, compaction],
        require_per_service_call_history_persistence=True,
    )

    await _drive(control, cap_a, "CONTROL (no compaction — today's behavior)")
    await _drive(compacted, cap_b, "COMPACTED (CompactionProvider + SummarizationStrategy)")

    print("\n=== HOW TO READ ===")
    print("- CONTROL 'msgs' should climb every run (unbounded growth = the context-window pain).")
    print("- COMPACTED 'msgs' should PLATEAU once summarization kicks in (~after 6 messages).")
    print("- If COMPACTED still answers run 6/7 (employee id / first question) correctly, the summary")
    print("  preserved the gist -> safe for our cross-agent + resume context-survival guarantee.")


if __name__ == "__main__":
    asyncio.run(main())
