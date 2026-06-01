"""Console tracing middleware (port of the POC Debug/print_middleware.py).

Prints a structured block around every LLM call: the recent messages, the tools
offered, and — after the call — the response text plus any tool call / handoff the
model chose. Attached to every agent to make the handoff flow visible in the logs.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from agent_framework import ChatContext, ChatMiddleware
from agent_framework._middleware import FunctionInvocationContext, FunctionMiddleware

logger = logging.getLogger("app.llm_telemetry")


# Cap one captured tool result so a giant MCP/JSON payload can't bloat the recovered-reply note.
_MAX_RESULT_CHARS = 4000


def _result_text(result: Any) -> str:
    """Best-effort readable string for a captured tool result, length-capped.

    ``context.result`` may be the raw return value or a content object wrapping it under ``.result``.
    A list/tuple (e.g. ``list_available_files`` -> ``list[str]``) renders as one item per line so the
    silent-tool safety net surfaces something readable rather than a Python repr.
    """
    value = getattr(result, "result", result)
    if isinstance(value, str):
        text = value
    elif isinstance(value, (list, tuple)):
        text = "\n".join(str(x) for x in value)
    else:
        text = str(value)
    if len(text) > _MAX_RESULT_CHARS:
        text = text[:_MAX_RESULT_CHARS] + " …(truncated)"
    return text


class UserReplyCaptureMiddleware(FunctionMiddleware):
    """Capture each agent's ``send_reply_to_user`` call AND its substantive tool results so the handoff
    executor can surface the agent's work — even when the model forgets to call ``send_reply_to_user``.

    Two buffers, both drained by the executor right after the agent runs (one fresh instance per agent,
    ephemeral per run):
      - ``replies``: ``send_reply_to_user(message=...)`` — the clean, agent-authored user reply. Captured
        BEFORE call_next so the text is recorded regardless of the (no-op) tool body.
      - ``tool_results``: ``(tool_name, result)`` for every real tool (NOT handoffs, NOT the reply tool).
        This is the safety net for the "silent-tool" path — e.g. right after an approval resume, where the
        model executes the approved tool and jumps straight to a handoff without reporting. Without this
        the result is lost (``clean_conversation_for_handoff`` strips it and there is no assistant text).
    """

    _REPLY_TOOL = "send_reply_to_user"
    _HANDOFF_PREFIX = "handoff_to_"

    def __init__(self) -> None:
        self.replies: list[str] = []
        self.tool_results: list[tuple[str, str]] = []

    async def process(self, context: FunctionInvocationContext, call_next):  # type: ignore[no-untyped-def]
        name = getattr(getattr(context, "function", None), "name", "") or ""
        if name == self._REPLY_TOOL:
            args = context.arguments
            msg = args.get("message") if isinstance(args, Mapping) else getattr(args, "message", None)
            if msg:
                self.replies.append(str(msg))
            await call_next()
            return

        await call_next()
        # After a real tool runs, record its result. Handoff tools never reach here — _AutoHandoffMiddleware
        # (appended after this one) short-circuits them via MiddlewareTermination before call_next returns —
        # but we exclude the prefix defensively too.
        if name and not name.startswith(self._HANDOFF_PREFIX):
            result = getattr(context, "result", None)
            if result is not None:
                self.tool_results.append((name, _result_text(result)))

    def drain(self) -> list[str]:
        """Return user-reply messages captured since the last drain and reset the buffer."""
        captured = self.replies[:]
        self.replies.clear()
        return captured

    def drain_tool_results(self) -> list[tuple[str, str]]:
        """Return ``(tool_name, result)`` pairs captured since the last drain and reset the buffer."""
        captured = self.tool_results[:]
        self.tool_results.clear()
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


# Cap one text/arguments/result field in the audit blob so a giant MCP/JSON payload can't bloat the
# aistudiobot_agent_llm_call.request/response JSONB columns.
_MAX_AUDIT_TEXT = 8000


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return str(v)


def _cap(v: Any) -> Any:
    if isinstance(v, str) and len(v) > _MAX_AUDIT_TEXT:
        return v[:_MAX_AUDIT_TEXT] + " …(truncated)"
    return v


def _serialize_messages(messages: Any) -> list[dict]:
    """Turn framework Messages into JSON-able dicts for the llm_call request/response audit columns:
    role + author + each content (text / function_call / function_result), length-capped."""
    out: list[dict] = []
    for m in messages or []:
        contents: list[dict] = []
        for c in getattr(m, "contents", None) or []:
            ctype = getattr(c, "type", None)
            entry: dict[str, Any] = {"type": ctype}
            if ctype == "text":
                entry["text"] = _cap(getattr(c, "text", None))
            elif ctype == "function_call":
                entry["name"] = getattr(c, "name", None)
                entry["call_id"] = getattr(c, "call_id", None) or getattr(c, "id", None)
                entry["arguments"] = _cap(_jsonable(getattr(c, "arguments", None)))
            elif ctype == "function_result":
                entry["call_id"] = getattr(c, "call_id", None)
                entry["result"] = _cap(_jsonable(getattr(c, "result", None)))
            elif ctype == "function_approval_request":
                fc = getattr(c, "function_call", None)
                entry["function_name"] = getattr(fc, "name", None)
            else:
                entry["value"] = _cap(str(c))
            contents.append(entry)
        out.append(
            {
                "role": str(getattr(m, "role", None)),
                "author_name": getattr(m, "author_name", None),
                "contents": contents,
            }
        )
    return out


class LlmTelemetryMiddleware(ChatMiddleware):
    """Records one ``aistudiobot_agent_llm_call`` row per model invocation (model,
    token usage, latency, AND the full request + response for audit). Constructed
    per-user-message per-agent so it knows the conversation + the user message it
    belongs to. Writes in its own DB session (it runs inside ``workflow.run``), so the
    enclosing user ``aistudiobot_chathistory`` row must already be committed.
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

        # Full request + response for audit (JSONB). Tools are summarized by name only (the full
        # MCP schemas would bloat every row); messages carry the actual prompt + the model's output.
        opts = dict(context.options) if context.options else {}
        request_payload = {
            "model": opts.get("model"),
            "tool_choice": opts.get("tool_choice"),
            "tools": [getattr(t, "name", type(t).__name__) for t in (opts.get("tools") or [])],
            "messages": _serialize_messages(context.messages),
        }
        response_payload = {
            "text": _cap(getattr(result, "text", None)),
            "finish_reason": getattr(result, "finish_reason", None),
            "messages": _serialize_messages(getattr(result, "messages", None)),
        }

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
                    request=request_payload,
                    response=response_payload,
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - telemetry must never break a turn
            logger.warning("Failed to record llm_call telemetry: %s", exc)
