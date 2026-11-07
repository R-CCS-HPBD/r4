"""hpc-job adapter — Slurm job lifecycle tools (ADR-022)."""

from __future__ import annotations

from typing import Any


class HpcJobMCP:
    """Submit/status/cancel/scancel through the platform job channel."""

    name = "hpc-job"

    def __init__(self, bridge: Any = None) -> None:
        self._bridge = bridge

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "submit", "description": "Submit Slurm job"},
            {"name": "status", "description": "Query job/cluster status"},
            {"name": "cancel", "description": "Cancel job"},
            {"name": "scancel", "description": "Force-cancel job"},
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in {"submit", "status", "cancel", "scancel"}:
            raise KeyError(f"unknown tool: {name!r}")
        return {
            "tool": name,
            "arguments": dict(arguments),
            "adapter": self.name,
            "status": "queued" if name == "submit" else "ok",
        }
