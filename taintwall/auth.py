"""Authentication as pure ASGI middleware: every HTTP request needs a valid grant token (D29).

Rejected requests get the same 401 whatever the reason, so a response never reveals whether a
token exists or has expired. The specific reason goes to the aggregated audit (D30). No token,
token fragment, hash or header value is ever logged or recorded, and there is deliberately no
per-request log line here: a client without a token must not control log volume.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import datetime
from typing import Any
from urllib.parse import unquote_to_bytes

from taintwall.audit.rejections import RejectionAggregator
from taintwall.errors import AGENT_TEXT, AUTH_ERROR_CODE, INTERNAL_ERROR_CODE, Outcome
from taintwall.grants.store import TOKEN_SHAPE_RE, Grant, GrantStore, is_well_formed, utc_now

logger = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

GRANT_STATE_KEY = "taintwall_grant"
HEADER = b"authorization"
SCHEME = "bearer"


def _error_body(code: int, message: str) -> bytes:
    # A JSON-RPC body reaches the agent intact; the SDK client turns a plain-text 401 into a
    # misleading generic -32603 (probe, M1-4 plan).
    payload = {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}
    return json.dumps(payload).encode()


UNAUTHORIZED_BODY = _error_body(AUTH_ERROR_CODE, AGENT_TEXT[Outcome.UNAUTHORIZED])
INTERNAL_BODY = _error_body(INTERNAL_ERROR_CODE, AGENT_TEXT[Outcome.INTERNAL_ERROR])


def grant_from_scope(scope: Scope) -> Grant | None:
    state = scope.get("state")
    grant = state.get(GRANT_STATE_KEY) if state is not None else None
    return grant if isinstance(grant, Grant) else None


def grant_from_request(ctx: Any) -> Grant | None:
    """The grant the middleware attached. None (so: deny) when there is no HTTP request."""
    request = ctx.request
    return grant_from_scope(request.scope) if request is not None else None


async def _respond(send: Send, status: int, body: bytes, *extra: tuple[bytes, bytes]) -> None:
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *extra,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class GrantAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        store: GrantStore,
        rejections: RejectionAggregator,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.app = app
        self._store = store
        self._rejections = rejections
        self._clock = clock

    def _authenticate(self, scope: Scope) -> tuple[Grant | None, str | None]:
        """Return (grant, None) if accepted, or (known grant or None, rejection reason)."""
        if TOKEN_SHAPE_RE.search(unquote_to_bytes(scope.get("query_string", b""))):
            return None, "auth_token_in_query"
        values = [value for name, value in scope["headers"] if name.lower() == HEADER]
        if not values:
            return None, "auth_missing_header"
        if len(values) > 1:
            return None, "auth_malformed"
        scheme, separator, token = values[0].decode("latin-1").partition(" ")
        if scheme.lower() != SCHEME:
            return None, "auth_missing_header"
        if not separator or not is_well_formed(token):
            return None, "auth_malformed"
        grant = self._store.lookup(token)
        if grant is None:
            return None, "auth_unknown_token"
        if self._clock() >= grant.expires_at:  # the exact instant of expiry is already expired
            return grant, "auth_expired"
        return grant, None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        t0 = time.perf_counter()
        try:
            grant, reason = self._authenticate(scope)
        except Exception as exc:
            logger.error("authentication failed closed: %s", type(exc).__name__)
            self._rejections.record("auth_internal_error", latency_ms=_ms_since(t0))
            await _respond(send, 500, INTERNAL_BODY)
            return
        if reason is not None:
            self._rejections.record(
                reason,
                grant.grant_id if grant else None,
                grant.agent_id if grant else None,
                _ms_since(t0),
            )
            await _respond(send, 401, UNAUTHORIZED_BODY, (b"www-authenticate", b"Bearer"))
            return
        scope["state"] = {**(scope.get("state") or {}), GRANT_STATE_KEY: grant}
        await self.app(scope, receive, send)


def _ms_since(start: float) -> float:
    return (time.perf_counter() - start) * 1000
