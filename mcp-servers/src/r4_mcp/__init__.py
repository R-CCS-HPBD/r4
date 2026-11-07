"""
R4 MCP Servers.

Model Context Protocol servers for tool integration.
"""

from .base import MCPServer, Tool, ToolResult
from .registry import ServerRegistry

__version__ = "0.1.0"
__all__ = ["MCPServer", "Tool", "ToolResult", "ServerRegistry"]
