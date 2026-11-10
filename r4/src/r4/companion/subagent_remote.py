"""Read-only owner inspection and lease-only persistence for remote children.

The owner reader always goes to UMRS when requested: it never falls back to a
local/parent row. The venue writer carries only the existing rail lease; UMRS
proves and owns every parent/child/backend binding server-side.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode

from ..errors import APIError
from ..venue_rail import VenueRailError


def inspect_remote_subagent(client: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Read a remote child through the same S3 projection, with no local read."""
    session_id = str(params.get("session_id") or "")
    if not session_id:
        raise ValueError("subagent session_id is required")
    if client is None:
        raise ValueError("remote subagent inspection unavailable (no authenticated client)")
    after_id, limit = _replay_params(params)
    query = urlencode({"after_id": after_id, "limit": limit})
    url = _owner_url(client, session_id, query)
    answer = _remote_get(client, url, session_id)
    return _remote_projection(answer, session_id)


def _replay_params(params: dict[str, Any]) -> tuple[int, int]:
    raw_after = params.get("after_id")
    try:
        after_id = int(raw_after or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("after_id must be a non-negative integer") from exc
    if isinstance(raw_after, bool) or after_id < 0:
        raise ValueError("after_id must be a non-negative integer")
    limit = max(1, min(int(params.get("limit", 100)), 200))
    return after_id, limit


def _owner_url(client: Any, session_id: str, query: str) -> str:
    from .session_store import journal_prefix

    child = quote(session_id, safe="")
    return f"{journal_prefix(client)}/companion/sessions/{child}/subagent?{query}"


def _remote_get(client: Any, url: str, session_id: str) -> dict[str, Any]:
    try:
        answer = client.get(url, timeout=15)
    except APIError as exc:
        if exc.status_code == 404:
            raise ValueError(f"remote subagent session not found: {session_id}") from exc
        detail = str(exc.message or type(exc).__name__)[:200]
        raise ValueError(f"remote subagent inspection refused: {detail}") from exc
    except Exception as exc:
        raise ValueError(f"remote subagent inspection unavailable ({type(exc).__name__})") from exc
    if not isinstance(answer, dict):
        raise ValueError("remote subagent inspection returned an invalid response")
    return answer


def _remote_projection(answer: dict[str, Any], session_id: str) -> dict[str, Any]:
    from .ui.subagent_bridge import _inspection_result, _validated_child_record

    record, metadata = _validated_child_record(answer.get("record"), session_id)
    events = answer.get("events")
    if not isinstance(events, list) or any(not isinstance(row, dict) for row in events):
        raise ValueError("remote subagent inspection returned an invalid event page")
    for event in events:
        _validate_event_scope(event, record, metadata)
    window = {
        "events": list(events),
        "next_after_id": int(answer.get("next_after_id") or 0),
        "has_more": bool(answer.get("has_more")),
        "total_events": int(answer.get("total_events") or 0),
        "limit": int(answer.get("limit") or 100),
    }
    return _inspection_result(record, metadata, window, source="remote")


def _validate_event_scope(
    event: dict[str, Any], record: dict[str, Any], metadata: dict[str, Any]
) -> None:
    session_id = str(record["id"])
    raw_data = event.get("data")
    data = raw_data if isinstance(raw_data, dict) else {}
    expected = {
        "child_session_id": session_id,
        "parent_session_id": metadata["parent_session_id"],
        "parent_agent_id": metadata["parent_agent_id"],
        "agent_id": metadata["agent_id"],
    }
    identity_ok = str(event.get("session_id") or "") == session_id
    lineage_ok = all(str(data.get(key) or "") == str(value) for key, value in expected.items())
    if not identity_ok or not lineage_ok:
        raise ValueError(f"remote subagent event lineage mismatch: {session_id}")


class VenueSubagentRemote:
    """Thin child adapter over the lease-authenticated venue rail."""

    def __init__(self, rail: Any) -> None:
        """Store only the injected lease-rail transport."""
        self.rail = rail

    def put_snapshot(self, record: dict[str, Any]) -> None:
        metadata = record.get("metadata") or {}
        child = str(record.get("id") or "")
        answer = self.rail.put_subagent_snapshot(child, payload=json.dumps(record))
        expected = {
            "session_id": child,
            "parent_session_id": str(metadata.get("parent_session_id") or ""),
            "agent_id": str(metadata.get("agent_id") or ""),
        }
        if not isinstance(answer, dict) or any(
            answer.get(key) != value for key, value in expected.items()
        ):
            raise VenueRailError(409, "subagent identity receipt does not match")

    def append_event(self, child: str, event: dict[str, Any]) -> None:
        source_id = int(event["id"])
        payload = {key: value for key, value in event.items() if key != "id"}
        answer = self.rail.append_subagent_event(child, source_event_id=source_id, event=payload)
        if not isinstance(answer, dict) or int(answer.get("source_event_id") or 0) != source_id:
            raise VenueRailError(409, "subagent event identity receipt does not match")
        if str(answer.get("session_id") or "") != child:
            raise VenueRailError(409, "subagent event identity receipt does not match")


__all__ = ["VenueSubagentRemote", "inspect_remote_subagent"]
