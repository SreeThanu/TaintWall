"""Upstream registry: one long-lived MCP client per upstream, tools cached at startup (D19, D24).

There is no MCP session to pool on the 2026-07-28 protocol; "reuse" means one open `Client` per
upstream over a shared, pooled HTTP client. Startup fails if any upstream is unreachable or two
namespaced tool names collide. Tool definitions are cached once at startup (THREAT_MODEL).

Each client is held by its own supervisor task (D25). A failed POST ends the SDK client's transport
task group, and anyio then cancels the task that entered it; inside a supervisor that only ends
that upstream, instead of cancelling the gateway's lifespan and every other upstream. A broken
upstream is not reconnected (M3-6); calls to it fail closed without being forwarded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal

import anyio
import httpx2
from anyio.abc import TaskGroup
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool

from taintwall.config import MAX_TOOL_NAME_LENGTH, NAMESPACE_SEPARATOR, GatewayConfig
from taintwall.errors import leaf_exceptions

logger = logging.getLogger(__name__)

Mode = Literal["auto", "legacy"]
STARTUP_TIMEOUT_S = 15.0


class UpstreamStartupError(RuntimeError):
    """An upstream could not be reached or its tools could not be listed at startup."""


@dataclass(frozen=True)
class UpstreamTarget:
    namespace: str
    target: Any  # a URL string, or (in tests) an in-process `Server` / `MCPServer`
    timeout_s: float = 30.0
    mode: Mode = "auto"


@dataclass(eq=False)
class UpstreamHandle:
    target: UpstreamTarget
    client: Client | None = None
    tools: list[Tool] = field(default_factory=list)
    startup_error: str | None = None
    broken: bool = False
    ready: anyio.Event = field(default_factory=anyio.Event)
    stop: anyio.Event = field(default_factory=anyio.Event)

    @property
    def available(self) -> bool:
        return self.client is not None and not self.broken


@dataclass(frozen=True)
class Route:
    namespace: str
    tool: str
    handle: UpstreamHandle
    timeout_s: float


def targets_from_config(config: GatewayConfig) -> list[UpstreamTarget]:
    return [UpstreamTarget(u.namespace, u.url, u.timeout_s, u.mode) for u in config.upstreams]


def namespaced(namespace: str, tool: str) -> str:
    return f"{namespace}{NAMESPACE_SEPARATOR}{tool}"


def _types(exc: BaseException) -> str:
    return ", ".join(type(leaf).__name__ for leaf in leaf_exceptions(exc))


async def _list_all_tools(client: Client) -> list[Tool]:
    tools: list[Tool] = []
    cursor: str | None = None
    while True:
        page = await client.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if not cursor:
            return tools


class UpstreamRegistry:
    def __init__(self, targets: list[UpstreamTarget]) -> None:
        self._handles = [UpstreamHandle(t) for t in targets]
        self._routes: dict[str, Route] = {}
        self._tools: list[Tool] = []
        self._http: httpx2.AsyncClient | None = None
        self._tg: TaskGroup | None = None

    async def __aenter__(self) -> UpstreamRegistry:
        self._http = httpx2.AsyncClient(timeout=httpx2.Timeout(10.0, read=None))
        self._tg = anyio.create_task_group()
        await self._tg.__aenter__()
        try:
            for handle in self._handles:
                self._tg.start_soon(
                    self._supervise, handle, name=f"upstream-{handle.target.namespace}"
                )
            with anyio.fail_after(STARTUP_TIMEOUT_S):
                for handle in self._handles:
                    await handle.ready.wait()
            for handle in self._handles:
                if handle.startup_error is not None:
                    raise UpstreamStartupError(
                        f"upstream {handle.target.namespace!r} unavailable at startup"
                        f" ({handle.startup_error})"
                    )
                self._add_tools(handle)
        except BaseException:
            await self._shutdown()
            raise
        return self

    async def _supervise(self, handle: UpstreamHandle) -> None:
        """Hold one upstream client for the registry's lifetime; contain its failures."""
        target = handle.target
        try:
            source = (
                streamable_http_client(target.target, http_client=self._http)
                if isinstance(target.target, str)
                else target.target
            )
            async with Client(source, mode=target.mode) as client:
                handle.tools = await _list_all_tools(client)
                handle.client = client
                handle.ready.set()
                await handle.stop.wait()
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt | SystemExit):
                raise
            if not handle.ready.is_set():
                handle.startup_error = _types(exc)
            elif not handle.stop.is_set():
                logger.warning("upstream %s connection lost (%s)", target.namespace, _types(exc))
        finally:
            handle.broken = True
            handle.ready.set()

    def _add_tools(self, handle: UpstreamHandle) -> None:
        ns = handle.target.namespace
        for tool in handle.tools:
            name = namespaced(ns, tool.name)
            if len(name) > MAX_TOOL_NAME_LENGTH:
                raise UpstreamStartupError(f"tool name too long: {name[:40]}...")
            if name in self._routes:
                raise UpstreamStartupError(f"duplicate tool name: {name}")
            self._routes[name] = Route(ns, tool.name, handle, handle.target.timeout_s)
            self._tools.append(tool.model_copy(update={"name": name}))
        logger.info("upstream %s: %d tools", ns, len(handle.tools))

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._shutdown()

    async def _shutdown(self) -> None:
        for handle in self._handles:
            handle.stop.set()
        try:
            if self._tg is not None:
                await self._tg.__aexit__(None, None, None)
        except Exception as exc:
            logger.warning("error while stopping upstream clients: %s", _types(exc))
        finally:
            self._tg = None
            if self._http is not None:
                await self._http.aclose()
                self._http = None

    def tools(self) -> list[Tool]:
        return list(self._tools)

    def resolve(self, name: str) -> Route | None:
        return self._routes.get(name)
