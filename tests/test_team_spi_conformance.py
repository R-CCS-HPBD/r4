"""Cross-engine SPI conformance suite (ADR-020 §11).

Engine-agnostic contract tests parameterized over engine factories. Every
adapter runs this suite in its own CI; the default factory is the
reference StubEngine from r4_teamengine_spi.testing.

Add engines for local runs via env:
    R4_TEST_TEAM_ENGINES="r4_engine_langgraph:LangGraphEngine,..."

The CORE ``plan`` engine (ADR-092 slice 2) runs the same suite. It declares
only ``WorkflowStyle.PLAN`` (D-2), so the suite's roster spec reaches it as
the degenerate plan (D-3: one agent step per roster agent, in order;
``plan_from_roster``), and its agents are scripted turns of
``MemoryAgentBroker`` in a run directory of their own: an engine-specific
harness, as every adapter's CI supplies its own services.
"""

from __future__ import annotations

import dataclasses
import importlib
import os
import tempfile
from collections.abc import Callable

import pytest

from r4.teams.plan import PlanEngine
from r4.teams.plan.engine import spec_from_roster
from r4_contracts.team_engine.capabilities import TrustTier
from r4_contracts.team_engine.events import (
    TERMINAL_EVENT_KINDS,
    EventKind,
)
from r4_contracts.team_engine.results import CheckpointRef
from r4_contracts.team_engine.spec import (
    TEAM_SPEC_VERSION,
    TaskSpec,
    TeamSpec,
    Topology,
    WorkflowStyle,
)
from r4_teamengine_spi import (
    IncompatibleCheckpoint,
    TeamEngine,
    is_compatible,
)
from r4_teamengine_spi.context import RuntimeServices
from r4_teamengine_spi.testing import (
    MemoryAgentBroker,
    StubEngine,
    make_engine_context,
    make_minimal_spec,
    make_services,
)

EngineFactory = Callable[[], TeamEngine]


def _plan_services() -> RuntimeServices:
    """Scripted agents (every label answers) in a fresh run directory."""
    services = make_services(agents=MemoryAgentBroker({"": {"answer": "ok"}}))
    services.run = dataclasses.replace(services.run, working_dir=tempfile.mkdtemp(prefix="spi-"))
    return services


# Engine name → (the suite's spec as that engine expresses it, its services).
# Engines without an entry take the spec and the in-memory services as they are.
HARNESSES: dict[str, tuple[Callable[[TeamSpec], TeamSpec], Callable[[], RuntimeServices]]] = {
    "plan": (spec_from_roster, _plan_services),
}


def _services_for(engine: TeamEngine) -> RuntimeServices:
    harness = HARNESSES.get(engine.descriptor().name)
    return harness[1]() if harness else make_services()


def _env_factories() -> list[tuple[str, EngineFactory]]:
    out: list[tuple[str, EngineFactory]] = []
    for item in filter(None, os.environ.get("R4_TEST_TEAM_ENGINES", "").split(",")):
        mod_name, _, cls_name = item.partition(":")
        module = importlib.import_module(mod_name)
        cls = getattr(module, cls_name)
        out.append((cls_name, cls))
    return out


ENGINE_FACTORIES: list[tuple[str, EngineFactory]] = [
    ("StubEngine", StubEngine),
    ("PlanEngine", PlanEngine),
    *_env_factories(),
]


@pytest.fixture(
    params=ENGINE_FACTORIES,
    ids=[name for name, _ in ENGINE_FACTORIES],
)
def engine(request: pytest.FixtureRequest) -> TeamEngine:
    factory: EngineFactory = request.param[1]
    return factory()


# ---------------------------------------------------------------------------
# Descriptor / capabilities
# ---------------------------------------------------------------------------


def test_descriptor_is_well_formed(engine: TeamEngine) -> None:
    desc = engine.descriptor()
    assert desc.name, "engine must declare a name"
    assert desc.version, "engine must declare a version"
    assert isinstance(desc.trust_tier, TrustTier)
    assert is_compatible(desc.spi_version), (
        f"engine {desc.name} targets SPI {desc.spi_version}, outside window"
    )
    # capabilities() convenience accessor agrees with descriptor
    assert engine.capabilities() == desc.capabilities


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def test_validate_flags_empty_team(engine: TeamEngine) -> None:
    bad = TeamSpec(
        spec_version=TEAM_SPEC_VERSION,
        run_name="bad",
        project="p",
        task=TaskSpec(objective="x"),
        agents=(),
        topology=Topology(style=WorkflowStyle.SEQUENTIAL),
    )
    issues = engine.validate(bad)
    assert any(i.severity == "error" for i in issues)


# ---------------------------------------------------------------------------
# compile() determinism
# ---------------------------------------------------------------------------


def test_compile_is_deterministic(engine: TeamEngine) -> None:
    spec = _expressible_spec_for(engine)
    ctx = make_engine_context()
    plan_a = engine.compile(spec, ctx)
    plan_b = engine.compile(spec, make_engine_context())
    assert plan_a.plan_hash == plan_b.plan_hash
    assert plan_a.plan_hash, "plan_hash must be non-empty"


def _expressible_spec_for(engine: TeamEngine) -> TeamSpec:
    """A spec the engine under test can definitely express: sequential,
    no special features. Engines that reject it fail conformance. An engine
    with a harness gets the same roster in its own spelling (the plan
    engine: the degenerate plan)."""
    spec = make_minimal_spec(agents=2)
    harness = HARNESSES.get(engine.descriptor().name)
    return harness[0](spec) if harness else spec


# ---------------------------------------------------------------------------
# Full lifecycle: stream -> terminal -> result
# ---------------------------------------------------------------------------


def _assert_well_formed_stream(events) -> None:
    assert events, "engine must emit at least one event"
    assert [e.seq for e in events] == list(range(len(events)))
    assert events[0].kind is EventKind.RUN_STARTED
    assert events[-1].kind in TERMINAL_EVENT_KINDS


def _assert_agent_lifecycle(events, agent_count: int) -> None:
    if events[-1].kind is not EventKind.RUN_COMPLETED:
        return
    started = [e for e in events if e.kind is EventKind.AGENT_STARTED]
    completed = [e for e in events if e.kind is EventKind.AGENT_COMPLETED]
    assert len(started) == agent_count
    assert len(completed) == agent_count


async def test_run_lifecycle_streams_to_terminal(engine: TeamEngine) -> None:
    spec = _expressible_spec_for(engine)
    plan = engine.compile(spec, make_engine_context())
    services = _services_for(engine)
    run = await engine.start(plan, services)
    assert run.run_id == services.run.run_id

    events = [event async for event in run.events()]
    _assert_well_formed_stream(events)
    assert run.state() in ("done", "failed", "cancelled")

    result = await run.result()
    assert result.status in ("success", "partial", "failed", "cancelled")
    if result.status == "success":
        assert result.artifacts or result.summary
    _assert_agent_lifecycle(events, len(spec.agents))


async def test_metrics_are_cross_engine_comparable(engine: TeamEngine) -> None:
    spec = _expressible_spec_for(engine)
    plan = engine.compile(spec, make_engine_context())
    run = await engine.start(plan, _services_for(engine))
    _ = [event async for event in run.events()]
    metrics = await run.metrics()
    assert metrics.llm_calls >= 1
    assert metrics.agent_steps >= 1


# ---------------------------------------------------------------------------
# cancel()
# ---------------------------------------------------------------------------


async def test_cancel_is_idempotent_and_terminal(engine: TeamEngine) -> None:
    spec = _expressible_spec_for(engine)
    plan = engine.compile(spec, make_engine_context())
    run = await engine.start(plan, _services_for(engine))

    terminal = None
    async for event in run.events():
        if event.kind is EventKind.AGENT_STARTED:
            await run.cancel("test cancel")
            await run.cancel("test cancel again")  # idempotent
        terminal = event.kind
    assert terminal in TERMINAL_EVENT_KINDS
    assert run.state() in ("done", "failed", "cancelled")


async def test_cancel_before_start_is_safe(engine: TeamEngine) -> None:
    spec = _expressible_spec_for(engine)
    plan = engine.compile(spec, make_engine_context())
    run = await engine.start(plan, _services_for(engine))
    await run.cancel("preempt")
    assert run.state() in ("cancelled", "pending", "running")


# ---------------------------------------------------------------------------
# checkpoint / resume (capability-gated)
# ---------------------------------------------------------------------------


async def test_checkpoint_resume_roundtrip(engine: TeamEngine) -> None:
    if not engine.capabilities().supports_checkpointing:
        pytest.skip(f"{engine.descriptor().name} does not support checkpointing")
    spec = _expressible_spec_for(engine)
    plan = engine.compile(spec, make_engine_context())
    run = await engine.start(plan, _services_for(engine))

    ref: CheckpointRef | None = None
    async for event in run.events():
        if event.kind is EventKind.AGENT_STARTED and ref is None:
            ref = await run.checkpoint()
    assert ref is not None
    assert ref.engine_name == engine.descriptor().name

    foreign = CheckpointRef(
        run_id=ref.run_id,
        attempt_id=ref.attempt_id,
        engine_name="some-other-engine",
        engine_version="0.0.0",
        state_blob_id=ref.state_blob_id,
        event_offset=ref.event_offset,
        created_at=ref.created_at,
    )
    with pytest.raises(IncompatibleCheckpoint):
        await run.resume(foreign)

    # same-engine ref must not raise
    await run.resume(ref)


# ---------------------------------------------------------------------------
# graph projection
# ---------------------------------------------------------------------------


async def test_graph_projection_covers_agents(engine: TeamEngine) -> None:
    spec = _expressible_spec_for(engine)
    plan = engine.compile(spec, make_engine_context())
    run = await engine.start(plan, _services_for(engine))
    _ = [event async for event in run.events()]
    graph = run.graph()
    # linear projection is the acceptable minimum
    assert len(graph.nodes) >= 1
