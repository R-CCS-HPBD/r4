"""Scenario e2e (Phase 15): user → agent tools → multi-KG → permission → extension.

Drives the REAL companion tool executables (no LLM) against a fake UMRS-side
knowledge facade that mirrors the deployed guard semantics:

  - agent-actor mutations require an approved proposal (server 409 otherwise)
  - decisions recorded per user; executed proposals carry provenance

Scenarios A–F from the task spec.
"""

from __future__ import annotations

import json
from typing import Any

from r4.companion.knowledge_graph_tools import build_knowledge_graph_tools
from r4.companion.knowledge_tools import build_knowledge_tools
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.knowledge import KnowledgeClient

# ── Fake UMRS-side knowledge facade ────────────────────────────────────────


class FakeKnowledgeServer:
    """Server-side semantics of the 0069 registry + proposal guard."""

    def __init__(self) -> None:
        self._graphs: list[dict[str, Any]] = [
            {
                "graph_id": "g-fwi",
                "name": "fwi-project",
                "kind": "project",
                "project_id": "fwi-project",
                "status": "active",
                "description": "FWI inversion project knowledge",
            },
            {
                "graph_id": "g-exp",
                "name": "experiment-history",
                "kind": "experiment",
                "project_id": None,
                "status": "active",
                "description": "Historical FWI experiments since 2025",
            },
            {
                "graph_id": "g-diff",
                "name": "diffusion-lab",
                "kind": "research",
                "project_id": None,
                "status": "active",
                "description": "Latent diffusion interpolation research",
            },
        ]
        # Content visible per graph (query answers)
        self._content: dict[str, list[dict[str, Any]]] = {
            "g-fwi": [{"title": "FWI run 2026-08-14 notes", "score": 0.9}],
            "g-exp": [{"title": "FWI experiments 2025-2026 summary", "score": 0.85}],
            "g-diff": [{"title": "Diffusion interpolation results", "score": 0.8}],
        }
        self._proposals: dict[str, dict[str, Any]] = {}
        self._next_proposal = 0
        self.ingest_calls: list[dict[str, Any]] = []
        # Actor context (set by companion tool build)
        self.actor: str | None = None
        self.session_id: str | None = None

    # Registry -----------------------------------------------------------
    def graphs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._graphs)

    # Query --------------------------------------------------------------
    def query(self, query: str, **kwargs: Any) -> dict[str, Any]:
        graph_ids = kwargs.get("graphs") or ["g-fwi"]
        results: list[dict[str, Any]] = []
        for gid in graph_ids:
            for hit in self._content.get(gid, []):
                results.append({**hit, "metadata": {"graph_id": gid, "graph_name": gid[2:]}})
        # "experiment zeta" is unknown UNTIL someone ingests it (scenario D/F:
        # before extension the answer is empty; after approval + ingest it
        # becomes visible).
        zeta_known = any(
            "zeta" in hit["title"].lower() for hits in self._content.values() for hit in hits
        )
        if "experiment zeta" in query.lower() and not zeta_known:
            results = []
        return {"query": {"results": results, "total_results": len(results)}}

    # Proposals ----------------------------------------------------------
    def graph_propose(self, graph_id: str, **kwargs: Any) -> dict[str, Any]:
        self._next_proposal += 1
        proposal = {
            "proposal_id": f"gp-{self._next_proposal}",
            "target_graph_id": graph_id,
            "status": "pending",
            **kwargs,
        }
        self._proposals[proposal["proposal_id"]] = proposal
        return proposal

    def graph_decide(self, proposal_id: str, *, approved: bool) -> dict[str, Any]:
        proposal = self._proposals[proposal_id]
        proposal["status"] = "approved" if approved else "declined"
        proposal["decided_by"] = "reviewer"
        return proposal

    # Guarded mutation ---------------------------------------------------
    # The real path planner (inline small text / upload the rest) drives the
    # fake's two mutation seams, exactly as it drives the real client.
    ingest_paths = KnowledgeClient.ingest_paths

    def upload_file(self, project_id: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.ingest_sources(
            project_id, [{"kind": "upload", "uri": path}], proposal_id=kwargs.get("proposal_id")
        )

    def ingest_sources(self, project_id: str, sources, **kwargs: Any) -> dict[str, Any]:
        proposal_id = kwargs.get("proposal_id")
        if self.actor == "agent":
            proposal = self._proposals.get(proposal_id or "")
            if proposal is None or proposal["status"] not in ("approved", "executed"):
                raise PermissionError(
                    "409 proposal_required: agent-originated mutation requires "
                    "an approved graph-extension proposal"
                )
            proposal["status"] = "executed"
            proposal["execution"] = {"actor": self.actor, "operation": "knowledge_ingest"}
        self.ingest_calls.append(
            {
                "project_id": project_id,
                "sources": sources,
                "proposal_id": proposal_id,
            }
        )
        # Ingested knowledge becomes visible in the target graph
        self._content.setdefault("g-exp", []).append(
            {"title": "experiment zeta ingested notes", "score": 0.88}
        )
        return {"job_id": "job-1", "status": "queued"}


class _FakeConfig:
    project_id = "fwi-project"


class _FakeClient:
    def __init__(self, server: FakeKnowledgeServer) -> None:
        self.knowledge = server
        self.config = _FakeConfig()


def _session() -> tuple[FakeKnowledgeServer, dict[str, dict[str, Any]]]:
    server = FakeKnowledgeServer()
    client = _FakeClient(server)
    tools = {
        t["name"]: t for t in (build_knowledge_tools(client) + build_knowledge_graph_tools(client))
    }
    return server, tools


def _run(tools, name: str, **kwargs: Any) -> dict[str, Any]:
    return json.loads(tools[name]["execute"](**kwargs))


def _selected_names(answer: dict[str, Any]) -> set[str]:
    return {s["name"] for s in answer["selection"]["selected_graphs"]}


def _any_title_contains(results: list[dict[str, Any]], token: str) -> bool:
    return any(token in r["title"].lower() for r in results)


def _all_hits_carry_graph(results: list[dict[str, Any]]) -> bool:
    return all(r["graph_id"] for r in results)


def _prompt_recording_manager(prompts: list) -> PermissionManager:
    return PermissionManager(
        mode=PermissionMode.ASK_ALWAYS,
        approval_callback=lambda tool, desc, args: prompts.append((tool, desc)) or True,
    )


def _assert_provenance_executed(server: FakeKnowledgeServer, proposal_id: str) -> None:
    proposal = server._proposals[proposal_id]
    assert proposal["status"] == "executed"
    assert proposal["execution"]["actor"] == "agent"
    assert proposal["execution"]["operation"] == "knowledge_ingest"


# ── Scenario A — automatic discovery + selection ──────────────────────────


def test_scenario_a_discovery_selects_relevant_graphs_only() -> None:
    server, tools = _session()
    discovery = _run(tools, "knowledge_graph_list")
    assert discovery["count"] == 3  # agent can see the registered graphs

    answer = _run(tools, "knowledge_query", query="previous FWI experiments")
    names = _selected_names(answer)
    # Spec: agent selects the FWI project + experiment-history…
    assert names == {"fwi-project", "experiment-history"}
    # …and does NOT query the unrelated diffusion graph
    assert "diffusion-lab" not in names
    assert _any_title_contains(answer["results"], "experiments")
    # provenance: every hit names its graph
    assert _all_hits_carry_graph(answer["results"])


# ── Scenario B — explicit "use only" ────────────────────────────────────────


def test_scenario_b_only_constraint() -> None:
    _, tools = _session()
    answer = _run(
        tools, "knowledge_query", query="FWI experiments", only_graphs=["experiment-history"]
    )
    selected = [s["graph_id"] for s in answer["selection"]["selected_graphs"]]
    assert selected == ["g-exp"]
    assert all(r["graph_id"] == "g-exp" for r in answer["results"])


# ── Scenario C — explicit include + exclude ────────────────────────────────


def test_scenario_c_include_with_exclusion() -> None:
    _, tools = _session()
    answer = _run(
        tools,
        "knowledge_query",
        query="FWI",
        graphs=["fwi-project", "experiment-history", "diffusion-lab"],
        exclude_graphs=["diffusion-lab"],
    )
    selected = {s["graph_id"] for s in answer["selection"]["selected_graphs"]}
    assert selected == {"g-fwi", "g-exp"}
    assert all(r["graph_id"] in selected for r in answer["results"])


# ── Scenario D — insufficient knowledge → proposal, no mutation ─────────────


def test_scenario_d_insufficient_triggers_proposal_not_mutation() -> None:
    server, tools = _session()
    answer = _run(tools, "knowledge_query", query="what happened in experiment zeta?")
    assert answer["knowledge_insufficient"] is True
    assert answer["requires_user_approval"] is True

    proposal = _run(
        tools,
        "knowledge_graph_propose",
        graph_id="g-exp",
        reason="experiment zeta is not in the knowledge base",
        missing_information=["experiment zeta parameters and results"],
        source_artifacts=["exp-zeta.json"],
    )
    assert proposal["proposal"]["status"] == "pending"

    # No mutation happened anywhere
    assert server.ingest_calls == []

    # Even in TRUST mode without an approval callback, decide must be denied
    mgr = PermissionManager(mode=PermissionMode.TRUST)
    assert mgr.check("knowledge_graph_decide", {"proposal_id": "gp-1", "approved": True}) is False
    # And the server guard blocks agent-actor ingest without approval
    server.actor = "agent"  # companion marks the actor at mutation time
    try:
        server.ingest_sources("fwi-project", [{"kind": "local_file", "uri": "exp-zeta.json"}])
        raise AssertionError("server guard should have rejected this")
    except PermissionError:
        assert server.ingest_calls == []


# ── Scenario E — user declines ─────────────────────────────────────────────


def test_scenario_e_decline_forbids_mutation() -> None:
    server, tools = _session()
    _run(tools, "knowledge_graph_propose", graph_id="g-exp", reason="missing experiment zeta")

    prompts: list[str] = []
    mgr = PermissionManager(
        mode=PermissionMode.ASK_ALWAYS,
        approval_callback=lambda tool, desc, args: prompts.append(tool) or True,
    )
    assert mgr.check("knowledge_graph_decide", {"proposal_id": "gp-1", "approved": False}) is True
    decision = _run(tools, "knowledge_graph_decide", proposal_id="gp-1", approved=False)
    assert decision["proposal"]["status"] == "declined"
    assert "do NOT mutate" in decision["next"]

    # Agent still cannot execute (server blocks declined proposals);
    # companion marks the actor at mutation time (per-call, not at build)
    server.actor = "agent"
    try:
        server.ingest_sources(
            "fwi-project", [{"kind": "local_file", "uri": "exp-zeta.json"}], proposal_id="gp-1"
        )
        raise AssertionError("declined proposal must not execute")
    except PermissionError:
        pass
    assert server.ingest_calls == []


# ── Scenario F — approve → execute → provenance → visible ──────────────────


def test_scenario_f_approve_executes_with_provenance() -> None:
    server, tools = _session()
    _run(
        tools,
        "knowledge_graph_propose",
        graph_id="g-exp",
        reason="missing experiment zeta",
        source_artifacts=["exp-zeta.json"],
    )

    prompts: list[tuple[str, str]] = []
    mgr = _prompt_recording_manager(prompts)
    # 1. Decision (always prompted, never auto-approved)
    assert mgr.check("knowledge_graph_decide", {"proposal_id": "gp-1", "approved": True}) is True
    decision = _run(tools, "knowledge_graph_decide", proposal_id="gp-1", approved=True)
    assert decision["proposal"]["status"] == "approved"
    assert decision["proposal"]["decided_by"] == "reviewer"

    # 2. Execution via the ingest tool (risky — also prompted)
    assert mgr.check("knowledge_ingest", {"files": ["exp-zeta.json"]}) is True
    _run(tools, "knowledge_ingest", files=["exp-zeta.json"], proposal_id="gp-1")

    assert server.ingest_calls[0]["proposal_id"] == "gp-1"
    # 3. Provenance: proposal marked executed with actor + operation
    _assert_provenance_executed(server, "gp-1")

    # 4. The new knowledge is now visible to subsequent queries
    after = _run(
        tools, "knowledge_query", query="experiment zeta", only_graphs=["experiment-history"]
    )
    # The fake makes ingested content visible immediately
    assert _any_title_contains(after["results"], "zeta")
    # 5. Both prompts (decision + ingest) were shown exactly once
    assert [t for t, _ in prompts] == ["knowledge_graph_decide", "knowledge_ingest"]
