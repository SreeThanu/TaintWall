"""M1-4 grants end to end: a real MCP client over HTTP through the auth layer, both eras."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent

from taintwall.audit.jsonl import JsonlAuditSink
from taintwall.errors import AGENT_TEXT, AUTH_ERROR_CODE, Outcome
from taintwall.errors import leaf_exceptions as leaves
from taintwall.grants.store import Grant, GrantStore, generate_token
from taintwall.mcp_proxy import Gateway, build_gateway
from taintwall.testing.http import serve_in_thread
from taintwall.upstreams import UpstreamTarget
from tests.support.grants import NOW, FakeClock, issue
from tests.support.upstream import TOOL_NAMES, build_upstream

MODES = ["auto", "legacy"]
NOT_IN_GRANT = "TaintwallError: tool not allowed by grant"
UNAUTHORIZED = "TaintwallError: unauthorized"


def spy_upstream(calls: list[str]) -> MCPServer:
    server = MCPServer("spy")

    @server.tool()
    def touch() -> str:
        calls.append("touched")
        return "touched"

    return server


def targets(calls: list[str] | None = None) -> list[UpstreamTarget]:
    return [
        UpstreamTarget("up", build_upstream("up"), timeout_s=5),
        UpstreamTarget("spy", spy_upstream(calls if calls is not None else []), timeout_s=5),
    ]


@contextmanager
def served(
    tmp_path: Path,
    grants: list[Grant],
    *,
    clock: Any = None,
    calls: list[str] | None = None,
    audit: Any = None,
    **gateway_kwargs: Any,
) -> Iterator[str]:
    sink = audit or JsonlAuditSink(tmp_path / "audit.jsonl")
    gateway = build_gateway(targets(calls), sink, **gateway_kwargs)
    extra = {"clock": clock} if clock else {}
    with serve_in_thread(gateway.http_app(GrantStore(grants), **extra)) as base:
        yield f"{base}/mcp"


@asynccontextmanager
async def connect(url: str, mode: str, token: str | None) -> AsyncIterator[Client]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with (
        httpx2.AsyncClient(headers=headers) as http,
        Client(streamable_http_client(url, http_client=http), mode=mode) as client,  # type: ignore[arg-type]
    ):
        yield client


def text_of(result: CallToolResult) -> str:
    return "".join(b.text for b in result.content if isinstance(b, TextContent))


def events(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]


def auth_failure(exc: BaseException) -> MCPError:
    [leaf] = leaves(exc)
    assert isinstance(leaf, MCPError), repr(leaf)
    return leaf


@pytest.mark.parametrize("case", ["missing", "malformed", "unknown", "expired"])
@pytest.mark.parametrize("mode", MODES)
def test_rejected_client_sees_the_generic_error(tmp_path: Path, mode: str, case: str) -> None:
    good_token, good = issue()
    expired_token, expired = issue(grant_id="old", expires_at=NOW)
    token = {
        "missing": None,
        "malformed": "not-a-token",
        "unknown": generate_token(),
        "expired": expired_token,
    }[case]

    async def go(url: str) -> None:
        async with connect(url, mode, token):
            pytest.fail("connected without a valid grant")

    with (
        served(tmp_path, [good, expired], clock=FakeClock()) as url,
        pytest.raises(BaseException) as caught,  # an ExceptionGroup, checked below
    ):
        asyncio.run(go(url))
    error = auth_failure(caught.value)
    assert error.error.code == AUTH_ERROR_CODE
    assert error.error.message == UNAUTHORIZED
    assert good_token not in str(error)


def test_every_path_and_method_requires_a_grant(tmp_path: Path) -> None:
    _, good = issue()
    with served(tmp_path, [good]) as url:
        base = url.removesuffix("/mcp")
        for method, target in [
            ("POST", url),
            ("GET", url),
            ("DELETE", url),
            ("PUT", url),
            ("GET", f"{base}/"),
            ("GET", f"{base}/other"),
        ]:
            response = httpx2.request(method, target, timeout=5)
            assert response.status_code == 401, (method, target)
            assert response.headers["www-authenticate"] == "Bearer"


def test_identical_http_response_for_all_rejection_kinds(tmp_path: Path) -> None:
    _, good = issue()
    expired_token, expired = issue(grant_id="old", expires_at=NOW)
    with served(tmp_path, [good, expired], clock=FakeClock()) as url:
        seen = set()
        for headers in [
            {},
            {"Authorization": "Bearer nope"},
            {"Authorization": f"Bearer {generate_token()}"},
            {"Authorization": f"Bearer {expired_token}"},
        ]:
            r = httpx2.post(url, headers=headers, json={}, timeout=5)
            seen.add(
                (
                    r.status_code,
                    r.content,
                    r.headers["www-authenticate"],
                    r.headers["content-type"],
                )
            )
    assert len(seen) == 1


@pytest.mark.parametrize("mode", MODES)
def test_grant_expiring_mid_session_stops_the_next_call(tmp_path: Path, mode: str) -> None:
    token, grant = issue(expires_at=NOW + timedelta(hours=1))
    clock = FakeClock()

    async def go(url: str) -> None:
        async with connect(url, mode, token) as client:
            assert text_of(await client.call_tool("up__echo", {"text": "a"})) == "a"
            clock.now = grant.expires_at
            with pytest.raises(MCPError) as caught:
                await client.call_tool("up__echo", {"text": "b"})
            assert caught.value.error.code == AUTH_ERROR_CODE

    with served(tmp_path, [grant], clock=clock) as url:
        asyncio.run(go(url))


@pytest.mark.parametrize("mode", MODES)
def test_star_grant_lists_and_calls_everything(tmp_path: Path, mode: str) -> None:
    token, grant = issue(tools=["*"])

    async def go(url: str) -> None:
        async with connect(url, mode, token) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert names == {f"up__{n}" for n in TOOL_NAMES} | {"spy__touch"}
            assert text_of(await client.call_tool("up__echo", {"text": "hi"})) == "hi"

    with served(tmp_path, [grant]) as url:
        asyncio.run(go(url))


@pytest.mark.parametrize("mode", MODES)
def test_tools_list_is_filtered_to_the_grant(tmp_path: Path, mode: str) -> None:
    token, grant = issue(tools=["up__echo"])

    async def go(url: str) -> None:
        async with connect(url, mode, token) as client:
            assert [t.name for t in (await client.list_tools()).tools] == ["up__echo"]

    with served(tmp_path, [grant]) as url:
        asyncio.run(go(url))
    [event] = [e for e in events(tmp_path) if e.get("method") == "tools/list"]
    assert event["event"] == "decision"
    assert event["decision"] == "allow"
    assert event["grant_id"] == grant.grant_id
    assert event["agent_id"] == grant.agent_id
    assert event["reason"] == "in_grant"
    assert "decide" in event["timings_ms"]


@pytest.mark.parametrize("mode", MODES)
def test_tool_outside_grant_is_denied_and_never_forwarded(tmp_path: Path, mode: str) -> None:
    token, grant = issue(tools=["up__echo"])
    calls: list[str] = []

    async def go(url: str) -> CallToolResult:
        async with connect(url, mode, token) as client:
            return await client.call_tool("spy__touch", {})

    with served(tmp_path, [grant], calls=calls) as url:
        result = asyncio.run(go(url))
    assert result.is_error
    assert text_of(result) == NOT_IN_GRANT
    assert calls == []
    event = [e for e in events(tmp_path) if e.get("tool") == "spy__touch"][-1]
    assert event["event"] == "decision"
    assert event["decision"] == "deny"
    assert event["reason"] == "tool_not_in_grant"
    assert event["rule_ids"] == ["tool_not_in_grant"]
    assert event["grant_id"] == grant.grant_id
    assert event["agent_id"] == grant.agent_id
    assert "decide" in event["timings_ms"]


def test_restricted_grant_cannot_probe_which_tools_exist(tmp_path: Path) -> None:
    token, grant = issue(tools=["up__echo"])

    async def go(url: str) -> tuple[str, str]:
        async with connect(url, "auto", token) as client:
            real = await client.call_tool("spy__touch", {})
            made_up = await client.call_tool("no__such_tool", {})
            return text_of(real), text_of(made_up)

    with served(tmp_path, [grant]) as url:
        assert asyncio.run(go(url)) == (NOT_IN_GRANT, NOT_IN_GRANT)


def test_star_grant_gets_unknown_tool_with_a_reason(tmp_path: Path) -> None:
    token, grant = issue(tools=["*"])

    async def go(url: str) -> CallToolResult:
        async with connect(url, "auto", token) as client:
            return await client.call_tool("no__such_tool", {})

    with served(tmp_path, [grant]) as url:
        assert text_of(asyncio.run(go(url))) == AGENT_TEXT[Outcome.UNKNOWN_TOOL]
    event = events(tmp_path)[-1]
    assert event["decision"] == "deny"
    assert event["reason"] == "unknown_tool"
    assert event["grant_id"] == grant.grant_id
    assert "decide" in event["timings_ms"]


def test_allowed_call_events_carry_grant_agent_and_reason(tmp_path: Path) -> None:
    token, grant = issue(grant_id="task-9", agent_id="agent-9")

    async def go(url: str) -> None:
        async with connect(url, "auto", token) as client:
            await client.call_tool("up__echo", {"text": "x"})

    with served(tmp_path, [grant]) as url:
        asyncio.run(go(url))
    decision, result = [e for e in events(tmp_path) if e.get("tool") == "up__echo"]
    for event in (decision, result):
        assert event["grant_id"] == "task-9"
        assert event["agent_id"] == "agent-9"
        assert event["reason"] == "in_grant"
    assert decision["decision"] == "allow"


@pytest.mark.parametrize("mode", MODES)
def test_app_without_the_middleware_still_denies(tmp_path: Path, mode: str) -> None:
    """Second lock: a handler with no grant denies, so forgetting the middleware fails closed."""
    calls: list[str] = []
    gateway: Gateway = build_gateway(targets(calls), JsonlAuditSink(tmp_path / "audit.jsonl"))
    app = gateway.server.streamable_http_app(stateless_http=True)

    async def go(url: str) -> CallToolResult:
        async with connect(url, mode, None) as client:
            with pytest.raises(MCPError) as caught:
                await client.list_tools()
            assert caught.value.error.code == AUTH_ERROR_CODE
            assert caught.value.error.message == UNAUTHORIZED
            return await client.call_tool("spy__touch", {})

    with serve_in_thread(app) as base:
        result = asyncio.run(go(f"{base}/mcp"))
    assert result.is_error
    assert text_of(result) == UNAUTHORIZED
    assert calls == []
    reasons = [e["reason"] for e in events(tmp_path)]
    assert reasons == ["no_grant", "no_grant"]


class FailOnlyToolsList:
    def __init__(self, path: Path) -> None:
        self.inner = JsonlAuditSink(path)

    def write(self, event: dict[str, Any]) -> None:
        if event.get("method") == "tools/list":
            raise OSError("disk full")
        self.inner.write(event)


def test_tools_list_fails_closed_when_its_audit_write_fails(tmp_path: Path) -> None:
    token, grant = issue()

    async def go(url: str) -> None:
        async with connect(url, "auto", token) as client:
            with pytest.raises(MCPError) as caught:
                await client.list_tools()
            assert caught.value.error.message == AGENT_TEXT[Outcome.AUDIT_UNAVAILABLE]

    with served(tmp_path, [grant], audit=FailOnlyToolsList(tmp_path / "audit.jsonl")) as url:
        asyncio.run(go(url))


def test_broken_clock_fails_closed_over_http(tmp_path: Path) -> None:
    def clock() -> Any:
        raise RuntimeError("clock failed")

    token, grant = issue()
    calls: list[str] = []
    with served(tmp_path, [grant], clock=clock, calls=calls) as url:
        response = httpx2.post(
            url, headers={"Authorization": f"Bearer {token}"}, json={}, timeout=5
        )
    assert response.status_code == 500
    assert response.json()["error"]["message"] == AGENT_TEXT[Outcome.INTERNAL_ERROR]
    assert calls == []


@pytest.mark.parametrize("with_header", [False, True], ids=["query_only", "query_and_header"])
def test_token_in_url_is_rejected_over_http(tmp_path: Path, with_header: bool) -> None:
    token, grant = issue()
    headers = {"Authorization": f"Bearer {token}"} if with_header else {}
    with served(tmp_path, [grant]) as url:
        response = httpx2.post(f"{url}?token={token}", headers=headers, json={}, timeout=5)
    assert response.status_code == 401
    raw = (tmp_path / "audit.jsonl").read_text()
    assert token not in raw
    assert "auth_token_in_query" in raw


class CappedSink:
    """A small disk: once full, every write fails, like a real full disk under D23."""

    def __init__(self, capacity_bytes: int) -> None:
        self.capacity = capacity_bytes
        self.used = 0
        self.events: list[dict[str, Any]] = []

    def write(self, event: dict[str, Any]) -> None:
        size = len(json.dumps(event))
        if self.used + size > self.capacity:
            raise OSError("disk full")
        self.used += size
        self.events.append(event)


def test_attack_rejection_flood_cannot_fill_the_audit_and_block_authorised_calls(
    tmp_path: Path,
) -> None:
    """T15: 300 one-line-per-request rejections would be about 45 KB, far over this 4 KB disk."""
    token, grant = issue()
    sink = CappedSink(4096)
    flood = 300

    async def authorised(url: str) -> CallToolResult:
        async with connect(url, "auto", token) as client:
            return await client.call_tool("up__echo", {"text": "still works"})

    with served(tmp_path, [grant], audit=sink) as url, httpx2.Client(timeout=5) as http:
        for i in range(flood):
            headers = {"Authorization": f"Bearer {generate_token()}"} if i % 2 else {}
            assert http.post(url, headers=headers, json={}).status_code == 401
        used_by_flood = sink.used
        result = asyncio.run(authorised(url))
    assert not result.is_error
    assert text_of(result) == "still works"
    assert used_by_flood == 0  # nothing written per request
    counts = {e["reason"]: e["count"] for e in sink.events if e["event"] == "auth_rejections"}
    assert counts == {"auth_missing_header": flood // 2, "auth_unknown_token": flood // 2}


class FailOnlyRejections:
    def __init__(self, path: Path) -> None:
        self.inner = JsonlAuditSink(path)

    def write(self, event: dict[str, Any]) -> None:
        if event["event"] == "auth_rejections":
            raise OSError("rejection write failed")
        self.inner.write(event)


def test_rejection_audit_failure_never_blocks_authorised_calls_or_opens_the_gate(
    tmp_path: Path,
) -> None:
    token, grant = issue()

    async def authorised(url: str) -> CallToolResult:
        async with connect(url, "auto", token) as client:
            return await client.call_tool("up__echo", {"text": "ok"})

    sink = FailOnlyRejections(tmp_path / "audit.jsonl")
    with served(tmp_path, [grant], audit=sink, rejection_window_s=0.05) as url:
        assert httpx2.post(url, json={}, timeout=5).status_code == 401
        time.sleep(0.2)  # let a flush hit the failing sink
        assert httpx2.post(url, json={}, timeout=5).status_code == 401
        assert text_of(asyncio.run(authorised(url))) == "ok"


def test_closed_windows_are_flushed_without_further_rejections(tmp_path: Path) -> None:
    _, grant = issue()
    audit_path = tmp_path / "audit.jsonl"
    with served(tmp_path, [grant], rejection_window_s=0.05) as url:
        assert httpx2.post(url, json={}, timeout=5).status_code == 401
        deadline = time.monotonic() + 5
        while "auth_rejections" not in audit_path.read_text():
            assert time.monotonic() < deadline, "no periodic flush"
            time.sleep(0.02)


def test_open_window_is_flushed_at_shutdown(tmp_path: Path) -> None:
    _, grant = issue()
    with served(tmp_path, [grant]) as url:
        assert httpx2.post(url, json={}, timeout=5).status_code == 401
        assert "auth_rejections" not in (tmp_path / "audit.jsonl").read_text()
    assert "auth_rejections" in (tmp_path / "audit.jsonl").read_text()
