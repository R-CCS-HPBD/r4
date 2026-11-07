"""The session summary helpers are plain static/class methods (2026-10-24).

``@classmethod`` sat above ``_turn_count_of``'s ``@staticmethod`` since
6b12eeba2. Python <= 3.12 chained the two by accident; 3.13 removed that, so
every session listing on a 3.13 client failed with "_turn_count_of() takes 1
positional argument but 2 were given" ("server (frontend) venue unavailable"
on New session -> server). The descriptor checks fail on the old code under
any Python version, not only 3.13.
"""

from __future__ import annotations

import inspect

from r4.companion.session_store import CompanionSessionStore


def test_turn_count_of_is_a_plain_staticmethod() -> None:
    raw = inspect.getattr_static(CompanionSessionStore, "_turn_count_of")
    assert isinstance(raw, staticmethod)
    assert CompanionSessionStore._turn_count_of({"turn_seq": 3}) == 3
    assert CompanionSessionStore._turn_count_of({"turn_seq": -1}) is None
    assert CompanionSessionStore._turn_count_of({}) is None


def test_summary_from_row_is_a_classmethod_and_carries_turn_count() -> None:
    raw = inspect.getattr_static(CompanionSessionStore, "_summary_from_row")
    assert isinstance(raw, classmethod)
    row = ("sid-1", "My chat", "2026-10-24T00:00:00Z", "2026-10-24T00:01:00Z", "m", "[]")
    summary = CompanionSessionStore._summary_from_row(row, metadata={"turn_seq": 2})
    assert summary["id"] == "sid-1"
    assert summary["turn_count"] == 2
