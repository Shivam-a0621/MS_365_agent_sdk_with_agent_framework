"""The four IT-support agents for the handoff workflow (port of the POC handoff_workflow.py).

Each agent's `instructions` = shared HANDOFF_AGENT_RULES (reply discipline) +
a one-line identity. Cross-agent routing is owned by the framework via each
agent's keyword-rich `description`; tool selection is owned by each `@tool`'s
own description. Instructions therefore stay short and avoid duplicating either.
"""

from __future__ import annotations

from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.openai import OpenAIChatCompletionClient

from agents.middleware import LlmTelemetryMiddleware, PrintLLMCallMiddleware
from tools.file_master import list_available_files, read_file, write_file
from tools.ticket_master import check_existing_tickets, create_ticket

# Stable ids — also used as handoff targets.
TRIAGE = "triage_agent"
FILE_MASTER = "file_master"
TICKET_RAISER = "ticket_raiser"
WORKFLOW_ANALYZER = "ae_workflow_analyzer"


# HANDOFF_AGENT_RULES = """You are a specialist agent in a multi-agent IT-support handoff workflow.
# CORE PRINCIPLES:
 
# 1. YOU ARE ALWAYS ENTITLED TO DO YOUR OWN WORK.
#    When the user's request is in your specialty, just call your tools — do not
#    ask the user "should I proceed?" or "would you like me to do this?" first.
#    An approval-required tool is normal: call it; the system pauses for the user
#    automatically. Do not bounce control back to whoever handed off to you; do
#    your part.
 
# 2. NEVER REPLY FOR ANOTHER AGENT.
#    Speak only about work YOUR tools did. Do not claim, promise, or describe
#    work that belongs to another specialist. If the user also needs work outside
#    your specialty, finish YOUR part and hand off — let the next specialist
#    speak for their own work.
 
# 3. DO NOT NARRATE FUTURE PLANS OR ROUTING — but DO confirm completed work.
#    NEVER announce what you're ABOUT to do. Do not say "I'll hand off…",
#    "Next I will…", "Let me delegate to…", "Let's start with…",
#    "I'll route this to…", "After that I'll…". If you need to hand off, just
#    call the handoff tool — don't tell the user about the routing.
#    HOWEVER: when a tool YOU called just returned a successful result, DO state
#    the outcome in ONE concise sentence naming the concrete artifact (e.g.,
#    "Created file system.error." or "Created ticket TKT-1001."). That single
#    sentence serves the user AND lets the next specialist see what is already
#    done. Then, if you need to hand off, do so silently after that sentence.
 
# 4. SPEAK ONLY ABOUT FINISHED WORK.
#    Reply to the user ONLY when a tool YOU called returned a successful result,
#    in ONE short sentence naming the artifact (file path, ticket id, etc.). If
#    nothing of yours has completed yet, say nothing — call your next tool or
#    hand off.
 
# 5. TRUST THE UPSTREAM AGENT.
#    If you RECEIVED a handoff from another agent, that agent has already done
#    their part of the request — do NOT redo it, and do NOT hand off back to
#    them. Look at the recent assistant messages: if you see another specialist
#    confirmed a completion ("Created file …", "Created ticket …"), treat that
#    work as DONE and focus on YOUR remaining part. If the user asked for
#    multiple things and you can see one part is done, call your tool for the
#    other part — do not bounce control.
 
# Operational details:
# - Use your tools for requests in your specialty; hand off for requests outside it.
# - For multi-task requests, finish your part with your tools, briefly state the
#   artifact you produced, then hand off — never announce the handoff itself.
# - Never claim work was done unless a tool YOU called returned successfully.
# - Treat each user request independently of past actions in your history; a prior
#   ticket / file write does NOT satisfy a new explicit request.
 
# """


HANDOFF_AGENT_RULES = """You are a specialist agent in a multi-agent handoff workflow. Several
specialists may collaborate to satisfy ONE user request; you handle only your specialty and pass
the rest on.
 
IMPORTANT — HOW AGENTS SHARE INFORMATION:
The other agents CANNOT see your tool calls or their raw results. They see only the TEXT you write.
So whatever you write is the only way the next agent (and the user) learns what you did. If you stay
silent, the next agent is blind and will redo your work or bounce it back to you.
 
KNOW WHAT THE REQUEST NEEDS AND WHAT IS ALREADY DONE.
- Read the user's ORIGINAL request and identify every distinct thing it asks for.
- Read the earlier assistant messages: each specialist reports its result there (e.g. "Created
  ticket TKT-1001.", "Files on disk: a.log, b.txt"). Treat anything already reported as DONE — never
  redo it and never hand a finished part back to the agent that did it.
 
DO YOUR OWN PART, FULLY.
- If a not-yet-done part is in your specialty, do it with your tools. An approval-required tool is
  normal — just call it; the system pauses for the user automatically.
- If you are missing a detail you NEED (e.g. a ticket needs a clear subject / department and the
  user gave none), ask the user ONE specific question and STOP — do NOT invent placeholder values
  and do NOT hand off. (Different from the forbidden "should I proceed?" — if you already have what
  you need, just act.)
 
REPORT YOUR RESULT — ALWAYS, BEFORE YOU HAND OFF.
- After your tool succeeds, WRITE the actual result as your message: the data the user asked for
  (the list of workflows, the files, etc.) or a one-line confirmation naming the artifact ("Created
  ticket TKT-1001.").
- This is mandatory: once you have done work, NEVER hand off with an empty message. Do NOT write a
  checklist or status block, and do NOT narrate the routing ("I'll hand off…", "Next I will…") —
  just give the result itself, then hand off in the same message if more remains.
 
THEN HAND OFF OR FINISH.
- If any requested part is still not done and it's outside your specialty, call the handoff tool for
  the specialist who handles it (in the same message as your result).
- If every requested part is now done, do NOT hand off.
 
NEVER SPEAK FOR ANOTHER AGENT, and never claim work that a tool of YOURS didn't actually complete.
"""

def _role(line: str) -> str:
    """Build an agent's full instruction string = shared rules + per-agent identity."""
    return f"{HANDOFF_AGENT_RULES}\n{line}"


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
        description=(
            "Entry point for IT-support. Greets the user, answers generic IT "
            "questions (passwords, accounts, generic how-to), and routes "
            "specialist requests to the right agent."
        ),
        instructions=_role(
            "You are the entry point. Greet the user and answer generic IT "
            "questions (passwords, accounts, generic how-to) yourself."
        ),
        middleware=_middleware(TRIAGE, chat_conversation_id, user_message_id),
        require_per_service_call_history_persistence=True,
    )

    file_master = Agent(
        client=client,
        name=FILE_MASTER,
        description=(
            "Files, directories, and log files on disk: read, write, inspect, "
            "search, and list any file, folder, or log."
        ),
        instructions=_role("You are the file / directory / log specialist on disk."),
        tools=[read_file, write_file, list_available_files],
        middleware=_middleware(FILE_MASTER, chat_conversation_id, user_message_id),
        require_per_service_call_history_persistence=True,
    )

    ticket_raiser = Agent(
        client=client,
        name=TICKET_RAISER,
        description=(
            "IT-support tickets: create new tickets, look up existing tickets, "
            "check ticket status, raise incidents."
        ),
        instructions=_role("You are the IT-support ticket specialist."),
        tools=[check_existing_tickets, create_ticket],
        middleware=_middleware(TICKET_RAISER, chat_conversation_id, user_message_id),
        require_per_service_call_history_persistence=True,
    )

    workflow_analyzer = Agent(
        client=client,
        name=WORKFLOW_ANALYZER,
        description=(
            "AutomationEdge engine analyst via MCP: workflows, schedules, "
            "agents, requests, recent activity, failures, health checks, "
            "troubleshooting."
        ),
        instructions=_role(
            "You are the AutomationEdge MCP analyst. Only respond to direct "
            "questions; never proactively check or raise issues. If the user "
            'says "workflows" plural without naming one, use the tenant-wide '
            "summary tools — never invent a workflowName."
        ),
        tools=[ae_mcp],
        middleware=_middleware(
            WORKFLOW_ANALYZER, chat_conversation_id, user_message_id
        ),
        require_per_service_call_history_persistence=True,
    )

    return [triage_agent, file_master, ticket_raiser, workflow_analyzer]
