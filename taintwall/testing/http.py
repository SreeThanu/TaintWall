"""Serve an ASGI app on 127.0.0.1 in a background thread, for tests and benchmarks."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import uvicorn

START_TIMEOUT_S = 10.0
STOP_TIMEOUT_S = 5.0


@contextmanager
def serve_in_thread(app: Any, name: str = "taintwall-http") -> Iterator[str]:
    """Yield the base URL (`http://127.0.0.1:<port>`) while `app` is served.

    The socket is bound to port 0 before uvicorn starts, so there is no free-port race. On exit
    the server is stopped and its thread joined; a thread that does not stop is an error.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name=name, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + START_TIMEOUT_S
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError(f"{name}: server did not start")
            time.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=STOP_TIMEOUT_S)
        sock.close()
        if thread.is_alive():
            raise RuntimeError(f"{name}: server thread did not stop")
