"""What each agent of a plan will be: profile, effective model, tools (ADR-092 D-9).

The approval card and ``r4 team plan run --dry-run`` show the user what a
plan will spend before anything runs. A per-profile list is not enough: a
step or stage may name its own ``model`` over its profile's, and a step
with no ``use`` runs the default profile's tools, which a profile table
never lists. So the surfaces read the effective view computed here, from
the same ``profile_for`` the engine resolves each turn with, and cannot
drift from what runs.

Stdlib and ``r4_contracts`` only: the plan package's import fence (D-2).
"""

from __future__ import annotations

from typing import Any

from r4_contracts.team_engine.plan import (
    AgentPart,
    iter_agent_parts,
    profile_for,
    step_agent_bound,
    step_isolation,
)

DEFAULT_PROFILE_NAME = "(default)"


def effective_agents(
    program: dict[str, Any], default_model: str | None = None
) -> list[dict[str, Any]]:
    """One row per agent step and per agent stage of a map, in plan order,
    those inside a repeat's body or an inlined template included (slice 2).

    Each row is ``{step, stage, label, profile, model, tools, replicas,
    max_items, iterations, isolation}``. ``step`` is the step's path (``hunt.find``
    inside repeat ``hunt``, ``review/find`` inside nested plan ``review``).
    ``model`` is the step's (or stage's) own model, else its profile's, else
    ``default_model``, else None: the precedence the engine applies (D-9:
    the card names every model that can spend). ``replicas`` is the fan-out
    count (1 without one); ``max_items`` is set only for a map stage, which
    runs once per item; ``iterations`` only inside a repeat, which may run
    it that many times. ``isolation`` (slice 5) is where its agents work:
    ``none`` (the project root), ``worktree`` or ``copy`` (one workspace per
    agent; a map stage's is its map's, one per item). Reduce steps and
    stages start no agent and have no row.
    """
    return [_row(program, part, default_model) for part in iter_agent_parts(program)]


def _row(program: dict[str, Any], part: AgentPart, default_model: str | None) -> dict[str, Any]:
    """A stage's label falls back to its map's, then to the map id, as the
    engine labels its turns (D-3)."""
    profile = profile_for(program, part.part)
    step = part.step
    return {
        "step": part.name,
        "stage": part.stage,
        "label": part.part.get("label") or step.get("label") or step.get("id"),
        "profile": _profile_name(part.part.get("use")),
        "model": profile.get("model") or default_model or None,
        "tools": list(profile.get("tools") or ()),
        "replicas": step_agent_bound(part.part),
        "max_items": step.get("max_items") if part.stage is not None else None,
        "iterations": part.times if part.times > 1 else None,
        "isolation": step_isolation(step),
    }


def _profile_name(use: Any) -> str:
    """The profile a step names, or ``(default)`` for the default profile."""
    return use if isinstance(use, str) and use else DEFAULT_PROFILE_NAME
