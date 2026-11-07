"""Companion multi-graph tools: discovery, selection, proposal, decision."""

from __future__ import annotations

import json
from typing import Any

from r4.companion.knowledge_graph_tools import build_knowledge_graph_tools


class _FakeKnowledge:
    def __init__(self, graphs: list[dict[str, Any]], query_results: list[dict[str, Any]]):
        self._graphs = graphs
        self._results = query_results
        self.calls: list[dict[str, Any]] = []
        self.actor: str | None = None
        self.session_id: str | None = None

    def graphs(self, **kwargs):
        self.calls.append({"graphs": kwargs})
        return self._graphs

    def query(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        return {"query": {"results": self._results, "total_results": len(self._results)}}

    def graph_propose(self, graph_id, **kwargs):
        self.calls.append({"graph_propose": graph_id, **kwargs})
        return {"proposal_id": "gp-1", "target_graph_id": graph_id, "status": "pending"}

    def graph_decide(self, proposal_id, *, approved):
        self.calls.append({"graph_decide": proposal_id, "approved": approved})
        return {"proposal_id": proposal_id, "status": "approved" if approved else "declined"}


class _FakeConfig:
    project_id = "project-a"


class _FakeClient:
    knowledge: _FakeKnowledge

    def __init__(self, knowledge: _FakeKnowledge):
        self.knowledge = knowledge
        self.config = _FakeConfig()


GRAPHS = [
    {
        "graph_id": "g-a",
        "name": "project-a",
        "kind": "project",
        "project_id": "project-a",
        "status": "active",
        "description": "FWI project knowledge",
    },
    {
        "graph_id": "g-b",
        "name": "experiment-history",
        "kind": "experiment",
        "project_id": None,
        "status": "active",
        "description": "Historical FWI experiments",
    },
    {
        "graph_id": "g-c",
        "name": "institutional",
        "kind": "institutional",
        "project_id": None,
        "status": "active",
        "description": "Institutional diffusion wiki",
    },
]


def _tools(client: _FakeClient) -> dict[str, dict[str, Any]]:
    return {t["name"]: t for t in build_knowledge_graph_tools(client)}


def _fake_client(results: list[dict[str, Any]] | None = None) -> _FakeClient:
    knowledge = _FakeKnowledge(GRAPHS, results or [])
    return _FakeClient(knowledge)


class TestGraphList:
    def test_lists_graphs_for_discovery(self):
        tools = _tools(_fake_client())
        out = json.loads(tools["knowledge_graph_list"]["execute"]())
        assert out["type"] == "knowledge_graphs"
        assert out["count"] == 3
        names = {g["name"] for g in out["graphs"]}
        assert {"project-a", "experiment-history", "institutional"} <= names

    def test_actor_marking_is_per_mutation_not_at_build(self):
        client = _fake_client()
        _tools(client)
        # build must NOT mark the shared client (Studio buttons are human paths)
        assert client.knowledge.actor is None


class TestKnowledgeQuery:
    def test_discovery_selects_relevant_graph(self):
        tools = _tools(_fake_client([{"title": "FWI 2026 run", "score": 0.9}]))
        out = json.loads(tools["knowledge_query"]["execute"](query="previous FWI experiments"))
        selected = {s["name"] for s in out["selection"]["selected_graphs"]}
        assert "experiment-history" in selected
        assert "institutional" not in selected
        assert out["results"][0]["score"] == 0.9

    def test_only_constraint_hard_limits(self):
        client = _fake_client()
        tools = _tools(client)
        out = json.loads(
            tools["knowledge_query"]["execute"](
                query="FWI experiments", only_graphs=["experiment-history"]
            )
        )
        graphs = [s["graph_id"] for s in out["selection"]["selected_graphs"]]
        assert graphs == ["g-b"]
        call = [c for c in client.knowledge.calls if "query" in c][-1]
        assert call["graphs"] == ["g-b"]

    def test_exclusion_wins(self):
        tools = _tools(_fake_client())
        out = json.loads(
            tools["knowledge_query"]["execute"](
                query="institutional diffusion", exclude_graphs=["institutional"]
            )
        )
        names = [s["name"] for s in out["selection"]["selected_graphs"]]
        assert "institutional" not in names

    def test_no_results_flags_insufficiency(self):
        tools = _tools(_fake_client([]))
        out = json.loads(
            tools["knowledge_query"]["execute"](
                query="FAISS experiment X", only_graphs=["project-a"]
            )
        )
        assert out["knowledge_insufficient"] is True
        assert out["requires_user_approval"] is True
        assert "knowledge_graph_propose" in out["next"]


class _SufficiencyKnowledge(_FakeKnowledge):
    """A K3 server that reports C2's sufficiency (ADR-093 C7)."""

    def __init__(self, results: list[dict[str, Any]], status: str):
        super().__init__(GRAPHS, results)
        self._status = status

    def query(self, query, **kwargs):
        payload = super().query(query, **kwargs)
        payload["query"]["sufficiency"] = {"status": self._status, "basis": "z_null"}
        return payload


_FUSED_HIT = {
    "title": "MERMAID catalog",
    "score": 0.42,
    "source_id": "node-7",
    "content": "chunk text",
    "metadata": {
        "graph_id": "g-a",
        "graph_name": "project-a",
        "node_type": "chunk",
        "lanes": ["embeddings", "rag"],
    },
}


class TestKnowledgeQueryHitsAndSufficiency:
    """ADR-093 C10: hits keep their handle, kind and lanes (KGC-04);
    knowledge_insufficient follows the server's sufficiency (LIVE-06)."""

    def _run(self, knowledge: _FakeKnowledge) -> dict[str, Any]:
        tools = _tools(_FakeClient(knowledge))
        return json.loads(
            tools["knowledge_query"]["execute"](query="FWI catalog", only_graphs=["project-a"])
        )

    def test_hit_keeps_source_id_node_type_and_lanes(self):
        out = self._run(_FakeKnowledge(GRAPHS, [_FUSED_HIT]))

        hit = out["results"][0]
        assert hit["source_id"] == "node-7"
        assert hit["node_type"] == "chunk"
        assert hit["lanes"] == ["embeddings", "rag"]
        assert hit["graph_id"] == "g-a"

    def test_single_lane_hit_reports_its_lane(self):
        hit = dict(_FUSED_HIT, metadata={"node_type": "paper", "lane": "similarity"})

        out = self._run(_FakeKnowledge(GRAPHS, [hit]))

        assert out["results"][0]["lanes"] == ["similarity"]

    def test_insufficient_verdict_flags_even_with_results(self):
        out = self._run(_SufficiencyKnowledge([_FUSED_HIT], "insufficient"))

        assert out["total_results"] == 1
        assert out["knowledge_insufficient"] is True
        assert out["sufficiency"]["status"] == "insufficient"

    def test_sufficient_verdict_does_not_flag(self):
        out = self._run(_SufficiencyKnowledge([_FUSED_HIT], "sufficient"))

        assert "knowledge_insufficient" not in out
        assert out["sufficiency"]["basis"] == "z_null"

    def test_older_server_without_sufficiency_flags_on_zero_results(self):
        out = self._run(_FakeKnowledge(GRAPHS, []))

        assert out["knowledge_insufficient"] is True
        assert "sufficiency" not in out

    def test_an_unknown_verdict_falls_back_to_zero_results(self):
        """K3 says ``unknown`` when no dense or lexical lane ran (catalogue
        only, no encoder): nothing found must still flag (integrator, 2026-10-10)."""
        empty = self._run(_SufficiencyKnowledge([], "unknown"))
        found = self._run(_SufficiencyKnowledge([_FUSED_HIT], "unknown"))

        assert empty["knowledge_insufficient"] is True
        assert "knowledge_insufficient" not in found


class TestProposalFlow:
    def test_propose_stages_without_mutating(self):
        client = _fake_client()
        tools = _tools(client)
        out = json.loads(
            tools["knowledge_graph_propose"]["execute"](
                graph_id="g-b", reason="missing experiment X", source_artifacts=["exp-x.json"]
            )
        )
        assert out["type"] == "knowledge_graph_proposal"
        assert out["proposal"]["status"] == "pending"
        assert "STOP" in out["next"]
        # No query/ingest calls happened — only the proposal
        assert not any("query" in c for c in client.knowledge.calls)

    def test_propose_requires_reason(self):
        tools = _tools(_fake_client())
        out = json.loads(tools["knowledge_graph_propose"]["execute"](graph_id="g-b", reason=""))
        assert out["code"] == "proposal_invalid"

    def test_decide_approve(self):
        tools = _tools(_fake_client())
        out = json.loads(
            tools["knowledge_graph_decide"]["execute"](proposal_id="gp-1", approved=True)
        )
        assert out["proposal"]["status"] == "approved"
        assert "proposal_id" in out["next"] or "provenance" in out["next"]

    def test_decide_decline_forbids_mutation(self):
        tools = _tools(_fake_client())
        out = json.loads(
            tools["knowledge_graph_decide"]["execute"](proposal_id="gp-1", approved=False)
        )
        assert out["proposal"]["status"] == "declined"
        assert "do NOT mutate" in out["next"]
