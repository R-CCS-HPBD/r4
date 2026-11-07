"""Plan templates with their r4-side references resolved (ADR-092 D-10, slice 2).

The plan package (``teams/plan/``) imports only the standard library and the
two contract packages (D-2), so a built-in template cannot import a prompt
that lives elsewhere in r4. A template names it instead, as data:

    "instructions": {"ref": "research_role:PR"}

and this module, outside the fence, resolves the reference when the template
is loaded. The ``research-round`` template reads the research roles' prompts
this way from ``r4.research_loop.team.RESEARCH_ROLES``: one source, never a
copy. Since slice 4 the crew templates (``code``, ``paper-writing``,
``kakenhi-report``, ``peer-review``) read their profiles' prompts the same
way, ``{"ref": "crew_role:<crew>/<profile>"}``, from ``r4.teams.crews``.
An unresolved reference reaching the validator is refused there
(``instructions`` must be a string), so a template loaded without this
module fails by name, never runs with a placeholder.

Callers that load templates for a run (``r4 team plan``, the chat tools, a
nested ``plan`` step) go through :func:`load_template` and
:func:`resolve_plan_source` here.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .plan import templates as _raw

REF_KEY = "ref"
_RESEARCH_ROLE = "research_role"
_CREW_ROLE = "crew_role"


def load_template(name: str) -> dict[str, Any]:
    """A built-in template with every instruction reference resolved."""
    return resolve_refs(_raw.load_template(name))


def resolve_plan_source(ref: str | Path | dict[str, Any]) -> dict[str, Any]:
    """:func:`r4.teams.plan.templates.resolve_plan_source`, references resolved."""
    return resolve_refs(_raw.resolve_plan_source(ref))


def resolve_refs(plan: dict[str, Any]) -> dict[str, Any]:
    """``plan`` with each profile's ``{"ref": …}`` instructions replaced by the
    text it names. Raises ``ValueError`` naming an unknown reference."""
    agents = plan.get("agents") if isinstance(plan, dict) else None
    if not isinstance(agents, dict):
        return plan
    out = copy.deepcopy(plan)
    for name, profile in out["agents"].items():
        if isinstance(profile, dict) and isinstance(profile.get("instructions"), dict):
            profile["instructions"] = instruction_text(profile["instructions"], f"agents.{name}")
    return out


def instruction_text(value: dict[str, Any], where: str = "instructions") -> str:
    """The text a ``{"ref": "<source>:<key>"}`` names.

    ``research_role:<RL|ED|DA|PR>`` (or the design names planner,
    experimenter, analyst, reviewer) is that research role's system prompt;
    ``crew_role:<crew>/<profile>`` is a crew profile's (``r4.teams.crews``).
    """
    ref = value.get(REF_KEY)
    if set(value) != {REF_KEY} or not isinstance(ref, str) or ":" not in ref:
        raise ValueError(f"{where}.instructions: a reference is {{'ref': '<source>:<key>'}}")
    source, _, key = ref.partition(":")
    if source == _RESEARCH_ROLE:
        return _research_role_prompt(key, where)
    if source == _CREW_ROLE:
        return _crew_role_prompt(key, where)
    raise ValueError(f"{where}.instructions: unknown reference source {source!r}")


def _crew_role_prompt(key: str, where: str) -> str:
    from .crews import crew_role_prompt

    crew, _, profile = key.partition("/")
    try:
        return crew_role_prompt(crew, profile)
    except KeyError as exc:
        raise ValueError(f"{where}.instructions: {exc.args[0]}") from exc


def _research_role_prompt(key: str, where: str) -> str:
    from ..research_loop.team import DESIGN_ROLE_NAMES, RESEARCH_ROLES

    by_design = {design: abbr for abbr, design in DESIGN_ROLE_NAMES.items()}
    abbr = key.upper() if key.upper() in RESEARCH_ROLES else by_design.get(key)
    if abbr is None:
        known = sorted(RESEARCH_ROLES) + sorted(by_design)
        raise ValueError(f"{where}.instructions: no research role {key!r}; one of {known}")
    return str(RESEARCH_ROLES[abbr]["system_prompt"])
