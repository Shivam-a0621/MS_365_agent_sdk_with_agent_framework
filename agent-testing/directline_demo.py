"""Local Direct Line demo — watch the WebChat <-> Direct Line <-> bot request/response cycle.

One aiohttp app plays two roles so it runs with no Azure:
  • the BOT  — the Microsoft 365 Agents SDK echo bot (greets on join, echoes messages).
  • DIRECT LINE — the channel: the real Direct Line REST shape (start a conversation) plus a
    WebSocket the browser keeps open to receive pushed replies.

The browser client is hand-rolled (not the polished Web Chat widget) on purpose, so every REST
request/response and every WebSocket frame is shown raw. Maps 1:1 to the sequence diagram:
  POST /v3/directline/conversations            -> { conversationId, streamUrl }   (REST)
  GET  .../stream            (HTTP Upgrade)    -> WebSocket; greeting pushed down it
  POST .../activities  "Hi"                    -> { id }                          (REST, 200 OK)
  bot reply "Echo: Hi"                         -> pushed to the browser over the WebSocket

Run:  python directline_demo.py     then open  http://localhost:3979/
"""

from os import environ

from aiohttp import web
from dotenv import load_dotenv

from microsoft_agents.activity import Activity, ResourceResponse, load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.aiohttp import CloudAdapter
from microsoft_agents.hosting.core import AgentApplication, MemoryStorage, TurnContext, TurnState

load_dotenv()

# --- the bot (SDK) ---------------------------------------------------------------------------
CONNECTION = MsalConnectionManager(**load_configuration_from_env(dict(environ)))
AGENT = AgentApplication[TurnState](storage=MemoryStorage(), connection_manager=CONNECTION)


@AGENT.activity("message")
async def on_message(context: TurnContext, state: TurnState) -> None:
    await context.send_activity(f"Echo: {context.activity.text}")


@AGENT.conversation_update("membersAdded")
async def on_join(context: TurnContext, state: TurnState) -> None:
    for m in context.activity.members_added or []:
        if m.id != context.activity.recipient.id:
            await context.send_activity("Hi! I'm an echo bot — say something.")


class CaptureAdapter(CloudAdapter):
    async def send_activities(self, context, activities):
        context.turn_state.setdefault("replies", []).extend(activities)
        return [ResourceResponse(id=str(i)) for i in range(len(activities))]


ADAPTER = CaptureAdapter(connection_manager=CONNECTION)


async def run_bot(conversation_id: str, inbound: dict) -> list[dict]:
    """Forward an inbound Activity to the bot (as Direct Line would) and capture its replies."""
    inbound = {
        "channelId": "directline",
        "serviceUrl": "http://localhost",
        "conversation": {"id": conversation_id},
        "from": {"id": "user"},
        "recipient": {"id": "bot"},
        **inbound,
    }
    replies: list[Activity] = []

    async def turn(context: TurnContext) -> None:
        await AGENT.on_turn(context)
        replies.extend(context.turn_state.get("replies", []))

    await ADAPTER.process_activity(ADAPTER.create_claims_identity(), Activity.model_validate(inbound), turn)
    return [
        a.model_dump(mode="json", exclude_none=True, by_alias=True)
        for a in replies
        if getattr(a, "type", None) == "message"
    ]


# --- Direct Line channel ---------------------------------------------------------------------
CONVERSATIONS: dict[str, dict] = {}  # id -> {"ws": WebSocketResponse|None, "queue": [activities]}


async def push(conversation_id: str, activities: list[dict]) -> None:
    """Send activities to the browser over its WebSocket, or queue them until it connects."""
    conv = CONVERSATIONS[conversation_id]
    ws = conv["ws"]
    if ws is not None and not ws.closed:
        await ws.send_json({"activities": activities, "watermark": str(len(activities))})
    else:
        conv["queue"].extend(activities)


async def start_conversation(request: web.Request) -> web.Response:
    """POST /v3/directline/conversations -> conversationId + WebSocket streamUrl (the REST start)."""
    conversation_id = f"dl-{len(CONVERSATIONS) + 1}"
    CONVERSATIONS[conversation_id] = {"ws": None, "queue": []}
    host = request.host
    stream_url = f"ws://{host}/v3/directline/conversations/{conversation_id}/stream"
    # The channel notifies the bot a user joined; the greeting is queued until the WS connects.
    greeting = await run_bot(conversation_id, {"type": "conversationUpdate", "membersAdded": [{"id": "user"}]})
    await push(conversation_id, greeting)
    return web.json_response(
        {"conversationId": conversation_id, "token": "demo-token", "streamUrl": stream_url, "expires_in": 3600}
    )


async def stream(request: web.Request) -> web.WebSocketResponse:
    """GET .../stream — HTTP Upgrade to a WebSocket; the persistent pipe for pushed replies."""
    conversation_id = request.match_info["id"]
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    conv = CONVERSATIONS.setdefault(conversation_id, {"ws": None, "queue": []})
    conv["ws"] = ws
    if conv["queue"]:  # flush anything produced before the socket existed (the greeting)
        await ws.send_json({"activities": conv["queue"], "watermark": str(len(conv["queue"]))})
        conv["queue"] = []
    async for _ in ws:  # keep the connection open; Direct Line clients don't send over it
        pass
    conv["ws"] = None
    return ws


async def post_activity(request: web.Request) -> web.Response:
    """POST .../activities — the user's message (REST). Returns {id} (200 OK); the bot's reply is
    NOT in this response — it is pushed separately over the WebSocket."""
    conversation_id = request.match_info["id"]
    activity = await request.json()
    replies = await run_bot(conversation_id, {"type": "message", "text": activity.get("text", "")})
    await push(conversation_id, replies)
    return web.json_response({"id": f"{conversation_id}|0001"})


async def index(request: web.Request) -> web.Response:
    return web.Response(text=PAGE, content_type="text/html")


PAGE = """<!doctype html>
<html><head><meta charset="utf-8" /><title>Direct Line demo</title>
<style>
  body { font: 13px system-ui, sans-serif; margin: 0; display: grid;
         grid-template-columns: 1fr 1fr; height: 100vh; }
  #left { display: flex; flex-direction: column; border-right: 1px solid #ccc; }
  #log { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 6px; }
  .b { max-width: 80%; padding: 6px 10px; border-radius: 10px; }
  .me { align-self: flex-end; background: #2563eb; color: #fff; }
  .bot { align-self: flex-start; background: #eee; }
  form { display: flex; gap: 6px; padding: 10px; border-top: 1px solid #ccc; }
  input { flex: 1; padding: 8px; }
  #right { overflow: auto; padding: 12px; }
  h4 { margin: 12px 0 4px; }
  pre { background: #f4f4f4; padding: 8px; white-space: pre-wrap; word-break: break-all; }
</style></head>
<body>
  <div id="left">
    <div id="log"></div>
    <form id="f"><input id="t" placeholder="Type and press Enter" autofocus /><button>Send</button></form>
  </div>
  <div id="right">
    <h4>1 ─ REST: POST /v3/directline/conversations</h4><pre id="start"></pre>
    <h4>2 ─ WebSocket: frames pushed from Direct Line</h4><pre id="ws"></pre>
    <h4>3 ─ REST: POST .../activities (request → 200 OK)</h4><pre id="post"></pre>
  </div>
<script>
const log = document.getElementById("log");
function bubble(text, who){ const d=document.createElement("div"); d.className="b "+who;
  d.textContent=text; log.appendChild(d); log.scrollTop=log.scrollHeight; }

let conversationId, socket;
(async () => {
  const r = await fetch("/v3/directline/conversations", { method:"POST" });
  const conv = await r.json();
  conversationId = conv.conversationId;
  document.getElementById("start").textContent =
    "REQUEST  POST /v3/directline/conversations\\n\\nRESPONSE " + JSON.stringify(conv, null, 2);
  socket = new WebSocket(conv.streamUrl);
  socket.onmessage = (e) => {
    document.getElementById("ws").textContent = e.data;          // raw frame
    const set = JSON.parse(e.data);
    for (const a of set.activities || []) if (a.text) bubble(a.text, "bot");
  };
})();

document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const t = document.getElementById("t");
  const text = t.value.trim(); if(!text) return; t.value="";
  bubble(text, "me");
  const body = { type:"message", from:{id:"user"}, text };
  const r = await fetch(`/v3/directline/conversations/${conversationId}/activities`,
    { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body) });
  const resp = await r.json();
  document.getElementById("post").textContent =
    "REQUEST  POST .../activities\\n" + JSON.stringify(body, null, 2) +
    "\\n\\nRESPONSE 200 OK  " + JSON.stringify(resp) +
    "\\n(note: the reply is NOT here — it arrives over the WebSocket above)";
});
</script>
</body></html>"""


app = web.Application()
app.router.add_get("/", index)
app.router.add_post("/v3/directline/conversations", start_conversation)
app.router.add_get("/v3/directline/conversations/{id}/stream", stream)
app.router.add_post("/v3/directline/conversations/{id}/activities", post_activity)

if __name__ == "__main__":
    web.run_app(app, host="localhost", port=3979)
