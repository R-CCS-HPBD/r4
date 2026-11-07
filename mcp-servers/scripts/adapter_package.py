#!/usr/bin/env python3
"""Shared builder for stdio-adapter Hatch! packages.

One vendor stdio MCP server per package. The package's ``server.py`` is a
thin manifest-driven entry: it declares the spawn command (and optionally a
deny env var / offline hint) and delegates everything else here.

Behavior guaranteed for every adapter package:
- Never fails at import or ``create_server()`` time — the vendor child is
  spawned lazily by :class:`StdioMcpAdapter` on first use and respawned on
  crash.
- Fail-clear on offline children: when the child is unavailable,
  ``list_tools()`` serves the static ``hatch.json`` tool list so the
  frontend launcher stays healthy; ``invoke()`` surfaces a clean
  :class:`StdioMcpError` to gateway clients instead of a traceback or a
  cascade.
- Unknown/downstream tool behavior: the adapter passes tools through to the
  child; a child-side error is surfaced as a clean client error
  (``StdioMcpError`` from the bridge), while the harness keeps a healthy
  runtime so the frontend never degrades into silent no-ops.
- Tool deny (runtime level) is driven by ``<PKG>_TOOL_DENY`` env
  (comma-separated); registration-level deny lives in the launcher.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stdio_adapter import StdioMcpAdapter  # noqa: E402

DEFAULT_VENV = "/opt/umrs/mcp-runtimes/venv"


def venv_python() -> str:
    """Interpreter of the shared MCP runtime venv (env-checked per call)."""
    return os.environ.get("UMRS_MCP_VENV", DEFAULT_VENV) + "/bin/python"


def _deny_list(deny_env: str | None) -> list[str]:
    if not deny_env:
        return []
    return [t.strip() for t in os.environ.get(deny_env, "").split(",") if t.strip()]


def _load_manifest(package_dir: Path) -> dict[str, Any]:
    return json.loads((package_dir / "hatch.json").read_text(encoding="utf-8"))


class ManifestAdapterServer:
    """Hatch contract backed by a stdio vendor child + manifest fallback."""

    def __init__(
        self,
        name: str,
        adapter: StdioMcpAdapter,
        manifest: dict[str, Any],
        offline_hint: str,
    ) -> None:
        self._name = name
        self._adapter = adapter
        self._manifest = manifest
        self._offline_hint = offline_hint

    def list_tools(self) -> list[dict[str, Any]]:
        try:
            return self._adapter.list_tools()
        except Exception:
            return [
                {
                    "name": tool_name,
                    "description": f"{self._name} tool ({self._offline_hint})",
                    "parameters": {"type": "object", "properties": {}},
                }
                for tool_name in self._manifest.get("tools", [])
            ]

    def invoke(self, tool_name: str, args: dict[str, Any]) -> Any:
        return self._adapter.invoke(tool_name, args)


def create_adapter_server(
    package_dir: Path,
    *,
    command: Iterable[str],
    deny_env: str | None = None,
    offline_hint: str = "runtime offline — check vendor install",
) -> ManifestAdapterServer:
    """Build the Hatch-facing server for one manifest-driven package.

    Parameters:
        package_dir: directory containing this package's ``hatch.json``.
        command: spawn argv for the vendor stdio server.
        deny_env: env var with a comma-separated runtime deny list
            (e.g. ``RIKYU_TOOL_DENY``); unset/empty = no runtime deny.
        offline_hint: description suffix used for the static fallback tools.
    """
    manifest = _load_manifest(package_dir)
    name = manifest.get("name", package_dir.name)
    adapter = StdioMcpAdapter(
        name=name,
        command=list(command),
        tool_deny=_deny_list(deny_env),
    )
    return ManifestAdapterServer(name, adapter, manifest, offline_hint)
