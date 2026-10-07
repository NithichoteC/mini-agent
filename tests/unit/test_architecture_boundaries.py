"""Architectural boundary checks for the reusable core.

Enforces ADR-003: `agentic_ai` must never depend on application layers, and
ADR-002: no third-party agent framework as a foundation.
"""
from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import agentic_ai

CORE_ROOT = Path(agentic_ai.__file__).parent

FORBIDDEN_LAYERS = {"backend", "frontend", "launcher", "deploy"}
FORBIDDEN_FRAMEWORKS = {
    "langchain", "langchain_core", "langgraph", "crewai",
    "autogen", "autogen_agentchat", "semantic_kernel",
}


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_all_core_subpackages_import() -> None:
    names = [m.name for m in pkgutil.walk_packages(agentic_ai.__path__, "agentic_ai.")]
    for expected in (
        "agentic_ai.agent", "agentic_ai.runtime", "agentic_ai.memory",
        "agentic_ai.models", "agentic_ai.messages", "agentic_ai.tools",
        "agentic_ai.tracing", "agentic_ai.evaluation", "agentic_ai.interfaces",
        "agentic_ai.integrations.web", "agentic_ai.integrations.database",
        "agentic_ai.integrations.knowledge_graph", "agentic_ai.integrations.external",
    ):
        assert expected in names, f"missing core subpackage: {expected}"
    for name in names:
        importlib.import_module(name)


def test_core_does_not_import_application_layers() -> None:
    violations = [
        f"{py.relative_to(CORE_ROOT)} imports {bad}"
        for py in CORE_ROOT.rglob("*.py")
        for bad in _top_level_imports(py) & FORBIDDEN_LAYERS
    ]
    assert not violations, "core depends on an application layer:\n" + "\n".join(violations)


def test_core_does_not_import_agent_frameworks() -> None:
    violations = [
        f"{py.relative_to(CORE_ROOT)} imports {bad}"
        for py in CORE_ROOT.rglob("*.py")
        for bad in _top_level_imports(py) & FORBIDDEN_FRAMEWORKS
    ]
    assert not violations, "core imports a third-party agent framework:\n" + "\n".join(violations)
