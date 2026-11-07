"""``knowledge.save``: keep a chat turn in project knowledge (K1.6 ``/save``).

The v3 console's ``/save`` (``commands/save-command.ts``) and its message
action (``s`` on a focused message) call this verb; the text is read here
from the session's own messages by turn id (``r4.companion.turn_save``),
never sent by the console or re-typed by the model. A remote write: UMRS
scans the text and tiers it (K-1).
"""

from __future__ import annotations

from typing import Any

from ..turn_save import receipt_line, request_from_params, save_turn


def knowledge_save(session: Any, params: dict[str, Any]) -> dict[str, Any]:
    """The receipt of one save, with the console line to print."""
    result = save_turn(session, session.client, request_from_params(params))
    return {**result, "line": receipt_line(result)}


class TurnSaveBridgeMixin:
    """``knowledge.save`` on the companion bridge."""

    def knowledge_save(self, params: dict[str, Any]) -> dict[str, Any]:
        return knowledge_save(self.session, params)  # type: ignore[attr-defined]


__all__ = ["TurnSaveBridgeMixin", "knowledge_save"]
