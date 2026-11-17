"""KnowledgeClient ontology methods (ADR-116 D-5): method, path, body, headers."""

from __future__ import annotations

from typing import Any

import pytest

from r4.knowledge import KnowledgeClient


class _FakeInnerClient:
    """Records calls; returns canned payloads (no HTTP)."""

    _api_prefix = "/gw"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.next: Any = {}

    def _record(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.next

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._record("GET", url, **kwargs)

    def post(self, url: str, json: Any = None, **kwargs: Any) -> Any:
        return self._record("POST", url, json=json, **kwargs)

    def patch(self, url: str, json: Any = None, **kwargs: Any) -> Any:
        return self._record("PATCH", url, json=json, **kwargs)


@pytest.fixture()
def pair() -> tuple[KnowledgeClient, _FakeInnerClient]:
    inner = _FakeInnerClient()
    return KnowledgeClient(inner), inner  # type: ignore[arg-type]


def test_register_posts_source_format_scope(pair):
    knowledge, inner = pair
    inner.next = {"ontology": {"ontology_id": "lab"}, "record": {}}
    out = knowledge.ontology_register("name: lab\n", fmt="linkml", scope="global")
    assert out["ontology"]["ontology_id"] == "lab"
    call = inner.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "/gw/knowledge/ontologies"
    assert call["json"] == {"source": "name: lab\n", "format": "linkml", "scope": "global"}


def test_register_defaults_to_linkml_project(pair):
    knowledge, inner = pair
    knowledge.ontology_register("x")
    assert inner.calls[0]["json"]["format"] == "linkml"
    assert inner.calls[0]["json"]["scope"] == "project"


def test_register_sends_project_header_when_named(pair):
    knowledge, inner = pair
    knowledge.ontology_register("x", project_id="proj-a")
    assert inner.calls[0]["headers"] == {"X-Project-ID": "proj-a"}


def test_register_marks_agent_actor(pair):
    """The server refuses agents; an agent's call must say it is one."""
    knowledge, inner = pair
    with knowledge.agent_scope():
        knowledge.ontology_register("x")
    assert inner.calls[0]["headers"]["x-r4-actor"] == "agent"


@pytest.mark.parametrize("kwargs", [{"fmt": "owl"}, {"scope": "site"}])
def test_register_refuses_unknown_format_or_scope(pair, kwargs):
    knowledge, inner = pair
    with pytest.raises(ValueError):
        knowledge.ontology_register("x", **kwargs)
    assert inner.calls == []


def test_list_passes_include_retired_and_unwraps(pair):
    knowledge, inner = pair
    inner.next = {"ontologies": [{"ontology_id": "r4-science", "version": "1"}]}
    rows = knowledge.ontology_list(include_retired=True)
    assert rows == [{"ontology_id": "r4-science", "version": "1"}]
    call = inner.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "/gw/knowledge/ontologies"
    assert call["params"] == {"include_retired": "true"}
    assert call["headers"] is None


def test_list_default_excludes_retired(pair):
    knowledge, inner = pair
    inner.next = {}
    assert knowledge.ontology_list() == []
    assert inner.calls[0]["params"] == {"include_retired": "false"}


def test_show_path(pair):
    knowledge, inner = pair
    inner.next = {"ontology": {}, "record": None}
    knowledge.ontology_show("lab", "1.2.0")
    call = inner.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "/gw/knowledge/ontologies/lab/1.2.0"


def test_show_quotes_path_segments(pair):
    knowledge, inner = pair
    knowledge.ontology_show("a/b", "1 x")
    assert inner.calls[0]["url"] == "/gw/knowledge/ontologies/a%2Fb/1%20x"


def test_retire_posts_to_retire_route(pair):
    knowledge, inner = pair
    inner.next = {"retired": True}
    assert knowledge.ontology_retire("lab", "1") == {"retired": True}
    call = inner.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "/gw/knowledge/ontologies/lab/1/retire"


def test_graph_bind_patches_graph_with_both_fields(pair):
    knowledge, inner = pair
    inner.next = {"graph_id": "g-1", "ontology_id": "lab", "ontology_version": "2"}
    knowledge.graph_bind("g-1", "lab", "2")
    call = inner.calls[0]
    assert call["method"] == "PATCH"
    assert call["url"] == "/gw/knowledge/graphs/g-1"
    assert call["json"] == {"ontology_id": "lab", "ontology_version": "2"}


def test_graph_bind_without_version_sends_null(pair):
    """The server pins the newest live version when the version is empty."""
    knowledge, inner = pair
    knowledge.graph_bind("g-1", "lab", None)
    assert inner.calls[0]["json"] == {"ontology_id": "lab", "ontology_version": None}


def test_direct_umrs_prefix(pair):
    knowledge, inner = pair
    inner._api_prefix = "/umrs"
    knowledge.ontology_list()
    assert inner.calls[0]["url"] == "/umrs/knowledge/ontologies"
