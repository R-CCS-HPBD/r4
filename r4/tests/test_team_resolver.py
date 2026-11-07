"""Registry + resolver + TeamService tests (ADR-020 phase 2).

LLM-free: TeamService submit uses a FakeClient; native runs execute with
iteration caps so tests stay fast and local.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from r4_contracts.team_engine.spec import (
    TEAM_SPEC_VERSION,
    AgentSpec,
    TaskSpec,
    TeamSpec,
    Topology,
    WorkflowStyle,
)
from r4_teamengine_spi.errors import UnknownEngineError

from r4.teams.registry import EngineRegistry
from r4.teams.resolver import resolve_engine
from r4.teams.service import TeamService


class FakeChatResponse:
    def __init__(self, content: str) -> None:
        self.content = content
        self.usage = {"prompt_tokens": 10, "completion_tokens": len(content.split())}


class FakeClient:
    """Sync client double: canned answers, records prompts."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def chat(self, message=None, **_kwargs) -> FakeChatResponse:
        self.calls.append(message or "")
        return FakeChatResponse(f"ACK[{len(self.calls)}]: proceed")

    def list_models(self):
        return [{"id": "fake-model"}]


def _spec(**overrides) -> TeamSpec:
    base = {
        "spec_version": TEAM_SPEC_VERSION,
        "run_name": "resolver-test",
        "project": "p",
        "task": TaskSpec(objective="check plumbing"),
        "agents": (
            AgentSpec(name="pm", role="pm", instructions="manage", max_iterations=1),
            AgentSpec(name="pg", role="pg", instructions="code"),
        ),
        "topology": Topology(style=WorkflowStyle.FILE_BASED),
    }
    base.update(overrides)
    return TeamSpec(**base)


def _assert_run_completed(events, handle) -> None:
    from r4_contracts.team_engine.events import EventKind

    assert events and events[0].kind is EventKind.RUN_STARTED
    assert events[-1].kind is EventKind.RUN_COMPLETED
    assert handle.state() == "done"


def _assert_langgraph_completed(events, handle) -> None:
    assert handle.engine_name == "langgraph"
    _assert_run_completed(events, handle)
    started = {e.actor.name for e in events if e.kind.value == "agent_started"}
    assert started == {"a", "b"}


def _assert_run_artifacts(tmp_path: Path, handle) -> None:
    run_dir = tmp_path / handle.run_id
    assert (run_dir / "events.jsonl").exists()
    assert (run_dir / "resolution.json").exists()


class TestRegistry:
    def test_native_registered_builtin(self) -> None:
        registry = EngineRegistry()
        names = registry.names()
        assert "native" in names
        engine = registry.get("native")
        assert engine.descriptor().name == "native"

    def test_list_reports_availability(self) -> None:
        rows = EngineRegistry().list()
        native = next(r for r in rows if r.descriptor.name == "native")
        assert native.available

    def test_unknown_engine_raises(self) -> None:
        with pytest.raises(UnknownEngineError):
            EngineRegistry().get("nope")


class TestResolver:
    def test_site_default_is_native(self) -> None:
        res = resolve_engine(EngineRegistry(), _spec())
        assert res.engine_name == "native"
        assert "site default" in res.reason

    def test_explicit_request_honored(self) -> None:
        res = resolve_engine(EngineRegistry(), _spec(), requested="native")
        assert res.reason.startswith("explicit request")

    def test_unknown_requested_fails_closed(self) -> None:
        with pytest.raises(UnknownEngineError):
            resolve_engine(EngineRegistry(), _spec(), requested="mada")

    def test_capability_gap_noted_in_reason(self) -> None:
        spec = _spec(topology=Topology(style=WorkflowStyle.GRAPH, max_parallel=4))
        res = resolve_engine(EngineRegistry(), spec, requested="native")
        assert "warning" in res.reason


class TestTeamServiceSubmit:
    async def test_submit_native_run_completes(self, tmp_path: Path, monkeypatch) -> None:
        # ADR-092 slice 9: native role turns go through the run's AgentBroker
        # (scripted here); the FakeClient is never called directly.
        from r4_teamengine_spi.testing import MemoryAgentBroker

        from r4.teams import service as team_service

        monkeypatch.setattr(
            team_service.TeamService,
            "_agent_broker",
            lambda _self, _spec, _run_id, meter: MemoryAgentBroker({"": "done"}, meter=meter),
        )
        service = TeamService(FakeClient(), run_root=tmp_path)
        handle = await service.submit(_spec(), engine="native")
        assert handle.engine_name == "native"

        events = [e async for e in handle.events()]
        _assert_run_completed(events, handle)
        result = await handle.result()
        assert result.status == "success"
        _assert_run_artifacts(tmp_path, handle)

    async def test_submit_validation_failure_raises(self, tmp_path: Path) -> None:
        service = TeamService(FakeClient(), run_root=tmp_path)
        bad = _spec(agents=())
        with pytest.raises(ValueError, match="not expressible"):
            await service.submit(bad, engine="native")

    async def test_run_dir_under_root(self, tmp_path: Path) -> None:
        service = TeamService(FakeClient(), run_root=tmp_path)
        handle = await service.submit(_spec(), engine="native")
        _ = [e async for e in handle.events()]
        assert handle.run_dir.parent == tmp_path


class TestTeamServiceLangGraph:
    """Foreign engine through the same service path (ADR-020 phase 3)."""

    async def test_submit_langgraph_run_completes(self, tmp_path: Path) -> None:
        pytest.importorskip("r4_engine_langgraph")
        service = TeamService(FakeClient(), run_root=tmp_path)
        spec = _spec(
            topology=Topology(style=WorkflowStyle.DAG),
            agents=(
                AgentSpec(name="a", role="planner", instructions="plan"),
                AgentSpec(name="b", role="coder", instructions="code"),
            ),
        )
        handle = await service.submit(spec, engine="langgraph")
        events = [e async for e in handle.events()]
        _assert_langgraph_completed(events, handle)

        result = await handle.result()
        assert result.status == "success"
        metrics = await handle.metrics()
        assert metrics.llm_calls == 2

    async def test_langgraph_spec_on_native_rejected(self, tmp_path: Path) -> None:
        service = TeamService(FakeClient(), run_root=tmp_path)
        spec = _spec(topology=Topology(style=WorkflowStyle.DAG))
        with pytest.raises(ValueError, match="not expressible"):
            await service.submit(spec, engine="native")


# ---------------------------------------------------------------------------
# ADR-092 D-2: resolver step 3, the spec-driven capability match
# ---------------------------------------------------------------------------


def _descriptor(name: str, tier, styles, **caps):
    from r4_contracts.team_engine.capabilities import EngineCapabilities, EngineDescriptor

    return EngineDescriptor(
        name=name,
        version="0.1.0",
        spi_version="0.1",
        trust_tier=tier,
        capabilities=EngineCapabilities(workflow_styles=tuple(styles), **caps),
    )


class _DescriptorRegistry:
    """Only what resolve_engine reads: name -> descriptor of available
    engines, and which of them are builtins (shipped with r4)."""

    def __init__(self, *descriptors, builtins=("native", "plan")) -> None:
        self._descriptors = {d.name: d for d in descriptors}
        self._builtins = frozenset(builtins)

    def descriptors(self):
        return dict(self._descriptors)

    def builtin_names(self):
        return self._builtins


def _native_descriptor():
    from r4.teams.native import NativeEngine

    return NativeEngine().descriptor()


def _plan_descriptor():
    from r4_contracts.team_engine.capabilities import TrustTier

    return _descriptor(
        "plan",
        TrustTier.CORE,
        (WorkflowStyle.PLAN,),
        supports_parallel_agents=True,
        supports_graph_execution=True,
        supports_multi_model=True,
    )


def _langgraph_descriptor():
    from r4_contracts.team_engine.capabilities import TrustTier

    return _descriptor(
        "langgraph",
        TrustTier.VERIFIED,
        (WorkflowStyle.DAG, WorkflowStyle.GRAPH, WorkflowStyle.SEQUENTIAL),
        supports_graph_execution=True,
        supports_parallel_agents=True,
    )


def _plan_spec() -> TeamSpec:
    return _spec(
        agents=(AgentSpec(name="finder", role="plan-profile", instructions="find"),),
        topology=Topology(style=WorkflowStyle.PLAN, max_parallel=4),
        plan={"plan_version": 1, "name": "p", "phases": []},
    )


def _registry_with_plan():
    return _DescriptorRegistry(_native_descriptor(), _plan_descriptor(), _langgraph_descriptor())


class TestCapabilityMatch:
    def test_plan_spec_resolves_to_plan_by_capability_match(self) -> None:
        res = resolve_engine(_registry_with_plan(), _plan_spec())
        assert res.engine_name == "plan"
        assert res.reason.startswith("capability match (native lacks style:plan")

    def test_plan_spec_with_incapable_project_default(self) -> None:
        res = resolve_engine(_registry_with_plan(), _plan_spec(), project_default="langgraph")
        assert res.engine_name == "plan"
        assert "langgraph lacks style:plan" in res.reason

    def test_plan_spec_on_real_registry(self) -> None:
        registry = EngineRegistry()
        if "plan" not in registry.descriptors():
            pytest.skip("r4.teams.plan.engine not importable yet")
        res = resolve_engine(registry, _plan_spec())
        assert res.engine_name == "plan"
        assert "capability match" in res.reason

    def test_roster_specs_still_resolve_to_native(self) -> None:
        # The research crew is a plan template since ADR-092 slice 4
        # (test_research_team_seam pins it resolving to plan); a roster
        # spec of named agents is the other example here.
        named = _spec(
            agents=(
                AgentSpec(name="planner", role="pm", instructions="plan"),
                AgentSpec(name="reviewer", role="cd", instructions="review"),
            )
        )
        for registry in (_registry_with_plan(), EngineRegistry()):
            for spec in (_spec(), named):
                res = resolve_engine(registry, spec)
                assert (res.engine_name, res.reason) == ("native", "site default")

    def test_dag_spec_without_capable_core_engine_falls_back_as_before(self) -> None:
        """langgraph can run it, but a capability gap never routes to a non-CORE
        plugin: the default stays, and native's validate refuses it by name."""
        spec = _spec(topology=Topology(style=WorkflowStyle.DAG))
        res = resolve_engine(_registry_with_plan(), spec)
        assert (res.engine_name, res.reason) == ("native", "site default")

    def test_hint_note_kept_on_capability_match(self) -> None:
        res = resolve_engine(_registry_with_plan(), _plan_spec(), hint="native")
        assert res.engine_name == "plan"
        assert res.reason.startswith("hint 'native' incapable/disallowed, fell back; ")

    def test_unavailable_default_matches_a_capable_core_engine(self) -> None:
        res = resolve_engine(_DescriptorRegistry(_plan_descriptor()), _plan_spec())
        assert res.engine_name == "plan"
        assert res.reason == "capability match (native unavailable)"

    def test_nothing_usable_still_raises(self) -> None:
        with pytest.raises(UnknownEngineError):
            resolve_engine(_DescriptorRegistry(_langgraph_descriptor()), _plan_spec())

    def test_explicit_request_is_never_overridden(self) -> None:
        res = resolve_engine(_registry_with_plan(), _plan_spec(), requested="native")
        assert res.engine_name == "native"
        assert "warning" in res.reason

    def test_a_plugin_that_declares_core_is_never_picked_by_capability(self) -> None:
        """Review finding 32: the tier is the plugin's own claim, so step 3
        picks builtins only and a roster spec native cannot fully express
        keeps resolving to native (D-2: nothing about roster specs moves)."""
        registry = _DescriptorRegistry(
            _native_descriptor(), _plan_descriptor(), _swarm_descriptor()
        )
        res = resolve_engine(registry, _parallel_roster_spec())
        assert (res.engine_name, res.reason) == ("native", "site default")

    def test_a_registry_that_names_no_builtins_never_matches_by_capability(self) -> None:
        class _Bare:
            def descriptors(self):
                return {"plan": _plan_descriptor()}

        with pytest.raises(UnknownEngineError):
            resolve_engine(_Bare(), _plan_spec())


def _swarm_descriptor(name: str = "aaa-swarm"):
    """A third-party engine that declares itself CORE and can run anything."""
    from r4_contracts.team_engine.capabilities import TrustTier

    return _descriptor(
        name,
        TrustTier.CORE,
        (WorkflowStyle.FILE_BASED, WorkflowStyle.SEQUENTIAL, WorkflowStyle.PLAN),
        supports_parallel_agents=True,
    )


def _parallel_roster_spec() -> TeamSpec:
    """A roster spec native lacks a capability for (teamup YAML can set it)."""
    return _spec(topology=Topology(style=WorkflowStyle.FILE_BASED, max_parallel=2))


class _EntryPoint:
    """An installed plugin whose engine is a swarm named like the entry point."""

    def __init__(self, name: str) -> None:
        self.name = name

    def load(self):
        name = self.name

        class _SwarmEngine:
            def descriptor(self):
                return _swarm_descriptor(name)

        return _SwarmEngine


def _registry_with_plugins(monkeypatch, *names: str, policy=None) -> EngineRegistry:
    from r4.teams import registry as registry_mod

    monkeypatch.setattr(
        registry_mod, "discover_entry_points", lambda: {n: _EntryPoint(n) for n in names}
    )
    return EngineRegistry(policy)


class TestBuiltinOnlyCapabilityMatch:
    """Review finding 32 on the real registry, with an installed plugin."""

    @pytest.mark.parametrize(
        "policy", [None, {"trust_overrides": {"aaa-swarm": "verified"}}], ids=["none", "demoted"]
    )
    def test_roster_spec_stays_on_native_with_a_core_claiming_plugin(
        self, monkeypatch, policy
    ) -> None:
        registry = _registry_with_plugins(monkeypatch, "aaa-swarm", policy=policy)
        assert "aaa-swarm" in registry.descriptors()
        res = resolve_engine(registry, _parallel_roster_spec())
        assert (res.engine_name, res.reason) == ("native", "site default")

    def test_the_plugin_still_runs_when_asked_by_name(self, monkeypatch) -> None:
        registry = _registry_with_plugins(monkeypatch, "aaa-swarm")
        res = resolve_engine(registry, _parallel_roster_spec(), requested="aaa-swarm")
        assert (res.engine_name, res.reason) == ("aaa-swarm", "explicit request")

    def test_builtin_names_exclude_an_entry_point_that_shadows_one(self, monkeypatch) -> None:
        """_instantiate loads the entry point first, so a plugin named
        ``native`` is the plugin: not a builtin, and not labelled one."""
        registry = _registry_with_plugins(monkeypatch, "aaa-swarm", "native")
        assert {"aaa-swarm", "native"}.isdisjoint(registry.builtin_names())
        labels = {row.descriptor.name: row.factory for row in registry.list()}
        assert (labels["aaa-swarm"], labels["native"]) == ("aaa-swarm", "native")
        assert labels.get("plan", "builtin") == "builtin"


class TestRegistryPlanBuiltin:
    def test_broken_optional_builtin_is_skipped_not_fatal(self, monkeypatch, caplog) -> None:
        from r4.teams import registry as registry_mod

        monkeypatch.setattr(
            registry_mod, "_OPTIONAL_BUILTINS", {"plan": "r4.teams.no_such_engine:PlanEngine"}
        )
        with caplog.at_level("WARNING", logger="r4.teams.registry"):
            registry = EngineRegistry()
        assert "native" in registry.names()
        assert "plan" not in registry._builtin
        assert "team engine 'plan' skipped" in caplog.text

    def test_plan_is_a_builtin_when_importable(self) -> None:
        import importlib.util

        try:
            spec = importlib.util.find_spec("r4.teams.plan.engine")
        except ImportError:
            spec = None
        if spec is None:
            pytest.skip("r4.teams.plan.engine not written yet")
        registry = EngineRegistry()
        assert {"native", "plan"} <= set(registry.names())

    def test_plan_resolves_as_a_builtin_with_no_installed_entry_points(self, monkeypatch) -> None:
        """ADR-092 slice-1 open item (2026-10-11): the ``plan`` entry point
        appears in installed metadata only after ``uv pip install -e r4``. Until
        then the builtin registration is the path that runs, so it is pinned
        with entry-point discovery empty: listed, labelled builtin, picked by
        capability match and instantiable."""
        from r4.teams import registry as registry_mod

        monkeypatch.setattr(registry_mod, "discover_entry_points", lambda: {})
        registry = EngineRegistry()
        assert "plan" in registry.builtin_names()
        labels = {row.descriptor.name: row.factory for row in registry.list()}
        assert labels["plan"] == "builtin"
        resolution = resolve_engine(registry, _plan_spec())
        assert resolution.engine_name == "plan"
        assert registry.get("plan").descriptor().name == "plan"

    def test_r4s_own_entry_points_are_its_builtins_after_a_fresh_install(self, monkeypatch) -> None:
        """CI 2026-10-12 (main dadf058f): a fresh ``pip install -e r4`` declares
        ``native`` and ``plan`` on the group with the builtins' own targets.
        Those are the builtins, so ``plan`` still wins the capability match;
        only a foreign target under a builtin's name shadows it."""
        from importlib.metadata import EntryPoint

        from r4.teams import registry as registry_mod

        own = {
            name: EntryPoint(name, target, "r4.team_engines")
            for name, target in registry_mod._BUILTIN_TARGETS.items()
        }
        monkeypatch.setattr(registry_mod, "discover_entry_points", lambda: own)
        registry = EngineRegistry()
        labels = {row.descriptor.name: row.factory for row in registry.list()}
        resolution = resolve_engine(registry, _plan_spec())
        assert {
            "builtins": registry.builtin_names() >= {"native", "plan"},
            "labels": (labels["native"], labels["plan"]),
            "engine": resolution.engine_name,
        } == {"builtins": True, "labels": ("builtin", "builtin"), "engine": "plan"}

    def test_a_foreign_target_under_a_builtin_name_still_shadows_it(self, monkeypatch) -> None:
        from importlib.metadata import EntryPoint

        from r4.teams import registry as registry_mod

        foreign = {"plan": EntryPoint("plan", "elsewhere.engine:PlanEngine", "r4.team_engines")}
        monkeypatch.setattr(registry_mod, "discover_entry_points", lambda: foreign)
        assert "plan" not in EngineRegistry().builtin_names()

    def test_the_plan_entry_point_is_declared_for_installed_metadata(self) -> None:
        """The declaration an install turns into metadata (read as text: the
        test interpreter predates ``tomllib``)."""
        pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
        section = pyproject.split('[project.entry-points."r4.team_engines"]', 1)[1]
        block = section.split("\n[", 1)[0]
        assert 'plan = "r4.teams.plan.engine:PlanEngine"' in block.splitlines()
