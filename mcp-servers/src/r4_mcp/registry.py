"""
MCP Server Registry (M-002).

Tracks available MCP servers and their capabilities.
Provides discovery for Gateway routing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ServerInfo:
    """Information about a registered MCP server."""

    name: str
    version: str
    url: str
    tools: list[dict[str, Any]]
    transport: str = "stdio"
    status: str = "unknown"
    last_heartbeat: Optional[datetime] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "url": self.url,
            "tools": self.tools,
            "transport": self.transport,
            "status": self.status,
            "last_heartbeat": (self.last_heartbeat.isoformat() if self.last_heartbeat else None),
            "metadata": self.metadata,
        }


class ServerRegistry:
    """
    Registry for MCP servers.

    Tracks available servers and provides discovery for Gateway routing.

    Can operate in two modes:
    1. Local: In-memory registry for single-node deployments
    2. Remote: Syncs with central Gateway registry
    """

    def __init__(self, gateway_url: Optional[str] = None):
        """
        Initialize registry.

        Args:
            gateway_url: Optional Gateway URL for remote registration.
        """
        self.gateway_url = gateway_url
        self._servers: dict[str, ServerInfo] = {}
        self._lock = asyncio.Lock()
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_interval = 30  # seconds

    async def register(
        self,
        name: str,
        version: str,
        url: str,
        tools: list[dict[str, Any]],
        transport: str = "stdio",
        metadata: Optional[dict] = None,
    ) -> None:
        """
        Register an MCP server.

        Args:
            name: Server name.
            version: Server version.
            url: Server URL (for SSE) or identifier (for stdio).
            tools: List of tool definitions.
            transport: Transport type (stdio, sse).
            metadata: Optional additional metadata.
        """
        async with self._lock:
            server = ServerInfo(
                name=name,
                version=version,
                url=url,
                tools=tools,
                transport=transport,
                status="healthy",
                last_heartbeat=datetime.now(timezone.utc),
                metadata=metadata or {},
            )
            self._servers[name] = server
            logger.info(f"Registered MCP server: {name} v{version}")

        # Register with remote Gateway if configured
        if self.gateway_url:
            await self._register_remote(server)

    async def _register_remote(self, server: ServerInfo) -> None:
        """Register server with remote Gateway."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.gateway_url}/gw/mcp/registry/servers",
                    json=server.to_dict(),
                    timeout=10.0,
                )
                response.raise_for_status()
                logger.info(f"Registered {server.name} with Gateway")
        except Exception as e:
            logger.warning(f"Failed to register {server.name} with Gateway: {e}")

    async def unregister(self, name: str) -> None:
        """
        Unregister an MCP server.

        Args:
            name: Server name to unregister.
        """
        async with self._lock:
            if name in self._servers:
                del self._servers[name]
                logger.info(f"Unregistered MCP server: {name}")

        # Unregister from remote Gateway
        if self.gateway_url:
            await self._unregister_remote(name)

    async def _unregister_remote(self, name: str) -> None:
        """Unregister server from remote Gateway."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.delete(
                    f"{self.gateway_url}/gw/mcp/registry/servers/{name}",
                    timeout=10.0,
                )
                response.raise_for_status()
        except Exception as e:
            logger.warning(f"Failed to unregister {name} from Gateway: {e}")

    async def get(self, name: str) -> Optional[ServerInfo]:
        """
        Get server info by name.

        Args:
            name: Server name.

        Returns:
            ServerInfo if found, None otherwise.
        """
        async with self._lock:
            return self._servers.get(name)

    async def list_all(self) -> list[ServerInfo]:
        """
        List all registered servers.

        Returns:
            List of registered servers.
        """
        async with self._lock:
            return list(self._servers.values())

    async def list_tools(self) -> list[dict[str, Any]]:
        """
        List all available tools across all servers.

        Returns:
            List of tool definitions with server info.
        """
        tools = []
        async with self._lock:
            for server in self._servers.values():
                if server.status == "healthy":
                    for tool in server.tools:
                        tools.append(
                            {
                                **tool,
                                "_server": server.name,
                                "_server_url": server.url,
                            }
                        )
        return tools

    async def find_tool(self, tool_name: str) -> Optional[tuple[ServerInfo, dict]]:
        """
        Find which server provides a specific tool.

        Args:
            tool_name: Name of the tool.

        Returns:
            Tuple of (ServerInfo, tool_definition) if found.
        """
        async with self._lock:
            for server in self._servers.values():
                if server.status != "healthy":
                    continue
                for tool in server.tools:
                    if tool.get("name") == tool_name:
                        return server, tool
        return None

    async def heartbeat(self, name: str) -> None:
        """
        Update heartbeat for a server.

        Args:
            name: Server name.
        """
        async with self._lock:
            if name in self._servers:
                self._servers[name].last_heartbeat = datetime.now(timezone.utc)
                self._servers[name].status = "healthy"

    async def check_health(self, timeout: float = 5.0) -> dict[str, str]:
        """
        Check health of all registered servers.

        Returns:
            Dict of server_name -> status.
        """
        results = {}
        async with self._lock:
            servers = list(self._servers.items())

        for name, server in servers:
            if server.transport == "sse":
                # Check HTTP health endpoint
                try:
                    async with httpx.AsyncClient() as client:
                        response = await client.get(
                            f"{server.url}/health",
                            timeout=timeout,
                        )
                        if response.status_code == 200:
                            results[name] = "healthy"
                            await self.heartbeat(name)
                        else:
                            results[name] = "unhealthy"
                except Exception:
                    results[name] = "unreachable"
            else:
                # For stdio, check last heartbeat
                if server.last_heartbeat:
                    age = (datetime.now(timezone.utc) - server.last_heartbeat).seconds
                    if age < self._heartbeat_interval * 2:
                        results[name] = "healthy"
                    else:
                        results[name] = "stale"
                else:
                    results[name] = "unknown"

        # Update status
        async with self._lock:
            for name, status in results.items():
                if name in self._servers:
                    self._servers[name].status = status

        return results

    async def start_heartbeat_loop(self) -> None:
        """Start background heartbeat checking."""
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Background loop for health checks."""
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                await self.check_health()
            except Exception as e:
                logger.error(f"Health check failed: {e}")

    async def stop(self) -> None:
        """Stop the registry."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass


# Global registry instance
_registry: Optional[ServerRegistry] = None


def get_registry(gateway_url: Optional[str] = None) -> ServerRegistry:
    """Get the global registry instance."""
    global _registry
    if _registry is None:
        _registry = ServerRegistry(gateway_url)
    return _registry
