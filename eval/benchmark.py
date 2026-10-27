"""Single source of truth for which AgentDojo package and benchmark version we evaluate on.

All eval code loads suites through `load_suites()`; nothing calls AgentDojo's loaders directly,
so the benchmark version is never left to a library default.
"""

from __future__ import annotations

from importlib.metadata import version
from typing import Any, Final

AGENTDOJO_VERSION: Final = "0.1.35"
BENCHMARK_VERSION: Final = "v1.2.2"
SUITE_NAMES: Final = frozenset({"workspace", "slack", "banking", "travel"})


class BenchmarkPinError(RuntimeError):
    """The installed AgentDojo does not match the pinned package or benchmark version."""


def registered_versions() -> list[str]:
    """Benchmark versions registered in the installed AgentDojo package."""
    from agentdojo.task_suite import load_suites

    # `_SUITES` is a defaultdict: `get_suites()` returns {} for an unknown version instead of
    # raising, so membership has to be checked against its keys.
    return list(load_suites._SUITES.keys())


def load_suites(benchmark_version: str = BENCHMARK_VERSION) -> dict[str, Any]:
    """Return `{suite_name: TaskSuite}` for the pinned benchmark version.

    Raises BenchmarkPinError if the installed package or the requested version does not match.
    """
    installed = version("agentdojo")
    if installed != AGENTDOJO_VERSION:
        raise BenchmarkPinError(f"agentdojo {installed} installed, {AGENTDOJO_VERSION} pinned")
    if benchmark_version not in registered_versions():
        raise BenchmarkPinError(f"benchmark version {benchmark_version!r} is not registered")

    from agentdojo.task_suite.load_suites import get_suites

    suites: dict[str, Any] = dict(get_suites(benchmark_version))
    if set(suites) != SUITE_NAMES:
        raise BenchmarkPinError(f"unexpected suites for {benchmark_version}: {sorted(suites)}")
    return suites
