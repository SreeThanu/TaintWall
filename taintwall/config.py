"""Gateway configuration (YAML, validated at load)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NAMESPACE_SEPARATOR = "__"
MAX_TOOL_NAME_LENGTH = 128  # mcp/shared/tool_name_validation.py TOOL_NAME_REGEX


class UpstreamConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=32)
    url: str
    timeout_s: float = Field(default=30.0, gt=0, le=300)
    mode: Literal["auto", "legacy"] = "auto"

    @field_validator("namespace")
    @classmethod
    def _no_separator(cls, value: str) -> str:
        if NAMESPACE_SEPARATOR in value:
            raise ValueError(f"namespace must not contain {NAMESPACE_SEPARATOR!r}")
        return value

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = "127.0.0.1"
    port: int = Field(default=8800, ge=0, le=65535)
    audit_path: Path
    upstreams: list[UpstreamConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_namespaces(self) -> GatewayConfig:
        names = [u.namespace for u in self.upstreams]
        if len(names) != len(set(names)):
            raise ValueError("upstream namespaces must be unique")
        return self


def load_config(path: Path) -> GatewayConfig:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return GatewayConfig.model_validate(data)
