"""M1-4 rejection aggregation: attacker volume must not become audit volume (THREAT_MODEL T15)."""

import logging
from datetime import timedelta
from typing import Any

import pytest

from taintwall.audit.rejections import RejectionAggregator
from tests.support.grants import NOW, FakeClock


class ListSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def write(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class FailingSink:
    def write(self, event: dict[str, Any]) -> None:
        raise OSError("disk full")


def test_many_rejections_are_one_event_per_reason() -> None:
    sink, clock = ListSink(), FakeClock()
    agg = RejectionAggregator(sink, clock=clock)
    for _ in range(10_000):
        agg.record("auth_unknown_token", latency_ms=1.0)
    agg.record("auth_missing_header", latency_ms=3.0)
    assert sink.events == []  # nothing written per request
    agg.flush_all()
    counts = {e["reason"]: e["count"] for e in sink.events}
    assert counts == {"auth_unknown_token": 10_000, "auth_missing_header": 1}


def test_event_explains_the_decision() -> None:
    sink = ListSink()
    agg = RejectionAggregator(sink, clock=FakeClock(), window_s=60)
    agg.record("auth_expired", grant_id="g-1", agent_id="agent-1", latency_ms=2.0)
    agg.record("auth_expired", grant_id="g-1", agent_id="agent-1", latency_ms=4.0)
    agg.flush_all()
    [event] = sink.events
    assert event["event"] == "auth_rejections"
    assert event["decision"] == "deny"
    assert event["reason"] == "auth_expired"
    assert event["rule_ids"] == ["auth_expired"]
    assert event["policy_version"] == "pass_through"
    assert (event["grant_id"], event["agent_id"]) == ("g-1", "agent-1")
    assert event["latency_ms"] == {"sum": 6.0, "max": 4.0}
    assert event["window_s"] == 60
    assert event["window_start"] == NOW.isoformat()


def test_a_later_rejection_flushes_closed_windows() -> None:
    sink, clock = ListSink(), FakeClock()
    agg = RejectionAggregator(sink, clock=clock, window_s=60)
    agg.record("auth_malformed")
    clock.now = NOW + timedelta(seconds=61)
    agg.record("auth_malformed")
    assert [e["count"] for e in sink.events] == [1]  # only the closed window
    agg.flush_all()
    assert [e["count"] for e in sink.events] == [1, 1]


def test_flush_closed_keeps_the_open_window() -> None:
    sink, clock = ListSink(), FakeClock()
    agg = RejectionAggregator(sink, clock=clock, window_s=60)
    agg.record("auth_malformed")
    agg.flush_closed()
    assert sink.events == []
    clock.now = NOW + timedelta(seconds=60)
    agg.flush_closed()
    assert len(sink.events) == 1


def test_flush_writes_each_bucket_once() -> None:
    sink = ListSink()
    agg = RejectionAggregator(sink, clock=FakeClock())
    agg.record("auth_malformed")
    agg.flush_all()
    agg.flush_all()
    assert len(sink.events) == 1


def test_sink_failure_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    agg = RejectionAggregator(FailingSink(), clock=clock, window_s=60)
    agg.record("auth_malformed")
    clock.now = NOW + timedelta(seconds=120)
    agg.record("auth_malformed")  # flushes a closed window into the failing sink
    agg.flush_closed()
    agg.flush_all()
    assert any("OSError" in r.getMessage() for r in caplog.records)


def test_memory_is_bounded_when_the_sink_keeps_failing() -> None:
    clock = FakeClock()
    agg = RejectionAggregator(FailingSink(), clock=clock, window_s=60)
    for minute in range(1000):
        clock.now = NOW + timedelta(minutes=minute)
        agg.record("auth_malformed")
    assert agg.open_buckets <= 1


def test_a_broken_clock_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> Any:
        raise RuntimeError("no clock")

    caplog.set_level(logging.ERROR)
    agg = RejectionAggregator(ListSink(), clock=broken)
    agg.record("auth_internal_error")
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)
