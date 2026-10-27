"""EnvBridge: exposes an AgentDojo suite's functions as MCP tools over one live environment.

The bridge runs in the harness process and mutates the same environment object that AgentDojo
later checks (ARCHITECTURE §4.9). It is a low-level MCP `Server` (DECISIONS D20): schemas come
from AgentDojo, not from Python signatures, and every exception that escapes AgentDojo's
`run_function` is mapped to a fixed message so the SDK never sends or logs its text.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.functions_runtime import FunctionsRuntime
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

logger = logging.getLogger(__name__)

# Same "ErrorType: message" shape AgentDojo uses, so the agent sees a normal tool error.
INTERNAL_ERROR_TEXT = "EnvBridgeError: internal error while running the tool"


class EnvHolder:
    """The one environment the bridge acts on, bound per task by the caller."""

    def __init__(self) -> None:
        self._env: Any = None

    def bind(self, env: Any) -> None:
        if self._env is not None and self._env is not env:
            raise RuntimeError("a different environment is already bound; clear() between tasks")
        self._env = env

    def clear(self) -> None:
        self._env = None

    def is_bound_to(self, env: Any) -> bool:
        return self._env is not None and self._env is env

    @property
    def env(self) -> Any:
        if self._env is None:
            raise RuntimeError("no environment bound")
        return self._env


def tool_from_function(function: Any) -> Tool:
    return Tool(
        name=function.name,
        description=function.description,
        input_schema=function.parameters.model_json_schema(),
    )


class EnvBridge:
    def __init__(self, functions: Sequence[Any], name: str = "taintwall-envbridge") -> None:
        self.holder = EnvHolder()
        self.runtime = FunctionsRuntime(list(functions))
        self._tools = [tool_from_function(f) for f in self.runtime.functions.values()]
        self.server: Server[Any] = Server(
            name, on_list_tools=self._on_list_tools, on_call_tool=self._on_call_tool
        )

    async def _on_list_tools(
        self, ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=self._tools)

    async def _on_call_tool(
        self, ctx: ServerRequestContext[Any], params: CallToolRequestParams
    ) -> CallToolResult:
        try:
            result, error = self.runtime.run_function(
                self.holder.env, params.name, params.arguments or {}, raise_on_error=False
            )
            text = error if error is not None else tool_result_to_str(result)
        except Exception as exc:
            # Exception text may carry data we must not echo or log (D20); log the type only.
            logger.warning("EnvBridge tool %r failed: %s", params.name, type(exc).__name__)
            return CallToolResult(content=[TextContent(text=INTERNAL_ERROR_TEXT)], is_error=True)
        return CallToolResult(content=[TextContent(text=text)], is_error=error is not None)
