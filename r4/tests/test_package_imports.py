"""Smoke test ensuring every top-level r4 subpackage imports cleanly.

Regression guard for incomplete refactors that leave dangling re-exports
in ``__init__.py`` (e.g. B1/B2 in bug.memo, 2026-04-25).
"""

import importlib

import pytest

MODULES = [
    "r4",
    "r4.agent",
    "r4.companion",
    "r4.integrations",
    "r4.jupyter",
    "r4.mcp",
    "r4.models",
    "r4.observability",
    "r4.rag",
    "r4.search",
    "r4.teamup",
    "r4.workflows",
    "r4.auth",
    "r4.oidc",
    "r4.jobs",
    "r4.client",
    "r4.config",
    "r4.errors",
    "r4.project",
    "r4.data",
    "r4.umrs_transport",
]


@pytest.mark.parametrize("module", MODULES)
def test_import(module: str) -> None:
    """Each declared r4 subpackage must import without side-effect errors."""
    importlib.import_module(module)
