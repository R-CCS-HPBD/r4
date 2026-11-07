"""Tests for UMRS platform skills and tools.

Validates that the UMRS-facing skills (platform_ops, model_management,
data_management, vision, workflow_ops, mcp_tools) are registered and
expose the correct tool names and keywords.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.companion.skills import SkillRegistry


def _serving_client():
    """A client whose platform serves the decision model (full tool inventory:
    the decision tool registers only where the model is served)."""
    from unittest.mock import MagicMock

    from r4.decisions.settings import DEFAULT_DECISION_MODEL

    client = MagicMock(**{"list_gateway_models.return_value": [DEFAULT_DECISION_MODEL]})
    client.config.decision_model = DEFAULT_DECISION_MODEL
    return client


@pytest.fixture
def registry():
    return SkillRegistry()


# -------------------------------------------------------------------
# Skill registration
# -------------------------------------------------------------------

_UMRS_SKILL_NAMES = [
    "platform_ops",
    "model_management",
    "data_management",
    "vision",
    "workflow_ops",
    "mcp_tools",
]


@pytest.mark.parametrize("skill_name", _UMRS_SKILL_NAMES)
def test_umrs_skill_registered(registry, skill_name):
    skill = registry.get(skill_name)
    assert skill is not None, f"Skill '{skill_name}' not found in registry"
    assert skill.description
    assert skill.system_prompt
    assert skill.tool_names


# -------------------------------------------------------------------
# Keyword auto-activation
# -------------------------------------------------------------------


def test_platform_ops_activates_on_endpoint_keyword(registry):
    activated = registry.auto_activate("provision an endpoint for qwen")
    assert "platform_ops" in activated


def test_platform_ops_activates_on_job_keyword(registry):
    activated = registry.auto_activate("check the durable job status")
    assert "platform_ops" in activated


def test_model_management_activates_on_model_keyword(registry):
    activated = registry.auto_activate("list models in the registry")
    assert "model_management" in activated


def test_model_management_activates_on_huggingface_keyword(registry):
    activated = registry.auto_activate("stage a huggingface model for inference")
    assert "model_management" in activated


def test_data_management_activates_on_upload_keyword(registry):
    activated = registry.auto_activate("upload a file to storage")
    assert "data_management" in activated


def test_data_management_activates_on_rag_keyword(registry):
    activated = registry.auto_activate("index this document in rag")
    assert "data_management" in activated


def test_vision_activates_on_image_keyword(registry):
    activated = registry.auto_activate("classify image with segmentation model")
    assert "vision" in activated


def test_workflow_ops_activates_on_trigger_keyword(registry):
    activated = registry.auto_activate("trigger the pipeline execution")
    assert "workflow_ops" in activated


def test_workflow_ops_activates_dependencies(registry):
    activated = registry.auto_activate("trigger the pipeline execution")
    assert "platform_ops" in activated


def test_mcp_tools_activates_on_mcp_keyword(registry):
    activated = registry.auto_activate("list mcp servers")
    assert "mcp_tools" in activated


# -------------------------------------------------------------------
# Tool name coverage
# -------------------------------------------------------------------


def test_platform_ops_tool_names(registry):
    skill = registry.get("platform_ops")
    expected = {
        "endpoint_list",
        "endpoint_status",
        "endpoint_provision",
        "endpoint_stop",
        "job_list",
        "job_status",
        "job_cancel",
        "job_retry",
        "platform_health",
    }
    assert expected == set(skill.tool_names)


def test_model_management_tool_names(registry):
    skill = registry.get("model_management")
    expected = {
        "list_models",
        "model_list_registry",
        "model_details",
        "model_stage",
    }
    assert expected == set(skill.tool_names)


def test_data_management_tool_names(registry):
    skill = registry.get("data_management")
    expected = {
        "file_upload_platform",
        "file_list_platform",
        "file_download_platform",
        "lab_catalog_search",
        "lab_file_read",
        "rag_query",
        "rag_upload",
        "rag_list_documents",
        "search_docs",
        "open_passage",  # K1.4: reads a search_docs hit in context
        "search_agentic",
        # ADR-105 skill audit (2026-11-01): an agent rag_upload needs an
        # approved proposal, so the skill must reach propose/decide.
        "knowledge_graph_list",
        "knowledge_graph_propose",
        "knowledge_graph_decide",
    }
    assert expected == set(skill.tool_names)


def test_hpc_skill_includes_search_agentic(registry):
    skill = registry.get("hpc")
    assert "search_agentic" in skill.tool_names


def test_workflow_ops_includes_search_agentic(registry):
    skill = registry.get("workflow_ops")
    assert "search_agentic" in skill.tool_names


def test_vision_tool_names(registry):
    skill = registry.get("vision")
    expected = {
        "vision_submit",
        "vision_status",
        "endpoint_list",
        # ADR-105 skill audit (2026-11-01): the body polls endpoint_status
        # and picks a vision model with model_list_registry(model_type=vision).
        "endpoint_status",
        "endpoint_provision",
        "model_list_registry",
    }
    assert expected == set(skill.tool_names)


def test_every_builtin_tool_resolves(registry):
    """No builtin skill may reference a tool that does not exist."""

    from r4.companion.tools import get_builtin_tools

    real = {t["name"] for t in get_builtin_tools(client=_serving_client())}
    orphans = sorted(
        t
        for s in registry.list_all()
        if s.source == "builtin"
        for t in s.tool_names
        if t not in real
    )
    assert not orphans


def test_workflow_ops_tool_names(registry):
    skill = registry.get("workflow_ops")
    expected = {
        "workflow_list",
        "workflow_describe",
        "workflow_upload",
        # ADR-105 S3: plan, then commit.
        "workflow_plan",
        "job_commit",
        "workflow_run",
        "workflow_trigger",
        "workflow_status",
        "workflow_wait",
        # ADR-105 S2: wait on several runs' pushed end events.
        "job_wait_any",
        "workflow_results",
        "workflow_cancel",
        "workflow_approve_gate",
        "workflow_save_definition",
        "workflow_replay",
        "lab_catalog_search",
        "search_agentic",
    }
    assert expected == set(skill.tool_names)


def test_mcp_tools_tool_names(registry):
    skill = registry.get("mcp_tools")
    expected = {
        "mcp_list_servers",
        "mcp_list_tools",
        "mcp_invoke",
        "mcp_register_server",
        "mcp_unregister_server",
        "mcp_refresh",
    }
    assert expected == set(skill.tool_names)


# -------------------------------------------------------------------
# Skill system prompt injection
# -------------------------------------------------------------------


def test_active_system_prompt_includes_umrs_guidance(registry):
    registry.activate("platform_ops")
    prompt = registry.active_system_prompt()
    assert "UMRS" in prompt or "endpoint" in prompt


def test_multiple_umrs_skills_combine_prompts(registry):
    registry.activate("platform_ops")
    registry.activate("model_management")
    prompt = registry.active_system_prompt()
    assert "platform_ops" in prompt
    assert "model_management" in prompt


def test_umrs_skill_states_the_real_routing_and_pool_semantics(registry):
    """ADR-105 skill audit (2026-11-01): source_use only sets the NEXT
    session's source (site= routes a call now), and endpoint_provision never
    splits model@source; pool_up/pool_down take a roster file and
    endpoint_upgrade takes no image."""
    registry.activate("umrs")
    registry.activate("platform_ops")
    prompt = registry.active_system_prompt()
    assert "use source_* tools" not in prompt
    assert "source_use only changes the stored source for the NEXT session" in prompt
    assert "scale worker" not in prompt
    assert "YAML roster file" in prompt
    assert "It cannot change the container image." in prompt
    assert "name or model@source" not in prompt


# -------------------------------------------------------------------
# Tool integration (tools.py get_builtin_tools)
# -------------------------------------------------------------------


def _stub_client():
    """Minimal stub that satisfies get_builtin_tools(client) tool builders."""
    client = MagicMock()
    client.config = SimpleNamespace(
        gateway_url="http://gateway.test",
        api_key="test",
        model="test-model",
        project_id="demo",
    )
    return client


_EXPECTED_CLIENT_TOOLS = frozenset(
    {
        "endpoint_list",
        "endpoint_provision",
        "endpoint_stop",
        "job_list",
        "job_retry",
        "model_list_registry",
        "model_details",
        "model_stage",
        "file_upload_platform",
        "file_list_platform",
        "file_download_platform",
        "lab_catalog_search",
        "lab_file_read",
        "rag_query",
        "rag_upload",
        "rag_list_documents",
        "search_agentic",
        "vision_submit",
        "vision_status",
        "workflow_upload",
    }
)


def test_get_builtin_tools_includes_umrs_tools():
    from r4.companion.tools import get_builtin_tools

    client = _stub_client()
    tools = get_builtin_tools(client)
    tool_names = {t["name"] for t in tools}

    assert _EXPECTED_CLIENT_TOOLS <= tool_names


def test_get_builtin_tools_without_client_excludes_platform_tools():
    from r4.companion.tools import get_builtin_tools

    tools = get_builtin_tools(client=None)
    tool_names = {t["name"] for t in tools}

    # Client-dependent tools should NOT be present
    assert "endpoint_list" not in tool_names
    assert "model_list_registry" not in tool_names
    assert "rag_query" not in tool_names
    assert "vision_submit" not in tool_names

    # Client-independent tools should still be present
    assert "shell_exec" in tool_names
    assert "file_read" in tool_names


# -------------------------------------------------------------------
# Tool execution (with mocked client)
# -------------------------------------------------------------------


def test_model_list_registry_tool_executes():
    from r4.companion.tools import get_builtin_tools

    client = _stub_client()
    tools = get_builtin_tools(client)
    tool = next(t for t in tools if t["name"] == "model_list_registry")

    result = tool["execute"]()
    # Should not crash; result depends on client mock behavior
    assert isinstance(result, str)


def test_file_list_platform_tool_executes():
    from r4.companion.tools import get_builtin_tools

    client = _stub_client()
    tools = get_builtin_tools(client)
    tool = next(t for t in tools if t["name"] == "file_list_platform")

    result = tool["execute"]()
    assert isinstance(result, str)


def test_rag_query_tool_executes():
    from r4.companion.tools import get_builtin_tools

    client = _stub_client()
    client.rag.query.return_value = SimpleNamespace(
        answer="Test answer",
        sources=[],
    )
    tools = get_builtin_tools(client)
    tool = next(t for t in tools if t["name"] == "rag_query")

    result = tool["execute"](query="test question")
    assert "Test answer" in result


def test_job_retry_tool_executes():
    from r4.companion.tools import get_builtin_tools

    client = _stub_client()
    client.jobs.retry.return_value = SimpleNamespace(
        job_id="job-123",
        status="queued",
    )
    tools = get_builtin_tools(client)
    tool = next(t for t in tools if t["name"] == "job_retry")

    result = tool["execute"](job_id="job-123")
    assert "job-123" in result
    assert "retried" in result


def test_vision_submit_tool_executes():
    from r4.companion.tools import get_builtin_tools

    client = _stub_client()
    client.vision.submit.return_value = {
        "endpoint_id": "ep-1",
        "adapter": "default",
        "task": {},
    }
    tools = get_builtin_tools(client)
    tool = next(t for t in tools if t["name"] == "vision_submit")

    result = tool["execute"](image="/path/to/image.png")
    assert "ep-1" in result


def test_search_agentic_returns_catalog_metadata():
    import json
    from unittest.mock import AsyncMock, patch

    from r4.companion.tools import get_builtin_tools
    from r4.search.core import SearchResult, Source, SourceType

    client = _stub_client()
    catalog_source = Source(
        type=SourceType.CATALOG_ENTRY,
        title="scan.png",
        content="Chest CT summary.",
        metadata={
            "s3_key": "proj-1/uploads/scan.png",
            "tags": ["vision_captioned"],
            "perception_summary": "Chest CT with infiltrates.",
            "deterministic_summary": "Image file scan.png",
        },
        relevance_score=2.0,
    )
    search_result = SearchResult(
        query="chest scans",
        answer="Found one chest scan.",
        sources=[catalog_source],
        confidence=0.9,
        warnings=[],
        citations=[],
    )

    with patch(
        "r4.search.AgenticSearch.search", new_callable=AsyncMock, return_value=search_result
    ):
        tools = get_builtin_tools(client)
        tool = next(t for t in tools if t["name"] == "search_agentic")
        raw = tool["execute"](query="chest scans", scope="lab_only")
        payload = json.loads(raw)

    assert payload["answer"] == "Found one chest scan."
    assert payload["sources"][0]["s3_key"] == "proj-1/uploads/scan.png"
    assert payload["sources"][0]["perception_summary"] == "Chest CT with infiltrates."
    assert payload["sources"][0]["tags"] == ["vision_captioned"]


def test_list_models_tool_fixed():
    """Verify list_models calls client.list_models(), not client.mcp.list_tools()."""
    from r4.companion.tools import get_builtin_tools

    client = _stub_client()
    client.list_models.return_value = ["model-a", "model-b"]
    tools = get_builtin_tools(client)
    tool = next(t for t in tools if t["name"] == "list_models")

    result = tool["execute"]()
    client.list_models.assert_called_once()
    assert "model-a" in result
    assert "model-b" in result
