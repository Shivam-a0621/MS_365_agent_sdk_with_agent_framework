"""Minimal echo bot on the Microsoft 365 Agents SDK (aiohttp). UI shows request + response."""

from os import environ

from aiohttp import web
from dotenv import load_dotenv

from microsoft_agents.activity import Activity, ResourceResponse, load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.aiohttp import CloudAdapter
from microsoft_agents.hosting.core import AgentApplication, MemoryStorage, TurnContext, TurnState

load_dotenv()

CONNECTION = MsalConnectionManager(**load_configuration_from_env(dict(environ)))
AGENT = AgentApplication[TurnState](storage=MemoryStorage(), connection_manager=CONNECTION)


@AGENT.activity("message")
async def on_message(context: TurnContext, state: TurnState) -> None:
    await context.send_activity(f"Echo: {context.activity.text}")


class CaptureAdapter(CloudAdapter):
    async def send_activities(self, context, activities):
        context.turn_state.setdefault("replies", []).extend(activities)
        return [ResourceResponse(id=str(i)) for i in range(len(activities))]


ADAPTER = CaptureAdapter(connection_manager=CONNECTION)


async def messages(request: web.Request) -> web.Response:
    body = await request.json()
    inbound = {
        "type": "message",
        "text": body.get("text", ""),
        "channelId": "webchat",
        "serviceUrl": "http://localhost",
        "id": "1",
        "conversation": {"id": "1"},
        "from": {"id": "user"},
        "recipient": {"id": "bot"},
    }
    replies: list[Activity] = []

    async def turn(context: TurnContext) -> None:
        await AGENT.on_turn(context)
        replies.extend(context.turn_state.get("replies", []))

    await ADAPTER.process_activity(ADAPTER.create_claims_identity(), Activity.model_validate(inbound), turn)
    return web.json_response({
        "request": inbound,
        "response": [a.model_dump(mode="json", exclude_none=True, by_alias=True) for a in replies],
    })


async def index(request: web.Request) -> web.Response:
    return web.Response(text=PAGE, content_type="text/html")


PAGE = """<!doctype html>
<title>Echo</title>
<style>
  body { font: 14px system-ui, sans-serif; display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 16px; }
  input { width: 100%; padding: 8px; }
  pre { background: #f4f4f4; padding: 12px; overflow: auto; }
</style>
<div>
  <h3>Echo bot</h3>
  <input id="t" placeholder="Type a message and press Enter" autofocus />
  <h4>Request</h4><pre id="req"></pre>
</div>
<div><h4>Response</h4><pre id="res"></pre></div>
<script>
  const t = document.getElementById("t");
  t.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter") return;
    const r = await fetch("/api/messages", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: t.value }),
    });
    const data = await r.json();
    document.getElementById("req").textContent = JSON.stringify(data.request, null, 2);
    document.getElementById("res").textContent = JSON.stringify(data.response, null, 2);
    t.value = "";
  });
</script>"""


app = web.Application()
app.router.add_get("/", index)
app.router.add_post("/api/messages", messages)

if __name__ == "__main__":
    web.run_app(app, host="localhost", port=3979)
