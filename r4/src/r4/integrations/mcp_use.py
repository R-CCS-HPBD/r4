"""
mcp-use Integration for R4.

Provides adapters to use mcp-use agent patterns with the R4 Platform.

Installation:
    pip install r4[mcp-use]

Usage:
    from r4.integrations.mcp_use import create_gateway_mcp_client

    client = create_gateway_mcp_client(
        gateway_url="https://gateway.example.com",
        project_id="my-project",
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

# Lazy import to avoid requiring mcp-use if not installed
if TYPE_CHECKING:
    from mcp_use import MCPClient

    from ..errors import R4Error

_MCP_USE_AVAILABLE = None


def _check_mcp_use_available() -> bool:
    """Check if mcp-use is installed."""
    global _MCP_USE_AVAILABLE
    if _MCP_USE_AVAILABLE is None:
        try:
            __import__("mcp_use")
            _MCP_USE_AVAILABLE = True
        except ImportError:
            _MCP_USE_AVAILABLE = False
    return _MCP_USE_AVAILABLE


class MCPUseNotInstalledError(ImportError):
    """Raised when mcp-use is not installed."""

    def __init__(self):
        super().__init__("mcp-use is not installed. Install with: pip install r4[mcp-use]")


def _raise_retired_http() -> "R4Error":
    """The retired ``/gw/mcp/v1`` streamable-MCP endpoint (2026-10-20, H-10.2).

    UMRS never mounted an MCP-protocol HTTP endpoint — its MCP surface is
    the REST registry (``/umrs/mcp/servers``) plus the tool-call proxy
    (``/umrs/mcp/call``) — so a streamable mcp-use client pointed there
    could only ever 404. Fail loudly with the live alternatives instead.
    """
    from ..errors import R4Error

    return R4Error(
        "create_gateway_mcp_client is retired: /gw/mcp/v1 has no server "
        "route — UMRS never mounted a streamable-MCP endpoint (its MCP "
        "surface is the REST registry /umrs/mcp/servers plus the tool-call "
        "proxy /umrs/mcp/call). Use `r4 mcp …` or the R4 Client MCP API, or "
        "GatewayMCPAdapter below, which routes tool calls through the R4 Client."
    )


def create_gateway_mcp_client(
    gateway_url: str,
    project_id: str,
    api_key: Optional[str] = None,
    token: Optional[str] = None,
) -> "MCPClient":
    """
    Create an mcp-use MCPClient configured to use the Platform Gateway.

    RETIRED 2026-10-20 (plan H-10.2): the ``/gw/mcp/v1`` endpoint this
    pointed at was never mounted server-side, so the function raises
    :class:`r4.errors.R4Error` naming the live lanes (``r4 mcp …``, the R4
    Client MCP API, :class:`GatewayMCPAdapter`). Docstring below kept as the
    historical record of the intent.

    Args:
        gateway_url: Base URL of the Platform Gateway (e.g., "https://gateway.example.com")
        project_id: Project ID for multi-tenant isolation
        api_key: Optional API key for authentication
        token: Optional JWT token for authentication

    Returns:
        MCPClient configured for Gateway

    Raises:
        MCPUseNotInstalledError: If mcp-use is not installed

    Example:
        >>> from r4.integrations.mcp_use import create_gateway_mcp_client
        >>> client = create_gateway_mcp_client(
        ...     gateway_url="https://gateway.example.com",
        ...     project_id="my-project",
        ...     api_key="sk-xxx",
        ... )
        >>> # Use with mcp-use Agent
        >>> from mcp_use import Agent
        >>> agent = Agent(client=client)
        >>> result = await agent.run("Read /data/report.txt")
    """
    if not _check_mcp_use_available():
        raise MCPUseNotInstalledError()

    raise _raise_retired_http()


class GatewayMCPAdapter:
    """
    Adapter that wraps R4 Client for mcp-use compatibility.

    Use this when you want to use mcp-use patterns but route through
    the R4 Client (which handles auth, retries, etc.).

    Example:
        >>> from r4 import Client
        >>> from r4.integrations.mcp_use import GatewayMCPAdapter
        >>>
        >>> r4_client = Client()
        >>> adapter = GatewayMCPAdapter(r4_client)
        >>>
        >>> # Use adapter with mcp-use
        >>> tools = await adapter.list_tools()
        >>> result = await adapter.call_tool("read_file", {"path": "/data/x.txt"})
    """

    def __init__(self, client):
        """
        Initialize adapter with R4 Client.

        Args:
            client: r4.Client instance
        """
        self._client = client

    async def list_tools(self) -> list[dict]:
        """List available MCP tools from Gateway."""
        return await self._client.mcp.list_tools()

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """
        Call an MCP tool via Gateway.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            Tool execution result
        """
        return await self._client.mcp.call_tool(name=name, arguments=arguments)

    async def get_tool_schema(self, name: str) -> dict:
        """Get JSON schema for a tool."""
        tools = await self.list_tools()
        for tool in tools:
            if tool.get("name") == name:
                return tool.get("inputSchema", {})
        raise ValueError(f"Tool not found: {name}")


__all__ = [
    "create_gateway_mcp_client",
    "GatewayMCPAdapter",
    "MCPUseNotInstalledError",
]
