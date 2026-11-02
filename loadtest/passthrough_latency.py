"""Pass-through overhead benchmark: direct upstream calls vs the same calls through the gateway.

Upstream and gateway each run as their own process (the gateway via its real CLI), over HTTP on
127.0.0.1, protocol auto (2026-07-28). Calls are sequential and interleaved, alternating which
path goes first; overhead is paired per iteration (gateway call minus the adjacent direct call).
The gateway path includes grant authentication with one grant in the store. Results are
ESTIMATE-local: one laptop, synchronous JSONL audit included (D23).

Usage: uv run python -m loadtest.passthrough_latency --n 1000 --warmup 50
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import yaml
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from taintwall.grants.store import add_grant, utc_now
from tests.support.processes import REPO_ROOT, ready_process, upstream_subprocess


def percentiles(values: list[float]) -> dict[str, float]:
    q = statistics.quantiles(values, n=100, method="inclusive")
    return {"p50": round(q[49], 3), "p95": round(q[94], 3), "p99": round(q[98], 3)}


async def measure(
    direct_url: str, gateway_url: str, token: str, n: int, warmup: int
) -> dict[str, list[float]]:
    direct_ms: list[float] = []
    gateway_ms: list[float] = []
    async with (
        httpx2.AsyncClient() as h1,
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as h2,
        Client(streamable_http_client(direct_url, http_client=h1)) as direct,
        Client(streamable_http_client(gateway_url, http_client=h2)) as via,
    ):

        async def timed(client: Client, name: str) -> float:
            t0 = time.perf_counter()
            result = await client.call_tool(name, {"text": "ping"})
            elapsed = (time.perf_counter() - t0) * 1000
            if result.is_error:
                raise RuntimeError(f"benchmark call failed: {name}")
            return elapsed

        for i in range(warmup + n):
            if i % 2 == 0:
                d = await timed(direct, "echo")
                g = await timed(via, "up__echo")
            else:
                g = await timed(via, "up__echo")
                d = await timed(direct, "echo")
            if i >= warmup:
                direct_ms.append(d)
                gateway_ms.append(g)
    return {"direct": direct_ms, "gateway": gateway_ms}


def git_state() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603  # fixed git invocation
            ["git", *args],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--n", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--runs-dir", type=Path, default=REPO_ROOT / "runs")
    args = parser.parse_args()

    with upstream_subprocess() as upstream, tempfile.TemporaryDirectory() as tmp:
        grants_path = Path(tmp) / "grants.yaml"
        token, _ = add_grant(
            grants_path,
            agent_id="benchmark",
            tools=["*"],
            expires_at=utc_now() + timedelta(hours=24),
        )
        config_path = Path(tmp) / "gateway.yaml"
        config = {
            "host": "127.0.0.1",
            "port": 0,
            "audit_path": str(Path(tmp) / "audit.jsonl"),
            "grants_path": str(grants_path),
            "upstreams": [{"namespace": "up", "url": upstream.url, "timeout_s": 30}],
        }
        config_path.write_text(yaml.safe_dump(config))
        gateway_args = ["-m", "taintwall.app", "--config", str(config_path), "--print-ready"]
        with ready_process(gateway_args) as gateway:
            samples = asyncio.run(measure(upstream.url, gateway.url, token, args.n, args.warmup))
        audit_lines = (Path(tmp) / "audit.jsonl").read_text().count("\n")

    overhead = [g - d for g, d in zip(samples["gateway"], samples["direct"], strict=True)]
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-passthrough-latency"
    metrics = {
        "run_id": run_id,
        "label": "ESTIMATE-local",
        "n": args.n,
        "warmup": args.warmup,
        "unit": "ms",
        "direct": percentiles(samples["direct"]),
        "gateway": percentiles(samples["gateway"]),
        "paired_overhead": percentiles(overhead),
        "audit_lines_written": audit_lines,
    }
    run_config = {
        "benchmark": "loadtest/passthrough_latency.py",
        "tool": "echo",
        "protocol_mode": "auto",
        "transport": "streamable HTTP, 127.0.0.1, separate processes",
        "git": git_state(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "versions": {p: importlib.metadata.version(p) for p in ("mcp", "uvicorn", "httpx2")},
    }
    out = args.runs_dir / run_id
    out.mkdir(parents=True)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    (out / "config.yaml").write_text(yaml.safe_dump(run_config, sort_keys=False))
    print(json.dumps(metrics, indent=2))
    resolved = out.resolve()
    shown = resolved.relative_to(REPO_ROOT) if resolved.is_relative_to(REPO_ROOT) else resolved
    print(f"saved: {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
