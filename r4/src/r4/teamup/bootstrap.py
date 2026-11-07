"""Team bootstrap shared by the companion tool and the bridge (B-family).

Confirmed intent (2026-09-27): a team is started FROM the chat flow — by the
agent at the right moment, or from an armed user toggle — with one config
builder, one root-agent prompt tailor, and one async first iteration.

ADR-092 slice 3: the session itself is opened through ``TeamService`` (the
one team seam, ADR-072), never constructed here.

ADR-092 slice 4: a teamup session is the HPC optimisation preset only. Every
other crew is a plan template (``r4.teams.crews``) and runs through the plan
engine; asking this module for one is refused by name.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .prompt_tailor import tailor_role_prompts
from .session import HPC_CREW, TeamupSessionConfig

if TYPE_CHECKING:
    from .session import TeamupSession


def _goal_slug(goal: str, crew: str, cap: int = 44) -> str:
    """Human-readable session name from the goal text (2026-09-28 —
    timestamps told the user nothing). Bounded, whitespace-collapsed,
    crew-prefixed: `code:add tests for the mcp store`. Never invented words.

    Word-boundary cut with an explicit "…" marker (2026-10-01): slicing
    mid-word minted phantom assignments ("…project a" → an agent went
    checking a project literally named "a" instead of the workspace's
    project; STATE of things class). The full goal ALWAYS reaches agents in
    the context's Goal line — this slug is only the label.
    """
    flat = " ".join(str(goal).split())
    if len(flat) <= cap:
        body = flat
    else:
        cut = flat[:cap].rsplit(" ", 1)[0]
        body = (cut or flat[:cap]).rstrip(" .,:;—-") + "…"
    body = body.rstrip(" .,:;—-") if not body.endswith("…") else body
    return f"{crew}:{body}" if body else f"{crew} team"


def _hpc_config(
    *,
    session_name: str,
    max_iterations: int,
    hardware_target: str,
    goal: str,
    code_path: str | None,
) -> TeamupSessionConfig:
    """The HPC preset's optimisation-shaped config."""
    return TeamupSessionConfig(
        name=session_name,
        max_iterations=max_iterations,
        hardware_target=hardware_target,
        optimization_goal=goal,
        base_code_path=Path(code_path) if code_path else None,
        crew_pack=HPC_CREW,
    )


def _hpc_crew(crew: str | None) -> str:
    """``hpc``, or a named refusal: every other crew is a plan template."""
    from ..teams.crews import crew_name, crew_template

    key = crew_name(crew or HPC_CREW)
    template = crew_template(key)
    if template is not None:
        raise ValueError(
            f"crew {key!r} is the plan template {template!r} (ADR-092 slice 4): run it "
            f"with team_plan_run(template={template!r}) or teamup_start(crew={key!r})"
        )
    return key


def build_team_config(
    goal: str,
    *,
    crew: str = HPC_CREW,
    name: str | None = None,
    max_iterations: int = 6,
    hardware_target: str = "generic",
    code_path: str | None = None,
    models: dict[str, str] | None = None,
    parallel_roles: int = 1,
    driver: str = "manual",
    project_id: str | None = None,
) -> TeamupSessionConfig:
    """Config for the HPC preset; models override wins. Any other crew is a
    plan template and is refused by name (ADR-092 slice 4)."""
    crew_key = _hpc_crew(crew)
    config = _hpc_config(
        session_name=name or _goal_slug(goal, crew_key),
        max_iterations=max_iterations,
        hardware_target=hardware_target,
        goal=goal,
        code_path=code_path,
    )
    for role_value, model in (models or {}).items():
        config.role_models[role_value] = model
    config.parallel_roles = max(1, int(parallel_roles or 1))
    config.driver = driver or "manual"
    # 2026-10-01: owning project for work-pane scoping; None stays untagged.
    config.project_id = project_id or None
    return config


def bootstrap_team(
    client: Any,
    *,
    goal: str,
    crew: str = HPC_CREW,
    name: str | None = None,
    max_iterations: int = 6,
    models: dict[str, str] | None = None,
    hardware_target: str = "generic",
    code_path: str | None = None,
    parallel_roles: int = 1,
    driver: str = "manual",
    model: str | None = None,
    project_id: str | None = None,
    service: Any = None,
) -> tuple[TeamupSession, dict[str, Any]]:
    """Create + persist + tailor a team session. Returns (session, tailor).

    ``model`` rides the calling surface's active model (public sources
    included); the CHAT model, never the spawn-profile default.

    The session is opened by ``TeamService.open_team_session`` (ADR-072 one
    team seam, ADR-092 slice 3): this function never constructs the runtime
    itself. ``service`` is the caller's ``TeamService``, carrying the lead's
    handles (permission manager, model, usage hooks); without one, a plain
    service over ``client`` is used.
    """
    config = build_team_config(
        goal,
        crew=crew,
        name=name,
        max_iterations=max_iterations,
        hardware_target=hardware_target,
        code_path=code_path,
        models=models,
        parallel_roles=parallel_roles,
        driver=driver,
        project_id=project_id,
    )
    if service is None:
        from ..teams.service import TeamService

        service = TeamService(client)
    session = service.open_team_session(config, chat_client=client)
    tailor = tailor_role_prompts(session, client, model=model)
    return session, tailor
