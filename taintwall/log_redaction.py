"""Defense in depth for logs (DECISIONS D20).

The primary guarantee is that gateway handlers catch every exception themselves, so the SDK never
logs one. This filter, attached to the gateway's log handlers, additionally drops exception
details and message arguments from library records, which is where a secret could appear.
"""

from __future__ import annotations

import logging

LIBRARY_LOGGERS = ("mcp", "httpx2", "httpcore2", "uvicorn", "sse_starlette")
# These libraries log full request/response payloads at DEBUG (observed in M1-3: SSE messages in
# mcp.client.streamable_http, response chunks in sse_starlette.sse). The gateway cannot tell
# which payload text is secret, so payload logging is never enabled in a gateway process.
PAYLOAD_LOGGERS = ("mcp", "httpx2", "httpcore2", "sse_starlette")
PAYLOAD_LOGGER_MIN_LEVEL = logging.INFO


class DropExceptionDetails(logging.Filter):
    """Strip tracebacks and %-arguments from records of the given library loggers."""

    def __init__(self, prefixes: tuple[str, ...] = LIBRARY_LOGGERS) -> None:
        super().__init__()
        self._prefixes = prefixes

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "taintwall" or record.name.startswith("taintwall."):
            return True
        if any(record.name == p or record.name.startswith(p + ".") for p in self._prefixes):
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            record.args = ()
        return True


def harden_library_logging() -> None:
    """Keep payload-logging libraries at INFO or above, whatever the root level is."""
    for name in PAYLOAD_LOGGERS:
        library = logging.getLogger(name)
        if library.level < PAYLOAD_LOGGER_MIN_LEVEL:
            library.setLevel(PAYLOAD_LOGGER_MIN_LEVEL)
