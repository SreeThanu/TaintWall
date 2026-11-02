"""M1-4 grant store: token format, hashing, expiry data, file validation."""

import hashlib
import re
import stat
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from taintwall.grants.store import (
    Grant,
    GrantsFileError,
    GrantStore,
    add_grant,
    generate_token,
    hash_token,
    is_well_formed,
    load_grants,
)
from tests.support.grants import LATER, NOW, issue

HASH = "a" * 64


def grant(**overrides: Any) -> dict[str, Any]:
    base = {
        "grant_id": "g-1",
        "agent_id": "agent-1",
        "tools": ["*"],
        "expires_at": "2030-01-01T00:00:00+00:00",
        "token_hash": HASH,
    }
    return base | overrides


def test_token_has_256_bits_of_url_safe_randomness() -> None:
    token = generate_token()
    assert re.fullmatch(r"tw_[A-Za-z0-9_-]{43}", token)
    assert is_well_formed(token)
    assert generate_token() != token


@pytest.mark.parametrize(
    "token",
    ["", "tw_", "tw_short", "xx_" + "a" * 43, "tw_" + "a" * 42, "tw_" + "a" * 44, "tw_" + "!" * 43],
)
def test_malformed_tokens(token: str) -> None:
    assert not is_well_formed(token)


def test_trailing_newline_is_malformed() -> None:
    assert not is_well_formed(generate_token() + "\n")


def test_hash_is_sha256_hex() -> None:
    token = generate_token()
    assert hash_token(token) == hashlib.sha256(token.encode()).hexdigest()


def test_lookup_finds_by_hash_of_presented_token() -> None:
    token, g = issue()
    store = GrantStore([g])
    assert store.lookup(token) == g
    assert store.lookup(generate_token()) is None


def test_store_rejects_duplicate_ids_and_hashes() -> None:
    _, a = issue(grant_id="same")
    _, b = issue(grant_id="same")
    with pytest.raises(ValueError, match="duplicate"):
        GrantStore([a, b])
    c = a.model_copy(update={"grant_id": "other"})
    with pytest.raises(ValueError, match="duplicate"):
        GrantStore([a, c])


def test_allows() -> None:
    assert Grant.model_validate(grant()).allows("anything")
    named = Grant.model_validate(grant(tools=["up__echo"]))
    assert named.allows("up__echo")
    assert not named.allows("up__other")
    assert not named.allows("*")


@pytest.mark.parametrize(
    "overrides",
    [
        {"tools": []},
        {"tools": ["*", "up__echo"]},  # a wildcard mixed with names is a mistake, not a grant
        {"tools": ["up__*"]},  # only the bare wildcard exists in M1
        {"expires_at": "2030-01-01T00:00:00"},  # naive: which timezone?
        {"token_hash": "not-a-hash"},
        {"token_hash": "A" * 64},
        {"grant_id": ""},
        {"agent_id": ""},
        {"unexpected": 1},
    ],
)
def test_invalid_grants_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Grant.model_validate(grant(**overrides))


def test_missing_expiry_rejected() -> None:
    data = grant()
    del data["expires_at"]
    with pytest.raises(ValidationError):
        Grant.model_validate(data)


def test_expiry_is_normalised_to_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    g = Grant.model_validate(grant(expires_at=datetime(2030, 1, 1, 2, 0, tzinfo=plus_two)))
    assert g.expires_at == datetime(2030, 1, 1, 0, 0, tzinfo=UTC)
    assert g.expires_at.utcoffset() == timedelta(0)


def test_missing_grants_file_fails(tmp_path: Path) -> None:
    with pytest.raises(GrantsFileError):
        load_grants(tmp_path / "absent.yaml")


@pytest.mark.parametrize(
    "content",
    [
        "grants: [{grant_id: g}]\n",
        "not_grants: []\n",
        "- a\n- b\n",
        "",
        "grants: !!python/object/apply:os.system ['echo pwned']\n",
        "grants: [",
    ],
)
def test_invalid_grants_file_fails(tmp_path: Path, content: str) -> None:
    path = tmp_path / "grants.yaml"
    path.write_text(content)
    with pytest.raises(GrantsFileError):
        load_grants(path)


def test_grants_file_error_does_not_echo_values(tmp_path: Path) -> None:
    path = tmp_path / "grants.yaml"
    path.write_text(f"grants:\n  - {{grant_id: g, token_hash: '{HASH}Z'}}\n")
    with pytest.raises(GrantsFileError) as caught:
        load_grants(path)
    assert HASH not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_add_grant_stores_only_the_hash(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "grants.yaml"
    token, g = add_grant(path, agent_id="a", tools=["*"], expires_at=LATER)
    raw = path.read_text()
    assert token not in raw
    assert hash_token(token) in raw
    assert load_grants(path).lookup(token) == g


def test_add_grant_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "grants.yaml"
    add_grant(path, agent_id="a", tools=["*"], expires_at=LATER)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_add_grant_keeps_existing_grants(tmp_path: Path) -> None:
    path = tmp_path / "grants.yaml"
    first, _ = add_grant(path, agent_id="a", tools=["*"], expires_at=LATER)
    second, _ = add_grant(path, agent_id="b", tools=["*"], expires_at=LATER)
    store = load_grants(path)
    assert store.lookup(first) is not None
    assert store.lookup(second) is not None


def test_add_grant_refuses_a_corrupt_file_without_overwriting(tmp_path: Path) -> None:
    path = tmp_path / "grants.yaml"
    path.write_text("grants: [")
    with pytest.raises(GrantsFileError):
        add_grant(path, agent_id="a", tools=["*"], expires_at=LATER)
    assert path.read_text() == "grants: ["


def test_add_grant_rejects_past_expiry_input_as_data_not_silently(tmp_path: Path) -> None:
    """An already-expired grant is valid data: it is simply rejected at use."""
    path = tmp_path / "grants.yaml"
    token, g = add_grant(path, agent_id="a", tools=["*"], expires_at=NOW - timedelta(days=1))
    assert load_grants(path).lookup(token) == g
