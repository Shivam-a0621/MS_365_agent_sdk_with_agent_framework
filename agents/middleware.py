"""Console tracing middleware (port of the POC Debug/print_middleware.py).

Prints a structured block around every LLM call: the recent messages, the tools
offered, and — after the call — the response text plus any tool call / handoff the
model chose. Attached to every agent to make the handoff flow visible in the logs.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agent_framework import ChatContext, ChatMiddleware
from agent_framework._middleware import FunctionInvocationContext, FunctionMiddleware

logger = logging.getLogger("app.llm_telemetry")


class ToolResultCaptureMiddleware(FunctionMiddleware):
    """Capture each domain-tool result as it executes, so the handoff orchestrator can share it
    (rendered as text) with the next agent.

    Why this exists: the framework strips tool results when broadcasting between agents, and on the
    approval-RESUME path a tool's result is consumed as input to the resumed run — so it is NOT in
    the agent's response messages. Capturing here, at execution time, catches every tool result
    reliably (normal calls AND post-approval). The handoff executor drains ``results`` right after
    the agent runs and broadcasts them into the (checkpointed) shared conversation, so this buffer is
    purely ephemeral per run and never needs persisting. One fresh instance per agent.
    """

    def __init__(self) -> None:
        self.results: list[tuple[str, Any]] = []

    async def process(self, context: FunctionInvocationContext, call_next):  # type: ignore[no-untyped-def]
        await call_next()
        # Handoff tools are short-circuited by the framework's auto-handoff middleware (raises before
        # call_next returns), so we never reach here for them; the name guard is a backstop.
        name = getattr(getattr(context, "function", None), "name", "") or ""
        if name.startswith("handoff_to_"):
            return
        self.results.append((name, getattr(context, "result", None)))

    def drain(self) -> list[tuple[str, Any]]:
        """Return captured (tool_name, result) pairs since the last drain and reset the buffer."""
        captured = self.results[:]
        self.results.clear()
        return captured


class PrintLLMCallMiddleware(ChatMiddleware):
    """Print a structured block around every chat-client (LLM) invocation.

    Why ``print`` (not a logger): this is a teaching/demo tracing aid meant to
    be visible in worker stdout with no logger configuration. For structured
    log ingestion use a real logger; this one is for looking at the flow with
    eyes during development and walkthroughs.

    Each block shows:
      - the recent messages the LLM saw,
      - the tools the model can call,
      - the response text,
      - any 🔧 TOOL CALL / 🔀 HANDOFF the model produced — surfaced separately
        at the bottom so a quick scan shows what the model decided to DO.
    """

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name

    async def process(self, context: ChatContext, call_next):
        print(f"\n┌──── LLM CALL · {self.agent_name} ────")
        print(f"│ messages: {len(context.messages)}")
        for msg in list(context.messages)[-3:]:
            role = getattr(msg, "role", "?")
            text = (getattr(msg, "text", "") or "").replace("\n", " ")[:160]
            print(f"│   [{role}] {text!r}")
        opts = dict(context.options) if context.options else {}
        tools = opts.get("tools", None) or []
        if opts:
            print(f"│ options: {opts}")
        if tools:
            print(f"│ tools ({len(tools)}):")
            for t in tools:
                name = getattr(t, "name", type(t).__name__)
                desc = (getattr(t, "description", "") or "").replace("\n", " ")
                print(f"│   - {name}: {desc!r}")
        print(f"│ stream: {context.stream}")
        print("├── calling LLM …")

        await call_next()

        if context.result is not None and not context.stream:
            reply_text = (getattr(context.result, "text", "") or "")[:240]
            print(f"│ response.text: {reply_text!r}")
            for msg in getattr(context.result, "messages", None) or []:
                for content in getattr(msg, "contents", None) or []:
                    if getattr(content, "type", "") != "function_call":
                        continue
                    fname = getattr(content, "name", "?")
                    args = getattr(content, "arguments", "")
                    args_str = str(args).replace("\n", " ")[:120]
                    if fname.startswith("handoff_to_"):
                        target = fname[len("handoff_to_"):]
                        print(f"│ 🔀 HANDOFF: {self.agent_name} → {target}")
                    else:
                        print(f"│ 🔧 TOOL CALL: {fname}({args_str})")
        print("└────────────────────────────────────\n")


def _get_usage_details(result) -> dict:
    """Best-effort fetch of token-usage details from a chat result.

    The framework normally exposes ``result.usage_details`` (a dict with
    ``input_token_count`` / ``output_token_count`` / ``total_token_count``).
    Some response shapes instead carry the usage info as a ``usage`` content
    inside one of the response messages — try the direct attribute first; fall
    back to scanning messages.
    """
    usage = getattr(result, "usage_details", None)
    if usage:
        return usage
    for msg in getattr(result, "messages", None) or []:
        for content in getattr(msg, "contents", None) or []:
            if getattr(content, "type", None) == "usage":
                fallback = getattr(content, "usage_details", None)
                if fallback:
                    return fallback
    return {}


class LlmTelemetryMiddleware(ChatMiddleware):
    """Records one ``aistudiobot_agent_llm_call`` row per model invocation (model,
    token usage, latency). Constructed per-user-message per-agent so it knows the
    conversation + the user message it belongs to. Writes in its own DB session
    (it runs inside ``workflow.run``), so the enclosing user ``aistudiobot_chathistory``
    row must already be committed.
    """

    def __init__(self, *, agent_name: str, chat_conversation_id: int, user_message_id: int) -> None:
        self.agent_name = agent_name
        self.chat_conversation_id = chat_conversation_id
        self.user_message_id = user_message_id

    async def process(self, context: ChatContext, call_next):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        await call_next()
        latency_ms = int((time.perf_counter() - started) * 1000)

        result = context.result
        if result is None or context.stream:
            return

        # UsageDetails keys are input_token_count / output_token_count / total_token_count.
        usage = _get_usage_details(result)
        model = (
            getattr(result, "model_id", None)
            or getattr(result, "model_name", None)
            or getattr(result, "model", None)
        )
        try:
            from db.repositories import add_llm_call
            from db.session import SessionLocal

            async with SessionLocal() as session:
                await add_llm_call(
                    session,
                    chat_conversation_id=self.chat_conversation_id,
                    user_message_id=self.user_message_id,
                    agent_name=self.agent_name,
                    model=model,
                    prompt_tokens=usage.get("input_token_count"),
                    completion_tokens=usage.get("output_token_count"),
                    total_tokens=usage.get("total_token_count"),
                    finish_reason=getattr(result, "finish_reason", None),
                    latency_ms=latency_ms,
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - telemetry must never break a turn
            logger.warning("Failed to record llm_call telemetry: %s", exc)
