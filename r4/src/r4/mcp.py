"""
R4 MCP Client (A-301, A-302, A-303, T-214).

Model Context Protocol (MCP) integration for the R4 SDK.

Features:
- A-301: Tool invocation
- A-302: Tool discovery
- A-303: Tool calling loop (LLM → tool → response)
- T-214: Injection scanning in tool results
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import httpx

from .errors import R4Error

if TYPE_CHECKING:
    from .agent.untrusted_scanner import ScanResult
    from .client import Client

logger = logging.getLogger(__name__)


def _cancel_check(turn_cancel: Any) -> Optional[Callable[[], None]]:
    """Return the cancellation check callable for a turn, if any."""
    return turn_cancel.check if turn_cancel is not None else None


def _cancel_register(turn_cancel: Any) -> Optional[Callable[[Callable[[], None]], None]]:
    """Return the abort-registration callable for a turn, if any."""
    return turn_cancel.register_close if turn_cancel is not None else None


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class MCPTool:
    """An MCP tool definition."""

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    server: str = ""
    # The r4_mcp ToolCapability flag names the tool's manifest declares
    # (``required_capabilities``), when the registry publishes them; empty
    # means undeclared, which a team plan reads as a writer (ADR-092 D-6).
    capabilities: tuple = ()

    def to_openai_function(self) -> Dict[str, Any]:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MCPTool":
        """Create from dictionary."""
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            parameters=data.get("parameters") or data.get("inputSchema", {}),
            server=data.get("server") or data.get("_server", ""),
            capabilities=_declared_capabilities(
                data.get("required_capabilities", data.get("capabilities"))
            ),
        )


def _declared_capabilities(raw: Any) -> tuple:
    """Capability flag names from a descriptor: a list, or ``"A|B"`` text.

    Anything else (an MCP ``capabilities`` object, a number) declares nothing.
    """
    if isinstance(raw, str):
        raw = raw.split("|")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(sorted({str(flag).strip() for flag in raw if str(flag).strip()}))


@dataclass
class MCPToolCall:
    """A tool call request."""

    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPToolCallResult:
    """Raw result from ``/gw/mcp/call`` (MCP wire shape).

    Keeps the content-block list structure MCP tools return so callers
    (agent session, integrations) can normalize text/media themselves.

    Attributes:
        content: List of MCP content blocks ({"type": "text", "text": ...}).
        is_error: True when the tool reported an error.
        server: Name of the MCP server that executed the call.
        error: Gateway-level error message, when transport/lookup failed.
    """

    content: List[Dict[str, Any]] = field(default_factory=list)
    is_error: bool = False
    server: str = ""
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        """Inverse of is_error, for convenience."""
        return not self.is_error


@dataclass
class MCPToolResult:
    """Result from a tool invocation."""

    tool_name: str
    result: Any
    success: bool = True
    error: Optional[str] = None
    call_id: str = ""
    scan_result: Optional["ScanResult"] = None  # T-214: Injection scan result
    _scanned_content: Optional[str] = None  # Cached scanned content

    def to_message(
        self,
        scan_for_injection: bool = True,
        wrap_content: bool = True,
    ) -> Dict[str, str]:
        """Convert to chat message format.

        Args:
            scan_for_injection: Enable injection scanning (T-214)
            wrap_content: Wrap content with boundary markers (T-213)

        Returns:
            Chat message dict with role, tool_call_id, and content
        """
        content = self._render_content()
        if scan_for_injection and self.scan_result is None and self.success:
            content = self._scan_and_cache(content)
        if self._scanned_content is not None:
            content = self._scanned_content
        if wrap_content and self.success:
            content = self._wrap_boundary(content)
        return {
            "role": "tool",
            "tool_call_id": self.call_id,
            "content": content,
        }

    def _render_content(self) -> str:
        """Render the raw tool result as message content."""
        if self.success:
            return json.dumps(self.result) if not isinstance(self.result, str) else self.result
        return f"Error: {self.error}"

    def _scan_and_cache(self, content: str) -> str:
        """Scan tool output for injection (T-214) and cache the sanitized text."""
        from .agent.untrusted_scanner import ContentSource, get_default_scanner

        scanner = get_default_scanner()
        self.scan_result = scanner.scan(
            content,
            source=ContentSource.TOOL_OUTPUT,
            context={"tool_name": self.tool_name},
        )
        if self.scan_result.is_blocked:
            content = f"[Content blocked: {self.scan_result.block_reason}]"
        elif self.scan_result.sanitized_content is not None:
            content = self.scan_result.sanitized_content
        self._scanned_content = content
        return content

    def _wrap_boundary(self, content: str) -> str:
        """Wrap external content with boundary markers (T-213)."""
        from .agent.untrusted_scanner import ContentSource, wrap_external_content

        return wrap_external_content(
            content,
            source=ContentSource.TOOL_OUTPUT,
            metadata={"tool_name": self.tool_name},
        )


# =============================================================================
# MCP Client (A-301, A-302)
# =============================================================================


def _auth_headers(auth_config: Optional[Dict[str, str]]) -> Dict[str, str]:
    """The header UMRS presents upstream for this auth_config (api/mcp.py
    ``_upstream_auth_headers``): Bearer token, or the bare token in ``header``."""
    cfg = auth_config or {}
    token = cfg.get("token") or cfg.get("api_key")
    if not token:
        return {}
    return {cfg["header"]: token} if cfg.get("header") else {"Authorization": f"Bearer {token}"}


def discover_tools(
    url: str, auth_config: Optional[Dict[str, str]] = None, *, timeout: float = 5.0
) -> List[Dict[str, Any]]:
    """The tools an r4 MCP bridge lists at ``GET {url}/tools``; [] when it lists none.

    Registration used to send no tools and nothing discovered them, so the agent
    saw a server it could not call (defect-wave 2026-10-08 #5). The registering
    client discovers, not UMRS: server-side discovery would make UMRS dial a
    caller-supplied URL.
    """
    if not url.startswith(("http://", "https://")):
        return []
    try:
        response = httpx.get(
            f"{url.rstrip('/')}/tools", headers=_auth_headers(auth_config), timeout=timeout
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    tools = payload.get("tools", []) if isinstance(payload, dict) else payload
    return [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []


def _probe_reachable(url: str) -> bool:
    """True when ``url`` answers a HEAD request within 5 s.

    Sent without credentials. A 4xx answer still means the server is serving
    (MCP endpoints commonly refuse HEAD with 401/405); 5xx or no answer does not.
    """
    if not url:
        return False
    try:
        with urlopen(Request(url, method="HEAD"), timeout=5):
            return True
    except HTTPError as exc:
        return exc.code < 500
    except (URLError, OSError, ValueError):
        return False


class MCPClient:
    """
    MCP (Model Context Protocol) client.

    Access via Client.mcp:
        client = Client()
        tools = client.mcp.list_tools()
        result = client.mcp.invoke("read_file", path="/path/to/file")

    Features:
    - Tool discovery (list available tools)
    - Tool invocation (call tools directly)
    - Tool calling loop (LLM decides which tools to call)
    """

    def __init__(self, parent: "Client"):
        """Initialize MCP client."""
        self._parent = parent
        self._tools_cache: Optional[List[MCPTool]] = None

    @staticmethod
    def _unwrap_list_payload(payload: Any, key: str) -> List[Dict[str, Any]]:
        """Accept either a bare list or a wrapped list payload."""
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            items = payload.get(key, [])
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    @staticmethod
    def _normalize_tool_call_content(content: Any) -> Any:
        """Collapse MCP content blocks into a more convenient R4 result."""
        if not isinstance(content, list):
            return content

        if len(content) == 1 and isinstance(content[0], dict):
            block = content[0]
            if block.get("type") == "text":
                return block.get("text", "")

        return content

    @staticmethod
    def _extract_error_message(data: Dict[str, Any]) -> Optional[str]:
        """Extract a human-readable error from Gateway tool responses."""
        if data.get("error"):
            return str(data["error"])

        content = data.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if text:
                        parts.append(str(text))
            if parts:
                return "\n".join(parts)

        return None

    @property
    def _config(self):
        """Get config from parent."""
        return self._parent.config

    def _url(self, path: str) -> str:
        """``{gateway}{/gw|/umrs}/mcp/{path}``: direct-UMRS mode serves /umrs/mcp.

        Stub parents without the client's prefix probe keep the gateway prefix.
        """
        prefix = getattr(self._parent, "_api_prefix", "/gw")
        return f"{self._config.gateway_url}{prefix}/mcp/{path}"

    # =========================================================================
    # A-302: Tool Discovery
    # =========================================================================

    def list_tools(
        self,
        server: Optional[str] = None,
        refresh: bool = False,
        _turn_cancel: Any = None,
    ) -> List[MCPTool]:
        """
        List available MCP tools (A-302).

        Args:
            server: Filter by server name (e.g., "filesystem", "database")
            refresh: Force refresh from server

        Returns:
            List of available tools
        """
        if self._tools_cache is not None and not refresh:
            return self._filter_tools_by_server(self._tools_cache, server)

        url = self._url("tools")

        try:
            data = self._parent.http.get(
                url,
                timeout=30,
                cancel_check=_cancel_check(_turn_cancel),
                register_abort=_cancel_register(_turn_cancel),
            )
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to list MCP tools: {e}")

        tools = [MCPTool.from_dict(t) for t in self._unwrap_list_payload(data, "tools")]
        self._tools_cache = tools
        return self._filter_tools_by_server(tools, server)

    @staticmethod
    def _filter_tools_by_server(tools: List[MCPTool], server: Optional[str]) -> List[MCPTool]:
        """Filter a tool list by server name when one is requested."""
        if server:
            return [t for t in tools if t.server == server]
        return tools

    def get_tool(self, tool_name: str) -> Optional[MCPTool]:
        """Get a specific tool by name."""
        tools = self.list_tools()
        for tool in tools:
            if tool.name == tool_name:
                return tool
        return None

    def list_servers(self) -> List[Dict[str, Any]]:
        """List available MCP servers."""
        url = self._url("servers")
        try:
            data = self._parent.http.get(url, timeout=30)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to list MCP servers: {e}")
        return self._unwrap_list_payload(data, "servers")

    # =========================================================================
    # X-206: MCP Server Registration
    # =========================================================================

    def register_server(
        self,
        name: str,
        url: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        auth_type: str = "none",
        auth_config: Optional[Dict[str, str]] = None,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Register a user-managed MCP server (X-206).

        Args:
            name: Unique server name
            url: Server base URL
            tools: Tool definitions; ``None`` discovers them from ``GET {url}/tools``
            auth_type: Authentication type (none, bearer, api_key)
            auth_config: Auth configuration (e.g., {"token": "..."})
            description: Server description
            metadata: Additional metadata

        Returns:
            Registration result with server_id

        Example:
            result = client.mcp.register_server(
                name="my-custom-server",
                url="http://localhost:8080",
                description="My custom MCP server",
            )
            print(result["server_id"])
        """
        reg_url = self._url("servers")
        if tools is None:
            tools = discover_tools(url, auth_config)
        body = {
            "name": name,
            "url": url,
            "tools": tools,
            "auth_type": auth_type,
            "auth_config": auth_config or {},
            "description": description,
            "metadata": metadata or {},
        }
        try:
            return self._parent.http.post(reg_url, json_body=body, timeout=30)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to register MCP server: {e}")

    def unregister_server(self, server_id: str) -> bool:
        """Unregister an MCP server."""
        url = self._url(f"servers/{server_id}")
        try:
            self._parent.http.delete(url, timeout=30)
            return True
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to unregister MCP server: {e}")

    def get_server_info(self, server_id: str) -> Dict[str, Any]:
        """Get MCP server details."""
        url = self._url(f"servers/{server_id}")
        try:
            return self._parent.http.get(url, timeout=30)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to get MCP server info: {e}")

    def check_server_health(self, server_id: str) -> Dict[str, Any]:
        """Probe whether an UMRS-registered MCP server answers at its URL.

        UMRS serves no per-server probe route (only ``GET /mcp/servers/{id}``
        and the subsystem-wide ``GET /mcp/health``), and the registry row says
        ``healthy`` from registration on with nothing updating it. So this
        reads the row for the URL and probes it from the client, the same HEAD
        check ``r4 mcp health`` uses for local servers; ``registry_status`` is
        the recorded value. An unknown server raises the route's 404
        ``APIError``.
        """
        try:
            info = self._parent.http.get(self._url(f"servers/{server_id}"), timeout=30)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Failed to check MCP server health: {e}")
        url = str(info.get("url") or "")
        healthy = _probe_reachable(url)
        return {
            "server": info.get("name", server_id),
            "server_id": info.get("server_id", server_id),
            "url": url,
            "healthy": healthy,
            "status": "healthy" if healthy else "unreachable (probe)",
            "registry_status": str(info.get("status") or "unknown"),
            "source": "probe",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    # =========================================================================
    # A-301: Tool Invocation
    # =========================================================================

    def invoke(
        self,
        tool_name: str,
        **kwargs: Any,
    ) -> MCPToolResult:
        """
        Invoke an MCP tool (A-301).

        Args:
            tool_name: Name of the tool to invoke
            **kwargs: Tool arguments

        Returns:
            MCPToolResult with the tool's response

        Example:
            result = client.mcp.invoke("read_file", path="/path/to/file")
            if result.success:
                print(result.result)
        """
        url = self._url("invoke")
        metadata = kwargs.pop("_meta", None)
        turn_cancel = kwargs.pop("_turn_cancel", None)
        payload = {"tool": tool_name, "arguments": kwargs}
        if isinstance(metadata, dict) and metadata:
            payload["_meta"] = metadata
        try:
            data = self._parent.http.post(
                url,
                json_body=payload,
                timeout=60,
                cancel_check=_cancel_check(turn_cancel),
                register_abort=_cancel_register(turn_cancel),
            )
        except R4Error as e:
            return MCPToolResult(tool_name=tool_name, result=None, success=False, error=str(e))
        except Exception as e:  # pragma: no cover - defensive
            return MCPToolResult(tool_name=tool_name, result=None, success=False, error=str(e))

        return self._parse_invoke_response(tool_name, data)

    def _parse_invoke_response(self, tool_name: str, data: Dict[str, Any]) -> MCPToolResult:
        """Interpret a raw MCP invoke response into an ``MCPToolResult``."""
        if "result" in data or "success" in data or "error" in data:
            return MCPToolResult(
                tool_name=tool_name,
                result=data.get("result"),
                success=data.get("success", True),
                error=data.get("error"),
            )

        is_error = data.get("isError", False)
        result = self._normalize_tool_call_content(data.get("content"))
        error = self._extract_error_message(data) if is_error else None
        return MCPToolResult(
            tool_name=tool_name,
            result=None if is_error else result,
            success=not is_error,
            error=error,
        )

    def invoke_batch(
        self,
        calls: List[MCPToolCall],
    ) -> List[MCPToolResult]:
        """
        Invoke multiple tools in batch.

        Args:
            calls: List of tool calls

        Returns:
            List of results in same order as calls
        """
        results = []
        for call in calls:
            arguments = dict(call.arguments)
            if call.metadata:
                arguments["_meta"] = call.metadata
            result = self.invoke(call.tool_name, **arguments)
            result.call_id = call.call_id
            results.append(result)
        return results

    def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        server: Optional[str] = None,
    ) -> MCPToolCallResult:
        """Call an MCP tool via ``/gw/mcp/call`` (G-004 wire shape).

        Compatibility surface used by the agent session and integrations
        (``agent/session.py``, ``integrations/langchain.py``,
        ``integrations/mcp_use.py``). Keeps the raw content-block list so
        those callers can normalize text/media themselves.

        Args:
            name: Tool name.
            arguments: Tool arguments.
            server: Accepted for call-site compatibility. Ignored — the
                gateway's tool lookup (``/umrs/mcp/tools/lookup``) resolves
                owning server from the tool name, and ``ToolCallRequest``
                has no ``server`` field.

        Returns:
            :class:`MCPToolCallResult` with ``content``/``is_error``/``server``.
        """
        _ = server  # compatibility parameter; ownership resolved by gateway
        url = self._url("call")
        payload: Dict[str, Any] = {"tool": name, "arguments": arguments or {}}
        return self._parse_call_tool_response(
            name,
            lambda: self._parent.http.post(url, json_body=payload, timeout=60),
        )

    def _parse_call_tool_response(
        self, name: str, call: Callable[[], Dict[str, Any]]
    ) -> MCPToolCallResult:
        """Execute *call* and map the response to :class:`MCPToolCallResult`."""
        try:
            data = call()
        except R4Error as e:
            return MCPToolCallResult(content=[], is_error=True, error=str(e))
        except Exception as e:  # pragma: no cover - defensive
            return MCPToolCallResult(content=[], is_error=True, error=str(e))

        content = data.get("content")
        is_error = bool(data.get("isError")) or bool(data.get("error"))
        error = self._extract_error_message(data) if is_error else None
        return MCPToolCallResult(
            content=content if isinstance(content, list) else [],
            is_error=is_error,
            server=str(data.get("server") or ""),
            error=error,
        )

    # =========================================================================
    # A-303: Tool Calling Loop
    # =========================================================================

    def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[str]] = None,
        model: Optional[str] = None,
        max_iterations: int = 5,
        temperature: float = 0.7,
        on_tool_call: Optional[Callable[[MCPToolCall], None]] = None,
        on_tool_result: Optional[Callable[[MCPToolResult], None]] = None,
    ) -> Dict[str, Any]:
        """
        Chat with automatic tool calling (A-303).

        This implements the LLM → tool → response loop:
        1. Send messages to LLM with available tools
        2. If LLM requests tool calls, execute them
        3. Send tool results back to LLM
        4. Repeat until LLM responds without tool calls or max iterations

        Args:
            messages: Initial chat messages
            tools: List of tool names to make available (None = all)
            model: LLM model to use
            max_iterations: Max tool calling iterations
            temperature: LLM temperature
            on_tool_call: Callback when tool is called
            on_tool_result: Callback when tool returns

        Returns:
            Final chat response from LLM

        Example:
            response = client.mcp.chat_with_tools(
                messages=[{"role": "user", "content": "Read and summarize /data/report.txt"}],
                tools=["read_file"],
            )
            print(response["choices"][0]["message"]["content"])
        """
        available_tools = self._select_tools(self.list_tools(), tools)
        if not available_tools:
            # No tools - just do regular chat
            return self._parent.chat(
                messages=messages,
                model=model,
                temperature=temperature,
            )

        # Convert to OpenAI format
        tools_param = [t.to_openai_function() for t in available_tools]

        # Run the loop
        current_messages = list(messages)
        iteration = 0
        chain_id = str(uuid.uuid4())

        while iteration < max_iterations:
            iteration += 1

            # Call LLM with tools
            response = self._parent.chat(
                messages=current_messages,
                model=model,
                temperature=temperature,
                tools=tools_param,
            )

            message = response.get("choices", [{}])[0].get("message", {})
            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                # LLM finished without tool calls
                return response

            # Process tool calls
            current_messages.append(message)  # Add assistant's response
            self._process_tool_calls(
                tool_calls,
                current_messages,
                chain_id=chain_id,
                iteration=iteration,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
            )

        # Max iterations reached
        logger.warning(f"Tool calling loop reached max iterations ({max_iterations})")
        return self._parent.chat(
            messages=current_messages,
            model=model,
            temperature=temperature,
        )

    @staticmethod
    def _select_tools(available: List[MCPTool], tools: Optional[List[str]]) -> List[MCPTool]:
        """Restrict available tools to the requested subset."""
        if tools:
            return [t for t in available if t.name in tools]
        return available

    def _process_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        current_messages: List[Dict[str, Any]],
        *,
        chain_id: str,
        iteration: int,
        on_tool_call: Optional[Callable[[MCPToolCall], None]],
        on_tool_result: Optional[Callable[[MCPToolResult], None]],
    ) -> None:
        """Execute the tool calls requested in one LLM turn and append results."""
        for tc in tool_calls:
            func = tc.get("function", {})
            tool_call = MCPToolCall(
                tool_name=func.get("name", ""),
                arguments=json.loads(func.get("arguments", "{}")),
                call_id=tc.get("id", ""),
                metadata={
                    "chain_id": chain_id,
                    "call_id": tc.get("id", ""),
                    "iteration": iteration,
                },
            )

            if on_tool_call:
                on_tool_call(tool_call)

            # Execute tool
            result = self.invoke(
                tool_call.tool_name,
                _meta=tool_call.metadata,
                **tool_call.arguments,
            )
            result.call_id = tool_call.call_id

            if on_tool_result:
                on_tool_result(result)

            # Add tool result to messages
            current_messages.append(result.to_message())

    def agent_run(
        self,
        task: str,
        *,
        tools: Optional[List[str]] = None,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_iterations: int = 10,
        verbose: bool = False,
    ) -> str:
        """
        Run an agentic task with tool access.

        Higher-level interface for running tasks that may require
        multiple tool calls.

        Args:
            task: The task description
            tools: Tools to make available
            system_prompt: Custom system prompt
            model: LLM model to use
            max_iterations: Max tool calling iterations
            verbose: Print progress

        Returns:
            Final response text

        Example:
            result = client.mcp.agent_run(
                "Analyze the data in /data/results.csv and summarize key findings",
                tools=["read_file", "execute_python"],
            )
            print(result)
        """
        if system_prompt is None:
            system_prompt = (
                "You are a helpful assistant with access to tools. "
                "Use the tools as needed to complete the user's task. "
                "Think step by step and explain your reasoning."
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]

        def on_tool_call(tc: MCPToolCall):
            if verbose:
                print(f"[Tool Call] {tc.tool_name}({tc.arguments})")

        def on_tool_result(tr: MCPToolResult):
            if verbose:
                status = "✓" if tr.success else "✗"
                print(f"[Tool Result] {status} {tr.tool_name}")

        response = self.chat_with_tools(
            messages=messages,
            tools=tools,
            model=model,
            max_iterations=max_iterations,
            on_tool_call=on_tool_call if verbose else None,
            on_tool_result=on_tool_result if verbose else None,
        )

        return response.get("choices", [{}])[0].get("message", {}).get("content", "")
