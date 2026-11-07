"""D-29 / ADR-072 / ADR-092 slice 4: the design's research team on the ONE team seam.

The four roles (planner, experimenter, analyst, reviewer) are the research
crew, and since slice 4 that crew is the ``research-round`` plan template:
``r4 team launch --team research`` builds its plan spec, which resolves to
the plan engine. They no longer re-skin the HPC preset's PM/SE/PG/CD slots
(``research_team_spec`` and ``research_role_prompts`` are gone). A roster
spec still lowers into the native engine with its prompts and models, and
per-role models/prompts survive a snapshot round trip.
"""

from __future__ import annotations

from types import SimpleNamespace

from r4_contracts.team_engine.spec import (
    TEAM_SPEC_VERSION,
    AgentSpec,
    TaskSpec,
    TeamSpec,
    Topology,
    WorkflowStyle,
)

from r4.research_loop.team import RESEARCH_ROLES
from r4.teams.native import spec_to_session_config
from r4.teamup.roles import AgentRole
from r4.teamup.session import TeamupSession, TeamupSessionConfig


def test_native_lowering_carries_prompts_and_models():
    spec = TeamSpec(
        spec_version=TEAM_SPEC_VERSION,
        run_name="r",
        project="p",
        task=TaskSpec(objective="g"),
        agents=(
            AgentSpec(name="pm", role="pm", instructions="You plan."),
            AgentSpec(name="cd", role="CD", instructions="You review.", model_ref="rev-model"),
        ),
        topology=Topology(style=WorkflowStyle.FILE_BASED),
    )
    cfg = spec_to_session_config(spec)
    assert cfg.role_models == {"cd": "rev-model"}
    assert cfg.role_prompts == {"pm": "You plan.", "cd": "You review."}


def test_session_uses_the_role_prompt_over_the_builtin():
    systems = []

    class _FakeCompanion:
        def __init__(self, *a, **kw):
            return None

        def on_event(self, cb):
            pass

        def send(self, context, *, extra_system, tools, stream):
            systems.append(extra_system)
            return "PM: proceed"

    class _Client:
        def __init__(self):
            self._factory = _FakeCompanion

        def chat(self, message=None, **kwargs):
            return SimpleNamespace(content="PM: proceed", tool_calls=[], usage={})

    cfg = TeamupSessionConfig(
        name="t", roles=[AgentRole.PM], role_prompts={"pm": "You plan research."}
    )
    s1 = TeamupSession(cfg, _Client())
    s1._subcompanion_factory = _FakeCompanion
    s1._run_agent_turn_sync(AgentRole.PM)
    plain = TeamupSessionConfig(name="t", roles=[AgentRole.PM])
    s2 = TeamupSession(plain, _Client())
    s2._subcompanion_factory = _FakeCompanion
    s2._run_agent_turn_sync(AgentRole.PM)
    assert systems[0] == "You plan research."
    assert "HPC" in systems[1]  # built-in prompt when none is given

    assert systems[0] == "You plan research."
    assert "HPC" in systems[1]  # built-in prompt when none is given


def test_role_models_and_prompts_survive_a_snapshot_round_trip():
    cfg = TeamupSessionConfig(
        name="t", roles=[AgentRole.PM], role_models={"pm": "m"}, role_prompts={"pm": "p"}
    )
    again = TeamupSessionConfig.from_dict(cfg.to_dict())
    assert (again.role_models, again.role_prompts) == ({"pm": "m"}, {"pm": "p"})


def test_launch_team_research_builds_the_research_crew_plan():
    """``--team research`` is the research crew: the research-round plan, with
    the research roles' own prompts, resolved to the plan engine."""
    from argparse import Namespace

    from r4._cli.teamup import _team_launch_spec
    from r4.teams.service import TeamService

    args = Namespace(
        spec=None,
        team="research",
        name="run",
        goal="does X hold?",
        roles="pm,se,pg",
        max_iterations=7,
        max_tokens=5000,
    )
    spec = _team_launch_spec(args, "proj")
    assert spec.topology.style is WorkflowStyle.PLAN
    assert spec.plan["name"] == "research-round"
    assert spec.plan["_bound_params"]["objective"] == "does X hold?"
    assert (spec.budgets.max_tokens, spec.project) == (5000, "proj")
    reviewer = spec.plan["agents"]["reviewer"]["instructions"]
    assert reviewer == RESEARCH_ROLES["PR"]["system_prompt"]
    assert {agent.role for agent in spec.agents} == {"plan-profile"}  # no HPC slot re-skinned
    assert TeamService(SimpleNamespace()).resolve(spec).engine_name == "plan"
