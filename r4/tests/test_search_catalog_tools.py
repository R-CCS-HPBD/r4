"""Tests for lab-data catalog search tools and planner heuristics."""

from __future__ import annotations

import pytest


class FakeClient:
    """Minimal client stub for catalog tool tests."""

    def __init__(self, responses: dict[tuple[str, tuple], object] | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str, *, params: dict | None = None):
        self.calls.append((path, params or {}))
        key = (path, tuple(sorted((params or {}).items())))
        if key not in self._responses:
            raise KeyError(f"no stub for {path} {params}")
        return self._responses[key]


@pytest.mark.asyncio
async def test_lab_catalog_search_tool_maps_gateway_results() -> None:
    from r4.search.core import SourceType
    from r4.search.executor import CatalogSearchTool, ToolStatus

    client = FakeClient(
        {
            (
                "/gw/catalog/search",
                (("limit", 20), ("q", "batch-3")),
            ): {
                "results": [
                    {
                        "s3_key": "proj-1/uploads/batch-3.csv",
                        "state": "ready",
                        "summary": "CSV columns: yield, batch_id",
                        "deterministic_summary": "columns: yield, batch_id",
                        "perception_summary": None,
                        "perception_model": None,
                        "detected_modality": "structured",
                        "tags": ["yield", "batch_id"],
                        "relevance_score": 2.0,
                    }
                ]
            }
        }
    )
    tool = CatalogSearchTool(client, project_id="proj-1")

    result = await tool.execute("batch-3")

    assert result.status == ToolStatus.SUCCESS
    assert len(result.sources) == 1
    source = result.sources[0]
    assert source.type == SourceType.CATALOG_ENTRY
    assert source.title == "batch-3.csv"
    assert source.metadata["s3_key"] == "proj-1/uploads/batch-3.csv"
    assert source.metadata["tags"] == ["yield", "batch_id"]
    assert source.metadata["suggested_read"] == "content"
    assert client.calls == [
        ("/gw/catalog/search", {"q": "batch-3", "limit": 20}),
    ]


@pytest.mark.asyncio
async def test_lab_catalog_search_tool_returns_partial_when_empty() -> None:
    from r4.search.executor import CatalogSearchTool, ToolStatus

    client = FakeClient(
        {
            (
                "/gw/catalog/search",
                (("limit", 20), ("q", "missing")),
            ): {"results": []},
        }
    )
    tool = CatalogSearchTool(client, project_id="proj-1")

    result = await tool.execute("missing")

    assert result.status == ToolStatus.PARTIAL
    assert result.sources == []
    assert "Do not retry" in (result.message or "")
    assert "knowledge_show" in (result.message or "")


@pytest.mark.asyncio
async def test_lab_file_read_tool_uses_s3_key_parameter() -> None:
    from r4.search.core import SourceType
    from r4.search.executor import LabFileReadTool, ToolStatus

    s3_key = "proj-1/uploads/batch-3.csv"
    client = FakeClient(
        {
            (
                "/gw/catalog/read",
                (
                    ("length", 1_048_576),
                    ("offset", 0),
                    ("s3_key", s3_key),
                    ("source", "auto"),
                ),
            ): {
                "text": "yield,batch_id\n0.9,3\n",
                "truncated": False,
                "total_size": 22,
                "length": 22,
                "read_source": "raw",
            },
        }
    )
    tool = LabFileReadTool(client, project_id="proj-1")

    result = await tool.execute("", s3_key=s3_key)

    assert result.status == ToolStatus.SUCCESS
    assert result.sources[0].type == SourceType.RAW_FILE
    assert "yield,batch_id" in result.sources[0].content
    assert result.sources[0].metadata["representation"] == "raw_bytes"


@pytest.mark.asyncio
async def test_lab_file_read_tool_fails_without_s3_key() -> None:
    from r4.search.executor import LabFileReadTool, ToolStatus

    tool = LabFileReadTool(FakeClient(), project_id="proj-1")

    result = await tool.execute("   ")

    assert result.status == ToolStatus.FAILED
    assert result.error == "s3_key required"


@pytest.mark.asyncio
async def test_tool_executor_wires_catalog_tools_for_default_all_scope() -> None:
    from r4.search.core import SearchConfig, SearchScope
    from r4.search.executor import ToolExecutor

    config = SearchConfig(scope=SearchScope.ALL, project_id="proj-1")
    config.apply_scope()
    executor = ToolExecutor(FakeClient(), config)

    assert "lab_catalog_search" in executor._tools
    assert "lab_file_read" in executor._tools
    assert "web_search" in executor._tools


@pytest.mark.asyncio
async def test_tool_executor_wires_catalog_tools_for_lab_only() -> None:
    from r4.search.core import SearchConfig, SearchScope
    from r4.search.executor import ToolExecutor

    config = SearchConfig(scope=SearchScope.LAB_ONLY, project_id="proj-1")
    config.apply_scope()
    executor = ToolExecutor(FakeClient(), config)

    assert "lab_catalog_search" in executor._tools
    assert "lab_file_read" in executor._tools
    assert "web_search" not in executor._tools


def test_quick_plan_includes_catalog_but_not_raw_read() -> None:
    """Heuristic (shallow) planning discovers files but does not auto-read them."""
    from r4.search.core import SearchConfig, SearchDepth, SearchScope
    from r4.search.planner import SearchPlanner, StepType

    config = SearchConfig(
        scope=SearchScope.LAB_ONLY,
        depth=SearchDepth.SHALLOW,
        project_id="proj-1",
    )
    config.apply_scope()
    planner = SearchPlanner(FakeClient(), config)

    steps = planner._quick_plan("What columns are in batch-3 CSV?")
    step_types = [step.step_type for step in steps]

    assert StepType.LAB_CATALOG_SEARCH in step_types
    assert StepType.RAG_QUERY in step_types
    assert StepType.LAB_FILE_READ not in step_types
    assert StepType.WEB_SEARCH not in step_types
