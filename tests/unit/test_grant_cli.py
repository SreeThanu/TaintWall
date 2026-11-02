"""M1-4 `taintwall-grant`: the token is printed once and only its hash is stored."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from taintwall.grants.cli import main
from taintwall.grants.store import hash_token, is_well_formed, load_grants


def create(path: Path, *extra: str) -> int:
    return main(
        ["create", "--grants-file", str(path), "--agent-id", "agent-1", "--ttl-hours", "2", *extra]
    )


def test_stdout_is_exactly_the_token_and_the_file_only_its_hash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "grants.yaml"
    assert create(path, "--tools", "*") == 0
    captured = capsys.readouterr()
    token = captured.out.strip()
    assert is_well_formed(token)
    assert captured.out.count(token) == 1
    assert token not in captured.err
    assert token not in path.read_text()
    assert hash_token(token) in path.read_text()


def test_grant_fields_come_from_arguments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "grants.yaml"
    before = datetime.now(UTC)
    assert create(path, "--tools", "up__echo", "up__slow", "--grant-id", "task-7") == 0
    token = capsys.readouterr().out.strip()
    grant = load_grants(path).lookup(token)
    assert grant is not None
    assert grant.grant_id == "task-7"
    assert grant.agent_id == "agent-1"
    assert grant.tools == ["up__echo", "up__slow"]
    remaining = grant.expires_at - before
    assert timedelta(hours=1, minutes=59) < remaining <= timedelta(hours=2, minutes=1)


def test_two_runs_give_different_tokens(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "grants.yaml"
    create(path, "--tools", "*")
    first = capsys.readouterr().out
    create(path, "--tools", "*")
    assert capsys.readouterr().out != first


@pytest.mark.parametrize("ttl", ["0", "-1", "nan", "inf"])
def test_bad_ttl_is_refused(tmp_path: Path, ttl: str, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "grants.yaml"
    code = main(
        [
            "create",
            "--grants-file",
            str(path),
            "--agent-id",
            "a",
            "--tools",
            "*",
            f"--ttl-hours={ttl}",
        ]
    )
    assert code != 0
    assert not path.exists()
    assert capsys.readouterr().out == ""


def test_invalid_tools_print_no_token(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "grants.yaml"
    assert create(path, "--tools", "*", "up__echo") != 0
    assert capsys.readouterr().out == ""
    assert not path.exists()


def test_duplicate_grant_id_prints_no_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "grants.yaml"
    assert create(path, "--tools", "*", "--grant-id", "same") == 0
    capsys.readouterr()
    before = path.read_text()
    assert create(path, "--tools", "*", "--grant-id", "same") != 0
    assert capsys.readouterr().out == ""
    assert path.read_text() == before
