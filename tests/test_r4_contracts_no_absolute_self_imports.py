"""U2.0 P0 guard: the umrs-protocol closure never imports ``r4_contracts`` by name.

The 16 modules below moved to ``umrs/protocol/src/umrs_protocol`` (ADR-117 D-2,
U2.0 spec §2 and §7). A module there that says ``from r4_contracts.x import y``
would reach back into r4-contracts, whose same-named module is now only an alias
of it, so every closure module must import its siblings relatively, and only
siblings that are themselves in the closure. The walk covers function-local
imports too.

Until U2.0b this test read the real modules in ``r4-contracts/src/r4_contracts``.
From U2.0b (2026-11-15) those files are ``sys.modules`` shims (checked by
``r4-contracts/tests/test_umrs_protocol_alias.py``), so it reads the code where
it now lives: the umrs_protocol tree in the pinned submodule.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PKG = REPO_ROOT / "umrs" / "protocol" / "src" / "umrs_protocol"

# U2.0 spec §2: the MOVED set. Keep in step with umrs/protocol/src/umrs_protocol.
CLOSURE = (
    "a2l",
    "ask_policy",
    "context_pack",
    "errors",
    "handshake_hmac",
    "hpc_gate",
    "models",
    "protocol",
    "provenance",
    "recipes",
    "research",
    "retrieval",
    "security",
    "skills",
    "workflow_package",
    "workflows",
)


def _is_self_name(name: str | None) -> bool:
    return name == "r4_contracts" or (name or "").startswith("r4_contracts.")


def _relative_targets(node: ast.ImportFrom) -> list[str]:
    """Sibling modules a ``level == 1`` import names (``from .m`` or ``from . import m``)."""
    if node.module:
        return [node.module.split(".")[0]]
    return [alias.name for alias in node.names]


def _import_from_findings(node: ast.ImportFrom) -> list[str]:
    if node.level == 0:
        return [f"absolute 'from {node.module} import'"] if _is_self_name(node.module) else []
    if node.level > 1:
        return [f"relative import escapes the package (level {node.level})"]
    outside = [t for t in _relative_targets(node) if t not in CLOSURE]
    return [f"relative import of non-closure module '.{t}'" for t in outside]


def _node_findings(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [f"absolute 'import {a.name}'" for a in node.names if _is_self_name(a.name)]
    if isinstance(node, ast.ImportFrom):
        return _import_from_findings(node)
    return []


def closure_import_findings(source: str) -> list[str]:
    """Every import in *source* that would tie a umrs_protocol copy back to r4_contracts."""
    findings: list[str] = []
    for node in ast.walk(ast.parse(source)):
        for finding in _node_findings(node):
            findings.append(f"line {node.lineno}: {finding}")
    return findings


@pytest.mark.parametrize("module", CLOSURE)
def test_closure_module_has_no_self_import(module: str) -> None:
    path = PROTOCOL_PKG / f"{module}.py"
    assert path.is_file(), f"closure module {module} missing at {path}"
    findings = closure_import_findings(path.read_text(encoding="utf-8"))
    assert not findings, f"{path.relative_to(REPO_ROOT)}: " + "; ".join(findings)


@pytest.mark.parametrize(
    "source",
    [
        "from r4_contracts.provenance import ArtifactRef\n",  # pre-P0 research.py:21
        "import r4_contracts.errors\n",
        "from r4_contracts import errors\n",
        "def f():\n    import r4_contracts\n",
        "from .knowledge import KnowledgeRecord\n",
        "from . import taint\n",
        "from ..other import thing\n",
    ],
)
def test_checker_flags_bad_imports(source: str) -> None:
    assert closure_import_findings(source)


@pytest.mark.parametrize(
    "source",
    [
        "from .provenance import ArtifactRef\n",
        "from . import retrieval\n",
        "import json\nfrom typing import Any\n",
        "import r4_contracts_extra\n",
    ],
)
def test_checker_accepts_closure_relative_and_stdlib(source: str) -> None:
    assert closure_import_findings(source) == []
