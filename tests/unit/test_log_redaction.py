import logging
import sys

import pytest

from taintwall.log_redaction import DropExceptionDetails

SECRET = "sk-LOGFILTER-TEST-0000"  # noqa: S105  # deliberately fake


def _record(name: str) -> logging.LogRecord:
    try:
        raise RuntimeError(f"api_key={SECRET}")
    except RuntimeError:
        exc_info = sys.exc_info()
    return logging.LogRecord(
        name, logging.ERROR, __file__, 1, "Tool %r failed: %r", ("x", SECRET), exc_info
    )


def test_library_records_lose_traceback_and_args() -> None:
    handler = logging.StreamHandler()
    record = _record("mcp.server.lowlevel")
    assert DropExceptionDetails().filter(record)
    assert SECRET not in handler.format(record)


def test_gateway_records_are_left_alone() -> None:
    record = _record("taintwall.mcp_proxy")
    assert DropExceptionDetails().filter(record)
    assert record.args == ("x", SECRET)  # gateway code never logs exception text in the first place


def test_gateway_never_enables_payload_debug_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    from taintwall.log_redaction import PAYLOAD_LOGGERS
    from taintwall.mcp_proxy import build_gateway

    for name in PAYLOAD_LOGGERS:
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)
    root = logging.getLogger()
    monkeypatch.setattr(root, "level", logging.DEBUG)

    class NullAudit:
        def write(self, event: dict[str, object]) -> None:
            pass

    build_gateway([], NullAudit())
    for name in (*PAYLOAD_LOGGERS, "mcp.client.streamable_http", "sse_starlette.sse"):
        assert not logging.getLogger(name).isEnabledFor(logging.DEBUG), name
