from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from taintwall.config import GatewayConfig, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]


def config(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "audit_path": "data/audit.jsonl",
        "grants_path": "data/grants.yaml",
        "upstreams": [{"namespace": "test", "url": "http://127.0.0.1:8801/mcp"}],
    }
    return base | overrides


def test_dev_config_loads() -> None:
    loaded = load_config(REPO_ROOT / "config" / "dev.yaml")
    assert [u.namespace for u in loaded.upstreams] == ["test"]
    assert loaded.host == "127.0.0.1"


def test_defaults() -> None:
    upstream = GatewayConfig.model_validate(config()).upstreams[0]
    assert upstream.timeout_s == 30.0
    assert upstream.mode == "auto"


@pytest.mark.parametrize(
    "upstream",
    [
        {"namespace": "a__b", "url": "http://x/mcp"},  # separator inside a namespace
        {"namespace": "Bad", "url": "http://x/mcp"},
        {"namespace": "ok", "url": "file:///etc/passwd"},
        {"namespace": "ok", "url": "http://x/mcp", "timeout_s": 0},
        {"namespace": "ok", "url": "http://x/mcp", "unexpected": 1},
        {"namespace": "ok", "url": "http://x/mcp", "mode": "v1"},
    ],
)
def test_invalid_upstreams_are_rejected(upstream: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        GatewayConfig.model_validate(config(upstreams=[upstream]))


def test_duplicate_namespaces_rejected() -> None:
    dup = [{"namespace": "a", "url": "http://x/mcp"}, {"namespace": "a", "url": "http://y/mcp"}]
    with pytest.raises(ValidationError, match="unique"):
        GatewayConfig.model_validate(config(upstreams=dup))


def test_grants_path_is_required() -> None:
    data = config()
    del data["grants_path"]
    with pytest.raises(ValidationError):
        GatewayConfig.model_validate(data)


def test_no_upstreams_rejected() -> None:
    with pytest.raises(ValidationError):
        GatewayConfig.model_validate(config(upstreams=[]))


def test_yaml_tags_are_not_executed(tmp_path: Path) -> None:
    path = tmp_path / "evil.yaml"
    path.write_text("audit_path: !!python/object/apply:os.system ['echo pwned']\n")
    with pytest.raises(Exception, match="could not determine a constructor"):
        load_config(path)
