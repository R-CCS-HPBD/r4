"""KnowledgeClient multi-graph surface tests (0069 registry/proposals)."""

from __future__ import annotations

from typing import Any

from r4.knowledge import KnowledgeClient


class _FakeInnerClient:
    """Records calls; returns canned payloads."""

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


def _knowledge() -> tuple[KnowledgeClient, _FakeInnerClient]:
    inner = _FakeInnerClient()
    return KnowledgeClient(inner), inner


class TestGraphRegistryMethods:
    def test_graph_list_params(self):
        knowledge, inner = _knowledge()
        inner.next = {"graphs": [{"graph_id": "g-1"}], "total": 1}
        graphs = knowledge.graphs(project_id="proj-a", query="fwi")
        assert graphs == [{"graph_id": "g-1"}]
        call = inner.calls[0]
        assert call["url"] == "/gw/knowledge/graphs"
        assert call["params"]["project_id"] == "proj-a"
        assert call["params"]["q"] == "fwi"
        assert call["params"]["status"] == "active"

    def test_graph_create_body(self):
        knowledge, inner = _knowledge()
        inner.next = {"graph_id": "g-new"}
        knowledge.graph_create(
            "experiment-history",
            description="Experiments",
            project_id="proj-a",
            kind="experiment",
        )
        call = inner.calls[0]
        assert call["method"] == "POST"
        assert call["url"] == "/gw/knowledge/graphs"
        assert call["json"]["name"] == "experiment-history"
        assert call["json"]["kind"] == "experiment"

    def test_graph_archive_is_patch_status(self):
        knowledge, inner = _knowledge()
        inner.next = {"graph_id": "g-1", "status": "archived"}
        result = knowledge.graph_archive("g-1")
        assert result["status"] == "archived"
        call = inner.calls[0]
        assert call["method"] == "PATCH"
        assert call["url"] == "/gw/knowledge/graphs/g-1"
        assert call["json"] == {"status": "archived"}


class TestProposalMethods:
    def test_propose_carries_session(self):
        knowledge, inner = _knowledge()
        knowledge.session_id = "s-1"
        inner.next = {"proposal_id": "gp-1", "status": "pending"}
        knowledge.graph_propose(
            "g-1",
            reason="missing X",
            source_artifacts=["a.json"],
            proposed_operations=[{"op": "ingest"}],
        )
        call = inner.calls[0]
        assert call["url"] == "/gw/knowledge/graphs/g-1/proposals"
        assert call["json"]["session_id"] == "s-1"
        assert call["json"]["missing_information"] == []

    def test_decide_body(self):
        knowledge, inner = _knowledge()
        inner.next = {"proposal_id": "gp-1", "status": "approved"}
        knowledge.graph_decide("gp-1", approved=True)
        call = inner.calls[0]
        assert call["url"] == "/gw/knowledge/proposals/gp-1/decision"
        assert call["json"] == {"approved": True}


class TestQueryConstraintsAndHeaders:
    def test_query_graph_constraints_become_csv_params(self):
        knowledge, inner = _knowledge()
        inner.next = {"query": {}}
        knowledge.query(
            "FWI?",
            project_id="proj-a",
            graphs=["project-a", "experiment-history"],
            exclude_graphs=["institutional"],
        )
        params = inner.calls[0]["params"]
        assert params["graphs"] == "project-a,experiment-history"
        assert params["exclude_graphs"] == "institutional"
        assert params["scope"] == "all"

    def test_query_without_constraints_sends_no_graph_params(self):
        knowledge, inner = _knowledge()
        inner.next = {"query": {}}
        knowledge.query("FWI?", project_id="proj-a")
        assert "graphs" not in inner.calls[0]["params"]

    def test_agent_actor_headers_on_ingest_sources_with_proposal(self):
        knowledge, inner = _knowledge()
        knowledge.actor = "agent"
        knowledge.session_id = "s-9"
        inner.next = {"job_id": "j-1"}
        knowledge.ingest_sources(
            "proj-a",
            [{"kind": "local_file", "uri": "/data/x.json"}],
            proposal_id="gp-1",
        )
        call = inner.calls[0]
        assert call["url"] == "/gw/knowledge/ingest/ingest"
        assert call["json"]["proposal_id"] == "gp-1"
        assert call["headers"] == {"x-r4-actor": "agent", "x-r4-session-id": "s-9"}

    def test_agent_actor_headers_on_graph_create(self):
        knowledge, inner = _knowledge()
        knowledge.actor = "agent"
        knowledge.session_id = "s-9"
        inner.next = {"graph_id": "g-new"}
        knowledge.graph_create("rogue")
        assert inner.calls[0]["headers"] == {"x-r4-actor": "agent", "x-r4-session-id": "s-9"}

    def test_agent_actor_headers_on_graph_update(self):
        knowledge, inner = _knowledge()
        knowledge.actor = "agent"
        knowledge.session_id = "s-9"
        inner.next = {"graph_id": "g-1", "status": "archived"}
        knowledge.graph_archive("g-1")
        assert inner.calls[0]["headers"] == {"x-r4-actor": "agent", "x-r4-session-id": "s-9"}

    def test_a_decision_is_the_persons_even_after_an_agent_ingest(self):
        """The server refuses agent decisions (ADR-084 D-3); the actor stays
        sticky after an agent ingest, so a person's later decision was a 403."""
        knowledge, inner = _knowledge()
        knowledge.actor = "agent"
        knowledge.session_id = "s-9"
        inner.next = {"proposal_id": "gp-1", "status": "approved"}
        knowledge.graph_decide("gp-1", approved=True)
        assert inner.calls[0]["headers"] == {"x-r4-session-id": "s-9"}

    def test_agent_actor_headers_on_propose(self):
        knowledge, inner = _knowledge()
        knowledge.actor = "agent"
        knowledge.session_id = "s-9"
        inner.next = {"proposal_id": "gp-1", "status": "pending"}
        knowledge.graph_propose("g-1", reason="missing X")
        assert inner.calls[0]["headers"] == {"x-r4-actor": "agent", "x-r4-session-id": "s-9"}

    def test_agent_actor_headers_on_restore(self):
        knowledge, inner = _knowledge()
        knowledge.actor = "agent"
        knowledge.session_id = "s-9"
        inner.next = {"restored": {}}
        knowledge.restore("/srv/bundles/x", project_id="proj-a")
        assert inner.calls[0]["headers"] == {"x-r4-actor": "agent", "x-r4-session-id": "s-9"}

    def test_recorded_caller_headers(self):
        knowledge, inner = _knowledge()
        knowledge.actor = "agent"
        knowledge.session_id = "s-9"
        inner.next = {"result": "ok"}
        # correct() was the probe here; it is retired (no server route,
        # 2026-10-20), graph_propose carries the same actor headers.
        knowledge.graph_propose("g-1", reason="extend")
        assert inner.calls[0]["headers"] == {"x-r4-actor": "agent", "x-r4-session-id": "s-9"}

    def test_human_actor_sends_no_headers_or_proposal(self):
        knowledge, inner = _knowledge()
        inner.next = {"job_id": "j-1"}
        knowledge.ingest_sources("proj-a", [{"kind": "local_file", "uri": "/x"}])
        call = inner.calls[0]
        assert "proposal_id" not in call["json"]
        assert call["headers"] is None
