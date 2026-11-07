"""Tests for Gateway-backed and local search sources."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.mcp import MCPTool, MCPToolResult
from r4.search.core import AgenticSearch, CapabilityUnavailable, SearchConfig, SourceType
from r4.search.executor import CodeSearchTool, ToolStatus, WebSearchTool
from r4.search.mcp_sources import resolve_search_tool, search_local_code


class FakeMCP:
    def __init__(self, tools=None, result=None):
        self.tools = tools or []
        self.result = result
        self.invocations = []

    def list_tools(self):
        return self.tools

    def invoke(self, name, **arguments):
        self.invocations.append((name, arguments))
        return self.result


def client_with(mcp):
    return SimpleNamespace(mcp=mcp)


def test_resolve_search_tool_uses_exact_priority():
    lower_priority = MCPTool("brave_search", "", {})
    preferred = MCPTool("web_search", "", {})
    unrelated = MCPTool("my_web_search_helper", "", {})
    client = client_with(FakeMCP([lower_priority, unrelated, preferred]))

    assert resolve_search_tool(client, "web") is preferred


def test_resolve_search_tool_rejects_substring_match():
    client = client_with(FakeMCP([MCPTool("my_code_search_helper", "", {})]))

    assert resolve_search_tool(client, "code") is None


@pytest.mark.asyncio
async def test_search_web_maps_wrapped_mcp_results():
    tool = MCPTool(
        "web_search",
        "",
        {"properties": {"query": {}, "limit": {}}},
    )
    mcp = FakeMCP(
        [tool],
        MCPToolResult(
            "web_search",
            {"results": [{"title": "R4", "link": "https://r4.example", "text": "Result"}]},
        ),
    )
    search = AgenticSearch(client_with(mcp), SearchConfig(web_max_results=3))

    sources = await search.search_web("research")

    assert isinstance(sources, list)
    assert sources[0].type == SourceType.WEB_PAGE
    assert sources[0].url == "https://r4.example"
    assert mcp.invocations == [("web_search", {"query": "research", "limit": 3})]


@pytest.mark.asyncio
async def test_search_web_reports_unavailable_capability():
    search = AgenticSearch(client_with(FakeMCP()), SearchConfig())

    result = await search.search_web("research")

    assert result == CapabilityUnavailable(
        "web",
        "web search unavailable — no MCP search tool registered",
    )


@pytest.mark.asyncio
async def test_search_code_maps_json_text_and_forwards_paths():
    tool = MCPTool(
        "grep_repo",
        "",
        {"properties": {"query": {}, "max_results": {}, "paths": {}}},
    )
    mcp = FakeMCP(
        [tool],
        MCPToolResult(
            "grep_repo",
            '[{"path": "src/app.py", "snippet": "def run():", "line": 4}]',
        ),
    )
    search = AgenticSearch(client_with(mcp), SearchConfig(code_max_files=2))

    sources = await search.search_code("run", ["src"])

    assert isinstance(sources, list)
    assert sources[0].title == "src/app.py"
    assert sources[0].metadata["line"] == 4
    assert mcp.invocations[0][1]["paths"] == ["src"]


@pytest.mark.asyncio
async def test_mcp_failure_does_not_fall_back_locally(tmp_path):
    path = tmp_path / "match.py"
    path.write_text("needle\n")
    tool = MCPTool("code_search", "", {"properties": {"query": {}}})
    mcp = FakeMCP(
        [tool],
        MCPToolResult("code_search", None, success=False, error="gateway unavailable"),
    )
    search = AgenticSearch(client_with(mcp), SearchConfig())

    with pytest.raises(RuntimeError, match="gateway unavailable"):
        await search.search_code("needle", [str(tmp_path)])


@pytest.mark.asyncio
async def test_malformed_successful_payload_fails():
    tool = MCPTool("web_search", "", {"properties": {"query": {}}})
    search = AgenticSearch(
        client_with(FakeMCP([tool], MCPToolResult("web_search", ["not-an-object"]))),
        SearchConfig(),
    )

    with pytest.raises(ValueError, match="no valid result objects"):
        await search.search_web("query")


def test_local_code_fallback_is_bounded_and_records_lines(tmp_path):
    source = tmp_path / "module.py"
    source.write_text("first\nneedle = True\nthird\nneedle = False\n")
    (tmp_path / "binary.bin").write_bytes(b"needle\x00")

    sources = search_local_code("needle", [str(tmp_path)], max_files=5)

    assert len(sources) == 1
    assert sources[0].metadata["line_numbers"] == [2, 4]
    assert sources[0].metadata["search_backend"] == "local_regex"
    assert sources[0].relevance_score == 1.0


def test_local_code_fallback_rejects_invalid_inputs(tmp_path):
    with pytest.raises(ValueError, match="invalid code-search regex"):
        search_local_code("[", [str(tmp_path)], max_files=5)
    with pytest.raises(ValueError, match="does not exist"):
        search_local_code("x", [str(tmp_path / "missing")], max_files=5)


@pytest.mark.asyncio
async def test_executor_distinguishes_unavailable_and_failed():
    unavailable = await WebSearchTool(client_with(FakeMCP())).execute("query")
    failed = await CodeSearchTool(
        client_with(FakeMCP([MCPTool("code_search", "", {})], result=None))
    ).execute("query")

    assert unavailable.status == ToolStatus.SKIPPED
    assert "unavailable" in unavailable.message
    assert failed.status == ToolStatus.FAILED
