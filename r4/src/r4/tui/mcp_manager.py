"""MCP connection manager — lifecycle, health, and tool aggregation.

Wraps the existing ``r4.mcp.MCPClient`` (gateway-backed) and adds:

* Auto-discovery from local mcp.json configs
* Lazy connect / health monitoring
* Unified tool registry across all servers
* Copilot CLI-compatible ``/mcp`` commands
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .mcp_config import MCPServerConfig, load_mcp_configs

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """Server connection state."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class ServerConnection:
    """Runtime state for one MCP server connection."""

    config: MCPServerConfig
    state: ConnectionState = ConnectionState.DISCONNECTED
    last_health: float = 0.0
    error: str = ""
    tools: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return self.state == ConnectionState.CONNECTED

    def status_line(self) -> str:
        emoji = {
            ConnectionState.CONNECTED: "●",
            ConnectionState.CONNECTING: "◐",
            ConnectionState.DISCONNECTED: "○",
            ConnectionState.ERROR: "✗",
        }[self.state]
        return f"{emoji} {self.config.name} ({self.config.type})"


class MCPConnectionManager:
    """Manages connections to multiple MCP servers.

    Usage::

        mgr = MCPConnectionManager()
        mgr.load_configs()           # discover from mcp.json files
        mgr.discover_gateway(client) # discover from r4 gateway
        tools = mgr.all_tools()      # aggregated tool list
    """

    def __init__(self) -> None:
        self._servers: Dict[str, ServerConnection] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def load_configs(self) -> int:
        """Load server configs from mcp.json files.

        Returns the number of servers loaded.
        """
        configs = load_mcp_configs()
        for cfg in configs:
            self._servers[cfg.name] = ServerConnection(config=cfg)
        logger.debug("loaded %d MCP server configs", len(configs))
        return len(configs)

    def add_server(self, config: MCPServerConfig) -> None:
        """Add (or replace) a server configuration at runtime."""
        self._servers[config.name] = ServerConnection(config=config)

    def remove_server(self, name: str) -> bool:
        """Remove a server by name."""
        return self._servers.pop(name, None) is not None

    # ------------------------------------------------------------------
    # Gateway discovery
    # ------------------------------------------------------------------

    def discover_gateway(self, r4_client: Any) -> int:
        """Auto-discover servers from the R4 Gateway.

        Args:
            r4_client: An ``r4.Client`` instance

        Returns:
            Number of servers discovered
        """
        try:
            gateway_servers = r4_client.mcp.list_servers()
        except Exception as exc:
            logger.debug("gateway MCP discovery failed: %s", exc)
            return 0

        count = 0
        for srv in gateway_servers:
            name = srv.get("name", srv.get("id", ""))
            if not name:
                continue
            cfg = MCPServerConfig(
                name=name,
                type="http",
                url=srv.get("url", ""),
                description=srv.get("description", ""),
                source="gateway",
            )
            conn = ServerConnection(config=cfg, state=ConnectionState.CONNECTED)
            # Gateway servers are pre-connected via gateway proxy
            try:
                tools = r4_client.mcp.list_tools(server=name)
                conn.tools = [
                    {"name": t.name, "description": t.description, "parameters": t.parameters}
                    for t in tools
                ]
            except Exception:
                pass
            conn.last_health = time.time()
            self._servers[name] = conn
            count += 1

        logger.debug("discovered %d MCP servers from gateway", count)
        return count

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self, name: str) -> bool:
        """Attempt to connect to a server.

        For HTTP servers: tries a health check.
        For stdio servers: would start the subprocess (placeholder).
        """
        conn = self._servers.get(name)
        if conn is None:
            return False

        conn.state = ConnectionState.CONNECTING
        try:
            if conn.config.type == "http" and conn.config.url:
                import urllib.request

                req = urllib.request.Request(
                    conn.config.url.rstrip("/"),
                    method="GET",
                    headers=conn.config.headers,
                )
                with urllib.request.urlopen(req, timeout=10):
                    pass
                conn.state = ConnectionState.CONNECTED
                conn.last_health = time.time()
            elif conn.config.type == "stdio":
                # stdio servers are connected lazily when first tool is called
                conn.state = ConnectionState.CONNECTED
                conn.last_health = time.time()
            else:
                conn.state = ConnectionState.CONNECTED
                conn.last_health = time.time()
            conn.error = ""
            return True
        except Exception as exc:
            conn.state = ConnectionState.ERROR
            conn.error = str(exc)
            logger.debug("connect to %s failed: %s", name, exc)
            return False

    def connect_all(self) -> Dict[str, bool]:
        """Connect to all configured servers."""
        return {name: self.connect(name) for name in self._servers}

    def disconnect(self, name: str) -> None:
        """Disconnect a server."""
        conn = self._servers.get(name)
        if conn:
            conn.state = ConnectionState.DISCONNECTED

    def disconnect_all(self) -> None:
        """Disconnect all servers."""
        for name in self._servers:
            self.disconnect(name)

    # ------------------------------------------------------------------
    # Tool aggregation
    # ------------------------------------------------------------------

    def all_tools(self) -> List[Dict[str, Any]]:
        """Return aggregated tools from all connected servers."""
        tools: List[Dict[str, Any]] = []
        for conn in self._servers.values():
            if conn.is_healthy:
                for tool in conn.tools:
                    t = dict(tool)
                    t.setdefault("_server", conn.config.name)
                    tools.append(t)
        return tools

    def find_tool(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Find a tool by name across all connected servers."""
        for tool in self.all_tools():
            if tool.get("name") == tool_name:
                return tool
        return None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def list_connections(self) -> List[ServerConnection]:
        """Return all server connections."""
        return list(self._servers.values())

    def summary(self) -> str:
        """One-line summary for status bar: '3/5 MCP'."""
        total = len(self._servers)
        healthy = sum(1 for c in self._servers.values() if c.is_healthy)
        if total == 0:
            return "no MCP"
        return f"{healthy}/{total} MCP"

    def __len__(self) -> int:
        return len(self._servers)


__all__ = [
    "ConnectionState",
    "MCPConnectionManager",
    "ServerConnection",
]
