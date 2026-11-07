"""Knowledge graph selection policy (multi-graph Phase 7).

Pure, stdlib-only resolution of *which knowledge graphs* a query should run
against, combining explicit user constraints with agent discovery.

Precedence (first match wins, applied in order):

  1. **explicit exclusion** — always removes the graph, no exceptions
  2. **explicit "only"** — hard set; nothing else is selected, no discovery
  3. **explicit inclusion** — guarantees the graph is selected
  4. **discovery** (when allowed) — relevance-ranked candidates the
     constraints did not pin down; may expand the included set but never
     violate an exclusion or an "only" constraint
  5. **default graph** — the current project's default graph, used when
     nothing else selected anything

Tokens match a graph by ``graph_id`` or case-insensitive ``name``.

The module is transport-free: callers pass graph metadata dicts (from
``KnowledgeClient.graphs``) and get back the selected graphs plus an
explanation of why each was selected — suitable for agent provenance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class GraphPick:
    """One selected graph with the reason for its selection."""

    graph: dict[str, Any]
    source: str  # "only" | "include" | "discovery" | "default"
    score: float = 0.0


@dataclass(frozen=True)
class GraphSelection:
    """Resolution result: selected picks plus audit metadata."""

    picks: list[GraphPick] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)  # tokens explicitly excluded
    rejected: list[str] = field(default_factory=list)  # tokens matched nothing
    only_constraint: bool = False  # True when an explicit "only" set was given

    @property
    def graph_ids(self) -> list[str]:
        return [p.graph["graph_id"] for p in self.picks]

    def explain(self) -> str:
        """One-line human explanation for chat/CLI output."""
        if not self.picks:
            return "no graphs selected"
        parts = ", ".join(
            f"{p.graph.get('name', p.graph['graph_id'])} ({p.source})" for p in self.picks
        )
        return f"selected: {parts}"


def _matches(token: str, graph: dict[str, Any]) -> bool:
    lowered = token.strip().lower()
    if not lowered:
        return False
    graph_id = str(graph.get("graph_id", "")).lower()
    name = str(graph.get("name", "")).lower()
    return lowered in (graph_id, name)


def _find(token: str, available: Iterable[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for graph in available:
        if _matches(token, graph):
            return graph
    return None


def graph_relevance(query_text: str, graph: dict[str, Any]) -> float:
    """Token-overlap relevance of a graph to query text (0.0–1.0).

    Simple deterministic scorer over name + description + kind + project_id.
    Digit-heavy tokens (e.g. "2026") match experimental names as well.
    """
    haystack = " ".join(
        str(graph.get(key) or "") for key in ("name", "description", "kind", "project_id")
    ).lower()
    tokens = [t for t in _TOKEN_SPLIT.split(query_text.lower()) if len(t) >= 3]
    if not tokens:
        return 0.0
    hits = sum(1 for token in tokens if token in haystack)
    return hits / len(tokens)


def _resolve_excluded_ids(
    exclude: Iterable[str], available: list[dict[str, Any]]
) -> tuple[set[str], list[str]]:
    """Excluded graph ids plus the raw excluded tokens (for audit output)."""
    excluded_ids: set[str] = set()
    excluded_tokens: list[str] = []
    for token in exclude:
        excluded_tokens.append(token)
        graph = _find(token, available)
        if graph is not None:
            excluded_ids.add(graph["graph_id"])
    return excluded_ids, excluded_tokens


def _match_tokens(
    tokens: Iterable[str], available: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split tokens into matched graphs and unmatched (rejected) tokens."""
    matched: list[dict[str, Any]] = []
    rejected: list[str] = []
    for token in tokens:
        graph = _find(token, available)
        if graph is None:
            rejected.append(token)
        else:
            matched.append(graph)
    return matched, rejected


def _only_picks(
    only: Iterable[str],
    available: list[dict[str, Any]],
    excluded_ids: set[str],
) -> tuple[list[dict[str, Any]], list[GraphPick], list[str]]:
    """Explicit 'only' set candidates, picks, and unmatched tokens.

    The decision to enter 'only' mode depends on *matching* (a fully
    excluded only-set yields an empty hard selection, not a fallback).
    """
    matched, rejected = _match_tokens(only, available)
    picks = [
        GraphPick(graph=g, source="only") for g in matched if g["graph_id"] not in excluded_ids
    ]
    return matched, picks, rejected


def _include_picks(
    include: Iterable[str],
    available: list[dict[str, Any]],
    excluded_ids: set[str],
    rejected: Optional[list[str]] = None,
) -> tuple[list[GraphPick], list[str]]:
    """Explicit inclusions (guaranteed), minus excluded/duplicate entries."""
    matched, include_rejected = _match_tokens(include, available)
    picks = [
        GraphPick(graph=g, source="include") for g in matched if g["graph_id"] not in excluded_ids
    ]
    return picks, (rejected or []) + include_rejected


def _discovery_picks(
    query_text: str,
    available: list[dict[str, Any]],
    excluded_ids: set[str],
    picked_ids: set[str],
    min_score: float,
    limit: int,
) -> list[GraphPick]:
    """Relevance-ranked expansion, best score first, ties by name."""
    scored = [
        (graph_relevance(query_text, graph), graph)
        for graph in available
        if graph["graph_id"] not in excluded_ids and graph["graph_id"] not in picked_ids
    ]
    candidates = sorted(
        (item for item in scored if item[0] >= min_score),
        key=lambda item: (-item[0], item[1].get("name", "")),
    )
    return [GraphPick(graph=g, source="discovery", score=s) for s, g in candidates[:limit]]


def _active_graphs(available: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Graphs eligible for selection (id present, not archived)."""
    return [g for g in available if g.get("graph_id") and g.get("status", "active") == "active"]


def _default_pick(
    picks: list[GraphPick],
    default_graph: Optional[dict[str, Any]],
    excluded_ids: set[str],
) -> None:
    """Append the project default pick when nothing else selected."""
    if not picks and default_graph is not None and default_graph["graph_id"] not in excluded_ids:
        picks.append(GraphPick(graph=default_graph, source="default"))


def resolve_graph_selection(
    *,
    query_text: str = "",
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
    only: Iterable[str] = (),
    available: Iterable[dict[str, Any]] = (),
    default_graph: Optional[dict[str, Any]] = None,
    allow_discovery: bool = True,
    discovery_min_score: float = 0.2,
    discovery_limit: int = 3,
) -> GraphSelection:
    """Resolve the final set of graphs for one query.

    See module docstring for the precedence rules.
    """
    available_list = _active_graphs(available)
    excluded_ids, excluded_tokens = _resolve_excluded_ids(exclude, available_list)

    only_matched, only_picks, only_rejected = _only_picks(only, available_list, excluded_ids)
    if only_matched:
        return GraphSelection(
            picks=only_picks,
            excluded=excluded_tokens,
            rejected=only_rejected,
            only_constraint=True,
        )

    picks, rejected = _include_picks(include, available_list, excluded_ids, rejected=only_rejected)

    if allow_discovery and query_text.strip():
        picks.extend(
            _discovery_picks(
                query_text,
                available_list,
                excluded_ids,
                {p.graph["graph_id"] for p in picks},
                discovery_min_score,
                discovery_limit,
            )
        )

    _default_pick(picks, default_graph, excluded_ids)

    return GraphSelection(
        picks=picks,
        excluded=excluded_tokens,
        rejected=rejected,
    )
