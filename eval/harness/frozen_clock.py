"""Freeze the wall clock AgentDojo's dev-suite tools read, so ground-truth replay is deterministic.

Clock sources in agentdojo 0.1.35 for workspace and slack (grep, 27 Oct 2026):
- `default_suites/v1/tools/email_client.py:95`  `timestamp=datetime.datetime.now()`
- `default_suites/v1/tools/cloud_drive_client.py:41, :62`  `last_modified=datetime.datetime.now()`
- `default_suites/v1/tools/types.py:80`  `Email.timestamp` `default_factory=datetime.datetime.now`.
  Pydantic holds that function reference, so it cannot be patched here; the only `Email(...)` in
  tool code (`email_client.py:88`) passes `timestamp`, and the replay precondition (two direct
  runs byte-identical) would catch the default ever firing.
No random or uuid sources were found. Both patched modules use `datetime` only for `now()`.
"""

from __future__ import annotations

import datetime
import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

CLOCK_MODULES = (
    "agentdojo.default_suites.v1.tools.email_client",
    "agentdojo.default_suites.v1.tools.cloud_drive_client",
)
# Arbitrary fixed instant; only determinism matters.
FIXED_NOW = datetime.datetime(2024, 5, 15, 12, 0, 0)


class _FrozenDatetimeType:
    """Stands in for `datetime.datetime`; `now()` returns a real `datetime.datetime`."""

    def __init__(self, fixed: datetime.datetime) -> None:
        self._fixed = fixed

    def now(self, tz: datetime.tzinfo | None = None) -> datetime.datetime:
        if tz is not None:
            raise NotImplementedError("frozen clock does not support tz-aware now()")
        return self._fixed

    def __getattr__(self, name: str) -> Any:
        return getattr(datetime.datetime, name)


class _FrozenDatetimeModule:
    """Stands in for the `datetime` module inside the patched modules."""

    def __init__(self, fixed: datetime.datetime) -> None:
        self.datetime = _FrozenDatetimeType(fixed)

    def __getattr__(self, name: str) -> Any:
        return getattr(datetime, name)


@contextmanager
def frozen_clock(fixed: datetime.datetime = FIXED_NOW) -> Iterator[datetime.datetime]:
    """Patch the clock in CLOCK_MODULES for the duration of the block, then restore it.

    The patch is process-wide, so it also covers the EnvBridge HTTP thread; tasks run one at a time.
    """
    if type(fixed) is not datetime.datetime:
        raise TypeError("fixed must be a datetime.datetime")
    modules = [importlib.import_module(name) for name in CLOCK_MODULES]
    for module in modules:
        if module.datetime is not datetime:
            raise RuntimeError(f"{module.__name__}.datetime is already patched")
    try:
        for module in modules:
            module.datetime = _FrozenDatetimeModule(fixed)  # type: ignore[attr-defined]
        yield fixed
    finally:
        for module in modules:
            module.datetime = datetime  # type: ignore[attr-defined]
