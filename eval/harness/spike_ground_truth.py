"""M1-5a spike: AgentDojo ground truth replayed directly (a), over in-process JSON-RPC (b) and
over streamable HTTP (c), for every dev-suite user and injection task. No LLM, no gateway.

Usage: uv run python -m eval.harness.spike_ground_truth
Exits non-zero if any criterion fails on any task.
"""

from __future__ import annotations

import faulthandler
import logging
import socket
import sys
import threading
import warnings
from collections import Counter
from typing import Any

from eval.benchmark import BENCHMARK_VERSION, load_suites
from eval.harness import connections
from eval.harness.connections import BridgeConnection
from eval.harness.env_bridge import EnvBridge
from eval.harness.replay import invalid_call_errors, replay, schemas_match
from eval.harness.synthetic import (
    INVALID_CASES,
    VALID_CASES,
    case_problems,
    differences,
    run_case,
)

DEV_SUITES = ("workspace", "slack")
KINDS = ("user", "injection")
PATHS = ("a", "b", "c")
ROWS = (
    "0 a1 == a2 byte-identical",
    "1 check",
    "2 tool texts",
    "3 post-env",
    "3' same env object",
    "4 invalid calls",
    "5 schemas",
)
A_ONLY = {"0 a1 == a2 byte-identical"}
WATCHDOG_S = 900


def _task_number(task: Any) -> int:
    return int(task.ID.rsplit("_", 1)[-1])


def _port_closed(url: str) -> bool:
    port = int(url.rsplit(":", 1)[1].split("/", 1)[0])
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


class Tally:
    def __init__(self) -> None:
        self.passed: Counter[tuple[str, str, str]] = Counter()
        self.na: Counter[tuple[str, str, str]] = Counter()
        self.runs: Counter[str] = Counter()
        self.failures: list[str] = []

    def record(self, kind: str, path: str, row: str, ok: bool | None, where: str) -> None:
        if ok is None:
            self.na[(kind, path, row)] += 1
        elif ok:
            self.passed[(kind, path, row)] += 1
        else:
            self.failures.append(f"{where} path {path}: {row}")

    def cell(self, kind: str, path: str, row: str) -> str:
        if path == "a" and row not in A_ONLY | {"1 check"}:
            return "—"
        if path != "a" and row in A_ONLY:
            return "—"
        n_na = self.na[(kind, path, row)]
        text = f"{self.passed[(kind, path, row)]}/{self.runs[kind] - n_na}"
        return f"{text} (N/A {n_na})" if n_na else text


def run_suite(suite_name: str, suite: Any, conns: dict[str, BridgeConnection]) -> Tally:
    tally = Tally()
    schema_ok = {path: schemas_match(suite, conn) for path, conn in conns.items()}
    direct_errors = invalid_call_errors(suite)
    bridged_errors = {path: invalid_call_errors(suite, conn) for path, conn in conns.items()}
    tasks = [("user", t) for t in sorted(suite.user_tasks.values(), key=_task_number)] + [
        ("injection", t) for t in sorted(suite.injection_tasks.values(), key=_task_number)
    ]
    for kind, task in tasks:
        tally.runs[kind] += 1
        where = f"{suite_name}/{task.ID}"
        try:
            a, a2 = replay(suite, task), replay(suite, task)
        except Exception as exc:
            tally.failures.append(f"{where} path a: {type(exc).__name__}: {exc}")
            continue
        deterministic = a.tool_texts == a2.tool_texts and a.env_dump == a2.env_dump
        tally.record(kind, "a", "0 a1 == a2 byte-identical", deterministic, where)
        # Path a's own check: ground truth must solve a user task and reach an injection goal.
        # An injection task with no ground-truth calls cannot reach its goal by replay: N/A.
        no_calls = not a.tool_texts
        a_ok = None if (kind == "injection" and no_calls) else a.check
        tally.record(kind, "a", "1 check", a_ok, where)
        for path, conn in conns.items():
            try:
                r = replay(suite, task, conn)
            except Exception as exc:
                tally.failures.append(f"{where} path {path}: {type(exc).__name__}: {exc}")
                continue
            results: dict[str, bool | None] = {
                "1 check": r.check == a.check and (a.check or kind == "injection"),
                "2 tool texts": r.tool_texts == a.tool_texts,
                "3 post-env": r.env_dump == a.env_dump,
                "3' same env object": None if no_calls else r.bridge_acted_on_checked_env is True,
                "4 invalid calls": bridged_errors[path] == direct_errors,
                "5 schemas": schema_ok[path],
            }
            for row, ok in results.items():
                tally.record(kind, path, row, ok, where)
    return tally


def run_synthetic(suite: Any, conns: dict[str, BridgeConnection]) -> list[str]:
    """M1-5c: direct vs each bridge path for the tools ground truth never executes."""
    failures: list[str] = []
    for case in VALID_CASES + INVALID_CASES:
        where = f"synthetic/{case.name}"
        try:
            direct = run_case(suite, case)
            failures += [f"{where} path a: {p}" for p in case_problems(case, direct)]
            for path, conn in conns.items():
                bridged = run_case(suite, case, conn)
                failures += [f"{where} path {path}: {p}" for p in case_problems(case, bridged)]
                failures += [f"{where} path {path}: {d}" for d in differences(direct, bridged)]
        except Exception as exc:
            failures.append(f"{where}: {type(exc).__name__}: {exc}")
    return failures


def _coverage(suite: Any, conns: dict[str, BridgeConnection]) -> str:
    """Which tools ran successfully through each bridge path, and which were only schema-checked."""
    tools = sorted(f.name for f in suite.tools)
    executed = {path: {t for t in tools if conn.executed[t]} for path, conn in conns.items()}
    both = executed["b"] & executed["c"]
    never = [t for t in tools if t not in executed["b"] | executed["c"]]
    lines = [
        f"tool coverage: {len(tools)} tools; executed via b: {len(executed['b'])}, "
        f"via c: {len(executed['c'])}, via both: {len(both)}; schema-checked only: {len(never)}",
        f"executed (both paths): {', '.join(sorted(both)) or 'none'}",
        f"schema-checked only (never executed): {', '.join(never) or 'none'}",
    ]
    return "\n".join(lines)


def main() -> int:
    faulthandler.dump_traceback_later(WATCHDOG_S, exit=True)
    logging.basicConfig(level=logging.WARNING, force=True)
    warnings.filterwarnings("ignore")
    threads_before = {t.name for t in threading.enumerate()}
    suites = load_suites()
    urls: list[str] = []
    all_failures: list[str] = []
    print(f"benchmark {BENCHMARK_VERSION}; a=direct, b=in-process JSON-RPC (legacy), c=HTTP")
    print("clock frozen during every replay (eval/harness/frozen_clock.py)")
    for name in DEV_SUITES:
        suite = suites[name]
        bridge = EnvBridge(suite.tools)
        with connections.in_process(bridge) as b, connections.over_http(bridge) as c:
            if c.url is None:
                raise RuntimeError("HTTP connection has no URL")
            urls.append(c.url)
            tally = run_suite(name, suite, {"b": b, "c": c})
            synthetic_failures = (
                run_synthetic(suite, {"b": b, "c": c}) if name == "workspace" else []
            )
            coverage = _coverage(suite, {"b": b, "c": c})
        all_failures += tally.failures + synthetic_failures
        print(f"\n## {name}: {tally.runs['user']} user tasks, {tally.runs['injection']} injection")
        print(
            "| row | user a | user b | user c | injection a | injection b | injection c |\n"
            "|---|---|---|---|---|---|---|"
        )
        for row in ROWS:
            cells = [tally.cell(kind, path, row) for kind in KINDS for path in PATHS]
            print(f"| {row} | " + " | ".join(cells) + " |")
        if name == "workspace":
            print(
                f"synthetic (M1-5c): {len(VALID_CASES)} valid and {len(INVALID_CASES)} invalid "
                f"cases on a, b, c; failures: {len(synthetic_failures)}"
            )
        print(coverage)
    print("\n## criterion 6: back-to-back run, cleanup")
    leftover = sorted({t.name for t in threading.enumerate()} - threads_before)
    closed = [_port_closed(u) for u in urls]
    print(f"threads started and still alive: {leftover or 'none'}")
    print(
        f"HTTP servers started: {len(urls)}; ports no longer accepting connections: {sum(closed)}"
    )
    faulthandler.cancel_dump_traceback_later()
    print("watchdog: not triggered")
    print("\n## failures")
    print("\n".join(all_failures) if all_failures else "none")
    return 0 if not leftover and all(closed) and not all_failures else 1


if __name__ == "__main__":
    sys.exit(main())
