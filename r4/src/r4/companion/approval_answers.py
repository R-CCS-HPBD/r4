"""What an approval ask may be answered with, and what an answer means
(ADR-104 D-3). Shared by every surface that asks a person (the console
bridge, ACP), so the words and the downgrades are the same everywhere.

The ask carries ``options``; the console renders exactly those. The answer
is one of :data:`DECISIONS`; older words (``yes``/``no``, ``y``/``a``/``n``)
are accepted. An answer the ask did not offer is downgraded: an ``always``
the ask did not allow approves this call only; anything unrecognised
rejects.

ADR-104 amendment 1 (2026-11-03): "always for this project" is no longer an
answer. One keypress wrote a durable, argument-blind allow rule that every
later session of the project inherited; a standing rule is deliberate
configuration (``~/.config/r4/permissions.json``, ``/settings rules``). An
older console's ``always_project`` / ``p`` counts as ``always`` (this session).
"""

from __future__ import annotations

from typing import Any

from .permissions import NEVER_AUTO_APPROVE

ONCE = "once"
ALWAYS = "always"
REJECT = "reject"
DECISIONS: tuple[str, ...] = (ONCE, ALWAYS, REJECT)

_WORDS: dict[str, str] = {
    "yes": ONCE,
    "y": ONCE,
    "allow": ONCE,
    "a": ALWAYS,
    "always_session": ALWAYS,
    "p": ALWAYS,
    "always_project": ALWAYS,
    "no": REJECT,
    "n": REJECT,
    "deny": REJECT,
}


def approval_options(tool_name: str, *, fenced: bool) -> list[str]:
    """The answers this ask offers: a never-auto tool or a fenced ask is a
    per-call decision (once / reject); otherwise also "always" (this
    session)."""
    if fenced or tool_name in NEVER_AUTO_APPROVE:
        return [ONCE, REJECT]
    return [ONCE, ALWAYS, REJECT]


def normalize_decision(raw: Any, options: list[str] | tuple[str, ...]) -> str:
    """The decision an answer stands for, limited to what the ask offered."""
    word = str(raw or "").strip().lower()
    decision = _WORDS.get(word, word)
    if decision not in DECISIONS:
        return REJECT
    if decision in options:
        return decision
    return ONCE if decision == ALWAYS else REJECT


def approves(decision: str) -> bool:
    return decision in (ONCE, ALWAYS)


__all__ = [
    "ALWAYS",
    "DECISIONS",
    "ONCE",
    "REJECT",
    "approval_options",
    "approves",
    "normalize_decision",
]
