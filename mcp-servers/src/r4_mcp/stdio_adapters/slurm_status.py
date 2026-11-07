"""slurm-status adapter — local Slurm probe (ADR-022)."""

from __future__ import annotations

from typing import Any


class SlurmStatusMCP:
    """Node/partition status derived from the local Slurm endpoint."""

    name = "slurm-status"

    def __init__(self, probe: Any = None) -> None:
        self._probe = probe

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "sinfo", "description": "Partition/node info"},
            {"name": "squeue", "description": "Queue jobs"},
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in {"sinfo", "squeue"}:
            raise KeyError(f"unknown tool: {name!r}")
        result = self._probe(name, arguments) if self._probe else {}
        return {
            "tool": name,
            "arguments": dict(arguments),
            "adapter": self.name,
            "status": "ok",
            "result": result,
        }
