"""Sync handles to an EnvBridge over MCP, for AgentDojo's synchronous pipeline.

Each connection owns one anyio blocking portal: a background thread running the event loop that
holds the MCP client. Callers block in `portal.call`; the loop never calls back into the portal,
and the HTTP server runs on its own thread and loop, so waits only go caller -> portal -> server.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import anyio
import httpx2
from anyio.from_thread import BlockingPortal, start_blocking_portal
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel.server import Server
from mcp.types import TextContent, Tool

from eval.harness.env_bridge import EnvBridge, EnvHolder
from taintwall.testing.http import serve_in_thread

CALL_TIMEOUT_S = 30.0


class BridgeConnection:
    def __init__(
        self, portal: BlockingPortal, client: Client, holder: EnvHolder, url: str | None = None
    ) -> None:
        self._portal = portal
        self._client = client
        self.holder = holder
        self.url = url  # set for HTTP connections
        self.executed: Counter[str] = Counter()  # tool name -> successful calls

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Call a tool; return (text, is_error)."""
        result: tuple[str, bool] = self._portal.call(self._call, name, arguments)
        if not result[1]:
            self.executed[name] += 1
        return result

    async def _call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        with anyio.fail_after(CALL_TIMEOUT_S):
            result = await self._client.call_tool(name, arguments)
        text = "".join(block.text for block in result.content if isinstance(block, TextContent))
        return text, bool(result.is_error)

    def list_tools(self) -> list[Tool]:
        return self._portal.call(self._list_tools)

    async def _list_tools(self) -> list[Tool]:
        with anyio.fail_after(CALL_TIMEOUT_S):
            return list((await self._client.list_tools()).tools)


@contextmanager
def in_process(bridge: EnvBridge, mode: str = "legacy") -> Iterator[BridgeConnection]:
    """Connect in the same process. `legacy` runs real JSON-RPC over an in-memory transport."""
    client = Client(bridge.server, mode=mode, read_timeout_seconds=CALL_TIMEOUT_S)
    with (
        start_blocking_portal() as portal,
        portal.wrap_async_context_manager(client) as connected,
    ):
        yield BridgeConnection(portal, connected, bridge.holder)


@asynccontextmanager
async def _http_client(url: str) -> AsyncIterator[Client]:
    async with (
        httpx2.AsyncClient() as http,
        Client(
            streamable_http_client(url, http_client=http), read_timeout_seconds=CALL_TIMEOUT_S
        ) as client,
    ):
        yield client


@contextmanager
def serve_http(server: Server[Any]) -> Iterator[str]:
    """Serve `server` with stateless HTTP on 127.0.0.1 in a background thread; yield its URL."""
    with serve_in_thread(
        server.streamable_http_app(stateless_http=True), name="envbridge-http"
    ) as base:
        yield f"{base}/mcp"


@contextmanager
def over_http(bridge: EnvBridge) -> Iterator[BridgeConnection]:
    with (
        serve_http(bridge.server) as url,
        start_blocking_portal() as portal,
        portal.wrap_async_context_manager(_http_client(url)) as client,
    ):
        yield BridgeConnection(portal, client, bridge.holder, url)
