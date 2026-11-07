"""Prompt-prefix stability for vLLM automatic prefix caching (wave 80).

vLLM reuses KV-cache blocks only for a token prefix that is byte-identical
to an earlier request. A companion turn therefore keeps two things fixed for
the whole session:

* the **system message** — the base prompt plus session-static context
  (workspace root, research spine, skill catalog, R4.md briefing, HPC sites);
* the **tools array** — every tool the session can serve, sorted by name.

Everything rebuilt per turn (project state, active skill bodies, memory,
session context, harness guides, active endpoints, the endpoint warning,
plan-mode instructions) rides one ``<turn_context>`` block appended to the
turn's user message, after the stable prefix. Mode restrictions that used to
remove tools from the array (plan mode, ``write_requires_plan``, a skill's
tool allowlist) are enforced when a call executes: the refused call returns a
tool error naming the reason.

The block is wire-only: the stored conversation keeps the user's own text,
so the next turn's request starts with the system message, every earlier
message, and this turn's user text — the shared prefix vLLM can reuse.

``R4_COMPANION_STABLE_PREFIX=0`` restores the pre-wave-80 layout, and so does
``R4_COMPANION_PROMPT_HYGIENE=0``: that switch promises the request origin/main
built before the TB 4.0 fixes, byte for byte (``test_agent_profile.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from . import agent_profile
from .env_switch import env_switch

STABLE_PREFIX_ENV = "R4_COMPANION_STABLE_PREFIX"

TURN_CONTEXT_OPEN = "<turn_context>"
TURN_CONTEXT_CLOSE = "</turn_context>"
_PREAMBLE = "Session state for this turn, refreshed by R4 every turn (not typed by the user)."

PLAN_MODE_REFUSAL = "plan mode is active — only read-only tools run until the plan is approved"
PLAN_GATE_REFUSAL = (
    "this session requires an approved plan before write tools run (write_requires_plan)"
)
SKILL_REFUSAL = "the active skills limit this turn's tools and this tool is not among them"


def stable_prefix_enabled() -> bool:
    """The wave-80 layout is on unless either switch restores the old one."""
    return agent_profile.hygiene_enabled() and env_switch(STABLE_PREFIX_ENV, True)


def split_fitted(fitted: str, stable: str) -> tuple[str, str]:
    """``(stable, reminder)`` from a budget-fitted ``join_parts(stable, reminder)``.

    The fit truncates the tail, so the reminder shrinks first; once the stable
    part itself is cut, the whole fitted text is the system block.
    """
    if fitted.startswith(stable):
        return stable, fitted[len(stable) :].lstrip("\n")
    return fitted, ""


def join_parts(*parts: str) -> str:
    """Non-empty parts joined by a blank line (the prompt assembly separator)."""
    return "\n\n".join(part for part in parts if part)


def with_turn_context(user_message: str, context: str) -> str:
    """The user message the model reads: the user's text, then the turn block."""
    if not context.strip():
        return user_message
    block = f"{TURN_CONTEXT_OPEN}\n{_PREAMBLE}\n\n{context}\n{TURN_CONTEXT_CLOSE}"
    return f"{user_message}\n\n{block}" if user_message else block


def project_state_block(text: str) -> str:
    """The D-28 ``<project_state>`` block, empty when there is no snapshot."""
    return f"<project_state>\n{text}\n</project_state>" if text else ""


def refusals_outside(
    names: Iterable[str], permitted: frozenset[str] | None, reason: str
) -> dict[str, str]:
    """``{name: reason}`` for each name outside ``permitted`` (None: no limit)."""
    if permitted is None:
        return {}
    return {name: reason for name in names if name not in permitted}


def merge_refusals(*maps: Mapping[str, str] | None) -> dict[str, str]:
    """One refusal map; the first reason given for a name wins."""
    merged: dict[str, str] = {}
    for refusal_map in maps:
        for name, reason in (refusal_map or {}).items():
            merged.setdefault(name, reason)
    return merged


def callable_tools_note(wire_names: Iterable[str], refusals: Mapping[str, str]) -> str:
    """Reminder line naming the tools that run this turn, when a mode refuses some."""
    if not refusals:
        return ""
    callable_names = ", ".join(name for name in wire_names if name not in refusals) or "none"
    reasons = "; ".join(sorted(set(refusals.values())))
    return (
        f"[Tool limit] Only these tools run this turn: {callable_names}. "
        f"Calls to the other tools are refused ({reasons})."
    )


def refusal_text(name: str, reason: str) -> str:
    """The tool error a refused call returns (model-facing)."""
    return f"Error: tool '{name}' cannot run this turn: {reason}."


# The compaction request (wave 80) is the turn's own prefix plus this user
# message, so the server reuses the cached prefix instead of a fresh prompt.
COMPACTION_INSTRUCTION = (
    "[compaction] Summarise the conversation above for an AI assistant that needs "
    "to continue helping the user. Preserve: key decisions, file paths, job IDs, "
    "error messages, and next steps. Be concise. Reply with the summary only; "
    "do not call tools."
)
