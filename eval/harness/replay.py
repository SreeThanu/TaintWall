"""Ground-truth replay through AgentDojo's own task runner, recording what equivalence needs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline
from agentdojo.functions_runtime import EmptyEnv, FunctionsRuntime

from eval.harness.bridged_runtime import bridged_runtime_class
from eval.harness.connections import BridgeConnection
from eval.harness.frozen_clock import frozen_clock

UNKNOWN_TOOL = "taintwall_no_such_tool"


class RecordingPipeline(BasePipelineElement):  # type: ignore[misc]  # agentdojo is untyped
    """Wraps a pipeline and keeps the messages and environment of its last query."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.messages: list[Any] = []
        self.env: Any = None

    def query(
        self,
        query: str,
        runtime: Any,
        env: Any = EmptyEnv(),  # noqa: B008  # AgentDojo's own default
        messages: Sequence[Any] = (),
        extra_args: dict[str, Any] | None = None,
    ) -> tuple[str, Any, Any, Sequence[Any], dict[str, Any]]:
        out: tuple[str, Any, Any, Sequence[Any], dict[str, Any]] = self.inner.query(
            query, runtime, env, list(messages), dict(extra_args or {})
        )
        self.env = out[2]
        self.messages = list(out[3])
        return out


@dataclass(frozen=True)
class ReplayResult:
    check: bool  # utility for a user task; the injection goal's check for an injection task
    tool_texts: list[str]
    env_dump: dict[str, Any]
    bridge_acted_on_checked_env: bool | None  # None on the direct path


def replay(suite: Any, task: Any, connection: BridgeConnection | None = None) -> ReplayResult:
    """Replay `task.ground_truth` via `suite.run_task_with_pipeline`, directly or over MCP.

    The wall clock AgentDojo stamps new emails and files with is frozen for the replay.
    """
    recorder = RecordingPipeline(GroundTruthPipeline(task))
    kwargs: dict[str, Any] = {}
    if connection is not None:
        kwargs["runtime_class"] = bridged_runtime_class(connection)
    try:
        with frozen_clock():
            check, _ = suite.run_task_with_pipeline(recorder, task, None, {}, **kwargs)
        acted = None if connection is None else connection.holder.is_bound_to(recorder.env)
    finally:
        if connection is not None:
            connection.holder.clear()
    texts = [
        block["content"]
        for message in recorder.messages
        if message["role"] == "tool"
        for block in message["content"]
    ]
    return ReplayResult(bool(check), texts, recorder.env.model_dump(), acted)


def invalid_calls(suite: Any) -> list[tuple[str, dict[str, Any]]]:
    """An unknown tool, and the first tool (by name) with required arguments called with none."""
    with_required = sorted(
        f.name for f in suite.tools if f.parameters.model_json_schema().get("required")
    )
    return [(UNKNOWN_TOOL, {}), (with_required[0], {})]


def invalid_call_errors(suite: Any, connection: BridgeConnection | None = None) -> list[str | None]:
    """Error strings from `run_function(..., raise_on_error=False)` for each invalid call."""
    env = suite.load_and_inject_default_environment({})
    runtime: Any = (
        FunctionsRuntime(suite.tools)
        if connection is None
        else bridged_runtime_class(connection)(suite.tools)
    )
    try:
        return [runtime.run_function(env, name, args)[1] for name, args in invalid_calls(suite)]
    finally:
        if connection is not None:
            connection.holder.clear()


def _normalized(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def schemas_match(suite: Any, connection: BridgeConnection) -> bool:
    """Bridge `list_tools` equals AgentDojo's functions: names, descriptions, normalized schemas."""
    direct = {
        f.name: (f.description, _normalized(f.parameters.model_json_schema())) for f in suite.tools
    }
    bridged = {
        t.name: (t.description, _normalized(t.input_schema)) for t in connection.list_tools()
    }
    return direct == bridged
