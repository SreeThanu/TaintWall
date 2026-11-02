"""M1-4 auth decisions at the ASGI layer: no server, no sleeping, every path through the check."""

import asyncio
import json
from datetime import timedelta, timezone
from typing import Any
from urllib.parse import quote

import pytest

from taintwall.audit.rejections import RejectionAggregator
from taintwall.auth import GrantAuthMiddleware, grant_from_scope
from taintwall.grants.store import GrantStore, generate_token
from tests.support.grants import NOW, FakeClock, issue

GOOD, GOOD_GRANT = issue(grant_id="good", agent_id="agent-good")
EXPIRED, EXPIRED_GRANT = issue(grant_id="old", agent_id="agent-old", expires_at=NOW)


class ListSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def write(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class Harness:
    def __init__(self, clock: Any = None) -> None:
        self.sink = ListSink()
        self.aggregator = RejectionAggregator(self.sink, clock=FakeClock())
        self.inner_scopes: list[dict[str, Any]] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            self.inner_scopes.append(scope)

        self.app = GrantAuthMiddleware(
            inner,
            GrantStore([GOOD_GRANT, EXPIRED_GRANT]),
            self.aggregator,
            clock=clock or FakeClock(),
        )

    def request(self, headers: list[tuple[bytes, bytes]], query: bytes = b"") -> tuple[int, bytes]:
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            raise AssertionError("the body must not be read before authentication")

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": headers,
            "query_string": query,
        }
        asyncio.run(self.app(scope, receive, send))
        if not sent:
            return 0, b""  # reached the inner app
        return sent[0]["status"], b"".join(m.get("body", b"") for m in sent[1:])

    def reasons(self) -> dict[str, int]:
        self.aggregator.flush_all()
        return {e["reason"]: e["count"] for e in self.sink.events}


def bearer(token: str) -> list[tuple[bytes, bytes]]:
    return [(b"authorization", f"Bearer {token}".encode())]


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ([], "auth_missing_header"),
        ([(b"authorization", b"Basic dXNlcjpwYXNz")], "auth_missing_header"),
        ([(b"x-api-key", GOOD.encode())], "auth_missing_header"),
        ([(b"authorization", b"")], "auth_missing_header"),
        ([(b"authorization", b"Bearer")], "auth_malformed"),
        ([(b"authorization", b"Bearer ")], "auth_malformed"),
        (bearer("not-a-token"), "auth_malformed"),
        (bearer(GOOD + " "), "auth_malformed"),
        (bearer(" " + GOOD), "auth_malformed"),
        (bearer(GOOD + "x"), "auth_malformed"),
        ([(b"authorization", b"Bearer tw_\xff" + b"a" * 42)], "auth_malformed"),
        (bearer(GOOD) + bearer(GOOD), "auth_malformed"),  # which one counts? refuse both
        (bearer(generate_token()), "auth_unknown_token"),
        (bearer(EXPIRED), "auth_expired"),
    ],
    ids=[
        "none",
        "basic",
        "other_header",
        "empty",
        "scheme_only",
        "scheme_space",
        "garbage",
        "trailing_space",
        "leading_space",
        "too_long",
        "non_ascii",
        "duplicate",
        "unknown",
        "expired",
    ],
)
def test_rejections_have_specific_reasons_and_never_reach_the_app(
    headers: list[tuple[bytes, bytes]], reason: str
) -> None:
    h = Harness()
    status, _ = h.request(headers)
    assert status == 401
    assert h.inner_scopes == []
    assert h.reasons() == {reason: 1}


def test_valid_token_reaches_the_app_with_the_grant() -> None:
    h = Harness()
    assert h.request(bearer(GOOD))[0] == 0
    [scope] = h.inner_scopes
    assert grant_from_scope(scope) == GOOD_GRANT
    assert h.reasons() == {}


def test_scheme_is_case_insensitive() -> None:
    h = Harness()
    assert h.request([(b"authorization", f"bEaReR {GOOD}".encode())])[0] == 0


def test_all_auth_rejections_have_identical_responses() -> None:
    h = Harness()
    responses = {
        h.request(headers)
        for headers in [
            [],
            bearer("garbage"),
            bearer(generate_token()),
            bearer(EXPIRED),
        ]
    }
    responses.add(h.request(bearer(GOOD), query=f"token={GOOD}".encode()))
    assert len(responses) == 1
    [(status, body)] = responses
    assert status == 401
    assert json.loads(body)["error"]["message"] == "TaintwallError: unauthorized"


def test_expired_rejection_records_who_but_unknown_does_not() -> None:
    h = Harness()
    h.request(bearer(EXPIRED))
    h.request(bearer(generate_token()))
    h.aggregator.flush_all()
    by_reason = {e["reason"]: e for e in h.sink.events}
    assert by_reason["auth_expired"]["grant_id"] == "old"
    assert by_reason["auth_expired"]["agent_id"] == "agent-old"
    assert by_reason["auth_unknown_token"]["grant_id"] is None
    assert by_reason["auth_unknown_token"]["agent_id"] is None


@pytest.mark.parametrize(
    ("offset_us", "allowed"),
    [(-1, True), (0, False), (1, False)],
    ids=["1us_before", "exactly_at", "1us_after"],
)
def test_expiry_boundary(offset_us: int, allowed: bool) -> None:
    token, grant = issue(grant_id="b", expires_at=NOW)
    clock = FakeClock(NOW + timedelta(microseconds=offset_us))
    h = Harness(clock=clock)
    h.app = GrantAuthMiddleware(h.app.app, GrantStore([grant]), h.aggregator, clock=clock)
    assert (h.request(bearer(token))[0] == 0) is allowed


def test_same_instant_in_another_timezone_is_still_expired() -> None:
    token, grant = issue(grant_id="b", expires_at=NOW)
    clock = FakeClock(NOW.astimezone(timezone(timedelta(hours=5, minutes=30))))
    h = Harness(clock=clock)
    h.app = GrantAuthMiddleware(h.app.app, GrantStore([grant]), h.aggregator, clock=clock)
    assert h.request(bearer(token))[0] == 401


@pytest.mark.parametrize(
    "clock_value", ["raises", "naive"], ids=["clock_raises", "clock_returns_naive"]
)
def test_broken_clock_fails_closed(clock_value: str) -> None:
    def clock() -> Any:
        if clock_value == "raises":
            raise RuntimeError("clock failed")
        return NOW.replace(tzinfo=None)

    h = Harness(clock=clock)
    status, body = h.request(bearer(GOOD))
    assert status == 500
    assert json.loads(body)["error"]["message"] == "TaintwallError: internal error"
    assert h.inner_scopes == []
    assert h.reasons() == {"auth_internal_error": 1}


@pytest.mark.parametrize(
    "query",
    [
        f"token={GOOD}",
        f"access_token={GOOD}&x=1",
        f"a=1&token={quote(GOOD, safe='')}",
        "token=" + "".join(f"%{b:02X}" for b in GOOD.encode()),  # fully percent-encoded
        f"{GOOD}",
    ],
    ids=["token", "access_token", "second_param", "percent_encoded", "bare"],
)
def test_token_in_query_string_is_rejected_even_with_a_valid_header(query: str) -> None:
    h = Harness()
    status, _ = h.request(bearer(GOOD), query=query.encode())
    assert status == 401
    assert h.inner_scopes == []
    assert h.reasons() == {"auth_token_in_query": 1}


def test_query_token_alone_does_not_authenticate() -> None:
    h = Harness()
    assert h.request([], query=f"token={GOOD}".encode())[0] == 401
    assert h.inner_scopes == []


def test_unrelated_query_string_is_ignored() -> None:
    h = Harness()
    assert h.request(bearer(GOOD), query=b"trace=abc")[0] == 0


def test_junk_tokens_cannot_grow_aggregation_state() -> None:
    h = Harness()
    for _ in range(500):
        h.request(bearer(generate_token()))
        h.request(bearer("garbage-" + generate_token()))
    assert h.aggregator.open_buckets == 2


def test_lifespan_passes_through_without_a_grant() -> None:
    h = Harness()

    async def go() -> None:
        await h.app({"type": "lifespan"}, None, None)  # type: ignore[arg-type]

    asyncio.run(go())
    assert len(h.inner_scopes) == 1


def test_websocket_is_refused() -> None:
    h = Harness()
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def go() -> None:
        await h.app({"type": "websocket", "headers": bearer(GOOD)}, None, send)  # type: ignore[arg-type]

    asyncio.run(go())
    assert h.inner_scopes == []
    assert sent[0]["type"] == "websocket.close"
