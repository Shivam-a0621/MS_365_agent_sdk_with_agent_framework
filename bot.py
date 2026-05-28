"""Microsoft 365 Agents SDK layer.

The SDK is AI-agnostic plumbing: it normalizes channel messages into an Activity,
routes them to handlers, manages turn/conversation state, and handles auth. The
``on_message`` handler delegates to the WorkflowManager, which runs the agentic
handoff workflow (Microsoft Agent Framework) and renders replies / approval cards.
"""

from __future__ import annotations

import logging
from os import environ

from dotenv import load_dotenv
from microsoft_agents.activity import load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.aiohttp import CloudAdapter
from microsoft_agents.hosting.core import (
    AgentApplication,
    TurnContext,
    TurnState,
)

from cards import approval_card
from db.storage import PostgresStorage
from services.conversation_service import handle_message

load_dotenv()
logger = logging.getLogger("app.bot")

# Reads CONNECTIONS__SERVICE_CONNECTION__SETTINGS__{CLIENTID,CLIENTSECRET,TENANTID}.
_config = load_configuration_from_env(environ)

# SDK state store. Postgres-backed (agent_store) so TurnState survives restarts;
# swap for a Redis-backed Storage here when needed (same SDK Storage interface).
STORAGE = PostgresStorage()
CONNECTION_MANAGER = MsalConnectionManager(**_config)
ADAPTER = CloudAdapter(connection_manager=CONNECTION_MANAGER)
AGENT_APP = AgentApplication[TurnState](
    storage=STORAGE, connection_manager=CONNECTION_MANAGER
)


def _resolve_auth_config():
    """The auth config used to validate inbound Bot Connector JWTs.

    Returns None when no bot credentials are configured yet, so the app still boots
    locally. With real credentials set,
    inbound requests must carry a valid token.
    """
    try:
        return CONNECTION_MANAGER.get_default_connection_configuration()
    except Exception as exc:  # noqa: BLE001 - no creds yet; boot anyway
        logger.warning("No bot auth configuration resolved (%s); running open.", exc)
        return None


AUTH_CONFIG = _resolve_auth_config()


def _activity_to_dict(activity) -> dict:
    """Best-effort serialize an SDK Activity to a JSON-friendly dict.

    Three fallbacks because the SDK's Activity class may use different model
    libraries across versions:
      1. pydantic v2 -> ``model_dump(mode="json", exclude_none=True)``,
      2. pydantic v1 -> ``.dict()``,
      3. plain class -> ``.to_dict()``.

    If none works we synthesize a minimal dict from the attributes we know
    exist (``type`` / ``text`` / ``value``). Goal: keep the
    ``aistudiobot_chathistory.activity`` JSONB column durable across SDK upgrades.
    """
    for method, kwargs in (
        ("model_dump", {"mode": "json", "exclude_none": True}),
        ("dict", {}),
        ("to_dict", {}),
    ):
        fn = getattr(activity, method, None)
        if not callable(fn):
            continue
        try:
            result = fn(**kwargs) if kwargs else fn()
        except TypeError:
            try:
                result = fn()
            except Exception:  # noqa: BLE001
                continue
        if isinstance(result, dict):
            return result
    return {
        "type": getattr(activity, "type", None),
        "text": getattr(activity, "text", None),
        "value": getattr(activity, "value", None),
    }


@AGENT_APP.activity("message")
async def on_message(context: TurnContext, state: TurnState) -> None:
    activity = context.activity
    result = await handle_message(
        channel=activity.channel_id or "msteams",
        conversation_ref=activity.conversation.id,
        user_id=getattr(activity.from_property, "id", "") or "",
        user_name=getattr(activity.from_property, "name", "") or "",
        text=(activity.text or "").strip(),
        value=activity.value,
        activity=_activity_to_dict(activity),
    )
    for reply in result.replies:
        await context.send_activity(reply)
    for prompt in result.prompts:
        await context.send_activity(prompt)
    for approval in result.approvals:
        await context.send_activity(
            approval_card(
                approval.request_id, approval.function_name, approval.arguments
            )
        )
    if not (result.replies or result.prompts or result.approvals):
        await context.send_activity("(no reply)")


@AGENT_APP.conversation_update("membersAdded")
async def on_members_added(context: TurnContext, state: TurnState) -> None:
    for member in context.activity.members_added or []:
        if member.id != context.activity.recipient.id:
            await context.send_activity(
                "Hi! I'm IT support. Ask a question or report an issue."
            )


@AGENT_APP.error
async def on_error(context: TurnContext, error: Exception) -> None:
    logger.exception("Unhandled error during turn: %s", error)
    try:
        await context.send_activity("Sorry, something went wrong this turn.")
    except Exception:  # noqa: BLE001
        pass
