"""The four IT-support agents for the handoff workflow (port of the POC handoff_workflow.py).

Each agent's `instructions` = shared HANDOFF_AGENT_RULES (reply discipline) +
a one-line identity. Cross-agent routing is owned by the framework via each
agent's keyword-rich `description`; tool selection is owned by each `@tool`'s
own description. Instructions therefore stay short and avoid duplicating either.
"""

from __future__ import annotations

from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.openai import OpenAIChatCompletionClient

from agents.middleware import (
    LlmTelemetryMiddleware,
    PrintLLMCallMiddleware,
    UserReplyCaptureMiddleware,
)
from tools.background_task import start_background_task
from tools.file_master import list_available_files, read_file, write_file
from tools.reply import send_reply_to_user
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

HOW THE USER HEARS FROM YOU — THE ONLY CHANNEL:
The user sees ONLY what you pass to the send_reply_to_user tool. Text you write in your normal reply
is NOT shown to the user (other specialists may read it, but the user never does). So for every
sub-task you finish, you MUST call send_reply_to_user once with the user-facing result.

TRACK THE WHOLE SCOPE OF THE REQUEST:
- Read the user's ORIGINAL request and list every distinct thing it asks for.
- The earlier assistant messages show what other specialists already reported (e.g. a note like
  "[file_master] Created custom.log."). Treat anything already reported as DONE — never redo it and
  never hand a finished part back to the agent that did it.

DO YOUR PART, THEN REPORT, THEN CHECK COMPLETION, THEN HAND OFF OR FINISH — in this order:
1. If a not-yet-done part is in YOUR specialty, do it with your tools. An approval-required tool is
   normal — just call it; the system pauses for the user automatically. (If you are missing a detail
   you NEED — e.g. a ticket subject/department the user never gave — call send_reply_to_user to ask
   ONE specific question and STOP; do NOT invent placeholders and do NOT hand off.)
2. IMMEDIATELY after your tool succeeds, call send_reply_to_user(message=...) with the ACTUAL result:
   the data requested (the list of workflows/files) or a one-line confirmation naming the artifact
   ("Created file custom.log."). Exactly ONE call per completed sub-task, BEFORE you hand off. This is
   absolute: the moment a tool returns — ESPECIALLY an approval-required tool that just resumed after
   the user approved it — your VERY NEXT action MUST be send_reply_to_user with that result. NEVER hand
   off (or do anything else) before you have reported the result you just got.
3. STOP AND CHECK COMPLETION before doing anything else. Walk every distinct part of the user's
   ORIGINAL request and mark each DONE if it is YOUR finished work OR is already reported in an
   earlier "[other_agent] …" note. If EVERY part is now DONE, you are finished: do NOT call any tool,
   do NOT hand off, and do NOT restate the results — just stop. (Handing off when nothing is left is
   what makes the agents bounce back and forth — never do it.)
4. Only if some part is STILL not done AND it is outside your specialty, call the
   handoff_to_<specialist> tool for the agent that owns that unfinished part — and hand off to that
   agent only.

NEVER narrate routing ("I'll hand off…", "Next I will…"). NEVER restate or re-list a result another
agent already reported in a note — that is THEIR reply, not yours; repeating it just spams the user.
NEVER call send_reply_to_user for work a tool of YOURS did not actually complete, and never speak for
another agent.
"""

def _role(line: str) -> str:
    """Build an agent's full instruction string = shared rules + per-agent identity."""
    return f"{HANDOFF_AGENT_RULES}\n{line}"


def _middleware(
    name: str, chat_conversation_id: int | None, user_message_id: int | None
) -> list:
    # UserReplyCaptureMiddleware captures this agent's send_reply_to_user calls; the handoff executor
    # surfaces each to BOTH the user (a reply) and the next agent (a context note). Fresh per agent;
    # not part of the workflow graph signature.
    mw: list = [PrintLLMCallMiddleware(name), UserReplyCaptureMiddleware()]
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
        tools=[send_reply_to_user],
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
        tools=[read_file, write_file, list_available_files, send_reply_to_user],
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
        tools=[check_existing_tickets, create_ticket, send_reply_to_user],
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
            "summary tools — never invent a workflowName. For a LONG-RUNNING "
            "engine run that will not finish in a few seconds, call "
            "start_background_task(task_type=..., summary=..., params=...) and "
            "then tell the user via send_reply_to_user that it has started — do "
            "NOT wait for it; the user will be notified when it completes."
        ),
        tools=[ae_mcp, start_background_task, send_reply_to_user],
        middleware=_middleware(
            WORKFLOW_ANALYZER, chat_conversation_id, user_message_id
        ),
        require_per_service_call_history_persistence=True,
    )

    return [triage_agent, file_master, ticket_raiser, workflow_analyzer]
