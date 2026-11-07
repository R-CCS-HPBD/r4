"""The user's graph constraints as retrieval arguments (ADR-108 D-8, W1).

``knowledge_query`` honours the session's graph constraints (include,
exclude, discovery; ``companion/knowledge_graph_tools._merged_constraints``),
but ``search_docs`` and the local loop's ``rag.retrieve`` passed no
``graphs=`` at all, so a user who excluded or pinned graphs still got
passages from every graph there (2026-11-03). This maps the same
constraints onto the retrieve lane (ADR-093: every lane honours ``graphs=``):

- excluded graphs are always sent as ``exclude_graphs``;
- included graphs restrict the search (``graphs``) only when discovery is
  off, i.e. the user pinned them; with discovery on they are a preference
  ``knowledge_query`` ranks by, not a filter, so retrieve is not narrowed.

The ADR-108 scope overlay (W3) narrows reads further; this is the user's own
choice only.
"""

from __future__ import annotations

from typing import Any


def _names(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def graph_constraints(client: Any) -> dict[str, Any]:
    """The session's constraints, else the persisted ones, else none."""
    constraints = getattr(getattr(client, "knowledge", None), "graph_constraints", None) or {}
    if constraints:
        return dict(constraints)
    try:
        from .graph_constraints_store import load_constraints

        return dict(load_constraints() or {})
    except Exception:  # noqa: BLE001 - no store means no constraints
        return {}


def retrieve_graph_args(client: Any) -> dict[str, list[str]]:
    """``graphs`` / ``exclude_graphs`` keyword arguments for ``rag.retrieve``."""
    constraints = graph_constraints(client)
    args: dict[str, list[str]] = {}
    exclude = _names(constraints.get("exclude"))
    if exclude:
        args["exclude_graphs"] = exclude
    include = _names(constraints.get("include"))
    if include and not bool(constraints.get("discovery", True)):
        args["graphs"] = include
    return args
