"""Shared `session/cost` payload (C3) for the companion transport rails.

One cost payload contract used by:
- the ACP session rail (webview + v3) — forwarded as `session/cost`
- the companion JSONL bridge (tui-js) — emitted as an event {"type": "cost"}

Data origin: `client.session_cost` (one `TurnCost` per LLM call). Because one
user turn typically contains several LLM calls, emissions delta-sum turns
appended since the last emission via a cursor tracked by the emitter.
"""

from __future__ import annotations

from typing import Any

DEFAULT_CONTEXT_LIMIT_TOKENS = 128_000


def _last_prompt_tokens(session: Any) -> int:
    records = getattr(session, "usage_records", None) or []
    if not records:
        return 0
    for type_name in ("prompt_tokens",):
        value = getattr(records[-1], type_name, 0) or 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    return 0


def _context_limit(session: Any) -> int:
    context = getattr(session, "context", None)
    limit = int(getattr(context, "max_context_tokens", 0) or 0)
    return limit or DEFAULT_CONTEXT_LIMIT_TOKENS


def _session_cost_tracker(session: Any) -> Any | None:
    """Extract the per-client SessionCost tracker (None when unavailable)."""
    client = getattr(session, "client", None)
    return getattr(client, "session_cost", None)


def _summarize_turns(turns: list[Any], cursor: int) -> tuple[int, int, float, str, bool]:
    """Delta-summarize turns appended since ``cursor``.

    Returns ``(next_cursor, turn_tokens, cost_usd_turn, model, price_known)``.
    """
    next_cursor = len(turns)
    safe_cursor = max(0, min(cursor, next_cursor))
    new_turns = turns[safe_cursor:]
    last = turns[-1]

    turn_tokens = sum(t.total_tokens for t in new_turns) or int(last.total_tokens or 0)
    cost_usd_turn = sum(t.cost_usd for t in new_turns)
    model = str(getattr(last, "model", "") or "")
    price_known = any(t.cost_usd > 0 for t in turns)
    return next_cursor, int(turn_tokens), float(cost_usd_turn), model, bool(price_known)


def cost_event_payload(session: Any, cursor: int) -> tuple[dict[str, Any] | None, int]:
    """Build one control event envelope for the just-finished turn.

    Returns ``(envelope, next_cursor)`` — envelope is ``None`` when no LLM
    usage was recorded yet, in which case the cursor is returned unchanged.
    The envelope is the canonical ``{"type": "cost", "payload": {...}}``
    control-schema shape (same wardrobe as ``budget_state`` on every rail).
    """
    from .control_schema import SessionCost
    from .control_schema import to_dict as control_event_to_dict

    cost_state = _session_cost_tracker(session)
    if cost_state is None:
        return None, cursor
    turns = list(getattr(cost_state, "turns", None) or [])
    if not turns:
        return None, cursor

    next_cursor, turn_tokens, cost_usd_turn, model, price_known = _summarize_turns(turns, cursor)
    event = SessionCost(
        session_id=str(getattr(session, "session_id", "") or ""),
        model=model,
        turn_tokens=turn_tokens,
        total_tokens=int(cost_state.total_tokens),
        cost_usd_turn=round(cost_usd_turn, 6),
        cost_usd_total=round(float(cost_state.total_cost_usd), 6),
        price_known=price_known,
        context_used_tokens=_last_prompt_tokens(session),
        context_limit_tokens=_context_limit(session),
    )
    return control_event_to_dict(event), next_cursor
