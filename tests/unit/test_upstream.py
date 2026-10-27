"""M1-2: the test upstream, called directly in-process over both protocol eras."""

import asyncio
import json
import time
from typing import Any

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent

from tests.support.upstream import (
    FAKE_SECRET,
    MAX_LARGE_BYTES,
    MAX_SLEEP_S,
    TOOL_NAMES,
    UNTRUSTED_EMAIL,
    UNTRUSTED_EMAIL_MIXED_CASE,
    UNTRUSTED_IBAN,
    UNTRUSTED_IBAN_SPACED,
    UNTRUSTED_URL,
    build_upstream,
)

# legacy: 2025-11-25 handshake with real JSON-RPC framing; auto: 2026-07-28 direct dispatch.
MODES = ["legacy", "auto"]


def call(mode: str, name: str, arguments: dict[str, Any]) -> CallToolResult:
    async def run() -> CallToolResult:
        async with Client(build_upstream(), mode=mode) as client:  # type: ignore[arg-type]
            return await client.call_tool(name, arguments)

    return asyncio.run(run())


def call_expecting_mcp_error(mode: str, name: str, arguments: dict[str, Any]) -> MCPError:
    # Catch inside the client context: on exit, anyio's task group would wrap the error in an
    # ExceptionGroup.
    async def run() -> MCPError:
        async with Client(build_upstream(), mode=mode) as client:  # type: ignore[arg-type]
            try:
                await client.call_tool(name, arguments)
            except MCPError as exc:
                return exc
        raise AssertionError("expected MCPError")

    return asyncio.run(run())


def text_of(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


@pytest.mark.parametrize("mode", MODES)
def test_lists_exactly_the_six_tools(mode: str) -> None:
    async def run() -> set[str]:
        async with Client(build_upstream(), mode=mode) as client:  # type: ignore[arg-type]
            return {tool.name for tool in (await client.list_tools()).tools}

    assert asyncio.run(run()) == TOOL_NAMES


@pytest.mark.parametrize("mode", MODES)
def test_echo_round_trips(mode: str) -> None:
    result = call(mode, "echo", {"text": "héllo ✓"})
    assert not result.is_error
    assert text_of(result) == "héllo ✓"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "atom",
    [
        UNTRUSTED_IBAN,
        UNTRUSTED_IBAN_SPACED,
        UNTRUSTED_EMAIL,
        UNTRUSTED_EMAIL_MIXED_CASE,
        UNTRUSTED_URL,
    ],
)
def test_read_message_contains_each_atom_and_variant(mode: str, atom: str) -> None:
    result = call(mode, "read_message", {})
    assert not result.is_error
    assert atom in text_of(result)


@pytest.mark.parametrize("mode", MODES)
def test_fail_tool_error_leaks_secret_as_error_result(mode: str) -> None:
    # Intentional: the upstream leaks so gateway tests can check that the gateway scrubs it.
    result = call(mode, "fail", {"kind": "tool_error"})
    assert result.is_error
    assert FAKE_SECRET in text_of(result)


@pytest.mark.parametrize("mode", MODES)
def test_fail_protocol_error_leaks_secret_as_jsonrpc_error(mode: str) -> None:
    error = call_expecting_mcp_error(mode, "fail", {"kind": "protocol_error"})
    assert FAKE_SECRET in error.error.message


@pytest.mark.parametrize("mode", MODES)
def test_fail_crash_is_redacted_by_the_sdk(mode: str) -> None:
    result = call(mode, "fail", {"kind": "crash"})
    assert result.is_error
    assert text_of(result) == "Error executing tool fail"
    assert FAKE_SECRET not in text_of(result)


@pytest.mark.parametrize("mode", MODES)
def test_echo_request_returns_arguments_and_no_headers_in_process(mode: str) -> None:
    payload = {"to": "someone@example.com", "token": "abc", "nested": {"n": [1, 2]}}
    result = call(mode, "echo_request", {"payload": payload})
    assert not result.is_error
    echoed = result.structured_content or json.loads(text_of(result))
    assert echoed == {"arguments": payload, "headers": {}}


@pytest.mark.parametrize("mode", MODES)
def test_slow_sleeps_and_rejects_over_cap(mode: str) -> None:
    started = time.monotonic()
    result = call(mode, "slow", {"seconds": 0.2})
    assert not result.is_error
    assert time.monotonic() - started >= 0.2
    started = time.monotonic()
    rejected = call(mode, "slow", {"seconds": MAX_SLEEP_S + 1})
    assert rejected.is_error
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("size", [0, 1, 17, 1_000_000])
def test_large_returns_exact_size(mode: str, size: int) -> None:
    result = call(mode, "large", {"size_bytes": size})
    assert not result.is_error
    assert len(text_of(result).encode()) == size


@pytest.mark.parametrize("mode", MODES)
def test_large_rejects_over_cap(mode: str) -> None:
    assert call(mode, "large", {"size_bytes": MAX_LARGE_BYTES + 1}).is_error
