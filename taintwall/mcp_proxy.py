"""The gateway's MCP server: pass-through of tools/list and tools/call to upstreams (M1-3).

Low-level `Server` (D20): `on_call_tool` catches every exception itself and returns a fixed,
redacted `CallToolResult(is_error=True)`, so the SDK never handles or logs one. Each call writes a
`decision` audit event before forwarding (if that write fails, the call is not forwarded) and a
`result` event after (D23).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

import anyio
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
)

from taintwall.errors import (
    AGENT_TEXT,
    OUTCOME_KNOWN,
    MappedError,
    Outcome,
    error_result,
    map_upstream_exception,
)
from taintwall.log_redaction import harden_library_logging
from taintwall.upstreams import UpstreamRegistry, UpstreamTarget

logger = logging.getLogger(__name__)

NO_POLICY_VERSION = "pass_through"


class AuditSink(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _ms(start: float, end: float) -> float:
    return round((end - start) * 1000, 3)


def _result_bytes(result: CallToolResult) -> int:
    return len(result.model_dump_json(by_alias=True).encode())


class Gateway:
    def __init__(self, registry_factory: Callable[[], UpstreamRegistry], audit: AuditSink) -> None:
        harden_library_logging()
        self._registry_factory = registry_factory
        self.audit = audit
        self.server: Server[UpstreamRegistry] = Server(
            "taintwall",
            lifespan=self._lifespan,
            on_list_tools=self._on_list_tools,
            on_call_tool=self._on_call_tool,
        )

    @asynccontextmanager
    async def _lifespan(self, _: Server[UpstreamRegistry]) -> AsyncIterator[UpstreamRegistry]:
        async with self._registry_factory() as registry:
            yield registry

    async def _on_list_tools(
        self, ctx: ServerRequestContext[UpstreamRegistry], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=ctx.lifespan_context.tools())

    async def _on_call_tool(
        self, ctx: ServerRequestContext[UpstreamRegistry], params: CallToolRequestParams
    ) -> CallToolResult:
        try:
            return await self._call(ctx.lifespan_context, params)
        except Exception as exc:
            logger.error("internal error in tools/call: %s", type(exc).__name__)
            return error_result(AGENT_TEXT[Outcome.INTERNAL_ERROR])

    async def _call(
        self, registry: UpstreamRegistry, params: CallToolRequestParams
    ) -> CallToolResult:
        t0 = time.perf_counter()
        call_id = uuid.uuid4().hex
        arguments = params.arguments or {}
        route = registry.resolve(params.name)
        base: dict[str, Any] = {
            "call_id": call_id,
            "tool": params.name,
            "upstream": route.namespace if route else None,
            "grant_id": None,  # M1-4
            "policy_version": NO_POLICY_VERSION,
            "rule_ids": [],
            "argument_keys": sorted(arguments),
        }
        decision = "allow" if route else "deny"
        try:
            self.audit.write({**base, "event": "decision", "ts": _now(), "decision": decision})
        except Exception as exc:
            logger.error("audit write failed, call not forwarded: %s", type(exc).__name__)
            return error_result(AGENT_TEXT[Outcome.AUDIT_UNAVAILABLE])
        t_decided = time.perf_counter()
        if route is None:
            logger.info("tools/call %s: unknown tool", params.name)
            return error_result(AGENT_TEXT[Outcome.UNKNOWN_TOOL])

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


def build_gateway(targets: list[UpstreamTarget], audit: AuditSink) -> Gateway:
    return Gateway(lambda: UpstreamRegistry(targets), audit)
