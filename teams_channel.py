"""FastAPI <-> Microsoft 365 Agents SDK glue.

The SDK ships only an aiohttp host. Its `CloudAdapter.process` simply wraps the
request and calls `process_request(adapted_request, agent)` (defined on the
transport-agnostic `HttpAdapterBase`). So we host the bot inside FastAPI with a
tiny request-adapter shim — no aiohttp server needed.

Inbound Bot Connector JWTs are validated here (mirroring the SDK's aiohttp
`jwt_authorization_middleware`) before the activity is processed.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from microsoft_agents.hosting.core.authorization import (
    ClaimsIdentity,
    JwtTokenValidator,
)

from bot import ADAPTER, AGENT_APP, AUTH_CONFIG

logger = logging.getLogger("app.teams")
router = APIRouter(tags=["teams"])


class FastAPIRequestAdapter:
    """Adapts a FastAPI request to the SDK's HttpRequestProtocol.

    (FastAPI's Request is a starlette.requests.Request; this mirrors the SDK's own
    aiohttp ``AiohttpRequestAdapter`` for the FastAPI/ASGI side.)
    """

    def __init__(self, request: Request, claims_identity: ClaimsIdentity) -> None:
        self._request = request
        self._claims = claims_identity

    @property
    def method(self) -> str:
        return self._request.method

    @property
    def headers(self) -> Any:
        return self._request.headers

    async def json(self) -> Any:
        return await self._request.json()

    def get_claims_identity(self) -> ClaimsIdentity:
        return self._claims

    def get_path_param(self, name: str) -> str:
        return self._request.path_params[name]


async def _authenticate(request: Request) -> ClaimsIdentity:
    """Validate the inbound Bot Connector JWT and return a ClaimsIdentity.

    - With auth configured: a missing or invalid token raises ``HTTPException(401)``.
    - With NO auth configured (no bot credentials yet, local/dev): returns an
      anonymous identity so the app still boots and can be exercised by spike
      scripts or REST callers without a JWT.
    """
    validator = JwtTokenValidator(AUTH_CONFIG) if AUTH_CONFIG is not None else None
    auth_header = request.headers.get("Authorization")
    client_id = getattr(AUTH_CONFIG, "CLIENT_ID", None)

    if auth_header and validator is not None:
        token = auth_header.split(" ", 1)[1] if " " in auth_header else auth_header
        try:
            return await validator.validate_token(token)
        except Exception as exc:  # noqa: BLE001 - any validation failure is a 401, not a 500
            logger.warning("JWT validation failed: %s: %s", type(exc).__name__, exc)
            raise HTTPException(
                status_code=401, detail="Invalid or unverifiable token"
            ) from exc

    # No Authorization header.
    if client_id:
        raise HTTPException(status_code=401, detail="Authorization header not found")
    # No bot credentials configured yet -> allow anonymous (local/dev).
    return validator.get_anonymous_claims() if validator else ClaimsIdentity({}, False)


def _to_response(http_response: Any) -> Response:
    headers = http_response.headers or None
    if http_response.body is not None:
        return JSONResponse(
            content=http_response.body,
            status_code=http_response.status_code,
            headers=headers,
        )
    return Response(status_code=http_response.status_code, headers=headers)


@router.post("/api/messages")
async def handle_messages(request: Request) -> Response:
    """POST /api/messages — the Bot Connector entry point Teams hits with each Activity."""
    claims = await _authenticate(request)
    adapter_request = FastAPIRequestAdapter(request, claims_identity=claims)
    http_response = await ADAPTER.process_request(adapter_request, AGENT_APP)
    return _to_response(http_response)
