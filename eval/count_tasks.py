"""Count AgentDojo tasks per suite, using the same task sets the benchmark runner iterates.

Counting logic mirrors agentdojo 0.1.35 `benchmark.benchmark_suite_with_injections`:
- every version-compatible user task is paired with every injection task (no pair filtering;
  only DoS attacks reduce this to one injection task per user task, and we do not use them);
- each injection task is also run once as a plain user task to check it is solvable.

Usage: uv run python -m eval.count_tasks [--all-versions]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from importlib.metadata import version

from eval.benchmark import BENCHMARK_VERSION, load_suites, registered_versions

SUITE_ORDER = ("workspace", "slack", "banking", "travel")


@dataclass(frozen=True)
class SuiteCounts:
    suite: str
    user_tasks: int
    injection_tasks: int

    @property
    def security_cases(self) -> int:
        """(user, injection) pairs run under attack."""
        return self.user_tasks * self.injection_tasks


def count(benchmark_version: str) -> list[SuiteCounts]:
    suites = load_suites(benchmark_version)
    return [
        SuiteCounts(
            suite=name,
            user_tasks=len(suites[name].user_tasks),
            injection_tasks=len(suites[name].injection_tasks),
        )
        for name in SUITE_ORDER
    ]


def render(benchmark_version: str, rows: list[SuiteCounts]) -> str:
    lines = [
        f"agentdojo {version('agentdojo')}, benchmark {benchmark_version}",
        "| suite | user tasks | injection tasks | security cases (user x injection) |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r.suite} | {r.user_tasks} | {r.injection_tasks} | {r.security_cases} |")
    lines.append(
        f"| total | {sum(r.user_tasks for r in rows)} | {sum(r.injection_tasks for r in rows)}"
        f" | {sum(r.security_cases for r in rows)} |"
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--all-versions",
        action="store_true",
        help="count every registered benchmark version, not only the pinned one",
    )
    args = parser.parse_args()
    versions = registered_versions() if args.all_versions else [BENCHMARK_VERSION]
    print("\n\n".join(render(v, count(v)) for v in versions))


if __name__ == "__main__":
    main()
