"""Native engine adapter tests (ADR-020 phase 1).

No LLM traffic: compile/validate are pure; the one runtime test uses a
fake sync client through TeamService.
"""

from __future__ import annotations

import pytest
from r4_contracts.team_engine.spec import (
    TEAM_SPEC_VERSION,
    AgentSpec,
    BudgetSpec,
    TaskSpec,
    TeamSpec,
    Topology,
    WorkflowStyle,
)
from r4_teamengine_spi.errors import SpecNotExpressible
from r4_teamengine_spi.testing import make_engine_context

from r4.teams.native import (
    NativeEngine,
    NativePlan,
    _max_iterations,
    spec_to_session_config,
)
from r4.teamup.roles import AgentRole


def _spec(**overrides) -> TeamSpec:
    base = dict(
        spec_version=TEAM_SPEC_VERSION,
        run_name="t",
        project="p",
        task=TaskSpec(objective="optimize fft"),
        agents=(
            AgentSpec(name="pm", role="pm", instructions="manage"),
            AgentSpec(name="pg", role="pg", instructions="code"),
        ),
        topology=Topology(style=WorkflowStyle.FILE_BASED),
    )
    base.update(overrides)
    return TeamSpec(**base)


class TestSpecToConfig:
    def test_roles_map_by_name(self) -> None:
        cfg = spec_to_session_config(_spec())
        assert cfg.roles == [AgentRole.PM, AgentRole.PG]

    def test_pm_injected_when_missing(self) -> None:
        spec = _spec(agents=(AgentSpec(name="pg", role="pg", instructions="code"),))
        cfg = spec_to_session_config(spec)
        assert cfg.roles[0] is AgentRole.PM
        assert AgentRole.PG in cfg.roles

    def test_unknown_agents_fall_back_to_default_set(self) -> None:
        spec = _spec(agents=(AgentSpec(name="reviewer", role="critic", instructions="x"),))
        cfg = spec_to_session_config(spec)
        assert cfg.roles == [AgentRole.PM, AgentRole.SE, AgentRole.PG]

    def test_objective_maps_to_optimization_goal(self) -> None:
        cfg = spec_to_session_config(_spec())
        assert cfg.optimization_goal == "optimize fft"

    def test_the_token_ceiling_stays_on_the_runs_meter(self) -> None:
        """ADR-092 slice 4: max_tokens no longer lowers into the retired
        compute budget (nothing charged it); the run's meter holds it."""
        spec = _spec(budgets=BudgetSpec(max_tokens=250_000))
        cfg = spec_to_session_config(spec)
        assert not hasattr(cfg, "compute_budget")

    def test_sequential_gets_smaller_iteration_cap(self) -> None:
        spec = _spec(topology=Topology(style=WorkflowStyle.SEQUENTIAL))
        assert _max_iterations(spec) == 10
        spec_hier = _spec(topology=Topology(style=WorkflowStyle.HIERARCHICAL))
        assert _max_iterations(spec_hier) == 50

    def test_agent_max_iterations_wins(self) -> None:
        spec = _spec(agents=(AgentSpec(name="pm", role="pm", instructions="m", max_iterations=3),))
        assert _max_iterations(spec) == 3


class TestNativeEngine:
    def test_descriptor_core_tier(self) -> None:
        desc = NativeEngine().descriptor()
        assert desc.name == "native"
        assert desc.trust_tier.value == "core"

    def test_validate_rejects_empty_and_wrong_style(self) -> None:
        engine = NativeEngine()
        issues = engine.validate(_spec(agents=()))
        assert any(i.field == "agents" for i in issues)
        bad_style = engine.validate(_spec(topology=Topology(style=WorkflowStyle.DEBATE)))
        assert any(i.field == "topology.style" for i in bad_style)

    def test_validate_rejects_distributed_resources(self) -> None:
        from r4_contracts.team_engine.spec import ResourceHints

        spec = _spec(resources=ResourceHints(gpu_workers=1))
        issues = NativeEngine().validate(spec)
        assert any(i.field == "resources" for i in issues)

    def test_compile_deterministic(self) -> None:
        engine = NativeEngine()
        spec = _spec()
        a = engine.compile(spec, make_engine_context())
        b = engine.compile(spec, make_engine_context())
        assert a.plan_hash == b.plan_hash

    def test_compile_raises_on_bad_spec(self) -> None:
        engine = NativeEngine()
        with pytest.raises(SpecNotExpressible):
            engine.compile(_spec(agents=()), make_engine_context())

    def test_plan_is_nativeplan(self) -> None:
        plan = NativeEngine().compile(_spec(), make_engine_context())
        assert isinstance(plan, NativePlan)
        assert plan.config.name == "t"


class TestPerRoleModels:
    """D-29 (suggestion 10): per-role models thread from spec to session."""

    def test_model_ref_lands_on_resolved_role(self) -> None:
        spec = _spec(
            agents=(
                AgentSpec(name="pm", role="pm", instructions="manage"),
                AgentSpec(name="pg", role="pg", instructions="code"),
                AgentSpec(
                    name="reviewer",
                    role="cd",
                    instructions="review",
                    model_ref="independent-reviewer-model",
                ),
            )
        )
        cfg = spec_to_session_config(spec)
        assert cfg.role_models == {"cd": "independent-reviewer-model"}

    def test_no_model_refs_means_empty_mapping(self) -> None:
        cfg = spec_to_session_config(_spec())
        assert cfg.role_models == {}  # broker default everywhere, never invented

    def test_role_models_change_the_plan_hash(self) -> None:
        from r4_teamengine_spi.testing import make_engine_context

        ctx = make_engine_context()
        plain = NativePlan(_spec(), ctx)
        with_reviewer = NativePlan(
            _spec(
                agents=(
                    AgentSpec(name="pm", role="pm", instructions="manage"),
                    AgentSpec(name="cd", role="cd", instructions="review", model_ref="rm"),
                )
            ),
            ctx,
        )
        assert plain.plan_hash != with_reviewer.plan_hash

    def test_session_chat_threads_the_role_model(self) -> None:
        """The run's chat calls carry the role's model; absent → None."""
        from r4.teamup.roles import AgentRole
        from r4.teamup.session import TeamupSession, TeamupSessionConfig

        captured = []

        class _FakeCompanion:
            def __init__(self, *a, **kw):
                captured.append(kw.get("model"))

            def on_event(self, cb):
                pass

            def send(self, context, *, extra_system, tools, stream):

                return "PM: proceed"

        cfg = TeamupSessionConfig(
            name="t",
            roles=[AgentRole.PM],
            role_models={"pm": "planner-model"},
        )
        from types import SimpleNamespace

        session = TeamupSession(cfg, SimpleNamespace())
        session._subcompanion_factory = _FakeCompanion
        session._run_agent_turn_sync(AgentRole.PM)
        assert captured == ["planner-model"]

        captured.clear()

        def _factory2(*a, **kw):
            captured.append(kw.get("model"))
            return _FakeCompanion2()

        class _FakeCompanion2:
            def on_event(self, cb):
                pass

            def send(self, context, *, extra_system, tools, stream):
                return "PM: proceed"

        session2 = TeamupSession(
            TeamupSessionConfig(name="t", roles=[AgentRole.PM]), SimpleNamespace()
        )
        session2._subcompanion_factory = _factory2
        session2._run_agent_turn_sync(AgentRole.PM)
        assert captured == [None]  # broker default, honest absence
