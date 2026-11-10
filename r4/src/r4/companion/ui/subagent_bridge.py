"""Read-only durable child inspection for the companion JSONL bridge.

The store row and event-log page are read through their existing owners. This
module never constructs a ``CompanionSession`` or saves anything, so a running
child keeps its sole writer. Event text is redacted and bounded again at the
wire boundary; conversation messages remain the child's full stored history.
"""

from __future__ import annotations

from typing import Any

from ..transcript import _TEXT_CAP, _clip


def _param_text(params: dict[str, Any], key: str) -> str:
    return str(params.get(key) or "")


def _metadata_text(metadata: dict[str, Any], key: str) -> str:
    return str(metadata.get(key) or "")


def child_list_filters(params: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Validate the opt-in child inventory filters; ordinary lists stay unchanged."""
    kind = _param_text(params, "session_kind")
    parent_id = _param_text(params, "parent_session_id")
    agent_id = _param_text(params, "agent_id")
    if kind not in ("", "subagent"):
        raise ValueError("session_kind must be 'subagent' when supplied")
    if kind != "subagent" and (parent_id or agent_id):
        raise ValueError("child lineage filters require session_kind='subagent'")
    filters: dict[str, Any] = {}
    if kind:
        filters["session_kind"] = kind
    if parent_id:
        filters["parent_session_id"] = parent_id
    if agent_id:
        filters["agent_id"] = agent_id
    return kind == "subagent", filters


def _validated_child_record(record: Any, session_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not session_id:
        raise ValueError("subagent session_id is required")
    metadata = record.get("metadata") if isinstance(record, dict) else None
    if not isinstance(metadata, dict) or metadata.get("session_kind") != "subagent":
        raise ValueError(f"subagent session not found: {session_id}")
    if str(record.get("id") or "") != session_id:
        raise ValueError(f"subagent session identity mismatch: {session_id}")
    required = ("parent_session_id", "parent_agent_id", "agent_id")
    if not all(metadata.get(key) for key in required):
        raise ValueError(f"subagent session has incomplete lineage: {session_id}")
    return record, metadata


def _child_record(store: Any, session_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    record = store.get_session_record(session_id) if session_id else None
    return _validated_child_record(record, session_id)


def _declared_tools(metadata: dict[str, Any]) -> list[str] | None:
    tools = metadata.get("declared_tools")
    if tools is None:
        return None
    return [str(tool) for tool in tools] if isinstance(tools, list) else None


def _session_projection(record: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """Expose child transcript and declared lifecycle, not unrelated store columns."""
    return {
        "id": record["id"],
        "name": record["name"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "model": record["model"],
        "session_kind": "subagent",
        "parent_session_id": str(metadata["parent_session_id"]),
        "parent_agent_id": str(metadata["parent_agent_id"]),
        "agent_id": str(metadata["agent_id"]),
        "task": _metadata_text(metadata, "task"),
        "system_prompt": _metadata_text(metadata, "system_prompt"),
        "declared_tools": _declared_tools(metadata),
        "execution_kind": _metadata_text(metadata, "execution_kind"),
        "status": _metadata_text(metadata, "subagent_status"),
        "started_at": _metadata_text(metadata, "started_at"),
        "ended_at": _metadata_text(metadata, "ended_at"),
        "terminal_error": _metadata_text(metadata, "terminal_error"),
        "follow_up_count": int(metadata.get("follow_up_count") or 0),
        "venue": _metadata_text(metadata, "venue"),
        "event_log_error": _metadata_text(metadata, "event_log_error"),
        "messages": record["messages"],
    }


def _inspection_result(
    record: dict[str, Any],
    metadata: dict[str, Any],
    window: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    """Project either transport through the same bounded S3 response contract."""
    events = [_clip(row) for row in window.pop("events")]
    return {
        "session": _session_projection(record, metadata),
        "session_id": str(record["id"]),
        "events": events,
        **window,
        "read_only": True,
        "event_payload_cap": _TEXT_CAP,
        "event_payloads_may_be_abridged": True,
        "source": source,
        "remote_available": source == "remote",
        "remote_reason": "",
    }


def inspect_subagent_record(
    store: Any,
    event_log: Any,
    params: dict[str, Any],
    *,
    remote_client: Any = None,
) -> dict[str, Any]:
    """Read one child locally, or explicitly from UMRS without local fallback."""
    if params.get("remote"):
        from ..subagent_remote import inspect_remote_subagent

        return inspect_remote_subagent(remote_client, params)
    session_id = str(params.get("session_id") or "")
    record, metadata = _child_record(store, session_id)
    window = event_log.page(
        session_id, after_id=params.get("after_id"), limit=params.get("limit", 100)
    )
    return _inspection_result(record, metadata, window, source="local")
