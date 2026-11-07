"""Milestone 5: research session aggregate view behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.runtime.session_view import research_session_view


def _client_with_graph(nodes=3, edges=2):
    client = SimpleNamespace(config=SimpleNamespace(project_id="proj-1"))
    client.knowledge = MagicMock()
    client.knowledge.get_graph.return_value = {
        "nodes": [{"id": f"n{i}"} for i in range(nodes)],
        "edges": [{"from": "a", "to": "b"} for _ in range(edges)],
    }
    client.start_loop = MagicMock(
        return_value={"loop_id": "loop-9", "status": "running", "message": "ok"}
    )
    client.list_loops = MagicMock(
        return_value={"loops": [{"loop_id": "loop-9", "status": "running", "phase": "observe"}]}
    )
    return client


def test_session_view_groups_runtime_and_knowledge():
    view = research_session_view(_client_with_graph(), "proj-1", mode="umrs")

    assert view["type"] == "research_session_view"
    assert view["authoritative_runtime"] == "umrs"
    assert view["runtime"] == "umrs"
    assert view["knowledge"]["available"] is True
    assert view["knowledge"]["nodes"] == 3
    assert "authoritative" in view["authority_note"]


def test_session_view_loop_items_grouped_by_venue():
    view = research_session_view(_client_with_graph(), "proj-1", mode="umrs")

    loops = view["loops"]
    assert loops["count"] == 1
    assert loops["active_count"] == 1
    item = loops["items"][0]
    assert item["loop_id"] == "loop-9"
    assert item["venue"] == "umrs"
    assert item["status"] == "running"


def test_session_view_knowledge_offline_is_graceful():
    client = _client_with_graph()
    client.knowledge.get_graph.side_effect = RuntimeError("gateway down")

    view = research_session_view(client, "proj-1", mode="umrs")

    assert view["knowledge"]["available"] is False
    assert "gateway down" in view["knowledge"]["reason"]
    assert view["loops"]["count"] == 1  # loops still listed


def test_session_view_without_knowledge_client():
    client = SimpleNamespace(config=SimpleNamespace(project_id="p"))
    client.start_loop = MagicMock(return_value={"loop_id": "loop-1", "status": "running"})
    client.list_loops = MagicMock(return_value={"loops": []})

    view = research_session_view(client, "p", mode="umrs")

    assert view["knowledge"]["available"] is False
    assert view["knowledge"]["reason"] == "no knowledge client"


def test_session_view_hybrid_venue_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("R4_RUNTIME_MODE", "local")
    monkeypatch.setenv("R4_RUNTIME_DB", str(tmp_path / "sess.db"))
    client = _client_with_graph()

    view = research_session_view(client, "proj-1")

    assert view["runtime"] == "local"
    assert view["durable"] is True


def test_view_companion_tool_registered_and_safe():
    from r4.companion.permissions import PermissionManager, PermissionMode
    from r4.companion.research_loop_tools import build_research_loop_tools

    client = _client_with_graph()
    client.mcp = MagicMock(list_servers=MagicMock(return_value=[]))
    tools = {t["name"]: t for t in build_research_loop_tools(client)}
    assert "research_session_view" in tools

    pm = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
    assert pm.is_safe("research_session_view") is True


def test_acp_research_session_route():

    from r4.acp import _research_session_view

    client = _client_with_graph()
    runtime = SimpleNamespace(client=lambda: client)
    out = _research_session_view(runtime, {})

    assert out["type"] == "research_session_view"
    assert out["project_id"] == "proj-1"
    assert out["loops"]["count"] == 1
