"""Adapter fallback / coverage smoke suite.

Verifies the never-fails-at-create-time guarantee of every stdio-adapter
package: with the vendor runtime venv absent, ``create_server()`` still
builds, ``list_tools()`` serves the static manifest tool list, and
``invoke()`` surfaces a clean :class:`StdioMcpError`.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_PACKAGES_DIR = Path(__file__).resolve().parents[1] / "hatch-registry" / "packages"
_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

sys.path.insert(0, str(_SCRIPTS))

from stdio_adapter import StdioMcpError  # noqa: E402

# Adapter packages under test. Expected tool counts are NOT hardcoded —
# they are derived from each package's own hatch.json manifest.
CASES = ["rikyu-hpc", "rikyu-docs", "fugaku"]


def _manifest_tool_names(pkg: str) -> list[str]:
    manifest = json.loads((_PACKAGES_DIR / pkg / "hatch.json").read_text(encoding="utf-8"))
    return list(manifest.get("tools", []))


def _load_package(pkg: str) -> ModuleType:
    sys.path.insert(0, str(_PACKAGES_DIR / pkg))
    try:
        for mod_name in ("server",):
            sys.modules.pop(mod_name, None)
        return importlib.import_module("server")
    finally:
        sys.path.remove(str(_PACKAGES_DIR / pkg))


@pytest.mark.parametrize("pkg", CASES)
def test_offline_fallback_serves_manifest_tools(pkg: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UMRS_MCP_VENV", "/nonexistent/mcp-venv")
    monkeypatch.setenv("FUGAKU_MCP_HOME", "/nonexistent/fugaku-mcp")
    srv = _load_package(pkg).create_server()

    manifest_tools = _manifest_tool_names(pkg)
    assert manifest_tools, f"{pkg}: hatch.json declares no tools"

    tools = srv.list_tools()
    assert len(tools) == len(manifest_tools)
    assert len(tools) > 0
    names = [t["name"] for t in tools]
    assert len(set(names)) == len(names)
    assert all("offline" in t["description"] for t in tools)


@pytest.mark.parametrize("pkg", CASES)
def test_offline_invoke_surfaces_spawn_error(pkg: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UMRS_MCP_VENV", "/nonexistent/mcp-venv")
    monkeypatch.setenv("FUGAKU_MCP_HOME", "/nonexistent/fugaku-mcp")
    srv = _load_package(pkg).create_server()
    tool = srv.list_tools()[0]["name"]

    with pytest.raises(StdioMcpError, match="spawn failed"):
        srv.invoke(tool, {})
