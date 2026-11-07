"""R4 TUI support utilities — shared by the JS companion bridge.

This package provides:
- commands: Slash command registry
- dashboard: Backend health snapshot
- mcp_config: MCP server configuration loading
- mcp_manager: MCP connection management
- modes: Mode enum (chat/plan/autopilot)

The interactive TUI is Console v3 (OpenTUI, packages/r4-tui-v3); the legacy
JS (Ink) app was removed 2026-09-11 (ADR-014).
"""

from __future__ import annotations


def __getattr__(name: str):
    """Lazy imports — avoid pulling in the entire r4 package at import time."""
    if name == "MCPServerConfig" or name == "load_mcp_configs":
        from .mcp_config import MCPServerConfig, load_mcp_configs

        return MCPServerConfig if name == "MCPServerConfig" else load_mcp_configs
    if name == "MCPConnectionManager":
        from .mcp_manager import MCPConnectionManager

        return MCPConnectionManager
    if name == "Mode":
        from .modes import Mode

        return Mode
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MCPConnectionManager",
    "MCPServerConfig",
    "Mode",
    "load_mcp_configs",
]
