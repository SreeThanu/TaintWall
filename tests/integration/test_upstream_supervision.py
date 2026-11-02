"""Characterization of MCP SDK behaviour behind D25 (mcp 2.3.0).

These tests pin what the SDK and anyio do, not what we want them to do. If an SDK upgrade makes
one fail, re-read D25 and docs/notes/sdk-issue-draft.md: the workaround or its reason may have
changed. Each upstream has one successful call first, so a late `initialized` notification can
never be what hits the dead server.
"""

import asyncio
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, INTERNAL_ERROR

from taintwall.errors import leaf_exceptions
from tests.support.processes import ReadyProcess, upstream_subprocess

MODES = ["auto", "legacy"]
WAIT_S = 10.0


def leaf_types(exc: BaseException | None) -> list[type[BaseException]]:
    return [type(leaf) for leaf in leaf_exceptions(exc)] if exc is not None else []


async def echo(client: Client, text: str) -> str:
    with anyio.fail_after(WAIT_S):
        result = await client.call_tool("echo", {"text": text})
    assert not result.is_error
    return str(result.content[0].text)  # type: ignore[union-attr]


async def closed_error(client: Client) -> MCPError:
    with pytest.raises(MCPError) as caught, anyio.fail_after(WAIT_S):
        await client.call_tool("echo", {"text": "after"})
    return caught.value


@dataclass
class Holder:
    """A task that enters client contexts and waits, recording how it ended."""

    urls: dict[str, str]
    mode: str
    http: httpx2.AsyncClient
    clients: dict[str, Client]
    manager: StreamableHTTPSessionManager | None = None
    ready: anyio.Event = field(default_factory=anyio.Event)
    stop: anyio.Event = field(default_factory=anyio.Event)
    ended: anyio.Event = field(default_factory=anyio.Event)
    failure: BaseException | None = None

    async def _enter(self, names: list[str]) -> None:
        if names:
            url = self.urls[names[0]]
            source = streamable_http_client(url, http_client=self.http)
            async with Client(source, mode=self.mode) as client:  # type: ignore[arg-type]
                self.clients[names[0]] = client
                await self._enter(names[1:])
        elif self.manager is not None:
            async with self.manager.run():
                await self._wait()
        else:
            await self._wait()

    async def _wait(self) -> None:
        self.ready.set()
        await self.stop.wait()

    async def run(self) -> None:
        try:
            await self._enter(list(self.urls))
        except Exception as exc:  # cancellation of the host itself is what the tests look at
            self.failure = exc
        finally:
            self.ended.set()


@dataclass
class Observed:
    a_error_code: int
    b_error_code: int | None
    holder_failures: dict[str, list[type[BaseException]]]
    a_holder_ended: bool
    b_holder_ended: bool


async def dead_upstream_scenario(
    a: ReadyProcess, b: ReadyProcess, mode: str, *, supervised: bool
) -> Observed:
    """Both upstreams connected; `a` dies; the caller then uses `a` and `b`.

    The caller is always a separate task from the holder(s), as in the gateway, where request
    handlers call clients that the registry entered. The only variable is who holds the clients.
    """
    clients: dict[str, Client] = {}
    urls = {"a": a.url, "b": b.url}
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(10.0, read=None)) as http:
        if supervised:
            holders = {n: Holder({n: urls[n]}, mode, http, clients) for n in urls}
        else:
            shared = Holder(urls, mode, http, clients)
            holders = {"a": shared, "b": shared}
        async with anyio.create_task_group() as tg:
            for holder in {id(h): h for h in holders.values()}.values():
                tg.start_soon(holder.run)
            with anyio.fail_after(WAIT_S):
                for holder in holders.values():
                    await holder.ready.wait()
            assert await echo(clients["a"], "a") == "a"
            assert await echo(clients["b"], "b") == "b"

            await anyio.to_thread.run_sync(a.stop)
            a_error = await closed_error(clients["a"])
            with anyio.fail_after(WAIT_S):
                await holders["a"].ended.wait()

            b_error: MCPError | None = None
            if holders["b"].ended.is_set():
                b_error = await closed_error(clients["b"])
            else:
                assert await echo(clients["b"], "still up") == "still up"
            observed = Observed(
                a_error.error.code,
                b_error.error.code if b_error else None,
                {n: leaf_types(h.failure) for n, h in holders.items()},
                holders["a"].ended.is_set(),
                holders["b"].ended.is_set(),
            )
            for holder in holders.values():
                holder.stop.set()
    return observed


def two_upstreams(body: Callable[[ReadyProcess, ReadyProcess], Observed]) -> Observed:
    with ExitStack() as stack:
        a = stack.enter_context(upstream_subprocess())
        b = stack.enter_context(upstream_subprocess())
        return body(a, b)


def test_control_plain_anyio_child_failure_cancels_the_host_scope() -> None:
    """No MCP: a failing child cancels the task that entered the group. This is anyio's contract."""
    reached_after_sleep = False

    async def fail() -> None:
        raise ConnectionError("child failed")

    async def host() -> None:
        nonlocal reached_after_sleep
        async with anyio.create_task_group() as tg:
            tg.start_soon(fail)
            await anyio.sleep(WAIT_S)
            reached_after_sleep = True

    with pytest.raises(ExceptionGroup) as caught:
        asyncio.run(host())
    assert leaf_types(caught.value) == [ConnectionError]
    assert not reached_after_sleep


@pytest.mark.parametrize("mode", MODES)
def test_one_holder_for_all_clients_dead_upstream_takes_healthy_one_down(mode: str) -> None:
    observed = two_upstreams(
        lambda a, b: asyncio.run(dead_upstream_scenario(a, b, mode, supervised=False))
    )
    assert observed.a_error_code == CONNECTION_CLOSED
    assert observed.holder_failures["a"] == [httpx2.ConnectError]
    # Same holder, so the healthy upstream's client was torn down too.
    assert observed.b_holder_ended
    assert observed.b_error_code == CONNECTION_CLOSED


@pytest.mark.parametrize("mode", MODES)
def test_one_holder_per_client_contains_the_failure(mode: str) -> None:
    observed = two_upstreams(
        lambda a, b: asyncio.run(dead_upstream_scenario(a, b, mode, supervised=True))
    )
    assert observed.a_error_code == CONNECTION_CLOSED
    assert observed.holder_failures["a"] == [httpx2.ConnectError]
    assert not observed.b_holder_ended
    assert observed.b_error_code is None
    assert observed.holder_failures["b"] == []


@pytest.mark.parametrize("mode", MODES)
def test_holder_shared_with_session_manager_produces_task_group_message(mode: str) -> None:
    """The message first seen in M1-3 came from the gateway's own server side, not an upstream."""

    async def scenario(a: ReadyProcess) -> None:
        clients: dict[str, Client] = {}
        manager = StreamableHTTPSessionManager(app=Server("gateway"), stateless=True)
        async with httpx2.AsyncClient(timeout=httpx2.Timeout(10.0, read=None)) as http:
            holder = Holder({"a": a.url}, mode, http, clients, manager=manager)
            async with anyio.create_task_group() as tg:
                tg.start_soon(holder.run)
                with anyio.fail_after(WAIT_S):
                    await holder.ready.wait()
                assert await echo(clients["a"], "a") == "a"
                await anyio.to_thread.run_sync(a.stop)
                await closed_error(clients["a"])
                with anyio.fail_after(WAIT_S):
                    await holder.ended.wait()
        assert leaf_types(holder.failure) == [httpx2.ConnectError]

        scope: dict[str, Any] = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_: dict[str, Any]) -> None:
            raise AssertionError("must fail before responding")

        with pytest.raises(RuntimeError, match="Task group is not initialized"):
            await manager.handle_request(scope, receive, send)

    with upstream_subprocess() as a:
        asyncio.run(scenario(a))


class FailNextPost(httpx2.AsyncBaseTransport):
    """Real transport, except that one chosen POST fails. The upstream stays healthy throughout."""

    def __init__(self) -> None:
        self._inner = httpx2.AsyncHTTPTransport()
        self.fail_next: Literal["connect_error", "http_500"] | None = None

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST" and self.fail_next is not None:
            failure, self.fail_next = self.fail_next, None
            if failure == "connect_error":
                raise httpx2.ConnectError("injected", request=request)
            return httpx2.Response(500, request=request)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


async def one_failed_post(
    url: str, mode: str, failure: Literal["connect_error", "http_500"]
) -> tuple[int, str | int, BaseException | None]:
    """Fail one POST against a healthy upstream; return its error, the next call, the holder end."""
    transport = FailNextPost()
    clients: dict[str, Client] = {}
    async with httpx2.AsyncClient(
        transport=transport, timeout=httpx2.Timeout(10.0, read=None)
    ) as http:
        holder = Holder({"up": url}, mode, http, clients)
        async with anyio.create_task_group() as tg:
            tg.start_soon(holder.run)
            with anyio.fail_after(WAIT_S):
                await holder.ready.wait()
            assert await echo(clients["up"], "first") == "first"

            transport.fail_next = failure
            first_error = await closed_error(clients["up"])
            await anyio.sleep(0.2)  # a teardown, if there is one, reaches the holder here

            next_call: str | int
            try:
                next_call = await echo(clients["up"], "next")
            except MCPError as exc:
                next_call = exc.error.code
            holder.stop.set()
    return first_error.error.code, next_call, holder.failure


@pytest.mark.parametrize("mode", MODES)
def test_one_connect_error_permanently_closes_the_client(mode: str) -> None:
    """The SDK defect in docs/notes/sdk-issue-draft.md: one failed POST ends the whole client."""
    with upstream_subprocess() as up:
        first, next_call, failure = asyncio.run(one_failed_post(up.url, mode, "connect_error"))
    assert first == CONNECTION_CLOSED
    assert next_call == CONNECTION_CLOSED  # the upstream is healthy, the client is gone
    assert leaf_types(failure) == [httpx2.ConnectError]


@pytest.mark.parametrize("mode", MODES)
def test_one_http_500_fails_only_that_request(mode: str) -> None:
    """Contrast: the SDK resolves an HTTP error per request, and the client keeps working."""
    with upstream_subprocess() as up:
        first, next_call, failure = asyncio.run(one_failed_post(up.url, mode, "http_500"))
    assert first == INTERNAL_ERROR
    assert next_call == "next"
    assert failure is None
