"""The plan engine's import fence (ADR-092 D-2).

Every module in ``r4/src/r4/teams/plan/`` imports only the standard
library, ``r4_contracts`` and ``r4_teamengine_spi``. So the engine never
holds a Client, a credential, a tool or a PermissionManager (ADR-020
reverse control), and moving it to ``engines/`` stays a file move.

The one named exception is PyYAML inside ``templates.py``: a pure data
parser imported only when a ``.yaml`` plan file is given.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PLAN_DIR = Path(__file__).resolve().parents[1] / "src" / "r4" / "teams" / "plan"
ALLOWED_ROOTS = frozenset({"r4_contracts", "r4_teamengine_spi", "__future__"})
OPTIONAL = frozenset({("templates.py", "yaml")})
DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def _sibling_modules() -> set[str]:
    return {path.stem for path in PLAN_DIR.glob("*.py")}


def _relative_violation(node: ast.ImportFrom, siblings: set[str]) -> str | None:
    """``from .x import y`` stays inside the package; ``..`` leaves it."""
    if node.level > 1:
        return f"from {'.' * node.level}{node.module or ''} (leaves the plan package)"
    head = (node.module or "").split(".")[0]
    if head and head not in siblings:
        return f"from .{node.module} (no such sibling module)"
    return None


def _absolute_violation(module: str, filename: str) -> str | None:
    root = module.split(".")[0]
    allowed = root in ALLOWED_ROOTS or root in sys.stdlib_module_names
    if allowed or (filename, root) in OPTIONAL:
        return None
    return f"import {module}"


def _node_violation(node: ast.AST, filename: str, siblings: set[str]) -> list[str]:
    if isinstance(node, ast.ImportFrom):
        found = (
            _relative_violation(node, siblings)
            if node.level
            else _absolute_violation(node.module or "", filename)
        )
        return [found] if found else []
    if isinstance(node, ast.Import):
        return [v for alias in node.names if (v := _absolute_violation(alias.name, filename))]
    return _dynamic_violation(node)


def _dynamic_violation(node: ast.AST) -> list[str]:
    """``importlib.import_module`` and ``__import__`` would walk around the fence."""
    if not isinstance(node, ast.Call):
        return []
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return [f"dynamic import via {name}()"] if name in DYNAMIC_IMPORTERS else []


def fence_violations(source: str, filename: str) -> list[str]:
    siblings = _sibling_modules()
    violations: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        violations += _node_violation(node, filename, siblings)
    return violations


def test_every_plan_module_imports_only_stdlib_and_the_two_contract_packages():
    modules = sorted(PLAN_DIR.glob("*.py"))
    assert {p.name for p in modules} >= {
        "__init__.py",
        "engine.py",
        "run.py",
        "journal.py",
        "templates.py",
    }

    found = {p.name: fence_violations(p.read_text(encoding="utf-8"), p.name) for p in modules}

    assert {name: v for name, v in found.items() if v} == {}


def test_the_fence_catches_what_it_is_for():
    source = "\n".join(
        [
            "import httpx",
            "from r4.client import Client",
            "from ..native import NativeEngine",
            "from .nowhere import thing",
            "import importlib",
            "importlib.import_module('r4.companion')",
            "import yaml",
        ]
    )

    violations = fence_violations(source, "run.py")

    assert sorted(violations) == sorted(
        [
            "import httpx",
            "import r4.client",
            "from ..native (leaves the plan package)",
            "from .nowhere (no such sibling module)",
            "dynamic import via import_module()",
            "import yaml",  # optional only in templates.py
        ]
    )
    assert fence_violations("import yaml", "templates.py") == []
