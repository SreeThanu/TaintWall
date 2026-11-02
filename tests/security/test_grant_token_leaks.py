"""M1-4 leak tests: the token and its hash appear in no log record and no audit line.

Matrix: agent protocol era x upstream protocol era x case. The token-bearing request is sent for
real in each case, and valid-token cases must succeed, so an empty result cannot pass by accident.
"""

import asyncio
import http.client
import logging
import traceback
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2
import pytest
import yaml
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from taintwall.audit.jsonl import JsonlAuditSink
from taintwall.grants.store import Grant, GrantStore, add_grant, hash_token
from taintwall.mcp_proxy import build_gateway
from taintwall.testing.http import serve_in_thread
from taintwall.upstreams import UpstreamRegistry, UpstreamTarget
from tests.support.grants import LATER, NOW, FakeClock, issue
from tests.support.processes import ready_process, upstream_subprocess

MODES = ["auto", "legacy"]


@dataclass
class Sent:
    """The secret a case sent, and proof that it was handled the expected way."""

    token: str
    status: int | None = None
    result: CallToolResult | None = None


async def call(url: str, mode: str, token: str, tool: str, args: dict[str, Any]) -> CallToolResult:
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx2.AsyncClient(headers=headers) as http,
        Client(streamable_http_client(url, http_client=http), mode=mode) as client,  # type: ignore[arg-type]
    ):
        return await client.call_tool(tool, args)


def text_of(result: CallToolResult) -> str:
    return "".join(b.text for b in result.content if isinstance(b, TextContent))


def in_grant(url: str, mode: str, token: str, _: Grant) -> Sent:
    result = asyncio.run(call(url, mode, token, "up__echo", {"text": "x"}))
    assert not result.is_error
    return Sent(token, result=result)


def tool_not_in_grant(url: str, mode: str, token: str, _: Grant) -> Sent:
    result = asyncio.run(call(url, mode, token, "up__large", {"size_bytes": 1}))
    assert result.is_error
    return Sent(token, result=result)


def upstream_failure(url: str, mode: str, token: str, _: Grant) -> Sent:
    result = asyncio.run(call(url, mode, token, "up__fail", {"kind": "crash"}))
    assert result.is_error
    return Sent(token, result=result)


def expired(url: str, _mode: str, _token: str, __: Grant, *, expired_token: str) -> Sent:
    response = httpx2.post(
        url, headers={"Authorization": f"Bearer {expired_token}"}, json={}, timeout=5
    )
    return Sent(expired_token, status=response.status_code)


def unknown(url: str, _mode: str, token: str, _: Grant) -> Sent:
    wrong = token[:-1] + ("a" if token[-1] != "a" else "b")
    response = httpx2.post(url, headers={"Authorization": f"Bearer {wrong}"}, json={}, timeout=5)
    return Sent(wrong, status=response.status_code)


def malformed_with_token_inside(url: str, _mode: str, token: str, _: Grant) -> Sent:
    headers = {"Authorization": f"Bearer {token} extra"}
    response = httpx2.post(url, headers=headers, json={}, timeout=5)
    return Sent(token, status=response.status_code)


def wrong_scheme(url: str, _mode: str, token: str, _: Grant) -> Sent:
    response = httpx2.post(url, headers={"Authorization": f"Token {token}"}, json={}, timeout=5)
    return Sent(token, status=response.status_code)


def query_string(url: str, _mode: str, token: str, _: Grant) -> Sent:
    # http.client, not httpx2: an HTTP client logs its own request URL at INFO, and that record
    # would come from the caller's side of this one test process, not from the gateway. The real
    # CLI test below checks a gateway process's stderr with no client in it.
    parts = urlsplit(url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    try:
        connection.request("POST", f"{parts.path}?token={token}", body=b"{}")
        return Sent(token, status=connection.getresponse().status)
    finally:
        connection.close()


CASES: dict[str, Callable[..., Sent]] = {
    "in_grant": in_grant,
    "tool_not_in_grant": tool_not_in_grant,
    "upstream_failure": upstream_failure,
    "unknown": unknown,
    "malformed": malformed_with_token_inside,
    "wrong_scheme": wrong_scheme,
    "query_string": query_string,
}


def assert_absent(needles: list[str], caplog: pytest.LogCaptureFixture, audit: str) -> None:
    assert caplog.records, "expected the gateway process to log something"
    for record in caplog.records:
        rendered = record.getMessage() + (record.exc_text or "") + (record.stack_info or "")
        if record.exc_info and record.exc_info[1] is not None:
            rendered += "".join(traceback.format_exception(*record.exc_info))
        for needle in needles:
            assert needle not in rendered, f"secret in log record from {record.name}"
    for needle in needles:
        assert needle not in audit, "secret in audit file"


def needles(token: str) -> list[str]:
    return [token, hash_token(token)]


@pytest.mark.parametrize("case", list(CASES))
@pytest.mark.parametrize("upstream_mode", MODES, ids=lambda m: f"upstream-{m}")
@pytest.mark.parametrize("agent_mode", MODES, ids=lambda m: f"agent-{m}")
def test_token_is_in_no_log_or_audit_record(
    tmp_path: Path,
    agent_mode: str,
    upstream_mode: str,
    case: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    token, grant = issue(tools=["up__echo", "up__fail"])
    audit_path = tmp_path / "audit.jsonl"
    with upstream_subprocess() as upstream:
        target = UpstreamTarget("up", upstream.url, timeout_s=5, mode=upstream_mode)  # type: ignore[arg-type]
        gateway = build_gateway([target], JsonlAuditSink(audit_path))
        with serve_in_thread(gateway.http_app(GrantStore([grant]))) as base:
            sent = CASES[case](f"{base}/mcp", agent_mode, token, grant)
    if sent.status is not None:
        assert sent.status == 401
    assert_absent(needles(sent.token) + needles(token), caplog, audit_path.read_text())


@pytest.mark.parametrize("agent_mode", MODES, ids=lambda m: f"agent-{m}")
def test_expired_token_is_in_no_log_or_audit_record(
    tmp_path: Path, agent_mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    token, grant = issue(grant_id="old", expires_at=NOW)
    audit_path = tmp_path / "audit.jsonl"
    with upstream_subprocess() as upstream:
        target = UpstreamTarget("up", upstream.url, timeout_s=5)
        gateway = build_gateway([target], JsonlAuditSink(audit_path))
        app = gateway.http_app(GrantStore([grant]), clock=FakeClock())
        with serve_in_thread(app) as base:
            sent = expired(f"{base}/mcp", agent_mode, token, grant, expired_token=token)
    assert sent.status == 401
    audit = audit_path.read_text()
    assert_absent(needles(token), caplog, audit)
    assert "auth_expired" in audit  # sensitivity: the rejection was recorded, without the token


@pytest.mark.parametrize("agent_mode", MODES, ids=lambda m: f"agent-{m}")
def test_internal_error_is_in_no_log_or_audit_record(
    tmp_path: Path,
    agent_mode: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    token, grant = issue()
    audit_path = tmp_path / "audit.jsonl"

    def broken_resolve(self: UpstreamRegistry, name: str) -> None:
        raise RuntimeError(f"internal state with token={token}")

    with upstream_subprocess() as upstream:
        target = UpstreamTarget("up", upstream.url, timeout_s=5)
        gateway = build_gateway([target], JsonlAuditSink(audit_path))
        with serve_in_thread(gateway.http_app(GrantStore([grant]))) as base:
            monkeypatch.setattr(UpstreamRegistry, "resolve", broken_resolve)
            result = asyncio.run(call(f"{base}/mcp", agent_mode, token, "up__echo", {"text": "x"}))
    assert result.is_error
    assert token not in text_of(result)
    assert_absent(needles(token), caplog, audit_path.read_text())


@pytest.mark.parametrize("upstream_mode", MODES, ids=lambda m: f"upstream-{m}")
@pytest.mark.parametrize("agent_mode", MODES, ids=lambda m: f"agent-{m}")
def test_token_is_not_forwarded_upstream(
    tmp_path: Path, agent_mode: str, upstream_mode: str
) -> None:
    """Attack: the upstream's echo_request returns the headers it received."""
    token, grant = issue()
    with upstream_subprocess() as upstream:
        target = UpstreamTarget("up", upstream.url, timeout_s=5, mode=upstream_mode)  # type: ignore[arg-type]
        gateway = build_gateway([target], JsonlAuditSink(tmp_path / "audit.jsonl"))
        with serve_in_thread(gateway.http_app(GrantStore([grant]))) as base:
            result = asyncio.run(
                call(f"{base}/mcp", agent_mode, token, "up__echo_request", {"payload": {"a": 1}})
            )
    assert not result.is_error
    assert result.structured_content is not None
    headers = result.structured_content["headers"]
    assert headers, "sensitivity: the upstream reported headers"
    assert "authorization" not in {k.lower() for k in headers}
    assert token not in str(result.model_dump())


@pytest.mark.parametrize("agent_mode", MODES, ids=lambda m: f"agent-{m}")
def test_real_cli_process_logs_no_token_and_no_access_lines(
    tmp_path: Path, agent_mode: str
) -> None:
    """The real logging config: caplog cannot see uvicorn's access log, the process stderr can."""
    grants_path = tmp_path / "grants.yaml"
    audit_path = tmp_path / "audit.jsonl"
    token, _ = add_grant(grants_path, agent_id="a", tools=["up__echo"], expires_at=LATER)
    with ExitStack() as stack:
        upstream = stack.enter_context(upstream_subprocess())
        config_path = tmp_path / "gateway.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "port": 0,
                    "audit_path": str(audit_path),
                    "grants_path": str(grants_path),
                    "upstreams": [{"namespace": "up", "url": upstream.url}],
                }
            )
        )
        args = ["-m", "taintwall.app", "--config", str(config_path), "--print-ready"]
        gateway = stack.enter_context(ready_process(args))
        url = gateway.url
        ok = asyncio.run(call(url, agent_mode, token, "up__echo", {"text": "x"}))
        denied = asyncio.run(call(url, agent_mode, token, "up__large", {"size_bytes": 1}))
        query = httpx2.post(f"{url}?token={token}", json={}, timeout=5)
        scheme = httpx2.post(url, headers={"Authorization": f"Token {token}"}, json={}, timeout=5)
        gateway.stop()
        stderr = gateway.stderr_text()
    assert not ok.is_error and denied.is_error
    assert (query.status_code, scheme.status_code) == (401, 401)
    assert "upstream up:" in stderr  # sensitivity: stderr captured the gateway's logs
    assert "uvicorn.access" not in stderr  # access log is off (its URLs carry query strings)
    for needle in needles(token):
        assert needle not in stderr
        assert needle not in audit_path.read_text()
