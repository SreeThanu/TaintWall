"""Grant tokens and the grants file (D28).

A token is 256 random bits. Only its SHA-256 hash is stored, and lookup is by that hash: a slow
password hash defends guessable secrets, and these cannot be guessed. Grants are read once at
startup in M1; errors here never echo file contents.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import tempfile
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

TOKEN_PREFIX = "tw_"  # noqa: S105  # a public marker, not a secret
TOKEN_RANDOM_BYTES = 32
TOKEN_RE = re.compile(rf"{TOKEN_PREFIX}[A-Za-z0-9_-]{{43}}")
TOKEN_SHAPE_RE = re.compile(rb"tw_[A-Za-z0-9_-]{43}")
WILDCARD = "*"


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_RANDOM_BYTES)


def is_well_formed(token: str) -> bool:
    return TOKEN_RE.fullmatch(token) is not None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)


class GrantsFileError(RuntimeError):
    """The grants file is missing or invalid. The gateway must not start without valid grants."""


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grant_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    agent_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    tools: list[str] = Field(min_length=1)
    expires_at: AwareDatetime
    token_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("tools")
    @classmethod
    def _bare_wildcard_only(cls, tools: list[str]) -> list[str]:
        if WILDCARD in tools and len(tools) > 1:
            raise ValueError("'*' cannot be combined with tool names")
        if any(WILDCARD in tool and tool != WILDCARD for tool in tools):
            raise ValueError("only the bare '*' wildcard is supported")
        return tools

    @field_validator("expires_at")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def allows(self, tool: str) -> bool:
        return self.tools == [WILDCARD] or tool in self.tools


class GrantStore:
    def __init__(self, grants: Iterable[Grant]) -> None:
        self._by_hash: dict[str, Grant] = {}
        ids: set[str] = set()
        for grant in grants:
            if grant.grant_id in ids:
                raise ValueError("duplicate grant_id")
            if grant.token_hash in self._by_hash:
                raise ValueError("duplicate token_hash")
            ids.add(grant.grant_id)
            self._by_hash[grant.token_hash] = grant

    def lookup(self, token: str) -> Grant | None:
        return self._by_hash.get(hash_token(token))

    def grants(self) -> list[Grant]:
        return list(self._by_hash.values())


def _safe_validation_summary(exc: ValidationError) -> str:
    """Locations and rule messages only: `str(exc)` would echo input values."""
    problems = exc.errors(include_input=False, include_url=False, include_context=False)
    return "; ".join(
        f"{'.'.join(str(part) for part in p['loc'])}: {p['type']}" for p in problems[:5]
    )


def load_grants(path: Path) -> GrantStore:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GrantsFileError(f"cannot read grants file ({type(exc).__name__})") from None
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError:
        raise GrantsFileError("grants file is not valid YAML") from None
    if (
        not isinstance(data, dict)
        or set(data) != {"grants"}
        or not isinstance(data["grants"], list)
    ):
        raise GrantsFileError("grants file must be a mapping with one key, 'grants' (a list)")
    try:
        return GrantStore(Grant.model_validate(item) for item in data["grants"])
    except ValidationError as exc:
        raise GrantsFileError(f"invalid grant: {_safe_validation_summary(exc)}") from None
    except ValueError as exc:
        raise GrantsFileError(f"invalid grants file: {exc}") from None


def _write_grants(path: Path, grants: list[Grant]) -> None:
    """Replace the file atomically. `mkstemp` creates it 0600: only this user can read it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".grants-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump({"grants": [g.model_dump(mode="json") for g in grants]}, f)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def add_grant(
    path: Path,
    *,
    agent_id: str,
    tools: list[str],
    expires_at: datetime,
    grant_id: str | None = None,
) -> tuple[str, Grant]:
    """Create a grant and store its hash. The returned token exists nowhere else."""
    existing = load_grants(path).grants() if path.exists() else []
    token = generate_token()
    grant = Grant(
        grant_id=grant_id or f"g_{uuid.uuid4().hex}",
        agent_id=agent_id,
        tools=tools,
        expires_at=expires_at,
        token_hash=hash_token(token),
    )
    combined = [*existing, grant]
    GrantStore(combined)  # raises on a duplicate grant_id before anything is written
    _write_grants(path, combined)
    return token, grant
