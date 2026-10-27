"""BridgedRuntime: an AgentDojo FunctionsRuntime whose calls go over MCP (DECISIONS D21).

Passed as `TaskSuite.run_task_with_pipeline(..., runtime_class=...)`, it routes every pipeline's
tool calls (ground-truth replay or LLM) through the bridge without replacing ToolsExecutor.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from agentdojo.functions_runtime import FunctionsRuntime

from eval.harness.connections import BridgeConnection


class BridgedToolError(RuntimeError):
    """A bridged tool returned an error and the caller asked for raise_on_error."""


class BridgedRuntime(FunctionsRuntime):  # type: ignore[misc]  # agentdojo is untyped
    connection: ClassVar[BridgeConnection]

    def run_function(
        self,
        env: Any,
        function: str,
        kwargs: Mapping[str, Any],
        raise_on_error: bool = False,
    ) -> tuple[Any, str | None]:
        # The bridge acts on this exact object; AgentDojo checks it after the task.
        self.connection.holder.bind(env)
        text, is_error = self.connection.call(function, dict(kwargs))
        if is_error:
            if raise_on_error:
                raise BridgedToolError(text)
            return "", text
        return text, None


def bridged_runtime_class(connection: BridgeConnection) -> type[BridgedRuntime]:
    """AgentDojo builds the runtime as `runtime_class(tools)`; the connection rides on the class."""
    return type("BoundBridgedRuntime", (BridgedRuntime,), {"connection": connection})
