"""Research Session aggregate view — a read-only projection over existing stores.

Milestone 5 shape (deliberately an aggregate *view*, not a new authoritative
store, per governance): one session = one authoritative runtime; the view
gathers the piecesconversation + loops + knowledge digest a user needs to
understand what's happening in their research project right now, without
inventing a second database. Keyed by (project_id, client).

Later milestones can promote this shape to a tracked session schema behind a
governance review and SCHEMA_VERSION bump — the contract here is the projection.
"""

from __future__ import annotations

from typing import Any, Dict

from .api import ResearchRuntime


def research_session_view(
    client: Any,
    project_id: str | None = None,
    mode: str | None = None,
) -> Dict[str, Any]:
    """Aggregate a project's live research session into one read-only summary.

    Includes the loops currently running in the resolved runtime (in hybrid,
    both stores merged), a knowledge-graph digest, and an authority note so
    the user sees *where* durable state actually lives. Never mutates state.
    """
    from .resolver import get_runtime

    runtime = get_runtime(mode=mode, project_id=project_id, client=client)
    caps = runtime.capabilities()
    loops = _loops_snapshot(runtime, project_id)
    knowledge_digest = _knowledge_digest(client, project_id)
    durable_sessions = _durable_sessions(client, project_id, caps.location)
    return {
        "type": "research_session_view",
        "project_id": project_id,
        "authoritative_runtime": caps.location,
        "runtime": caps.location,
        "durable": caps.durable,
        "distributed": caps.distributed,
        "backends": list(caps.backends),
        "loops": loops,
        "sessions": durable_sessions,
        "knowledge": knowledge_digest,
        "authority_note": (
            "view only — durable sessions live in UMRS (ADR-025); one "
            "authoritative runtime per loop, never dual-write"
        ),
    }


def _loops_snapshot(runtime: ResearchRuntime, project_id: str | None) -> Dict[str, Any]:
    """Loop summaries grouped by venue (local- vs umrs-id convention)."""
    states = runtime.list_loops(project_id)
    items = []
    for state in states:
        items.append(
            {
                "loop_id": state.loop_id,
                "status": state.status,
                "phase": state.phase,
                "iterations": f"{state.current_iteration}/{state.max_iterations}",
                "venue": state.metadata.get("venue") or _venue_from_id(state.loop_id),
                "research_goal": state.research_goal,
                "session_id": state.metadata.get("session_id") or None,
            }
        )
    active = [item for item in items if item["status"] in {"running", "paused", "pending"}]
    return {"count": len(items), "active_count": len(active), "items": items}


def _venue_from_id(loop_id: str) -> str:
    return "local" if loop_id.startswith("local-") else "umrs"


def _sessions_unavailable(reason: str) -> Dict[str, Any]:
    return {"available": False, "reason": reason, "count": 0, "items": []}


def _session_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "session_id": raw.get("session_id"),
        "title": raw.get("title") or "",
        "status": raw.get("status") or "active",
        "loop_count": len(raw.get("loops") or []),
    }


def _durable_sessions(client: Any, project_id: str | None, location: str) -> Dict[str, Any]:
    """Durable sessions from UMRS (ADR-025) — graceful when offline.

    Only meaningful on UMRS-backed venues; the local venue has no durable
    session store by design. Older gateways lacking the route fall through
    with `available: False` instead of failing the aggregate.
    """
    if location == "local":
        return _sessions_unavailable("local venue has no durable store")
    if not project_id:
        return _sessions_unavailable("no project")
    list_fn = getattr(client, "list_research_sessions", None)
    if list_fn is None:
        return _sessions_unavailable("client has no sessions API")
    try:
        data = list_fn(project_id)
    except Exception as exc:  # noqa: BLE001 - view must tolerate pre-ADR-025 gateways
        return _sessions_unavailable(str(exc))
    items = [_session_item(s) for s in (data.get("sessions") or [])]
    return {"available": True, "count": len(items), "items": items}


def _knowledge_digest(client: Any, project_id: str | None) -> Dict[str, Any]:
    """Digest of the project knowledge graph; degrades gracefully offline."""
    knowledge = getattr(client, "knowledge", None)
    if knowledge is None or not project_id:
        return {
            "available": False,
            "reason": "no knowledge client" if knowledge is None else "no project",
        }
    try:
        graph = knowledge.get_graph(project_id)
    except Exception as exc:  # noqa: BLE001 - view must never fail because the graph is down
        return {"available": False, "reason": str(exc)}
    return {
        "available": True,
        "nodes": len(graph.get("nodes") or []),
        "edges": len(graph.get("edges") or []),
    }
