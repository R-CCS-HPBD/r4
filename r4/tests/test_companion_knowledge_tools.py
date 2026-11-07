"""Companion knowledge tools: structured outputs + registry + permissions."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.companion.knowledge_tools import build_knowledge_tools
from r4.companion.permissions import PermissionManager, PermissionMode


def _client():
    knowledge = MagicMock()
    knowledge.get_graph.return_value = {
        "nodes": [
            {"id": "goal-1", "type": "goal", "name": "Optimize CO2"},
            {"id": "paper-1", "type": "paper", "name": "Paper A", "confidence": 0.9},
            {"id": "hyp-1", "type": "hypothesis", "name": "H7"},
        ],
        "edges": [{"from": "goal-1", "to": "paper-1", "label": "supports"}],
    }
    knowledge.list_loops.return_value = [
        {"loop_id": "loop-1", "status": "running"},
        {"loop_id": "loop-2", "status": "completed"},
    ]
    knowledge.ingest.return_value = {"docs_ingested": 2}
    knowledge.ingest_paths.return_value = {
        "job_id": "job-1",
        "status": "queued",
        "jobs": [{"job_id": "job-1", "job_type": "knowledge_ingest", "status": "queued"}],
    }
    knowledge.external_search.return_value = {"hits": 3}
    return SimpleNamespace(config=SimpleNamespace(project_id="proj-1"), knowledge=knowledge)


def _tools(client=None):
    return {t["name"]: t for t in build_knowledge_tools(client or _client())}


def test_registry_shape():
    tools = _tools()
    # 0069 multi-graph: registry tools bundle the graph discovery/selection/
    # proposal/decision tools with the original three knowledge verbs.
    assert {
        "knowledge_status",
        "knowledge_ingest",
        "knowledge_show",
        "knowledge_graph_list",
        "knowledge_query",
        "knowledge_graph_propose",
        "knowledge_graph_decide",
        "knowledge_build_from_context",
    } == set(tools)


def test_status_reports_node_edge_and_loop_counts():
    payload = json.loads(_tools()["knowledge_status"]["execute"]())
    assert payload["type"] == "knowledge_status"
    assert payload["nodes_total"] == 3
    assert payload["edges_total"] == 1
    assert payload["active_loops"] == 1
    assert payload["nodes_by_type"]["hypothesis"] == 1


def test_ingest_accepts_files_and_sources():
    tools = _tools()
    payload = json.loads(
        tools["knowledge_ingest"]["execute"](
            files=["/papers/a.pdf"],
            sources="CO2 binding MOF",
        )
    )
    assert payload["type"] == "knowledge_ingested"
    # Files go through the durable ingest job (k2 family, reached via the
    # stable /knowledge/ingest/* gateway lane since 2026-08-23).
    assert payload["ingest_job"]["job_id"] == "job-1"
    assert payload["external_search"]["queries"] == ["CO2 binding MOF"]


def test_ingest_urls_queue_durable_http_job():
    client = _client()
    tools = _tools(client)
    payload = json.loads(tools["knowledge_ingest"]["execute"](urls="https://example.com/mermaids"))
    assert payload["ingest_job"]["status"] == "queued"
    args, kwargs = client.knowledge.ingest_paths.call_args
    assert args[0] == "proj-1"
    assert args[1] == []
    assert kwargs["urls"] == ["https://example.com/mermaids"]


def test_ingest_files_ride_ingest_paths_never_server_specs(tmp_path):
    """Workstation paths go to the client planner (inline/upload); the tool
    never builds local_file/local_dir specs the server would resolve on ITS
    filesystem."""
    client = _client()
    tools = _tools(client)
    payload = json.loads(tools["knowledge_ingest"]["execute"](files=[str(tmp_path), "/x/y.pdf"]))
    assert payload["ingest_job"]["job_id"] == "job-1"
    args, _kwargs = client.knowledge.ingest_paths.call_args
    assert args[1] == [str(tmp_path), "/x/y.pdf"]
    client.knowledge.ingest_sources.assert_not_called()


def test_ingest_without_input_fails_structured():
    payload = json.loads(_tools()["knowledge_ingest"]["execute"]())
    assert payload["code"] == "empty_ingest"


def test_show_filters_nodes_by_type():
    payload = json.loads(_tools()["knowledge_show"]["execute"](type_filter="hypothesis"))
    assert payload["type"] == "knowledge_show"
    assert payload["count"] == 1
    assert any("H7" in line for line in payload["nodes"])


def test_tools_register_with_builtin_tools():
    from r4.companion.tools import get_builtin_tools

    client = _client()
    client.mcp = MagicMock(list_servers=MagicMock(return_value=[]))
    names = {t["name"] for t in get_builtin_tools(client)}
    for expected in {"knowledge_status", "knowledge_ingest", "knowledge_show"}:
        assert expected in names


def test_permissions_classification():
    pm = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
    assert pm.is_safe("knowledge_status") is True
    assert pm.is_safe("knowledge_show") is True
    assert pm.is_safe("knowledge_ingest") is False
