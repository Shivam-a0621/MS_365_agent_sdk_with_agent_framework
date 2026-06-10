# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""A very simple Microsoft 365 Agents SDK echo agent — the sample's structure, stripped to the
essentials: SDK wiring, a welcome on join, an echo on message, hosted on the canonical
aiohttp + start_agent_process + jwt_authorization_middleware path.

Run:  PORT=3978 python simple_agent.py
Test: point the Bot Framework Emulator at http://localhost:3978/api/messages
"""

import json
from os import environ

from dotenv import load_dotenv
from aiohttp.web import Application, Request, Response, run_app

from microsoft_agents.activity import load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.aiohttp import (
    CloudAdapter,
    jwt_authorization_middleware,
    start_agent_process,
)
from microsoft_agents.hosting.core import (
    AgentApplication,
    MemoryStorage,
    TurnContext,
    TurnState,
)

load_dotenv()
config = load_configuration_from_env(environ)

class PrintAdapter(CloudAdapter):
    """CloudAdapter that prints each reply and the serviceUrl it is being POSTed to."""

    async def send_activities(self, context: TurnContext, activities):
        service_url = context.activity.service_url
        for a in activities:
            print(f"  [2] REPLY  -> POST {service_url}  : {a.type} {getattr(a, 'text', None)!r}")
        try:
            return await super().send_activities(context, activities)
        except Exception as e:  # so a missing serviceUrl (e.g. a curl test) prints instead of crashing
            print(f"      (send to serviceUrl failed: {type(e).__name__}: {e})")
            from microsoft_agents.activity import ResourceResponse

            return [ResourceResponse(id=str(i)) for i in range(len(activities))]


STORAGE = MemoryStorage()
CONNECTION_MANAGER = MsalConnectionManager(**config)
ADAPTER = PrintAdapter(connection_manager=CONNECTION_MANAGER)
AGENT_APP = AgentApplication[TurnState](storage=STORAGE, adapter=ADAPTER, **config)


@AGENT_APP.conversation_update("membersAdded")
async def on_members_added(context: TurnContext, _state: TurnState):
    for member in context.activity.members_added:
        if member.id != context.activity.recipient.id:
            await context.send_activity("Hello! I'm a simple echo agent.")


@AGENT_APP.activity("message")
async def on_message(context: TurnContext, _state: TurnState):
    await context.send_activity(f"You said: {context.activity.text}")


async def entry_point(req: Request) -> Response:
    try:
        body = await req.json()
    except Exception:
        body = None
    print("\n[1] REQUEST  POST /api/messages")
    print(json.dumps(body, indent=2))
    # start_agent_process runs the whole turn (which sends the reply to serviceUrl, see [2]) and
    # THEN returns the HTTP response — so in this SDK the reply goes out BEFORE this 200 ack.
    resp = await start_agent_process(req, req.app["agent_app"], req.app["adapter"])
    print(f"[3] ACK      HTTP {resp.status} returned to the channel\n")
    return resp


APP = Application(middlewares=[jwt_authorization_middleware])
APP.router.add_post("/api/messages", entry_point)
APP["agent_configuration"] = CONNECTION_MANAGER.get_default_connection_configuration()
APP["agent_app"] = AGENT_APP
APP["adapter"] = ADAPTER

if __name__ == "__main__":
    run_app(APP, host="localhost", port=int(environ.get("PORT", 3979)))
