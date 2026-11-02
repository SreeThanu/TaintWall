"""Error mapping (D20): fixed agent text, no exception text anywhere. Attack strings included."""

import re
from pathlib import Path

import anyio
import httpx2
import mcp
import mcp_types
import pytest
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import CONNECTION_CLOSED, INTERNAL_ERROR, REQUEST_TIMEOUT

from taintwall.errors import (
    AGENT_TEXT,
    AUTH_ERROR_CODE,
    OUTCOME_KNOWN,
    Outcome,
    map_upstream_exception,
)

SECRET = "sk-ERRMAP-TEST-0000"  # noqa: S105  # deliberately fake


@pytest.mark.parametrize(
    ("exc", "outcome"),
    [
        (TimeoutError(SECRET), Outcome.TIMEOUT),
        (MCPError(code=REQUEST_TIMEOUT, message=SECRET), Outcome.TIMEOUT),
        (MCPError(code=INTERNAL_ERROR, message=f"api_key={SECRET}"), Outcome.PROTOCOL_ERROR),
        (httpx2.ConnectError(SECRET), Outcome.UNAVAILABLE),
        (ConnectionRefusedError(SECRET), Outcome.UNAVAILABLE),
        (anyio.BrokenResourceError(SECRET), Outcome.UNAVAILABLE),
        (ExceptionGroup("g", [httpx2.ReadError(SECRET)]), Outcome.UNAVAILABLE),
        (
            ExceptionGroup("g", [ExceptionGroup("inner", [MCPError(code=-1, message=SECRET)])]),
            Outcome.PROTOCOL_ERROR,
        ),
        (ExceptionGroup("g", [OSError(SECRET), httpx2.ReadError(SECRET)]), Outcome.UNAVAILABLE),
        (ExceptionGroup("g", [OSError(SECRET), ValueError(SECRET)]), Outcome.INTERNAL_ERROR),
        (RuntimeError(SECRET), Outcome.INTERNAL_ERROR),
    ],
)
def test_mapping_and_redaction(exc: Exception, outcome: Outcome) -> None:
    mapped = map_upstream_exception(exc)
    assert mapped.outcome is outcome
    assert SECRET not in mapped.agent_text
    assert SECRET not in repr(mapped.log_fields)
    result = mapped.result()
    assert result.is_error
    assert SECRET not in result.model_dump_json()


def test_protocol_error_text_carries_only_the_code() -> None:
    mapped = map_upstream_exception(MCPError(code=INTERNAL_ERROR, message=SECRET))
    assert mapped.agent_text == f"TaintwallError: upstream error (code {INTERNAL_ERROR})"


def test_fixed_texts_and_outcome_knowledge() -> None:
    assert map_upstream_exception(TimeoutError()).agent_text == AGENT_TEXT[Outcome.TIMEOUT]
    assert OUTCOME_KNOWN[Outcome.TIMEOUT] is False
    assert OUTCOME_KNOWN[Outcome.UNAVAILABLE] is False
    assert OUTCOME_KNOWN[Outcome.OK] is True
    assert set(OUTCOME_KNOWN) == set(Outcome)


def test_auth_error_code_is_in_the_server_range_and_free_in_the_installed_sdk() -> None:
    """A code the SDK defines has a meaning to clients: -32001 would read as a request timeout."""
    assert -32099 <= AUTH_ERROR_CODE <= -32000
    package = Path(mcp.__file__).parent
    types_package = Path(mcp_types.__file__).parent
    defined: set[int] = set()
    for root in (package, types_package):
        for source in root.rglob("*.py"):
            defined |= {int(m) for m in re.findall(r"-32\d{3}\b", source.read_text())}
    # Sensitivity: the scan must see well-known codes, or an empty result would pass.
    assert {INTERNAL_ERROR, REQUEST_TIMEOUT, CONNECTION_CLOSED} <= defined
    assert AUTH_ERROR_CODE not in defined
