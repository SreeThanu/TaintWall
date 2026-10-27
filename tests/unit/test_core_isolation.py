"""Non-negotiable 5: the gateway core never imports AgentDojo."""

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "taintwall"
FORBIDDEN = "agentdojo"


def forbidden_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        else:
            continue
        found += [n for n in names if n == FORBIDDEN or n.startswith(FORBIDDEN + ".")]
    return found


def test_checker_flags_agentdojo_imports() -> None:
    attack = "import os\nimport agentdojo\nfrom agentdojo.task_suite import load_suites\n"
    assert forbidden_imports(attack) == ["agentdojo", "agentdojo.task_suite"]


def test_checker_ignores_lookalike_names() -> None:
    assert forbidden_imports("import agentdojo_compat\nfrom mypkg import agentdojo\n") == []


def test_core_has_no_agentdojo_imports() -> None:
    files = sorted(CORE.rglob("*.py"))
    assert files, f"no sources found under {CORE}"
    offenders = {
        str(f.relative_to(CORE)): hits
        for f in files
        if (hits := forbidden_imports(f.read_text(encoding="utf-8")))
    }
    assert offenders == {}
