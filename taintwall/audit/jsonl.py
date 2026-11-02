"""Synchronous JSONL audit sink (DECISIONS D23).

Each event is one line, written and flushed with no await in between, so lines from concurrent
calls cannot interleave. Events carry names, sizes, outcomes and timings, never argument values
or result content (non-negotiable 2).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, TextIO


class JsonlAuditSink:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file: TextIO = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._file.close()
