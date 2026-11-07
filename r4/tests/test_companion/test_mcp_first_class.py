"""Unit pins for first-class MCP registration (companion W1).

All deterministic: fake client/mcp — no gateway, no network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from r4.agent.untrusted_scanner import TOOL_OUTPUT_END, TOOL_OUTPUT_START
from r4.companion import mcp_first_class as fc
from r4.mcp import MCPToolResult


class FakeMCP:
    def __init__(self, tools: list[Any], raise_on_list: bool = False) -> None:
        self.tools = tools
        self.raise_on_list = raise_on_list
        self.list_calls: list[bool] = []
        self.invocations: list[tuple[str, dict[str, Any]]] = []

    def list_tools(
        self, server: Any = None, refresh: bool = False, _turn_cancel: Any = None
    ) -> list[Any]:
        self.list_calls.append(refresh)
        if self.raise_on_list:
            raise RuntimeError("gateway down")
        return self.tools

    def invoke(self, tool_name: str, **kwargs: Any) -> MCPToolResult:
        # The real ``MCPClient.invoke`` return type (r4/mcp.py), not a stand-in.
        self.invocations.append((tool_name, kwargs))
        return MCPToolResult(tool_name=tool_name, result="ok")


def _client(tools: list[Any], raise_on_list: bool = False) -> SimpleNamespace:
    return SimpleNamespace(mcp=FakeMCP(tools, raise_on_list))


def setup_function() -> None:
    fc.invalidate_cache()


TWO_TOOLS = [
    SimpleNamespace(
        name="search_database",
        description="Search the DB",
        server="labdata",
        parameters={
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["q"],
        },
    ),
    SimpleNamespace(
        name="fetch_schema",
        description="",
        server="labdata",
        parameters={"type": "object", "properties": {}},
    ),
]


def test_registers_each_tool_with_dotted_name_and_schema() -> None:
    client = _client(TWO_TOOLS)
    built = fc.build_first_class_mcp_tools(client)
    names = [t["name"] for t in built]
    assert "mcp.labdata.search_database" in names
    assert "mcp.labdata.fetch_schema" in names
    first = next(t for t in built if t["name"] == "mcp.labdata.search_database")
    assert first["parameters"]["required"] == ["q"]
    assert first["parameters"]["properties"]["q"]["type"] == "string"
    assert first["description"].startswith("[first-class MCP]")


def test_handler_invokes_client_mcp_with_original_name() -> None:
    client = _client(TWO_TOOLS)
    built = fc.build_first_class_mcp_tools(client)
    first = next(t for t in built if t["name"] == "mcp.labdata.search_database")
    out = first["execute"](q="hello", limit=3)
    assert "ok" in out
    assert client.mcp.invocations == [("search_database", {"q": "hello", "limit": 3})]


def test_handler_prevalidation_missing_required_never_invokes_upstream() -> None:
    client = _client(TWO_TOOLS)
    built = fc.build_first_class_mcp_tools(client)
    first = next(t for t in built if t["name"] == "mcp.labdata.search_database")
    out = first["execute"](limit=3)
    assert "missing required field(s): q" in out
    assert client.mcp.invocations == []  # never reached upstream


def test_handler_prevalidation_wrong_primitive_type() -> None:
    client = _client(TWO_TOOLS)
    built = fc.build_first_class_mcp_tools(client)
    first = next(t for t in built if t["name"] == "mcp.labdata.search_database")
    out = first["execute"](q=42)
    assert "must be string" in out and client.mcp.invocations == []


def test_registry_caches_within_ttl_and_rebuilds_after_invalidate() -> None:
    client = _client(TWO_TOOLS)
    first = fc.build_first_class_mcp_tools(client)
    second = fc.build_first_class_mcp_tools(client)
    assert len(client.mcp.list_calls) == 2  # descriptors re-listed
    assert len(second) == len(first)
    fc.invalidate_cache()
    third = fc.build_first_class_mcp_tools(client)
    assert len(third) == len(first)


def test_gateway_down_yields_only_refresh_tool() -> None:
    client = _client([], raise_on_list=True)
    built = fc.build_first_class_mcp_tools(client)
    assert len(built) == 1 and built[0]["name"] == "mcp_refresh"


def test_refresh_tool_invalidates_cache_and_forces_refresh_flag() -> None:
    client = _client(TWO_TOOLS)
    built = fc.build_first_class_mcp_tools(client)
    refresh = next(t for t in built if t["name"] == "mcp_refresh")
    out = refresh["execute"]()
    assert "cache invalidated" in out.lower()
    assert client.mcp.list_calls[-1] is True  # refresh=True reached client
    rebuilt = fc.build_first_class_mcp_tools(client)
    assert any(t["name"] == "mcp.labdata.search_database" for t in rebuilt)


def test_empty_descriptor_list_still_provides_refresh_tool() -> None:
    client = _client([])
    built = fc.build_first_class_mcp_tools(client)
    assert [t["name"] for t in built] == ["mcp_refresh"]


def test_tool_without_server_defaults_to_mcp_namespace() -> None:
    client = _client(
        [
            SimpleNamespace(name="naked", description="d", server="", parameters={}),
        ]
    )
    built = fc.build_first_class_mcp_tools(client)
    assert any(t["name"] == "mcp.mcp.naked" for t in built)


def test_handler_propagates_upstream_error_as_friendly_string() -> None:
    class ErrorMCP(FakeMCP):
        def invoke(self, tool_name: str, **kwargs: Any) -> MCPToolResult:
            return MCPToolResult(tool_name=tool_name, result=None, success=False, error="bad input")

    client = SimpleNamespace(mcp=ErrorMCP(TWO_TOOLS))
    built = fc.build_first_class_mcp_tools(client)
    first = next(t for t in built if t["name"] == "mcp.labdata.search_database")
    out = first["execute"](q="x")
    assert out == "Tool error: bad input"


class StructuredMCP(FakeMCP):
    """A structured (dict) MCP result, as ``_parse_invoke_response`` builds one."""

    def invoke(self, tool_name: str, **kwargs: Any) -> MCPToolResult:
        self.invocations.append((tool_name, kwargs))
        return MCPToolResult(tool_name=tool_name, result={"rows": [{"id": 7, "name": "beryl"}]})


def _search(client: SimpleNamespace) -> Any:
    built = fc.build_first_class_mcp_tools(client)
    return next(t for t in built if t["name"] == "mcp.labdata.search_database")["execute"]


def test_handler_returns_the_real_result_scanned_and_wrapped() -> None:
    """2026-10-30: the handler read ``is_error``/``content`` (absent on
    MCPToolResult), so every first-class MCP call returned "Error invoking"."""
    out = _search(SimpleNamespace(mcp=StructuredMCP(TWO_TOOLS)))(q="beryl")
    assert not out.startswith("Error")
    assert '"name": "beryl"' in out
    assert out.startswith(TOOL_OUTPUT_START) and out.rstrip().endswith(TOOL_OUTPUT_END)


def test_legacy_mcp_invoke_tool_uses_the_real_result_shape() -> None:
    from unittest.mock import MagicMock

    from r4.companion.tools import _client_tools

    client = MagicMock()
    client.mcp = StructuredMCP(TWO_TOOLS)
    tools = {t["name"]: t for t in _client_tools(client)}
    out = tools["mcp_invoke"]["execute"](tool_name="search_database", arguments='{"q": "beryl"}')
    assert not out.startswith("Error") and '"name": "beryl"' in out
    assert client.mcp.invocations == [("search_database", {"q": "beryl"})]
