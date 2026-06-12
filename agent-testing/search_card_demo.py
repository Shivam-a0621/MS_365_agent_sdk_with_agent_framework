"""Prototype: Adaptive Card dynamic typeahead search (Teams) backed by the LIVE AutomationEdge MCP.

A single agent_framework Agent (wired with the real `ae_mcp` tool) handles chat, and the workflow
picker is a filtered Input.ChoiceSet whose choices come from the live MCP. Flow:
  1. message "workflows" / "search …"  -> send the filtered Input.ChoiceSet card
  2. invoke "application/search"        -> ae_mcp.list_workflows(nameContains=queryText) -> searchResponse
  3. Action.Submit (user picks one)     -> the single agent acts on that exact workflowName via the MCP
  4. any other message                  -> the single agent answers (it can call ae_mcp tools)

The search path is LLM-free (just the MCP call), so it works even without the Azure client. Dynamic
typeahead is a TEAMS feature; the invoke is still testable locally via curl (response is the HTTP body).

Run (from repo root):  PORT=3979 python agent-testing/search_card_demo.py
"""

import json
import os
import sys

# allow importing the app's agents/* and mcps/* when run as `python agent-testing/search_card_demo.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os import environ  # noqa: E402

from dotenv import load_dotenv  # noqa: E402
from aiohttp.web import Application, Request, Response, run_app  # noqa: E402

from agent_framework import Agent  # noqa: E402
from microsoft_agents.activity import (  # noqa: E402
    Activity,
    ActivityTypes,
    InvokeResponse,
    load_configuration_from_env,
)
from microsoft_agents.authentication.msal import MsalConnectionManager  # noqa: E402
from microsoft_agents.hosting.aiohttp import (  # noqa: E402
    CloudAdapter,
    jwt_authorization_middleware,
    start_agent_process,
)
from microsoft_agents.hosting.core import (  # noqa: E402
    AgentApplication,
    CardFactory,
    MemoryStorage,
    MessageFactory,
    TurnContext,
    TurnState,
)

from agents.client import build_chat_client  # noqa: E402
from mcps.sse_tool import build_ae_mcp  # noqa: E402

load_dotenv()
config = load_configuration_from_env(environ)

# --- SDK wiring ----------------------------------------------------------------------------------
STORAGE = MemoryStorage()
CONNECTION_MANAGER = MsalConnectionManager(**config)
ADAPTER = CloudAdapter(connection_manager=CONNECTION_MANAGER)
AGENT_APP = AgentApplication[TurnState](storage=STORAGE, adapter=ADAPTER, **config)

# --- the single agent + the live MCP tool --------------------------------------------------------
AE_MCP = build_ae_mcp()
ANALYST = Agent(
    client=build_chat_client(),
    name="workflow_analyst",
    instructions=(
        "You are an AutomationEdge analyst. Use the ae_mcp tools to answer questions about workflows, "
        "requests, agents, and schedules. Workflow tools need an EXACT workflowName — never invent one. "
        "If you need an exact workflowName the user has NOT given precisely, reply with ONLY the token "
        "[[PICK_WORKFLOW]] and nothing else — do NOT list workflows or ask in prose; the app will show "
        "the user a searchable picker and your next turn continues with the exact name they choose."
    ),
    tools=[AE_MCP],
)
_SESSIONS: dict[str, object] = {}  # conversation id -> agent session


async def _agent_reply(context: TurnContext, prompt: str) -> str:
    cid = context.activity.conversation.id
    session = _SESSIONS.get(cid) or ANALYST.create_session()
    _SESSIONS[cid] = session
    result = await ANALYST.run(prompt, session=session)
    return (getattr(result, "text", "") or "").strip() or "(no reply)"


async def _list_workflows(name_contains: str) -> list[dict]:
    """Live MCP search: list_workflows(nameContains=…) -> the workflows array."""
    fns = {getattr(f, "name", ""): f for f in (AE_MCP.functions or [])}
    res = await fns["list_workflows"].invoke(arguments={"nameContains": name_contains, "size": 15})
    items = res if isinstance(res, list) else [res]
    text = next((getattr(c, "text", None) for c in items if getattr(c, "text", None)), "{}")
    return json.loads(text).get("workflows", [])


# --- the dynamic-search card ---------------------------------------------------------------------
def workflow_search_card():
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": [
            {"type": "TextBlock", "text": "Search workflows", "weight": "Bolder", "size": "Medium"},
            {
                "type": "Input.ChoiceSet",
                "id": "workflowChoice",
                "label": "Workflow",
                "style": "filtered",
                "placeholder": "Type to search…",
                "choices.data": {"type": "Data.Query", "dataset": "workflows"},
            },
        ],
        "actions": [{"type": "Action.Submit", "title": "Select"}],
    }
    return MessageFactory.attachment(CardFactory.adaptive_card(card))


@AGENT_APP.conversation_update("membersAdded")
async def on_members_added(context: TurnContext, _state: TurnState):
    for member in context.activity.members_added:
        if member.id != context.activity.recipient.id:
            await context.send_activity("Ask me about AutomationEdge, or type 'workflows' to pick one.")


@AGENT_APP.activity("message")
async def on_message(context: TurnContext, _state: TurnState):
    print(
        f"[MSG] channel={context.activity.channel_id!r} text={(context.activity.text or '')!r} "
        f"value={context.activity.value!r}",
        flush=True,
    )
    # 1. Action.Submit: the picked workflow comes back as a message with activity.value.
    if context.activity.value:
        choice = context.activity.value.get("workflowChoice")
        await context.send_activity(f"Selected workflow: {choice}. Looking it up…")
        reply = await _agent_reply(
            context, f"Give a short summary of the workflow '{choice}' and its most recent requests."
        )
        await context.send_activity(reply)
        return

    text = (context.activity.text or "").strip()
    low = text.lower()
    # 2. show the search card on an explicit trigger.
    if low == "workflows" or low.startswith("search"):
        await context.send_activity(workflow_search_card())
        return
    # 3. otherwise the single agent answers (it can call ae_mcp tools). When it needs an exact
    #    workflowName it doesn't have, it emits the [[PICK_WORKFLOW]] token -> show the search card.
    reply = await _agent_reply(context, text)
    if "[[PICK_WORKFLOW]]" in reply:
        await context.send_activity(workflow_search_card())
    else:
        await context.send_activity(reply)


async def on_search(context: TurnContext, _state: TurnState):
    """application/search invoke -> live MCP list_workflows -> searchResponse (the HTTP body)."""
    query = (context.activity.value or {}).get("queryText", "") or ""
    print(f"[INVOKE] application/search  queryText={query!r}", flush=True)
    try:
        workflows = await _list_workflows(query)
    except Exception as e:  # noqa: BLE001
        print(f"[INVOKE] list_workflows FAILED: {type(e).__name__}: {e}", flush=True)
        workflows = []
    results = [{"value": w["name"], "title": w["name"]} for w in workflows][:15]
    print(f"[INVOKE] returning {len(results)} results: {[r['title'] for r in results][:5]}", flush=True)
    body = {"type": "application/vnd.microsoft.search.searchResponse", "value": {"results": results}}
    await context.send_activity(
        Activity(type=ActivityTypes.invoke_response, value=InvokeResponse(status=200, body=body))
    )


@AGENT_APP.activity("invoke")
async def on_any_invoke(context: TurnContext, _state: TurnState):
    # Diagnostic: catches invokes whose name is NOT application/search (that one is handled above,
    # at higher priority via is_invoke=True). If you type in the card and see NOTHING here AND no
    # [INVOKE] line, Teams isn't sending the invoke to this endpoint at all (manifest/tunnel issue).
    print(f"[INVOKE-OTHER] name={context.activity.name!r} value={context.activity.value!r}", flush=True)


AGENT_APP.add_route(
    lambda ctx: ctx.activity.type == ActivityTypes.invoke
    and ctx.activity.name == "application/search",
    on_search,
    is_invoke=True,
)


# --- host --------------------------------------------------------------------------------------
async def entry_point(req: Request) -> Response:
    return await start_agent_process(req, req.app["agent_app"], req.app["adapter"])


async def _connect_mcp(_app):
    await AE_MCP.connect()


async def _close_mcp(_app):
    try:
        await AE_MCP.close()
    except Exception:  # noqa: BLE001
        pass


AUTH_CONFIG = CONNECTION_MANAGER.get_default_connection_configuration()
AUTH_CONFIG.ANONYMOUS_ALLOWED = True  # local testing: tokenless curl can exercise the invoke

APP = Application(middlewares=[jwt_authorization_middleware])
APP.router.add_post("/api/messages", entry_point)
APP["agent_configuration"] = AUTH_CONFIG
APP["agent_app"] = AGENT_APP
APP["adapter"] = ADAPTER
APP.on_startup.append(_connect_mcp)
APP.on_cleanup.append(_close_mcp)

if __name__ == "__main__":
    run_app(APP, host="localhost", port=int(environ.get("PORT", 3979)))
