"""Grants and a settable clock for tests."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from taintwall.grants.store import Grant, generate_token, hash_token

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=3650)


def issue(
    grant_id: str = "g-1",
    agent_id: str = "agent-1",
    tools: Iterable[str] = ("*",),
    expires_at: datetime = LATER,
) -> tuple[str, Grant]:
    token = generate_token()
    grant = Grant(
        grant_id=grant_id,
        agent_id=agent_id,
        tools=list(tools),
        expires_at=expires_at,
        token_hash=hash_token(token),
    )
    return token, grant


@dataclass
class FakeClock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now
