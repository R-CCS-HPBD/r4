"""Session-scoped knowledge-acquisition mode (write-side constraints).

Read-side graph constraints (``graph_constraints_store`` /
``knowledge_selection``) pick WHICH graphs a query sees. This module
governs WHETHER the chat may acquire knowledge at all in the current
session — and into WHICH pre-existing graph when it may (user request
2026-09-11: "select if their current chat may create a new / extend a
specific pre-existing knowledge graph or not … changed during one single
session").

Modes:
  ``open``     — default. propose/ingest allowed; per-call approvals and
                 the propose→decide flow still gate every mutation
                 (behavior identical to before this feature existed).
  ``pinned``   — EXTEND-ONLY into named pre-existing graph(s). A
                 ``knowledge_ingest`` without an explicit ``graph_id``
                 auto-routes into the single pinned graph; ingests and
                 proposals for any other target are refused before any
                 server call. Creating brand-new graphs stays refused
                 (it was never an agent capability — the mode name says
                 so honestly).
  ``disabled`` — no acquisition at all: propose and ingest refuse before
                 any approval prompt or server call.

The constraints live on the session (``session.metadata``) and are
mirrored onto the shared client facade for tool-side enforcement. They
are session-scoped: changing them mid-session takes effect immediately;
they never grant anything across sessions the user did not set there.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Tuple

MODES = frozenset({"open", "pinned", "disabled"})

_DEFAULT: dict[str, Any] = {"mode": "open", "graphs": []}

_REENABLE_HINT = "The user can change this any time with /acquisition."


def norm_token(token: str) -> str:
    """Case-insensitive comparison token (names AND ids)."""
    return str(token or "").strip().lower()


def sanitize_acquisition(raw: Any) -> dict[str, Any]:
    """Coerce any stored value into the constraints shape (open on bad input)."""
    if not isinstance(raw, dict):
        return dict(_DEFAULT)
    mode = str(raw.get("mode") or "open").strip().lower()
    if mode not in MODES:
        mode = "open"
    graphs = [str(t).strip() for t in (raw.get("graphs") or []) if str(t).strip()]
    if mode == "pinned" and not graphs:
        mode = "open"
    return {"mode": mode, "graphs": graphs}


def acquisition_constraints(client: Any) -> dict[str, Any]:
    """Current constraints on the shared client facade (sanitized)."""
    knowledge = getattr(client, "knowledge", None)
    raw = getattr(knowledge, "write_constraints", None) if knowledge is not None else None
    return sanitize_acquisition(raw)


def set_acquisition(
    client: Any,
    mode: str,
    graphs: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Validate + store constraints on the shared client facade.

    Returns the sanitized constraints. Raises ValueError on unknown mode
    or pinned-without-graphs.
    """
    normalized = sanitize_acquisition({"mode": mode, "graphs": list(graphs or [])})
    if str(mode).strip().lower() == "pinned" and not normalized["graphs"]:
        raise ValueError("pinned mode needs at least one target graph")
    knowledge = getattr(client, "knowledge", None)
    if knowledge is None:
        raise ValueError("client has no knowledge facade")
    knowledge.write_constraints = dict(normalized)
    return normalized


def describe_acquisition(constraints: dict[str, Any]) -> str:
    """One-line human summary for chat/CLI output."""
    mode = constraints.get("mode", "open")
    if mode == "disabled":
        return "disabled — this chat must not create or extend any knowledge"
    if mode == "pinned":
        names = ", ".join(constraints.get("graphs") or [])
        return f"pinned — this chat may only extend: {names}"
    return "open — agent may propose/extend knowledge (approvals still gate each write)"


def _pinned_targets(client: Any, tokens: Iterable[str]) -> set[str]:
    """All acceptable id/name strings for the pinned graphs.

    Raw pinned tokens always match; when the registry lists graphs, ids
    and names of the pinned graphs cross-resolve (the user pins by name,
    the agent often passes an id, or vice versa). Listing is best-effort —
    a fetch failure narrows matching to the raw tokens, never widens.
    """
    wanted = {norm_token(t) for t in tokens}
    try:
        for graph in client.knowledge.graphs() or []:
            gid = str(graph.get("graph_id") or "")
            name = str(graph.get("name") or "")
            if norm_token(gid) in wanted or norm_token(name) in wanted:
                wanted.add(norm_token(gid))
                wanted.add(norm_token(name))
    except Exception:  # noqa: BLE001 — best-effort crosswalk only
        pass
    return wanted


def check_propose(client: Any, graph_id: str) -> Optional[str]:
    """None when a graph-extension proposal may proceed; refusal message otherwise."""
    constraints = acquisition_constraints(client)
    mode = constraints["mode"]
    if mode == "open":
        return None
    if mode == "disabled":
        return (
            "Knowledge acquisition is DISABLED for this session — do not stage "
            f"proposals or ingest; answer with existing knowledge. {_REENABLE_HINT}"
        )
    targets = _pinned_targets(client, constraints["graphs"])
    if norm_token(graph_id) in targets:
        return None
    allowed = ", ".join(constraints["graphs"])
    return (
        f"This session pins knowledge acquisition to: {allowed}. A proposal for "
        f"'{graph_id}' is refused — propose extending one of the pinned graphs "
        f"instead, or ask the user to widen the mode. {_REENABLE_HINT}"
    )


def resolve_ingest_target(
    client: Any, graph_id: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve the ingest target graph under the acquisition mode.

    Returns ``(target_graph_id_or_None, error_or_None)``. In pinned mode an
    explicit ``graph_id`` must be one of the pinned graphs; without an
    explicit id the single pinned graph is auto-selected (extend semantics).
    ``open`` mode passes ``graph_id`` through unchanged (None = project
    default graph, as before).
    """
    constraints = acquisition_constraints(client)
    mode = constraints["mode"]
    if mode == "open":
        return graph_id, None
    if mode == "disabled":
        return None, (
            "Knowledge acquisition is DISABLED for this session — do not ingest; "
            f"answer with existing knowledge. {_REENABLE_HINT}"
        )
    graphs = constraints["graphs"]
    targets = _pinned_targets(client, graphs)
    if graph_id and graph_id.strip():
        if norm_token(graph_id) in targets:
            return graph_id.strip(), None
        allowed = ", ".join(graphs)
        return None, (
            f"This session pins knowledge acquisition to: {allowed}. Ingest into "
            f"'{graph_id}' is refused — use one of the pinned graphs (or leave "
            f"graph_id empty to route there). {_REENABLE_HINT}"
        )
    if len(graphs) == 1:
        return graphs[0], None
    allowed = ", ".join(graphs)
    return None, (
        f"This session pins knowledge acquisition to: {allowed}. Pass graph_id "
        f"explicitly (one of the pinned graphs). {_REENABLE_HINT}"
    )
