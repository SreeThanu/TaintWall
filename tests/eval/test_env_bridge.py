"""M1-5a: EnvBridge equivalence on five workspace tasks, cleanup, clock freeze, and redaction."""

import datetime
import importlib
import logging
import socket
import threading
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any

import pytest

pytest.importorskip("agentdojo")

from eval.benchmark import load_suites
from eval.harness import connections
from eval.harness.connections import BridgeConnection
from eval.harness.env_bridge import INTERNAL_ERROR_TEXT, EnvBridge
from eval.harness.frozen_clock import CLOCK_MODULES, FIXED_NOW, frozen_clock
from eval.harness.replay import invalid_call_errors, replay, schemas_match

# Two calendar writes, a file+email write, a read that marks emails read, a file create+share.
TASKS = ["user_task_0", "user_task_7", "user_task_13", "user_task_24", "user_task_32"]
PATHS = ["legacy", "http"]
FAKE_SECRET = "sk-TEST-0123456789abcdef"  # noqa: S105  # deliberately fake, for the redaction test


@pytest.fixture(scope="module")
def suite() -> Any:
    return load_suites()["workspace"]


@pytest.fixture(scope="module")
def conns(suite: Any) -> Iterator[dict[str, BridgeConnection]]:
    bridge = EnvBridge(suite.tools)
    with ExitStack() as stack:
        yield {
            "legacy": stack.enter_context(connections.in_process(bridge)),
            "http": stack.enter_context(connections.over_http(bridge)),
        }


def _port_closed(url: str) -> bool:
    port = int(url.rsplit(":", 1)[1].split("/", 1)[0])
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


@pytest.mark.parametrize("task_id", TASKS)
def test_direct_replay_is_deterministic(suite: Any, task_id: str) -> None:
    task = suite.user_tasks[task_id]
    a1, a2 = replay(suite, task), replay(suite, task)
    assert a1.tool_texts == a2.tool_texts
    assert a1.env_dump == a2.env_dump


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("task_id", TASKS)
def test_bridge_matches_direct(
    suite: Any, conns: dict[str, BridgeConnection], task_id: str, path: str
) -> None:
    task = suite.user_tasks[task_id]
    direct = replay(suite, task)
    bridged = replay(suite, task, conns[path])
    assert direct.check and bridged.check  # 1
    assert bridged.tool_texts == direct.tool_texts  # 2, byte-identical
    assert bridged.env_dump == direct.env_dump  # 3
    assert bridged.bridge_acted_on_checked_env is True  # same object AgentDojo checked


@pytest.mark.parametrize("path", PATHS)
def test_invalid_calls_match(suite: Any, conns: dict[str, BridgeConnection], path: str) -> None:
    direct = invalid_call_errors(suite)
    assert all(error and ": " in error for error in direct)
    assert invalid_call_errors(suite, conns[path]) == direct  # 4


@pytest.mark.parametrize("path", PATHS)
def test_schemas_match(suite: Any, conns: dict[str, BridgeConnection], path: str) -> None:
    assert schemas_match(suite, conns[path])  # 5


def test_back_to_back_runs_clean_up(suite: Any) -> None:  # 6
    threads_before = {t.name for t in threading.enumerate()}
    bridge = EnvBridge(suite.tools)
    with connections.in_process(bridge) as b, connections.over_http(bridge) as c:
        assert c.url is not None
        url = c.url
        for _ in range(2):
            for task_id in TASKS:
                for conn in (b, c):
                    assert replay(suite, suite.user_tasks[task_id], conn).check
    assert {t.name for t in threading.enumerate()} - threads_before == set()
    assert _port_closed(url)


def test_frozen_clock_restores_originals() -> None:
    modules = [importlib.import_module(name) for name in CLOCK_MODULES]
    originals = [module.datetime for module in modules]
    assert all(original is datetime for original in originals)
    with frozen_clock():
        for module in modules:
            now = module.datetime.datetime.now()
            assert type(now) is datetime.datetime  # a real datetime, not a stand-in subclass
            assert now == FIXED_NOW
    assert [module.datetime for module in modules] == originals
    with pytest.raises(RuntimeError, match="boom"), frozen_clock():
        raise RuntimeError("boom")
    assert [module.datetime for module in modules] == originals


def _raise_with_secret(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError(f"upstream said: api_key={FAKE_SECRET}")


@pytest.mark.parametrize("transport", ["legacy", "auto", "http"])
def test_internal_errors_are_redacted(
    suite: Any,
    transport: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Attack case (D20): an exception escaping run_function never reaches the client or logs."""
    caplog.set_level(logging.DEBUG)
    bridge = EnvBridge(suite.tools)
    monkeypatch.setattr(bridge.runtime, "run_function", _raise_with_secret)
    bridge.holder.bind(suite.load_and_inject_default_environment({}))
    with (
        connections.over_http(bridge)
        if transport == "http"
        else connections.in_process(bridge, mode=transport)
    ) as conn:
        text, is_error = conn.call(
            "send_email", {"recipients": ["a@b.c"], "subject": "s", "body": "b"}
        )
    assert is_error
    assert text == INTERNAL_ERROR_TEXT
    assert FAKE_SECRET not in caplog.text
