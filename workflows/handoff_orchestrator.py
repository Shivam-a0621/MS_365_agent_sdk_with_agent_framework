"""Context-aware handoff orchestrator.

The stock ``HandoffBuilder`` strips every agent's tool CALLS and RESULTS before broadcasting the
shared conversation to the other agents (``clean_conversation_for_handoff``). So when an agent hands
off — often with an empty message — the next agent can't see what the prior agent's tools actually
did, and it redoes the work or bounces it back.

This module subclasses the framework's handoff executor + builder and changes ONE thing: each agent
reports its result via the ``send_reply_to_user`` tool, and we surface every such message in BOTH
places — to the USER as a workflow output (the sole user-reply channel) AND to the NEXT AGENT as a
plain-text note in the shared ``_full_conversation`` (the sole cross-agent context channel). One clean
summary, written by the agent, serves both. Plain text avoids the LLM-API "unmatched tool-call state"
rejection that made the framework strip tool content. (A plain-text FALLBACK covers the user when an
agent answers in text instead of calling the tool; the next agent already sees that text.)

Why this also fixes the approval-resume context loss: because it reuses ``HandoffAgentExecutor``, the
cross-agent ``_full_conversation`` is EXTENDED (never reassigned) across handoffs AND across an
approval pause+resume. So the original user request and all prior results survive a durable approval
resume — the exact failure the from-scratch ``WorkflowBuilder`` mesh had (base ``AgentExecutor``
reassigns ``_full_conversation`` on resume and discards the restored history).

Everything else — HITL approvals, checkpointing, fan-out edges, request_info, autonomous mode — is
inherited unchanged. The ``_run_agent_and_emit`` override below is a FAITHFUL COPY of the installed
framework body (``agent_framework_orchestrations._handoff.HandoffAgentExecutor``, pinned to
``==1.0.0rc1``) with only the reply-surfacing/broadcast block inserted — re-diff against the framework
on any upgrade.

This module also owns the run-output extractors at the bottom (``extract_replies`` /
``find_tool_result`` / ``is_function_approval``) — the readers of the UserReply/ToolExecuted outputs
emitted here — so producer and reader live together.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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

from agents.middleware import UserReplyCaptureMiddleware

logger = logging.getLogger("app.handoff")


@dataclass
class UserReply:
    """A user-visible reply emitted by an agent via ``send_reply_to_user``, yielded as a workflow
    output. ``extract_replies`` (below) surfaces these (and ONLY these) to the user; the
    recorder safely ignores them (no ``.messages``)."""

    agent: str
    text: str


@dataclass
class ToolExecuted:
    """A real tool that ACTUALLY executed (captured by ``UserReplyCaptureMiddleware``), surfaced as a
    workflow output so the audit can record ``tool_call`` + ``tool_result`` on the turn the tool RAN.

    This matters for approval-gated tools: the framework runs the approved tool on the RESUME turn but
    does NOT re-emit its ``function_result``, so the service reconstructs the audit rows from this output
    (after the decision is stamped onto the approval_request row). ``extract_replies`` and the recorder ignore it (not a ``UserReply``,
    no ``.messages``); only the service's approval-resume branch consumes it."""

    agent: str
    tool: str
    result: str


# Cap one cross-agent reply note so a giant summary can't bloat every downstream agent's context.
_MAX_NOTE_CHARS = 4000


def _reply_note(agent_name: str, message: str) -> Message:
    """Render an agent's reported result as a plain-text assistant message so the NEXT agent sees what
    this agent reported (and did) — the same summary the user gets. Plain text avoids the LLM-API
    "unmatched tool-call state" rejection that a replayed tool call/result would trigger.
    """
    text = message if len(message) <= _MAX_NOTE_CHARS else message[:_MAX_NOTE_CHARS] + " …(truncated)"
    return Message(role="assistant", author_name=agent_name, contents=[f"[{agent_name}] {text}"])


def _tool_results_text(tool_results: list[tuple[str, str]]) -> str:
    """Join captured tool results into one user/agent-facing block. Used ONLY on the silent-tool path
    (the agent ran tools but never called send_reply_to_user) so the work is not lost."""
    parts = [res.strip() for _name, res in tool_results if res and res.strip()]
    return "\n".join(parts)


class ContextAwareHandoffExecutor(HandoffAgentExecutor):
    """``HandoffAgentExecutor`` that also shares each agent's tool RESULTS (as text) across handoffs.

    Identical to the base executor except for the broadcast block, which appends this agent's tool
    notes so the next agent sees what was actually done even if the model handed off with empty text.
    """

    async def _run_agent_and_emit(self, ctx: WorkflowContext[Any, Any]) -> None:
        # First run: broadcast the initial cache to all other agents .
        if self._is_start_agent and not self._full_conversation:
            await self._broadcast_messages(self._cache.copy(), ctx)

        self._full_conversation.extend(self._cache.copy())

        # Within-turn handoff loop hit the cap (see MAX_HOPS_PER_TURN). In a ping-pong the agent
        # hands off and returns at the handoff block below BEFORE reaching the post-response check,
        # so THIS start-of-run check is where a loop is caught. Stop cleanly with a user-facing notice
        # instead of the base class's silent return (which would leave the user hanging). We
        # intentionally do NOT open a resume pause here: the looped _full_conversation is
        # bloated/confused, so letting the next user message start a fresh turn resets it (a healthy
        # turn converges to its own handoff_user pause far below the cap and never reaches this).
        if await self._should_terminate():
            await self._surface(
                ["I couldn't finish that request — could you rephrase it or break it into smaller steps?"],
                ctx,
                broadcast=False,
            )
            return

        if ctx.is_streaming():
            response = await self._run_agent_streaming(ctx)
        else:
            response = await self._run_agent(ctx)

        self._cache.clear()

        # --- THE CHANGE vs the base class ---------------------------------------------------------
        # Each agent reports via send_reply_to_user. We surface ONE result per run to BOTH the USER (a
        # UserReply output) and the NEXT AGENT (a plain-text note), choosing the best source in priority
        # order so the agent's work is NEVER silently dropped:
        #   1. send_reply_to_user message(s) — the clean, agent-authored reply.
        #   2. else plain assistant text in the response — the agent answered in prose.
        #   3. else captured tool results — the "silent-tool" path: the agent ran a tool but neither
        #      replied nor spoke. This is exactly what happens right after an approval resume, where the
        #      base executor runs the approved tool and the model jumps straight to a handoff. Without
        #      this, clean_conversation_for_handoff strips the result and it is lost to user + next agent.
        # Drained NOW, BEFORE the approval-pause early-return, so a reply emitted right before a same-run
        # approval pause still reaches both channels (the capture buffers are ephemeral per run).
        user_replies = self._drain_user_replies()
        tool_results = self._drain_tool_results()

        # Surface every tool that actually executed this run so the audit can record tool_call +
        # tool_result on the turn it RAN — crucial on an approval resume, where the framework runs the
        # approved tool but does not re-emit its function_result (see ToolExecuted). Empty on a pause turn
        # (the approval-gated tool has not executed yet).
        for tool_name, tool_result in tool_results:
            await ctx.yield_output(ToolExecuted(agent=self.id, tool=tool_name, result=tool_result))

        # Awaiting a function approval / user input — only a pre-pause send_reply_to_user can exist yet.
        if response is None:
            await self._surface(user_replies, ctx, broadcast=True)
            logger.debug("ContextAwareHandoffExecutor %s: awaiting user input", self.id)
            return

        # Broadcast the agent's cleaned text to the other agents (stock behavior).
        cleaned_response = clean_conversation_for_handoff(response.messages)
        self._full_conversation.extend(cleaned_response)
        await self._broadcast_messages(cleaned_response, ctx)
        # ------------------------------------------------------------------------------------------

        if user_replies:
            # Clean channel: surface the agent's reply to the user AND the next agent.
            await self._surface(user_replies, ctx, broadcast=True)
        else:
            # The agent didn't call send_reply_to_user. Recover something so nothing is silently dropped.
            plain_text = "\n".join(
                t for m in response.messages if (t := (getattr(m, "text", "") or "").strip())
            )
            if plain_text:
                # Answered in prose; the next agent already has it via cleaned_response above.
                await self._surface([plain_text], ctx, broadcast=False)
            elif tool_results:
                # Silent-tool path: ran a tool but said nothing. cleaned_response stripped the result,
                # so surface it to the user AND re-attach it as a note for the next agent.
                await self._surface([_tool_results_text(tool_results)], ctx, broadcast=True)

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

    async def _surface(
        self, texts: list[str], ctx: WorkflowContext[Any, Any], *, broadcast: bool
    ) -> None:
        """Emit each non-empty text as a UserReply output and, if ``broadcast``, also as a cross-agent
        note appended to the shared conversation (so the next agent sees what this one reported)."""
        clean = [t for t in texts if t]
        for text in clean:
            await ctx.yield_output(UserReply(agent=self.id, text=text))
        if broadcast and clean:
            notes = [_reply_note(self.id, t) for t in clean]
            self._full_conversation.extend(notes)
            await self._broadcast_messages(notes, ctx)

    def _drain_user_replies(self) -> list[str]:
        """Pull user-facing messages this agent emitted via ``send_reply_to_user`` during the run,
        from its UserReplyCaptureMiddleware on the (cloned) agent. Degrades to empty if a future
        framework clone drops the middleware."""
        replies: list[str] = []
        for mw in getattr(self._agent, "middleware", None) or []:
            if isinstance(mw, UserReplyCaptureMiddleware):
                replies.extend(mw.drain())
        return replies

    def _drain_tool_results(self) -> list[tuple[str, str]]:
        """Pull ``(tool_name, result)`` pairs this agent's real tools produced during the run, from its
        UserReplyCaptureMiddleware. Used as the silent-tool safety net (see ``_run_agent_and_emit``)."""
        results: list[tuple[str, str]] = []
        for mw in getattr(self._agent, "middleware", None) or []:
            if isinstance(mw, UserReplyCaptureMiddleware):
                results.extend(mw.drain_tool_results())
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


# --- run-output extractors (read back what the executor above emitted) ----------------------------
# These turn one workflow run into channel-neutral values for the service layer. They live here, next
# to the UserReply/ToolExecuted producers, so producer + reader are one module (no import cycle to
# dodge). NOTE: resume-value reconstruction lives in workflows/manager.py (_resume_value) — it works off
# a stored HumanInput row (cross-process resume), not a live run, so it stays separate.


def extract_replies(result: Any) -> list[str]:
    """User-visible replies = the ``send_reply_to_user`` outputs ONLY (the UserReply outputs emitted by
    ``_surface``), in emission order, de-duplicated. The framework also yields each agent's implicit
    AgentResponse as an output event; we deliberately skip those — send_reply_to_user is the sole
    user-reply channel.

    De-dup safety net: in a handoff mesh a receiving agent sometimes RESTATES a result another agent
    already reported (it sees the "[other_agent] …" note and parrots it as plain text, which the
    executor's fallback then surfaces). Identical replies are collapsed by whitespace-normalized text so
    the user never sees the same answer twice, regardless of how the models route."""
    replies: list[str] = []
    seen: set[str] = set()
    for output in result.get_outputs():
        if isinstance(output, UserReply) and output.text:
            key = " ".join(output.text.split())  # normalize whitespace for the comparison only
            if key in seen:
                continue
            seen.add(key)
            replies.append(output.text)
    return replies


def find_tool_result(result: Any, tool_name: str | None) -> str | None:
    """Return the captured result of the executed tool named ``tool_name`` from this run's ToolExecuted
    outputs, or None. Used by the service to write the ``tool_result`` audit row on an approval RESUME
    (the framework runs the approved tool but does not re-emit its function_result)."""
    if not tool_name:
        return None
    for output in result.get_outputs():
        if isinstance(output, ToolExecuted) and output.tool == tool_name:
            return output.result
    return None


def is_function_approval(event: Any) -> bool:
    """True if a request_info event is a tool/function approval request."""
    return getattr(getattr(event, "data", None), "type", None) == "function_approval_request"
