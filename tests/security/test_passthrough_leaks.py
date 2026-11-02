"""M1-3 leak tests (D20, non-negotiable 2).

The upstream runs as a subprocess, so every log record captured here comes from the gateway
process (gateway code, the MCP SDK, httpx2, uvicorn). The gateway is served over HTTP and called
over HTTP. Matrix: agent->gateway mode x gateway->upstream mode x failure case.
"""

import asyncio
import json
import logging
import traceback
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from taintwall.audit.jsonl import JsonlAuditSink
from taintwall.errors import AGENT_TEXT, Outcome
from taintwall.grants.store import GrantStore
from taintwall.mcp_proxy import build_gateway
from taintwall.testing.http import serve_in_thread
from taintwall.upstreams import UpstreamRegistry, UpstreamTarget
from tests.support.grants import issue
from tests.support.processes import ReadyProcess, upstream_subprocess
from tests.support.upstream import FAKE_SECRET

MODES = ["auto", "legacy"]
GATEWAY_TIMEOUT_S = 1.0
TOKEN, GRANT = issue()
STORE = GrantStore([GRANT])


@dataclass
class RunningGateway:
    url: str
    audit_path: Path
    upstream: ReadyProcess


@pytest.fixture(scope="module")
def upstream() -> Iterator[ReadyProcess]:
    with upstream_subprocess() as running:
        yield running


@pytest.fixture(scope="module", params=MODES, ids=lambda m: f"upstream-{m}")
def gw(
    request: pytest.FixtureRequest, upstream: ReadyProcess, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[RunningGateway]:
    audit_path = tmp_path_factory.mktemp("audit") / "audit.jsonl"
    target = UpstreamTarget("up", upstream.url, timeout_s=GATEWAY_TIMEOUT_S, mode=request.param)
    gateway = build_gateway([target], JsonlAuditSink(audit_path))
    with serve_in_thread(gateway.http_app(STORE)) as base:
        yield RunningGateway(f"{base}/mcp", audit_path, upstream)


def call(url: str, agent_mode: str, name: str, arguments: dict[str, Any]) -> CallToolResult:
    async def go() -> CallToolResult:
        async with (
            httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http,
            Client(streamable_http_client(url, http_client=http), mode=agent_mode) as client,  # type: ignore[arg-type]
        ):
            return await client.call_tool(name, arguments)

    return asyncio.run(go())


def text_of(result: CallToolResult) -> str:
    return "".join(b.text for b in result.content if isinstance(b, TextContent))


def assert_secret_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    assert caplog.records, "expected the gateway process to log something"
    for record in caplog.records:
        rendered = record.getMessage() + (record.exc_text or "") + (record.stack_info or "")
        if record.exc_info and record.exc_info[1] is not None:
            rendered += "".join(traceback.format_exception(*record.exc_info))
        assert FAKE_SECRET not in rendered, f"secret in log record from {record.name}"


def last_result_event(audit_path: Path) -> dict[str, Any]:
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    return next(e for e in reversed(events) if e["event"] == "result")


CASES = {
    # case: (tool, arguments, expected agent text or None for "forwarded upstream text")
    "tool_error": ("up__fail", {"kind": "tool_error"}, None),
    "protocol_error": (
        "up__fail",
        {"kind": "protocol_error"},
        "TaintwallError: upstream error (code -32603)",
    ),
    "crash": ("up__fail", {"kind": "crash"}, "Error executing tool fail"),
    "timeout": ("up__slow", {"seconds": GATEWAY_TIMEOUT_S + 2}, AGENT_TEXT[Outcome.TIMEOUT]),
}


@pytest.mark.parametrize("case", list(CASES))
@pytest.mark.parametrize("agent_mode", MODES, ids=lambda m: f"agent-{m}")
def test_no_secret_in_logs_or_audit(
    gw: RunningGateway, agent_mode: str, case: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    tool, arguments, expected = CASES[case]
    result = call(gw.url, agent_mode, tool, arguments)
    assert result.is_error
    if expected is None:
        # Upstream tool output is forwarded unchanged, including text the upstream chose to echo.
        # Scrubbing vault-injected secret values from results is M3-4 (THREAT_MODEL T5).
        assert FAKE_SECRET in text_of(result)
    else:
        assert text_of(result) == expected
        assert FAKE_SECRET not in text_of(result)
    assert_secret_not_logged(caplog)
    assert FAKE_SECRET not in gw.audit_path.read_text()
    event = last_result_event(gw.audit_path)
    if case == "timeout":
        assert event["outcome"] == "timeout"
        assert event["outcome_known"] is False


@pytest.mark.parametrize("agent_mode", MODES, ids=lambda m: f"agent-{m}")
def test_upstream_really_had_the_secret(gw: RunningGateway, agent_mode: str) -> None:
    """Sensitivity: the crash secret exists and is logged by the upstream process itself."""
    call(gw.url, agent_mode, "up__fail", {"kind": "crash"})
    assert FAKE_SECRET in gw.upstream.stderr_text()


@pytest.mark.parametrize("agent_mode", MODES, ids=lambda m: f"agent-{m}")
def test_gateway_internal_error_is_redacted(
    gw: RunningGateway,
    agent_mode: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    def broken_resolve(self: UpstreamRegistry, name: str) -> None:
        raise RuntimeError(f"internal state with api_key={FAKE_SECRET}")

    monkeypatch.setattr(UpstreamRegistry, "resolve", broken_resolve)
    result = call(gw.url, agent_mode, "up__echo", {"text": "x"})
    assert result.is_error
    assert text_of(result) == AGENT_TEXT[Outcome.INTERNAL_ERROR]
    assert_secret_not_logged(caplog)


def assert_clean_shutdown(caplog: pytest.LogCaptureFixture) -> None:
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [], [f"{r.name}: {r.getMessage()[:80]}" for r in errors]


@pytest.mark.parametrize("upstream_mode", MODES, ids=lambda m: f"upstream-{m}")
@pytest.mark.parametrize("agent_mode", MODES, ids=lambda m: f"agent-{m}")
def test_dead_upstream_fails_closed_and_others_keep_working(
    tmp_path: Path, agent_mode: str, upstream_mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    """D25: one upstream dying must not cancel the gateway or the other upstreams."""
    caplog.set_level(logging.DEBUG)
    audit_path = tmp_path / "audit.jsonl"
    with ExitStack() as stack:
        a = stack.enter_context(upstream_subprocess())
        b = stack.enter_context(upstream_subprocess())
        targets = [
            UpstreamTarget("a", a.url, timeout_s=GATEWAY_TIMEOUT_S, mode=upstream_mode),  # type: ignore[arg-type]
            UpstreamTarget("b", b.url, timeout_s=GATEWAY_TIMEOUT_S, mode=upstream_mode),  # type: ignore[arg-type]
        ]
        gateway = build_gateway(targets, JsonlAuditSink(audit_path))
        url = f"{stack.enter_context(serve_in_thread(gateway.http_app(STORE)))}/mcp"
        assert text_of(call(url, agent_mode, "a__echo", {"text": "a"})) == "a"
        a.stop()
        for _ in range(3):
            dead = call(url, agent_mode, "a__echo", {"text": "after"})
            assert dead.is_error
            assert text_of(dead) == AGENT_TEXT[Outcome.UNAVAILABLE]
            event = last_result_event(audit_path)
            assert event["outcome"] == "unavailable"
            # Unknown only if the call was actually sent before the break was noticed.
            assert event["outcome_known"] is (not event["forwarded"])
            assert text_of(call(url, agent_mode, "b__echo", {"text": "b"})) == "b"
    # serve_in_thread raises if the gateway thread does not stop.
    assert_secret_not_logged(caplog)
    assert_clean_shutdown(caplog)
