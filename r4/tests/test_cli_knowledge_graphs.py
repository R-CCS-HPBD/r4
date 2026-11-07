"""CLI handlers for the knowledge graph registry commands (0069)."""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from r4._cli.knowledge import KnowledgeCommand


class _FakeKnowledge:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def graphs(self, **kwargs):
        self.calls.append({"graphs": kwargs})
        return [
            {
                "graph_id": "g-a",
                "name": "project-a",
                "kind": "project",
                "status": "active",
                "project_id": "project-a",
                "description": "",
            },
        ]

    def graph_info(self, graph_id):
        self.calls.append({"graph_info": graph_id})
        return {"graph_id": graph_id, "name": "project-a"}

    def graph_create(self, name, **kwargs):
        self.calls.append({"graph_create": {"name": name, **kwargs}})
        return {"graph_id": "g-new", "name": name, "kind": kwargs.get("kind")}

    def graph_archive(self, graph_id):
        self.calls.append({"graph_archive": graph_id})
        return {"graph_id": graph_id, "name": "project-a"}

    def query(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        return {"query": {"results": [], "selected_graphs": ["g-a"], "excluded_graphs": []}}


class _FakeClient:
    knowledge = _FakeKnowledge()


@pytest.fixture(autouse=True)
def _client(monkeypatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr("r4.client.Client", lambda: fake)
    return fake


def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        "knowledge_command": "graphs",
        "project_id": None,
        "all": False,
        "query": None,
        "as_json": False,
        "graph_id": "g-a",
        "name": "graph-x",
        "description": "",
        "kind": "research",
        "graphs": None,
        "exclude_graphs": None,
        "scope": "all",
        "limit": 20,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestGraphRegistryCommands:
    def test_graphs_lists_registered_graphs(self, capsys):
        rc = KnowledgeCommand().handle(_args(project_id="project-a"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "g-a" in out and "project-a" in out

    def test_graph_show(self, capsys):
        rc = KnowledgeCommand().handle(_args(knowledge_command="graph", graph_id="g-a"))
        assert rc == 0
        assert '"g-a"' in capsys.readouterr().out

    def test_graph_create(self, capsys):
        rc = KnowledgeCommand().handle(
            _args(
                knowledge_command="graph-create",
                name="experiment-history",
                description="Experiments",
                kind="experiment",
            )
        )
        assert rc == 0
        assert "g-new" in capsys.readouterr().out

    def test_graph_archive(self, capsys):
        rc = KnowledgeCommand().handle(_args(knowledge_command="graph-archive", graph_id="g-a"))
        assert rc == 0
        assert "archived" in capsys.readouterr().out

    def test_query_forwards_graph_constraints(self, capsys):
        # Facade unified search moved from 'query' to 'search' on 2026-09-13
        # (WP-15 fold owns 'query' for the corpus rag flow).
        handler = KnowledgeCommand()
        rc = handler.handle(
            _args(
                knowledge_command="search",
                query="FWI?",
                project_id="project-a",
                graphs="project-a,experiment-history",
                exclude_graphs="institutional",
            )
        )
        assert rc == 0
        call = _FakeClient.knowledge.calls[-1]
        assert call["graphs"] == ["project-a", "experiment-history"]
        assert call["exclude_graphs"] == ["institutional"]
        out = capsys.readouterr().out
        assert "graphs: g-a" in out
