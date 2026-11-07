"""Shared protocol types for local stdio MCP adapters (ADR-022)."""

from __future__ import annotations

from typing import Any, Protocol

MAX_CAPABILITIES = 5


class AdapterError(Exception):
    """Base for adapter-level failures."""


class CapabilityRegistry(dict[str, Any]):
    """Manifest-backed capabilities for one adapter.

    Shape follows ``packages/r4-mcp-hatch-registry/README.md``: required
    keys are ``capabilities`` (list of tool descriptors) plus optionally
    ``adapter`` (name/version) and ``required_env``. Unknown keys are kept
    verbatim so future manifest fields stay forward-compatible.
    """

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> "CapabilityRegistry":
        if "capabilities" not in manifest or not isinstance(manifest["capabilities"], list):
            raise AdapterError("manifest must define a 'capabilities' list")
        registry = cls()
        registry.update(manifest)
        return registry

    def capability_names(self) -> list[str]:
        return [c.get("name", "") for c in self.get("capabilities", [])]


class ToolRegistry(Protocol):
    """tools/call surface for one adapter."""

    def list_tools(self) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...
