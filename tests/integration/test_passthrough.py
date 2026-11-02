"""M1-3 pass-through, in-process (fast): results identical to direct, both protocol eras."""

import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from taintwall.audit.jsonl import JsonlAuditSink
from taintwall.errors import AGENT_TEXT, Outcome
from taintwall.mcp_proxy import Gateway, build_gateway
from taintwall.upstreams import UpstreamStartupError, UpstreamTarget
from tests.support.upstream import TOOL_NAMES, build_upstream

MODES = ["auto", "legacy"]

# Calls compared field by field against the same call made directly to the upstream.
IDENTICAL_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("echo", {"text": "héllo ✓"}),
    ("read_message", {}),
    ("echo_request", {"payload": {"a": 1, "b": ["x", None]}}),
    ("large", {"size_bytes": 4096}),
    ("slow", {"seconds": 0.01}),
    ("fail", {"kind": "tool_error"}),
    ("fail", {"kind": "crash"}),
]


def beta_upstream() -> MCPServer:
    server = MCPServer("beta")

    @server.tool()
    def whoami() -> str:
        return "beta"

    return server


def run[T](fn: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return asyncio.run(fn())


def gateway(tmp_path: Path, upstream_mode: str, **extra: Any) -> Gateway:
    targets = [
        UpstreamTarget("alpha", build_upstream("alpha"), timeout_s=5, mode=upstream_mode),  # type: ignore[arg-type]
        UpstreamTarget("beta", beta_upstream(), timeout_s=5, mode=upstream_mode),  # type: ignore[arg-type]
        *extra.get("more", []),
    ]
    return build_gateway(targets, extra.get("audit") or JsonlAuditSink(tmp_path / "audit.jsonl"))


def fields(result: CallToolResult) -> tuple[Any, ...]:
    return (
        [block.model_dump() for block in result.content],
        result.is_error,
        result.structured_content,
    )


def text_of(result: CallToolResult) -> str:
    return "".join(b.text for b in result.content if isinstance(b, TextContent))


def audit_events(tmp_path: Path) -> list[dict[str, Any]]:
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


@pytest.mark.parametrize("upstream_mode", MODES)
@pytest.mark.parametrize("agent_mode", MODES)
def test_tools_list_is_namespaced_and_unchanged(
    tmp_path: Path, agent_mode: str, upstream_mode: str
) -> None:
    async def go() -> None:
        async with (
            Client(gateway(tmp_path, upstream_mode).server, mode=agent_mode) as via,  # type: ignore[arg-type]
            Client(build_upstream(), mode=upstream_mode) as direct,  # type: ignore[arg-type]
        ):
            gw_tools = {t.name: t for t in (await via.list_tools()).tools}
            assert set(gw_tools) == {f"alpha__{n}" for n in TOOL_NAMES} | {"beta__whoami"}
            for tool in (await direct.list_tools()).tools:
                proxied = gw_tools[f"alpha__{tool.name}"]
                assert proxied.description == tool.description
                assert proxied.input_schema == tool.input_schema
                assert proxied.output_schema == tool.output_schema

    run(go)


@pytest.mark.parametrize("upstream_mode", MODES)
@pytest.mark.parametrize("agent_mode", MODES)
def test_results_identical_to_direct(tmp_path: Path, agent_mode: str, upstream_mode: str) -> None:
    async def go() -> None:
        async with (
            Client(gateway(tmp_path, upstream_mode).server, mode=agent_mode) as via,  # type: ignore[arg-type]
            Client(build_upstream(), mode=upstream_mode) as direct,  # type: ignore[arg-type]
        ):
            for name, args in IDENTICAL_CALLS:
                expected = fields(await direct.call_tool(name, args))
                assert fields(await via.call_tool(f"alpha__{name}", args)) == expected, name

    run(go)


@pytest.mark.parametrize("agent_mode", MODES)
def test_routes_by_namespace(tmp_path: Path, agent_mode: str) -> None:
    async def go() -> None:
        async with Client(gateway(tmp_path, "auto").server, mode=agent_mode) as via:  # type: ignore[arg-type]
            assert text_of(await via.call_tool("beta__whoami", {})) == "beta"
            assert text_of(await via.call_tool("alpha__echo", {"text": "a"})) == "a"

    run(go)


@pytest.mark.parametrize("agent_mode", MODES)
def test_protocol_error_becomes_fixed_error(tmp_path: Path, agent_mode: str) -> None:
    async def go() -> CallToolResult:
        async with Client(gateway(tmp_path, "auto").server, mode=agent_mode) as via:  # type: ignore[arg-type]
            return await via.call_tool("alpha__fail", {"kind": "protocol_error"})

    result = run(go)
    assert result.is_error
    assert text_of(result) == "TaintwallError: upstream error (code -32603)"


@pytest.mark.parametrize("name", ["nope", "alpha__nope", "gamma__echo", "alpha_echo", ""])
def test_unknown_tool_is_rejected(tmp_path: Path, name: str) -> None:
    async def go() -> CallToolResult:
        async with Client(gateway(tmp_path, "auto").server) as via:
            return await via.call_tool(name, {})

    result = run(go)
    assert result.is_error
    assert text_of(result) == AGENT_TEXT[Outcome.UNKNOWN_TOOL]
    decision = audit_events(tmp_path)[-1]
    assert decision["event"] == "decision"
    assert decision["decision"] == "deny"


def test_concurrent_calls_reuse_one_client_and_route_correctly(tmp_path: Path) -> None:
    async def go() -> list[str]:
        async with Client(gateway(tmp_path, "auto").server) as via:
            results = await asyncio.gather(
                *(via.call_tool("alpha__echo", {"text": f"msg-{i}"}) for i in range(20))
            )
            return [text_of(r) for r in results]

    assert run(go) == [f"msg-{i}" for i in range(20)]


def test_audit_events_have_reasons_and_no_argument_values(tmp_path: Path) -> None:
    probe = "AUDIT-PROBE-ARGUMENT-VALUE"

    async def go() -> None:
        async with Client(gateway(tmp_path, "auto").server) as via:
            await via.call_tool("alpha__echo", {"text": probe})

    run(go)
    raw = (tmp_path / "audit.jsonl").read_text()
    assert probe not in raw
    decision, result = audit_events(tmp_path)
    assert decision["event"] == "decision" and decision["decision"] == "allow"
    assert result["event"] == "result"
    for event in (decision, result):
        assert event["call_id"] == decision["call_id"]
        assert event["tool"] == "alpha__echo"
        assert event["upstream"] == "alpha"
        assert event["policy_version"] == "pass_through"
        assert event["rule_ids"] == []
        assert event["argument_keys"] == ["text"]
    assert result["outcome"] == "ok" and result["outcome_known"] is True
    assert set(result["timings_ms"]) == {"decide_and_audit", "upstream", "total"}


def test_timeout_outcome_is_unknown(tmp_path: Path) -> None:
    slow_target = UpstreamTarget("slowup", build_upstream("slowup"), timeout_s=0.3)

    async def go() -> CallToolResult:
        async with Client(gateway(tmp_path, "auto", more=[slow_target]).server) as via:
            return await via.call_tool("slowup__slow", {"seconds": 2})

    result = run(go)
    assert text_of(result) == AGENT_TEXT[Outcome.TIMEOUT]
    event = audit_events(tmp_path)[-1]
    assert event["outcome"] == "timeout"
    assert event["outcome_known"] is False  # the upstream may have executed the call


class FailingAudit:
    def write(self, event: dict[str, Any]) -> None:
        raise OSError("disk full")


def test_audit_failure_blocks_the_call(tmp_path: Path) -> None:
    calls: list[str] = []
    spy = MCPServer("spy")

    @spy.tool()
    def touch() -> str:
        calls.append("touched")
        return "touched"

    gw = build_gateway([UpstreamTarget("spy", spy)], FailingAudit())

    async def go() -> CallToolResult:
        async with Client(gw.server) as via:
            return await via.call_tool("spy__touch", {})

    result = run(go)
    assert text_of(result) == AGENT_TEXT[Outcome.AUDIT_UNAVAILABLE]
    assert calls == []  # fail closed: never forwarded


def test_startup_fails_fast_when_an_upstream_is_unreachable(tmp_path: Path) -> None:
    # Port 9 (discard) is closed on a normal machine: the connection is refused.
    dead = UpstreamTarget("dead", "http://127.0.0.1:9/mcp", timeout_s=2)
    gw = build_gateway([dead], JsonlAuditSink(tmp_path / "audit.jsonl"))

    async def go() -> None:
        async with Client(gw.server):
            pass

    with pytest.raises(BaseException) as excinfo:
        run(go)
    leaves: list[BaseException] = [excinfo.value]
    while any(isinstance(e, BaseExceptionGroup) for e in leaves):
        leaves = [
            x for e in leaves for x in (e.exceptions if isinstance(e, BaseExceptionGroup) else [e])
        ]
    assert any(isinstance(e, UpstreamStartupError) for e in leaves)
