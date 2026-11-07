"""Regression tests for #206: bundle export must not silently ship an empty graph.

Server payloads use {"nodes": [...], "edges": [...]}; the export path
previously read "entities"/"relations" and printed ✓ with zero rows.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from r4._cli.knowledge import _fetch_knowledge_graph


class _GraphClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def get_graph(self, project_id: str) -> dict[str, Any]:
        del project_id
        return self._payload


def _client(payload: dict[str, Any]) -> Any:
    return SimpleNamespace(knowledge=_GraphClient(payload))


def test_nodes_edges_payload_maps_to_entities_relations() -> None:
    payload = {
        "nodes": [{"id": "n1", "name": "MERMAID"}],
        "edges": [{"from": "n1", "to": "n2", "type": "uses"}],
    }
    entities, relations = _fetch_knowledge_graph(_client(payload), "p1")
    assert entities == payload["nodes"]
    assert relations == payload["edges"]


def test_legacy_entities_relations_still_accepted() -> None:
    payload = {"entities": [{"id": "e1"}], "relations": [{"a": "e1"}]}
    entities, relations = _fetch_knowledge_graph(_client(payload), "p1")
    assert entities == payload["entities"]
    assert relations == payload["relations"]


def test_unknown_shape_warns_and_returns_empty(capsys: Any) -> None:
    entities, relations = _fetch_knowledge_graph(_client({"surprise": 1}), "p1")
    assert entities == [] and relations == []
    assert "WARNING" in capsys.readouterr().err


def test_fetch_failure_warns_and_returns_empty(capsys: Any) -> None:
    class BoomClient:
        def get_graph(self, project_id: str) -> dict[str, Any]:
            del project_id
            raise RuntimeError("gateway down")

    client = SimpleNamespace(knowledge=BoomClient())
    entities, relations = _fetch_knowledge_graph(client, "p1")
    assert entities == [] and relations == []
    err = capsys.readouterr().err
    assert "WARNING" in err and "gateway down" in err
