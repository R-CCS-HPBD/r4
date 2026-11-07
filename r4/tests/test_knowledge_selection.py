"""Selection policy tests (multi-graph Phase 7 precedence rules)."""

from __future__ import annotations

from r4.knowledge_selection import (
    graph_relevance,
    resolve_graph_selection,
)

A = {
    "graph_id": "g-a",
    "name": "project-a",
    "status": "active",
    "kind": "project",
    "project_id": "project-a",
    "description": "FWI project knowledge",
}
B = {
    "graph_id": "g-b",
    "name": "experiment-history",
    "status": "active",
    "kind": "experiment",
    "project_id": None,
    "description": "Historical FWI experiments 2026",
}
C = {
    "graph_id": "g-c",
    "name": "institutional",
    "status": "active",
    "kind": "institutional",
    "project_id": None,
    "description": "Institutional diffusion research wiki",
}
AVAILABLE = [A, B, C]


class TestRelevance:
    def test_relevance_token_overlap(self):
        assert graph_relevance("compare FWI experiments", B) > 0
        assert graph_relevance("quantum chromodynamics", B) == 0.0


class TestPrecedence:
    def test_only_is_hard_constraint(self):
        sel = resolve_graph_selection(
            query_text="FWI experiments",
            only=["experiment-history"],
            include=["project-a"],
            available=AVAILABLE,
            default_graph=A,
        )
        assert sel.only_constraint is True
        assert sel.graph_ids == ["g-b"]
        assert sel.picks[0].source == "only"

    def test_exclusion_beats_inclusion(self):
        sel = resolve_graph_selection(
            include=["experiment-history", "project-a"],
            exclude=["experiment-history"],
            available=AVAILABLE,
            default_graph=A,
        )
        assert sel.graph_ids == ["g-a"]
        assert sel.excluded == ["experiment-history"]

    def test_exclusion_beats_discovery(self):
        sel = resolve_graph_selection(
            query_text="institutional diffusion research",
            exclude=["institutional"],
            available=AVAILABLE,
            default_graph=A,
        )
        assert "g-c" not in sel.graph_ids

    def test_include_plus_discovery_expands_without_violating(self):
        sel = resolve_graph_selection(
            query_text="FWI experiments 2026",
            include=["institutional"],
            available=AVAILABLE,
            default_graph=A,
        )
        assert "g-c" in sel.graph_ids  # explicit include
        assert "g-b" in sel.graph_ids  # discovery (relevance > threshold)
        assert set(p.source for p in sel.picks) <= {"include", "discovery"}

    def test_discovery_selects_relevant_leaves_unrelated_out(self):
        sel = resolve_graph_selection(
            query_text="previous FWI experiments",
            available=AVAILABLE,
            default_graph=A,
        )
        assert "g-b" in sel.graph_ids
        assert "g-c" not in sel.graph_ids

    def test_default_graph_when_nothing_matches(self):
        sel = resolve_graph_selection(
            query_text="quantum chromodynamics",
            available=AVAILABLE,
            default_graph=A,
        )
        assert sel.graph_ids == ["g-a"]
        assert sel.picks[0].source == "default"

    def test_no_discovery_when_disabled(self):
        sel = resolve_graph_selection(
            query_text="FWI experiments 2026",
            include=["project-a"],
            available=AVAILABLE,
            default_graph=A,
            allow_discovery=False,
        )
        assert sel.graph_ids == ["g-a"]

    def test_archived_graphs_never_selected(self):
        archived = [dict(A), dict(B, status="archived"), dict(C)]
        sel = resolve_graph_selection(
            query_text="FWI experiments 2026",
            available=archived,
            default_graph=A,
        )
        assert "g-b" not in sel.graph_ids

    def test_unknown_tokens_are_rejected(self):
        sel = resolve_graph_selection(
            include=["does-not-exist"],
            available=AVAILABLE,
            default_graph=A,
        )
        assert sel.rejected == ["does-not-exist"]

    def test_explain_names_sources(self):
        sel = resolve_graph_selection(only=["project-a"], available=AVAILABLE)
        assert "project-a (only)" in sel.explain()
