"""The four IT-support agents for the handoff workflow (port of the POC handoff_workflow.py)."""

from __future__ import annotations

from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.openai import OpenAIChatCompletionClient

from agents.middleware import LlmTelemetryMiddleware, PrintLLMCallMiddleware
from tools.file_master import mock_read_file, mock_write_file
from tools.ticket_master import check_existing_tickets, create_ticket

# Stable ids — also used as handoff targets.
TRIAGE = "triage_agent"
FILE_MASTER = "file_master"
TICKET_RAISER = "ticket_raiser"
WORKFLOW_ANALYZER = "ae_workflow_analyzer"


# Shared behavioral contract for every handoff agent. Per-agent instructions are
# just `HANDOFF_AGENT_RULES + one-line role anchor` (see `_role` below). Two core
# principles drive everything else:
#   1. you are ALWAYS ENTITLED to do your own work,
#   2. NEVER reply for another agent.
HANDOFF_AGENT_RULES = """You are a specialist agent in a multi-agent handoff workflow.

CORE PRINCIPLES:

1. YOU ARE ALWAYS ENTITLED TO DO YOUR OWN WORK.
   When the user's request is in your specialty, just call your tools — do not
   ask the user "should I proceed?" or "would you like me to do this?" first.
   An approval-required tool is normal: call it; the system pauses for the user
   automatically. Do not bounce control back to whoever handed off to you; do
   your part.

2. NEVER REPLY FOR ANOTHER AGENT.
   Speak only about work YOUR tools did. Do not claim, promise, or describe
   work that belongs to another specialist. If the user also needs work outside
   your specialty, finish YOUR part and hand off — let the next specialist
   speak for their own work.

3. DO NOT NARRATE FUTURE PLANS OR ROUTING — but DO confirm completed work.
   NEVER announce what you're ABOUT to do. Do not say "I'll hand off…",
   "Next I will…", "Let me delegate to…", "Let's start with…",
   "I'll route this to…", "After that I'll…". If you need to hand off, just
   call the handoff tool — don't tell the user about the routing.
   HOWEVER: when a tool YOU called just returned a successful result, DO state
   the outcome in ONE concise sentence naming the concrete artifact (e.g.,
   "Created file system.error." or "Created ticket TKT-1001."). That single
   sentence serves the user AND lets the next specialist see what is already
   done. Then, if you need to hand off, do so silently after that sentence.

4. SPEAK ONLY ABOUT FINISHED WORK.
   Reply to the user ONLY when a tool YOU called returned a successful result,
   in ONE short sentence naming the artifact (file path, ticket id, etc.). If
   nothing of yours has completed yet, say nothing — call your next tool or
   hand off.

5. TRUST THE UPSTREAM AGENT.
   If you RECEIVED a handoff from another agent, that agent has already done
   their part of the request — do NOT redo it, and do NOT hand off back to
   them. Look at the recent assistant messages: if you see another specialist
   confirmed a completion ("Created file …", "Created ticket …"), treat that
   work as DONE and focus on YOUR remaining part. If the user asked for
   multiple things and you can see one part is done, call your tool for the
   other part — do not bounce control.

Operational details:
- Use your tools for requests in your specialty; hand off for requests outside it.
- For multi-task requests, finish your part with your tools, briefly state the
  artifact you produced, then hand off — never announce the handoff itself.
- Never claim work was done unless a tool YOU called returned successfully.
- Treat each user request independently of past actions in your history; a prior
  ticket / file write does NOT satisfy a new explicit request.
"""


def _role(line: str) -> str:
    """Build an agent's instruction string = shared rules + one-line role anchor."""
    return f"{HANDOFF_AGENT_RULES}\nRole: {line}"


def _middleware(
    name: str, chat_conversation_id: int | None, user_message_id: int | None
) -> list:
    mw: list = [PrintLLMCallMiddleware(name)]
    # Attach LLM telemetry only when running a real turn (ids known). Middleware is
    # not part of the workflow graph signature, so this stays checkpoint-compatible.
    if chat_conversation_id is not None and user_message_id is not None:
        mw.append(
            LlmTelemetryMiddleware(
                agent_name=name,
                chat_conversation_id=chat_conversation_id,
                user_message_id=user_message_id,
            )
        )
    return mw


def build_handoff_agents(
    client: OpenAIChatCompletionClient,
    ae_mcp: MCPStreamableHTTPTool,
    *,
    chat_conversation_id: int | None = None,
    user_message_id: int | None = None,
) -> list[Agent]:
    triage_agent = Agent(
        client=client,
        name=TRIAGE,
        description="General IT support triage. Greets the user, answers generic IT questions.",
        instructions=_role(
            "entry point. Greet the user, answer generic IT questions, and route "
            "specialist work to the right agent."
        ),
        middleware=_middleware(TRIAGE, chat_conversation_id, user_message_id),
        require_per_service_call_history_persistence=True,
    )

    file_master = Agent(
        client=client,
        name=FILE_MASTER,
        description="Reads, writes, inspects, and searches files and log files on disk.",
        instructions=_role(
            "file and log operations on disk (read, write, inspect, search)."
        ),
        tools=[mock_read_file, mock_write_file],
        middleware=_middleware(FILE_MASTER, chat_conversation_id, user_message_id),
        require_per_service_call_history_persistence=True,
    )

    ticket_raiser = Agent(
        client=client,
        name=TICKET_RAISER,
        description="Creates, looks up, updates, and checks the status of IT support tickets.",
        instructions=_role(
            "IT support tickets — create new tickets and provide updates on existing ones."
        ),
        tools=[check_existing_tickets, create_ticket],
        middleware=_middleware(TICKET_RAISER, chat_conversation_id, user_message_id),
        require_per_service_call_history_persistence=True,
    )

    workflow_analyzer = Agent(
        client=client,
        name=WORKFLOW_ANALYZER,
        description="Analyzes AutomationEdge engine workflow details using mcp.",
        instructions=_role(
            "AutomationEdge workflow analysis via MCP. Only respond to direct "
            "questions about workflow status/details/troubleshooting; do not "
            "proactively check or raise issues."
        ),
        tools=[ae_mcp],
        middleware=_middleware(
            WORKFLOW_ANALYZER, chat_conversation_id, user_message_id
        ),
        require_per_service_call_history_persistence=True,
    )

    return [triage_agent, file_master, ticket_raiser, workflow_analyzer]
