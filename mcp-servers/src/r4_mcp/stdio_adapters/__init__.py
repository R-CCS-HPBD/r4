"""Local stdio MCP adapters for the default HPC set (ADR-022).

Adapters let the runtime present a small platform-owned tool surface while
keeping the same MCP wire contract. tools/call resolves to the adapter's
local ``ToolRegistry`` first; when an adapter is unavailable, the persistent
server falls back to the spawned reference MCP (adapter-fallback lifecycle).
"""

from __future__ import annotations

from .registry import AdapterError, CapabilityRegistry, ToolRegistry

__all__ = ["AdapterError", "CapabilityRegistry", "ToolRegistry"]
