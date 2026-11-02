"""Entry point: `taintwall --config config/dev.yaml`."""

from __future__ import annotations

import argparse
import logging
import socket
import sys
from pathlib import Path
from typing import Any

import uvicorn

from taintwall.audit.jsonl import JsonlAuditSink
from taintwall.config import GatewayConfig, load_config
from taintwall.log_redaction import DropExceptionDetails
from taintwall.mcp_proxy import build_gateway
from taintwall.upstreams import targets_from_config


def build_app(config: GatewayConfig, audit: JsonlAuditSink | None = None) -> Any:
    """The gateway's ASGI app: one MCP endpoint at /mcp, stateless HTTP (D19)."""
    gateway = build_gateway(targets_from_config(config), audit or JsonlAuditSink(config.audit_path))
    return gateway.server.streamable_http_app(stateless_http=True, host=config.host)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(DropExceptionDetails())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="taintwall", description="Taintwall MCP gateway")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int, help="override the configured port; 0 picks a free one")
    parser.add_argument(
        "--print-ready", action="store_true", help="print 'READY <url>' to stdout once listening"
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    port = config.port if args.port is None else args.port
    configure_logging()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((config.host, port))
    bound_port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(build_app(config), lifespan="on", log_config=None))
    if args.print_ready:
        _announce_when_started(server, f"http://{config.host}:{bound_port}/mcp")
    server.run(sockets=[sock])
    return 0 if server.started else 1


def _announce_when_started(server: uvicorn.Server, url: str) -> None:
    original = server.startup

    async def startup(sockets: list[socket.socket] | None = None) -> None:
        await original(sockets=sockets)
        if server.started:
            print(f"READY {url}", flush=True)

    server.startup = startup  # type: ignore[method-assign]


if __name__ == "__main__":
    sys.exit(main())
