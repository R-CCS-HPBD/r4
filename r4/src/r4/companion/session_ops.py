"""On-demand session operations shared by the bridge and ACP rails (C4).

`compact_history` mirrors the bridge's session.compact semantics: force-run
compaction regardless of context-limit pressure (user trigger), rewrite the
session's message list with the compacted slice, and emit the CONTEXT_COMPACT
event when an agent is attached. Store save is the CALLER's job (rails differ:
bridge saves store.jsonl, ACP saves via session.save()).
"""

from __future__ import annotations

from typing import Any

from .session_models import SessionMessage

DEFAULT_KEEP_RECENT = 6


def _compactable_payload(session: Any, keep_recent: int) -> list[dict[str, Any]] | None:
    """Build the conversation payload or None when already short enough."""
    payload = [{"role": message.role, "content": message.content} for message in session.messages]
    non_system = [message for message in payload if message["role"] != "system"]
    if len(non_system) <= keep_recent:
        return None
    return payload


def _apply_compacted_messages(session: Any, compacted: list[dict[str, Any]]) -> None:
    """Write the compacted slice back during the session (persisted by caller)."""
    session.messages = [
        SessionMessage(role=message["role"], content=message.get("content") or "")
        for message in compacted
        if message.get("content")
    ]


def _emit_compact_mark(agent: Any, kept_messages: int) -> None:
    """Emit CONTEXT_COMPACT on the agent channel if one is attached."""
    emit = getattr(agent, "_emit", None)
    if not callable(emit):
        return
    try:
        from .agent import CompanionEventType

        emit(
            CompanionEventType.CONTEXT_COMPACT,
            "Conversation compacted.",
            {"kept_messages": kept_messages},
        )
    except Exception:
        pass


def compact_history(session: Any, keep_recent: int = DEFAULT_KEEP_RECENT) -> dict[str, Any]:
    """Force-run compaction on a session's history in place.

    Returns a result dict describing the operation:
    - changed=False when nothing to compact (small history).
    - changed=True with from/to message counts on success.
    """
    keep_recent = max(1, int(keep_recent if keep_recent is not None else DEFAULT_KEEP_RECENT))
    payload = _compactable_payload(session, keep_recent)
    if payload is None:
        return {
            "changed": False,
            "reason": "nothing to compact",
            "message_count": len(session.messages),
            "keep_recent": keep_recent,
        }

    old_count = len(session.messages)
    compacted = session.context.compact(payload, keep_recent=keep_recent)
    _apply_compacted_messages(session, compacted)
    _emit_compact_mark(getattr(session, "agent", None), len(session.messages))
    return {
        "changed": True,
        "from": old_count,
        "to": len(session.messages),
        "keep_recent": keep_recent,
    }
