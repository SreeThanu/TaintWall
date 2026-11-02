"""The gateway's MCP server: tools/list and tools/call to upstreams, scoped by grant (M1-3, M1-4).

Low-level `Server` (D20): `on_call_tool` catches every exception itself and returns a fixed,
redacted `CallToolResult(is_error=True)`, so the SDK never handles or logs one. Each call writes a
`decision` audit event before forwarding (if that write fails, the call is not forwarded) and a
`result` event after (D23). HTTP authentication is `taintwall.auth`; a handler with no grant still
denies, so an app served without that middleware fails closed.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import anyio
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
)

from taintwall.audit.rejections import (
    DEFAULT_WINDOW_S,
    NO_POLICY_VERSION,
    AuditSink,
    RejectionAggregator,
)
from taintwall.auth import GrantAuthMiddleware, grant_from_request
from taintwall.errors import (
    AGENT_TEXT,
    AUTH_ERROR_CODE,
    INTERNAL_ERROR_CODE,
    OUTCOME_KNOWN,
    MappedError,
    Outcome,
    error_result,
    map_upstream_exception,
)
from taintwall.grants.store import Grant, GrantStore, utc_now
from taintwall.log_redaction import harden_library_logging
from taintwall.upstreams import UpstreamRegistry, UpstreamTarget

logger = logging.getLogger(__name__)

# Grant decisions in M1; the policy engine replaces this mapping in M2-1 (D32).
REASON_IN_GRANT = "in_grant"
DENY_TEXT = {
    "no_grant": AGENT_TEXT[Outcome.UNAUTHORIZED],
    "tool_not_in_grant": AGENT_TEXT[Outcome.TOOL_NOT_IN_GRANT],
    "unknown_tool": AGENT_TEXT[Outcome.UNKNOWN_TOOL],
}
GrantResolver = Callable[[ServerRequestContext[UpstreamRegistry]], Grant | None]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _ms(start: float, end: float) -> float:
    return round((end - start) * 1000, 3)


def _result_bytes(result: CallToolResult) -> int:
    return len(result.model_dump_json(by_alias=True).encode())


def _grant_fields(grant: Grant | None) -> dict[str, str | None]:
    return {
        "grant_id": grant.grant_id if grant else None,
        "agent_id": grant.agent_id if grant else None,
    }


class Gateway:
    def __init__(
        self,
        registry_factory: Callable[[], UpstreamRegistry],
        audit: AuditSink,
        *,
        grant_resolver: GrantResolver = grant_from_request,
        clock: Callable[[], datetime] = utc_now,
        rejection_window_s: float = DEFAULT_WINDOW_S,
    ) -> None:
        harden_library_logging()
        self._registry_factory = registry_factory
        self._grant_resolver = grant_resolver
        self.audit = audit
        self.rejections = RejectionAggregator(audit, clock=clock, window_s=rejection_window_s)
        self.server: Server[UpstreamRegistry] = Server(
            "taintwall",
            lifespan=self._lifespan,
            on_list_tools=self._on_list_tools,
            on_call_tool=self._on_call_tool,
        )

    def http_app(
        self,
        store: GrantStore,
        *,
        host: str = "127.0.0.1",
        clock: Callable[[], datetime] = utc_now,
    ) -> Any:
        """The servable app: every HTTP request authenticated before MCP sees it (D29)."""
        inner = self.server.streamable_http_app(stateless_http=True, host=host)
        return GrantAuthMiddleware(inner, store, self.rejections, clock=clock)

    @asynccontextmanager
    async def _lifespan(self, _: Server[UpstreamRegistry]) -> AsyncIterator[UpstreamRegistry]:
        async with self._registry_factory() as registry, anyio.create_task_group() as tg:
            tg.start_soon(self._flush_rejections_periodically)
            try:
                yield registry
            finally:
                tg.cancel_scope.cancel()
                self.rejections.flush_all()

    async def _flush_rejections_periodically(self) -> None:
        while True:
            await anyio.sleep(self.rejections.window_s)
            self.rejections.flush_closed()

    def _resolve_grant(self, ctx: ServerRequestContext[UpstreamRegistry]) -> Grant | None:
        """A failure here is no grant, so the call is denied (non-negotiable 1)."""
        try:
            return self._grant_resolver(ctx)
        except Exception as exc:
            logger.error("grant lookup failed closed: %s", type(exc).__name__)
            return None

    async def _on_list_tools(
        self, ctx: ServerRequestContext[UpstreamRegistry], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        t0 = time.perf_counter()
        grant = self._resolve_grant(ctx)
        reason = REASON_IN_GRANT if grant else "no_grant"
        try:
            self.audit.write(
                {
                    "event": "decision",
                    "ts": _now(),
                    "method": "tools/list",
                    "decision": "allow" if grant else "deny",
                    "reason": reason,
                    "rule_ids": [reason],
                    "policy_version": NO_POLICY_VERSION,
                    **_grant_fields(grant),
                    "timings_ms": {"decide": _ms(t0, time.perf_counter())},
                }
            )
        except Exception as exc:
            logger.error("audit write failed, tools/list denied: %s", type(exc).__name__)
            raise MCPError(
                code=INTERNAL_ERROR_CODE, message=AGENT_TEXT[Outcome.AUDIT_UNAVAILABLE]
            ) from None
        if grant is None:
            raise MCPError(code=AUTH_ERROR_CODE, message=AGENT_TEXT[Outcome.UNAUTHORIZED])
        try:
            tools = [t for t in ctx.lifespan_context.tools() if grant.allows(t.name)]
        except Exception as exc:
            logger.error("internal error in tools/list: %s", type(exc).__name__)
            raise MCPError(
                code=INTERNAL_ERROR_CODE, message=AGENT_TEXT[Outcome.INTERNAL_ERROR]
            ) from None
        return ListToolsResult(tools=tools)

    async def _on_call_tool(
        self, ctx: ServerRequestContext[UpstreamRegistry], params: CallToolRequestParams
    ) -> CallToolResult:
        try:
            return await self._call(ctx.lifespan_context, params, self._resolve_grant(ctx))
        except Exception as exc:
            logger.error("internal error in tools/call: %s", type(exc).__name__)
            return error_result(AGENT_TEXT[Outcome.INTERNAL_ERROR])

    async def _call(
        self, registry: UpstreamRegistry, params: CallToolRequestParams, grant: Grant | None
    ) -> CallToolResult:
        t0 = time.perf_counter()
        call_id = uuid.uuid4().hex
        arguments = params.arguments or {}
        route = registry.resolve(params.name)
        # Grant before existence, so a restricted grant cannot probe which tools exist.
        if grant is None:
            reason = "no_grant"
        elif not grant.allows(params.name):
            reason = "tool_not_in_grant"
        elif route is None:
            reason = "unknown_tool"
        else:
            reason = REASON_IN_GRANT
        base: dict[str, Any] = {
            "call_id": call_id,
            "method": "tools/call",
            "tool": params.name,
            "upstream": route.namespace if route else None,
            **_grant_fields(grant),
            "policy_version": NO_POLICY_VERSION,
            "reason": reason,
            "rule_ids": [reason],
            "argument_keys": sorted(arguments),
        }
        decision = "allow" if reason == REASON_IN_GRANT else "deny"
        try:
            self.audit.write(
                {
                    **base,
                    "event": "decision",
                    "ts": _now(),
                    "decision": decision,
                    "timings_ms": {"decide": _ms(t0, time.perf_counter())},
                }
            )
        except Exception as exc:
            logger.error("audit write failed, call not forwarded: %s", type(exc).__name__)
            return error_result(AGENT_TEXT[Outcome.AUDIT_UNAVAILABLE])
        t_decided = time.perf_counter()
        if route is None or reason != REASON_IN_GRANT:
            logger.info("tools/call %s denied: %s", params.name, reason)
            return error_result(DENY_TEXT[reason])

        mapped: MappedError | None = None
        client = route.handle.client
        forwarded = route.handle.available and client is not None
        if not forwarded or client is None:
            # Broken upstream (D25): fail closed without forwarding, so the outcome is known.
            mapped = MappedError(Outcome.UNAVAILABLE, AGENT_TEXT[Outcome.UNAVAILABLE])
            outcome = mapped.outcome
            result = mapped.result()
        else:
            try:
                with anyio.fail_after(route.timeout_s):
                    result = await client.call_tool(route.tool, arguments)
                outcome = Outcome.TOOL_ERROR if result.is_error else Outcome.OK
            except Exception as exc:
                mapped = map_upstream_exception(exc)
                outcome = mapped.outcome
                result = mapped.result()
        t_upstream = time.perf_counter()

        event = {
            **base,
            "event": "result",
            "ts": _now(),
            "outcome": outcome.value,
            "forwarded": forwarded,
            "outcome_known": OUTCOME_KNOWN[outcome] if forwarded else True,
            "is_error": bool(result.is_error),
            "result_bytes": _result_bytes(result),
            "timings_ms": {
                "decide_and_audit": _ms(t0, t_decided),
                "upstream": _ms(t_decided, t_upstream),
                "total": _ms(t0, time.perf_counter()),
            },
            **({"error": mapped.log_fields} if mapped else {}),
        }
        try:
            self.audit.write(event)
        except Exception as exc:
            logger.error("audit result write failed: %s", type(exc).__name__)
        logger.info("tools/call %s -> %s", params.name, outcome.value)
        return result


def build_gateway(targets: list[UpstreamTarget], audit: AuditSink, **options: Any) -> Gateway:
    return Gateway(lambda: UpstreamRegistry(targets), audit, **options)
