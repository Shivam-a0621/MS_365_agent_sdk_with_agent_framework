"""Context-aware handoff orchestrator.

The stock ``HandoffBuilder`` strips every agent's tool CALLS and RESULTS before broadcasting the
shared conversation to the other agents (``clean_conversation_for_handoff``). So when an agent hands
off — often with an empty message — the next agent can't see what the prior agent's tools actually
did, and it redoes the work or bounces it back.

This module subclasses the framework's handoff executor + builder and changes ONE thing: it ALSO
broadcasts each agent's tool RESULTS, rendered as plain TEXT, into the shared ``_full_conversation``.
Plain text avoids the LLM-API "unmatched tool-call state" rejection that made the framework strip
them in the first place.

Why this also fixes the approval-resume context loss: because it reuses ``HandoffAgentExecutor``, the
cross-agent ``_full_conversation`` is EXTENDED (never reassigned) across handoffs AND across an
approval pause+resume. So the original user request and all prior results survive a durable approval
resume — the exact failure the from-scratch ``WorkflowBuilder`` mesh had (base ``AgentExecutor``
reassigns ``_full_conversation`` on resume and discards the restored history).

Everything else — HITL approvals, checkpointing, fan-out edges, request_info, autonomous mode — is
inherited unchanged. The ``_run_agent_and_emit`` override below is a FAITHFUL COPY of the installed
framework body (``agent_framework_orchestrations._handoff.HandoffAgentExecutor``, pinned to
``==1.0.0rc1``) with only the tool-notes broadcast inserted — re-diff it against the framework on any
upgrade.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_framework import Message
from agent_framework._workflows._agent_executor import AgentExecutorRequest
from agent_framework._workflows._agent_utils import resolve_agent_id
from agent_framework._workflows._events import WorkflowEvent
from agent_framework._workflows._workflow_context import WorkflowContext
from agent_framework_orchestrations import (
    HandoffAgentExecutor,
    HandoffAgentUserRequest,
    HandoffBuilder,
    HandoffSentEvent,
    clean_conversation_for_handoff,
)

from agents.middleware import ToolResultCaptureMiddleware

logger = logging.getLogger("app.handoff")

# Cap one rendered tool result so a large MCP/JSON payload can't bloat every agent's context.
_MAX_RESULT_CHARS = 2000


def _stringify(result: Any) -> str:
    """Render a tool result as a compact, single-line, length-capped string."""
    if result is None:
        return "(no result)"
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, default=str, ensure_ascii=False)
        except Exception:  # noqa: BLE001 - tracing helper must never raise
            text = str(result)
    text = " ".join(text.split())  # collapse whitespace/newlines to one line
    if len(text) > _MAX_RESULT_CHARS:
        text = text[:_MAX_RESULT_CHARS] + " …(truncated)"
    return text


def _result_note(agent_name: str, tool_name: str, result: Any) -> Message:
    """Render one captured tool result as a plain-text message attributed to the agent.

    Why text (not raw ``function_result`` content): replaying tool-call structure into another
    agent's turn makes the LLM API reject the request ("unmatched tool-call state") — exactly why the
    framework strips it. Plain text carries the same information with no problem.
    """
    return Message(
        role="assistant",
        author_name=agent_name,
        contents=[f"[{agent_name} · {tool_name or 'tool'}] {_stringify(result)}"],
    )


class ContextAwareHandoffExecutor(HandoffAgentExecutor):
    """``HandoffAgentExecutor`` that also shares each agent's tool RESULTS (as text) across handoffs.

    Identical to the base executor except for the broadcast block, which appends this agent's tool
    notes so the next agent sees what was actually done even if the model handed off with empty text.
    """

    async def _run_agent_and_emit(self, ctx: WorkflowContext[Any, Any]) -> None:
        # First run: broadcast the initial cache to all other agents (start agent only).
        if self._is_start_agent and not self._full_conversation:
            await self._broadcast_messages(self._cache.copy(), ctx)

        self._full_conversation.extend(self._cache.copy())

        if await self._should_terminate():
            return

        if ctx.is_streaming():
            response = await self._run_agent_streaming(ctx)
        else:
            response = await self._run_agent(ctx)

        self._cache.clear()

        # Awaiting a function approval / user input — nothing to emit yet.
        if response is None:
            logger.debug("ContextAwareHandoffExecutor %s: awaiting user input", self.id)
            return

        # --- THE ONE CHANGE vs the base class -----------------------------------------------------
        # Broadcast the agent's cleaned text PLUS a plain-text rendering of its tool results
        # (captured at execution time by ToolResultCaptureMiddleware), so the next agent sees what
        # was actually done even when the model hands off with an empty message — and even across an
        # approval pause (where the result is consumed as input to the resumed run, not in messages).
        cleaned_response = clean_conversation_for_handoff(response.messages)
        tool_notes = [
            _result_note(self.id, name, res) for name, res in self._drain_tool_results()
        ]
        broadcast = cleaned_response + tool_notes
        self._full_conversation.extend(broadcast)
        await self._broadcast_messages(broadcast, ctx)
        # ------------------------------------------------------------------------------------------

        if is_handoff_requested := self._is_handoff_requested(response):
            handoff_target, handoff_message = is_handoff_requested
            if handoff_target not in self._handoff_targets:
                raise ValueError(
                    f"Agent '{resolve_agent_id(self._agent)}' attempted to handoff to unknown "
                    f"target '{handoff_target}'. Valid targets are: {', '.join(self._handoff_targets)}"
                )
            self._cache.append(handoff_message)
            await ctx.send_message(
                AgentExecutorRequest(messages=[], should_respond=True),
                target_id=handoff_target,
            )
            await ctx.add_event(
                WorkflowEvent("handoff_sent", data=HandoffSentEvent(source=self.id, target=handoff_target))
            )
            self._autonomous_mode_turns = 0
            return

        if await self._should_terminate():
            return

        if self._autonomous_mode and self._autonomous_mode_turns < self._autonomous_mode_turn_limit:
            self._cache.extend([Message(role="user", contents=[self._autonomous_mode_prompt])])
            self._autonomous_mode_turns += 1
            await self._run_agent_and_emit(ctx)
        else:
            self._autonomous_mode_turns = 0
            await ctx.request_info(HandoffAgentUserRequest(response), list[Message])

    def _drain_tool_results(self) -> list[tuple[str, Any]]:
        """Pull the raw (tool_name, result) pairs this agent captured during the run, via its
        ToolResultCaptureMiddleware. The capture middleware rides on the (cloned) agent's middleware
        list — ``self._agent`` is that clone — so we find it there. If a future framework clone drops
        the middleware, this returns empty and the executor degrades to stock behavior."""
        results: list[tuple[str, Any]] = []
        for mw in getattr(self._agent, "middleware", None) or []:
            if isinstance(mw, ToolResultCaptureMiddleware):
                results.extend(mw.drain())
        return results


class ContextAwareHandoffBuilder(HandoffBuilder):
    """``HandoffBuilder`` that wires up ``ContextAwareHandoffExecutor``s instead of the stock ones."""

    def _resolve_executors(
        self,
        agents: dict[str, Any],
        handoffs: dict[str, list[Any]],
    ) -> dict[str, ContextAwareHandoffExecutor]:
        executors: dict[str, ContextAwareHandoffExecutor] = {}
        for id, agent in agents.items():
            resolved_id = self._resolve_to_id(agent)
            if resolved_id not in handoffs or not handoffs.get(resolved_id):
                logger.warning("No handoff configuration found for agent '%s'.", resolved_id)
            autonomous_mode = self._autonomous_mode and (
                not self._autonomous_mode_enabled_agents or id in self._autonomous_mode_enabled_agents
            )
            executors[resolved_id] = ContextAwareHandoffExecutor(
                agent=agent,
                handoffs=handoffs.get(resolved_id, []),
                is_start_agent=(id == self._start_id),
                termination_condition=self._termination_condition,
                autonomous_mode=autonomous_mode,
                autonomous_mode_prompt=self._autonomous_mode_prompts.get(id, None),
                autonomous_mode_turn_limit=self._autonomous_mode_turn_limits.get(id, None),
            )
        return executors
