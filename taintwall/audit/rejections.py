"""Aggregated audit of rejected requests (D30, THREAT_MODEL T15).

A client with no token must not be able to turn its request volume into audit volume: with a
fail-closed audit (D23) that would let it fill the disk and block every authorised call. So
rejections become one event per window and key. Keys are a fixed reason plus a grant that already
exists, never request data, so state and output stay bounded whatever the client sends. This
never raises: a rejected request is rejected whether or not its count can be written.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from taintwall.grants.store import utc_now

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_S = 60.0
NO_POLICY_VERSION = "pass_through"


class AuditSink(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...


@dataclass
class _Bucket:
    count: int = 0
    latency_sum_ms: float = 0.0
    latency_max_ms: float = 0.0


_Key = tuple[int, str, str | None, str | None]  # window index, reason, grant, agent


class RejectionAggregator:
    def __init__(
        self,
        sink: AuditSink,
        *,
        clock: Callable[[], datetime] = utc_now,
        window_s: float = DEFAULT_WINDOW_S,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self.window_s = window_s
        self._buckets: dict[_Key, _Bucket] = {}

    @property
    def open_buckets(self) -> int:
        return len(self._buckets)

    def _window(self) -> int:
        return int(self._clock().timestamp() // self.window_s)

    def record(
        self,
        reason: str,
        grant_id: str | None = None,
        agent_id: str | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        try:
            window = self._window()
            self._flush(lambda key: key[0] < window)
            bucket = self._buckets.setdefault((window, reason, grant_id, agent_id), _Bucket())
            bucket.count += 1
            bucket.latency_sum_ms += latency_ms
            bucket.latency_max_ms = max(bucket.latency_max_ms, latency_ms)
        except Exception as exc:
            logger.error("could not record a rejection: %s", type(exc).__name__)

    def flush_closed(self) -> None:
        try:
            window = self._window()
            self._flush(lambda key: key[0] < window)
        except Exception as exc:
            logger.error("could not flush rejections: %s", type(exc).__name__)

    def flush_all(self) -> None:
        self._flush(lambda key: True)

    def _flush(self, due: Callable[[_Key], bool]) -> None:
        for key in [k for k in self._buckets if due(k)]:
            bucket = self._buckets.pop(key)  # dropped if the write fails: memory stays bounded
            try:
                self._sink.write(self._event(key, bucket))
            except Exception as exc:
                logger.error(
                    "dropped %d rejection(s) (%s): audit write failed: %s",
                    bucket.count,
                    key[1],
                    type(exc).__name__,
                )

    def _event(self, key: _Key, bucket: _Bucket) -> dict[str, Any]:
        window, reason, grant_id, agent_id = key
        start = datetime.fromtimestamp(window * self.window_s, UTC)
        return {
            "event": "auth_rejections",
            "ts": utc_now().isoformat(timespec="microseconds"),
            "decision": "deny",
            "reason": reason,
            "rule_ids": [reason],
            "policy_version": NO_POLICY_VERSION,
            "grant_id": grant_id,
            "agent_id": agent_id,
            "window_start": start.isoformat(),
            "window_s": self.window_s,
            "count": bucket.count,
            "latency_ms": {
                "sum": round(bucket.latency_sum_ms, 3),
                "max": round(bucket.latency_max_ms, 3),
            },
        }
