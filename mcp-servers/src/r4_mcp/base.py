"""
MCP Server Base Framework (M-001).

Provides the base class for implementing MCP servers with:
- Tool registration via decorators
- stdio and SSE transport support
- Standard MCP protocol implementation
- Structured logging
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

import structlog

from .chain_scanner import ToolChainContext, ToolChainScanner

logger = structlog.get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class TransportType(str, Enum):
    """MCP transport types."""

    STDIO = "stdio"
    SSE = "sse"


class ToolResultType(str, Enum):
    """Tool result types."""

    TEXT = "text"
    IMAGE = "image"
    RESOURCE = "resource"
    ERROR = "error"


@dataclass
class ToolParameter:
    """Tool parameter definition."""

    name: str
    type: str
    description: str
    required: bool = True
    enum: Optional[list[str]] = None
    default: Any = None

    def to_json_schema(self) -> dict[str, Any]:
        """Convert to JSON Schema format."""
        schema: dict[str, Any] = {
            "type": self.type,
            "description": self.description,
        }
        if self.enum:
            schema["enum"] = self.enum
        if self.default is not None:
            schema["default"] = self.default
        return schema


@dataclass
class ToolDefinition:
    """Tool definition for MCP."""

    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)
    handler: Optional[Callable] = None
    # ADR-105 D-2: a complete JSON Schema returned verbatim as ``inputSchema``
    # (nested objects, arrays, enums), for tools generated from a manifest.
    # When set, ``parameters`` is ignored.
    input_schema: Optional[dict[str, Any]] = None

    def to_mcp_format(self) -> dict[str, Any]:
        """Convert to MCP tool format."""
        if self.input_schema is not None:
            return {
                "name": self.name,
                "description": self.description,
                "inputSchema": self.input_schema,
            }
        properties = {}
        required = []

        for param in self.parameters:
            properties[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }


@dataclass
class ToolResult:
    """Result from a tool invocation."""

    type: ToolResultType
    content: Any
    is_error: bool = False

    @classmethod
    def success(cls, content: str) -> ToolResult:
        """Create a successful text result."""
        return cls(type=ToolResultType.TEXT, content=content, is_error=False)

    @classmethod
    def error(cls, message: str) -> ToolResult:
        """Create an error result."""
        return cls(type=ToolResultType.ERROR, content=message, is_error=True)

    @classmethod
    def image(cls, data: str, mime_type: str = "image/png") -> ToolResult:
        """Create an image result (base64 encoded)."""
        return cls(
            type=ToolResultType.IMAGE,
            content={"data": data, "mimeType": mime_type},
            is_error=False,
        )

    def to_mcp_format(self) -> dict[str, Any]:
        """Convert to MCP response format."""
        if self.type == ToolResultType.TEXT:
            return {
                "content": [{"type": "text", "text": self.content}],
                "isError": self.is_error,
            }
        elif self.type == ToolResultType.IMAGE:
            return {
                "content": [
                    {
                        "type": "image",
                        "data": self.content["data"],
                        "mimeType": self.content["mimeType"],
                    }
                ],
                "isError": self.is_error,
            }
        elif self.type == ToolResultType.ERROR:
            return {
                "content": [{"type": "text", "text": self.content}],
                "isError": True,
            }
        else:
            return {
                "content": [{"type": "text", "text": str(self.content)}],
                "isError": self.is_error,
            }


def Tool(
    name: str,
    description: str,
    parameters: Optional[dict[str, dict[str, Any]]] = None,
) -> Callable[[F], F]:
    """
    Decorator to register a method as an MCP tool.

    Usage:
        @Tool(
            name="read_file",
            description="Read contents of a file",
            parameters={
                "path": {"type": "string", "description": "File path to read"}
            }
        )
        async def read_file(self, path: str) -> ToolResult:
            ...
    """

    def decorator(func: F) -> F:
        # Parse parameters
        tool_params = []
        if parameters:
            for param_name, param_spec in parameters.items():
                tool_params.append(
                    ToolParameter(
                        name=param_name,
                        type=param_spec.get("type", "string"),
                        description=param_spec.get("description", ""),
                        required=param_spec.get("required", True),
                        enum=param_spec.get("enum"),
                        default=param_spec.get("default"),
                    )
                )

        # Attach tool definition to function
        func._mcp_tool = ToolDefinition(
            name=name,
            description=description,
            parameters=tool_params,
            handler=func,
        )
        return func

    return decorator


def _request_meta(params: dict[str, Any]) -> dict[str, Any]:
    meta = params.get("_meta", {})
    return meta if isinstance(meta, dict) else {}


def _optional_string(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


class MCPServer(ABC):
    """
    Base class for MCP servers.

    Implements the Model Context Protocol for tool integration with LLMs.

    Subclasses should:
    1. Set `name` and `version` class attributes
    2. Implement tools using the @Tool decorator
    3. Optionally override lifecycle methods

    Example:
        class FilesystemServer(MCPServer):
            name = "filesystem"
            version = "1.0.0"

            @Tool(name="read_file", ...)
            async def read_file(self, path: str) -> ToolResult:
                ...
    """

    name: str = "base"
    version: str = "0.1.0"

    # Protocol versions this server can speak (newest first). The server
    # claims the oldest in initialize responses for broad client compat and
    # echoes the client's requested version when it is in this set.
    SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
        "2025-06-18",
        "2025-03-26",
        "2024-11-05",
    )
    DEFAULT_PROTOCOL_VERSION: str = "2024-11-05"

    def __init__(
        self,
        transport: TransportType = TransportType.STDIO,
        chain_scanner: Optional[ToolChainScanner] = None,
    ):
        """Initialize MCP server."""
        self.transport = transport
        self.chain_scanner = chain_scanner or ToolChainScanner()
        self._chain_contexts: dict[str, ToolChainContext] = {}
        self._tools: dict[str, ToolDefinition] = {}
        self._running = False

        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            stream=sys.stderr,  # MCP uses stdout for protocol
        )

        # Discover tools
        self._discover_tools()

    def _discover_tools(self) -> None:
        """Discover tools from decorated methods."""
        for name in dir(self):
            if name.startswith("_"):
                continue
            method = getattr(self, name)
            if hasattr(method, "_mcp_tool"):
                tool_def: ToolDefinition = method._mcp_tool
                # Bind method to instance
                tool_def.handler = method
                self._tools[tool_def.name] = tool_def
                logger.debug(f"Registered tool: {tool_def.name}")

    def register_tool(self, tool_def: ToolDefinition) -> None:
        """Register a tool built at runtime (e.g. generated from a manifest).

        The handler is called as ``handler(**arguments)``; generated handlers
        take ``**kwargs`` and validate the arguments themselves. A name
        already registered (a decorated method) is refused, never shadowed.
        """
        if tool_def.handler is None:
            raise ValueError(f"tool {tool_def.name!r} has no handler")
        if tool_def.name in self._tools:
            raise ValueError(f"tool {tool_def.name!r} is already registered")
        self._tools[tool_def.name] = tool_def
        logger.debug(f"Registered generated tool: {tool_def.name}")

    @property
    def tools(self) -> list[ToolDefinition]:
        """Get list of registered tools."""
        return list(self._tools.values())

    async def initialize(self) -> None:
        """Called when server starts. Override for setup logic."""
        return

    async def shutdown(self) -> None:
        """Called when server stops. Override for cleanup logic."""
        return

    def _server_info(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version}

    def _capabilities(self) -> dict[str, Any]:
        return {"tools": {}}

    def _negotiate_protocol_version(self, params: dict) -> str:
        """Echo the client's requested version when supported, else ours."""
        requested = params.get("protocolVersion")
        if requested in self.SUPPORTED_PROTOCOL_VERSIONS:
            return str(requested)
        return self.DEFAULT_PROTOCOL_VERSION

    async def handle_initialize(self, params: dict) -> dict:
        """Handle MCP initialize request."""
        await self.initialize()

        return {
            "protocolVersion": self._negotiate_protocol_version(params),
            "capabilities": self._capabilities(),
            "serverInfo": self._server_info(),
        }

    async def handle_discover(self, _params: dict) -> dict:
        """Handle server/discover (mandatory from protocol 2026-07-28).

        Lets clients learn supported versions, capabilities, and identity
        before any other request.
        """
        return {
            "protocolVersions": list(self.SUPPORTED_PROTOCOL_VERSIONS),
            "capabilities": self._capabilities(),
            "serverInfo": self._server_info(),
        }

    async def handle_list_tools(self, params: dict) -> dict:
        """Handle MCP tools/list request."""
        return {"tools": [tool.to_mcp_format() for tool in self.tools]}

    async def handle_call_tool(self, params: dict) -> dict:
        """Handle MCP tools/call request."""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name not in self._tools:
            return ToolResult.error(f"Unknown tool: {tool_name}").to_mcp_format()

        tool = self._tools[tool_name]
        try:
            # Call the tool handler (bug.memo MCPS1 2026-04-25: use
            # ``inspect.iscoroutinefunction`` so wrapped/decorated async
            # handlers are detected reliably).
            if inspect.iscoroutinefunction(tool.handler):
                result = await tool.handler(**arguments)
            else:
                result = tool.handler(**arguments)

            # Ensure result is ToolResult
            if not isinstance(result, ToolResult):
                result = ToolResult.success(str(result))

            result = self._scan_tool_result(tool_name, result, params)
            return result.to_mcp_format()

        except TypeError as e:
            logger.warning(f"Invalid arguments for tool {tool_name}: {e}")
            return ToolResult.error(f"Invalid arguments: {e}").to_mcp_format()
        except Exception as e:
            logger.exception(f"Error executing tool {tool_name}")
            return ToolResult.error(f"Tool execution failed: {e}").to_mcp_format()

    def _scan_tool_result(
        self,
        tool_name: str,
        result: ToolResult,
        params: dict[str, Any],
    ) -> ToolResult:
        if result.is_error:
            return result

        scan_result = self.chain_scanner.scan_output(
            self._tool_result_content(result),
            source_tool=tool_name,
            target_tool=self._target_tool(params),
            context=self._chain_context(params),
        )
        if scan_result.is_blocked:
            reason = "; ".join(scan_result.block_reasons) or "blocked output"
            return ToolResult.error(f"Tool output blocked by ChainScanner: {reason}")
        return result

    def _tool_result_content(self, result: ToolResult) -> str:
        if isinstance(result.content, str):
            return result.content
        return json.dumps(result.content, default=str)

    def _chain_context(self, params: dict[str, Any]) -> Optional[ToolChainContext]:
        meta = _request_meta(params)
        chain_id = meta.get("chain_id") or meta.get("chainId") or params.get("chain_id")
        if not chain_id:
            return None
        chain_key = str(chain_id)
        if chain_key not in self._chain_contexts:
            self._chain_contexts[chain_key] = ToolChainContext(
                chain_id=chain_key,
                user_id=_optional_string(meta.get("user_id")),
                project_id=_optional_string(meta.get("project_id")),
            )
        return self._chain_contexts[chain_key]

    def _target_tool(self, params: dict[str, Any]) -> Optional[str]:
        meta = _request_meta(params)
        return _optional_string(
            meta.get("target_tool") or meta.get("targetTool") or params.get("target_tool")
        )

    async def handle_message(self, message: dict) -> Optional[dict]:
        """
        Handle an incoming MCP message.

        Returns response dict or None for notifications.
        """
        method = message.get("method")
        params = message.get("params", {})
        msg_id = message.get("id")

        logger.debug(f"Handling message: method={method}, id={msg_id}")

        # Notifications we explicitly tolerate (never answered, no warning).
        if method and method.startswith("notifications/"):
            logger.debug(f"Ignoring notification: {method}")
            return None

        # Route to handler
        handler_map = {
            "initialize": self.handle_initialize,
            "server/discover": self.handle_discover,
            "ping": self.handle_ping,
            "tools/list": self.handle_list_tools,
            "tools/call": self.handle_call_tool,
        }

        handler = handler_map.get(method)
        if handler:
            try:
                result = await handler(params)
                if msg_id is not None:
                    return {"jsonrpc": "2.0", "id": msg_id, "result": result}
            except Exception as e:
                logger.exception(f"Error handling {method}")
                if msg_id is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32603, "message": str(e)},
                    }
        else:
            logger.warning(f"Unknown method: {method}")
            if msg_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }

        return None

    async def handle_ping(self, _params: dict) -> dict:
        """Handle MCP ping request (heartbeat)."""
        return {}

    async def run_stdio(self) -> None:
        """Run server using stdio transport."""
        logger.info(f"Starting {self.name} MCP server (stdio)")
        self._running = True

        # Read from stdin, write to stdout
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)

        loop = asyncio.get_event_loop()
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        writer_transport, writer_protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, loop)

        try:
            while self._running:
                # Read JSON-RPC message (newline-delimited)
                line = await reader.readline()
                if not line:
                    break

                try:
                    message = json.loads(line.decode())
                    response = await self.handle_message(message)
                    if response:
                        writer.write((json.dumps(response) + "\n").encode())
                        await writer.drain()
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON: {e}")
                    continue

        finally:
            await self.shutdown()
            logger.info(f"Stopped {self.name} MCP server")

    # -- SSE transport handlers (kept small: C901 gate) ------------------------

    async def _sse_handle_connect(self, request: Any, clients: dict) -> Any:
        """Handle an SSE connection from a client (GET /sse)."""
        from aiohttp import web

        session_id = request.query.get("session_id", str(id(request)))
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await response.prepare(request)

        queue: asyncio.Queue = asyncio.Queue()
        clients[session_id] = queue
        logger.info(f"SSE client connected: {session_id}")

        try:
            while self._running:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    await response.write(f"data: {json.dumps(message)}\n\n".encode())
                except asyncio.TimeoutError:
                    await response.write(b": keepalive\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            clients.pop(session_id, None)
            logger.info(f"SSE client disconnected: {session_id}")
        return response

    async def _sse_handle_message(self, request: Any, clients: dict) -> Any:
        """Handle an incoming JSON-RPC message from a client (POST /message)."""
        from aiohttp import web

        session_id = request.query.get("session_id")
        if not session_id:
            return web.json_response({"error": "session_id required"}, status=400)
        try:
            message = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        response = await self.handle_message(message)

        # If the client has an SSE connection, deliver via SSE queue.
        if session_id in clients:
            if response:
                await clients[session_id].put(response)
            return web.json_response({"status": "queued"})
        if response:
            return web.json_response(response)
        return web.json_response({"status": "ok"})

    async def _sse_handle_cors_preflight(self, _request: Any) -> Any:
        """Handle CORS preflight requests."""
        from aiohttp import web

        return web.Response(
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )

    async def _sse_handle_health(self, _request: Any) -> Any:
        """Health check endpoint."""
        from aiohttp import web

        return web.json_response(
            {
                "status": "ok",
                "server": self.name,
                "version": self.version,
                "transport": "sse",
            }
        )

    async def run_sse(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """
        Run server using SSE transport (for web clients).

        Implements a simple HTTP server with:
        - GET /sse: SSE endpoint for server -> client events
        - POST /message: Receive JSON-RPC messages from client
        """
        try:
            from aiohttp import web
        except ImportError:
            raise ImportError("aiohttp required for SSE transport: pip install aiohttp")

        logger.info(f"Starting {self.name} MCP server (SSE on {host}:{port})")
        self._running = True

        # Client connections (mapping from session_id -> queue)
        clients: dict[str, asyncio.Queue] = {}

        app = web.Application()
        app.router.add_get("/sse", lambda request: self._sse_handle_connect(request, clients))
        app.router.add_post("/message", lambda request: self._sse_handle_message(request, clients))
        app.router.add_options("/message", self._sse_handle_cors_preflight)
        app.router.add_get("/health", self._sse_handle_health)

        runner = web.AppRunner(app)
        await runner.setup()

        site = web.TCPSite(runner, host, port)
        await site.start()

        logger.info(f"{self.name} MCP server running on http://{host}:{port}")

        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            await self.shutdown()
            await runner.cleanup()
            logger.info(f"Stopped {self.name} MCP server")

    def run(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """Run the MCP server."""
        if self.transport == TransportType.STDIO:
            asyncio.run(self.run_stdio())
        elif self.transport == TransportType.SSE:
            asyncio.run(self.run_sse(host, port))
        else:
            raise ValueError(f"Unknown transport: {self.transport}")

    def stop(self) -> None:
        """Stop the server."""
        self._running = False
