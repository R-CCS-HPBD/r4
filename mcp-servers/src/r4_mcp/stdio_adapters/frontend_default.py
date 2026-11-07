"""frontend-default adapter — the platform-default toolset (ADR-022)."""

from __future__ import annotations

from typing import Any


class FrontendDefaultMCP:
    """Exposes the default capabilities on tools/call."""

    name = "frontend-default"
    version = "1.0.0"

    def __init__(self, capabilities: dict[str, Any]) -> None:
        self.capabilities = dict(capabilities)
        self.tools = list(capabilities.get("capabilities", []))

    def list_tools(self) -> list[dict[str, Any]]:
        return [dict(tool, adapter=self.name, version=self.version) for tool in self.tools]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        known = {t.get("name") for t in self.tools}
        if name not in known:
            raise KeyError(f"unknown tool: {name!r}")
        return {
            "tool": name,
            "arguments": dict(arguments),
            "adapter": self.name,
            "status": "ok",
        }
