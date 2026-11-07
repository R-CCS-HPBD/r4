"""LangGraph adapter unit tests (ADR-020 phase 3).

The generic SPI conformance suite already covers lifecycle; these tests
pin adapter-specific semantics: edge validation, DAG construction,
deterministic compile, entry-point registration.
"""

from __future__ import annotations

import pytest

from r4_contracts.team_engine.spec import (
    TEAM_SPEC_VERSION,
    AgentSpec,
    EdgeSpec,
    TaskSpec,
    TeamSpec,
    Topology,
    WorkflowStyle,
)
from r4_engine_langgraph import LangGraphEngine
from r4_engine_langgraph.engine import _adjacency, _has_cycle
from r4_teamengine_spi.errors import SpecNotExpressible
from r4_teamengine_spi.testing import make_engine_context


def _spec(**overrides) -> TeamSpec:
    base = {
        "spec_version": TEAM_SPEC_VERSION,
        "run_name": "lg-test",
        "project": "p",
        "task": TaskSpec(objective="t"),
        "agents": (
            AgentSpec(name="a", role="planner", instructions="plan"),
            AgentSpec(name="b", role="coder", instructions="code"),
            AgentSpec(name="c", role="reviewer", instructions="review"),
        ),
        "topology": Topology(style=WorkflowStyle.DAG),
    }
    base.update(overrides)
    return TeamSpec(**base)


class TestValidate:
    def test_conditional_edges_rejected_in_v1(self) -> None:
        spec = _spec(edges=(EdgeSpec(source="a", target="b", condition="x > 1"),))
        issues = LangGraphEngine().validate(spec)
        assert any("conditional" in i.message for i in issues)

    def test_unknown_endpoints_rejected(self) -> None:
        spec = _spec(edges=(EdgeSpec(source="a", target="ghost"),))
        issues = LangGraphEngine().validate(spec)
        assert any("unknown endpoint" in i.message for i in issues)

    def test_reserved_names_allowed(self) -> None:
        spec = _spec(edges=(EdgeSpec(source="$start", target="a"),))
        issues = LangGraphEngine().validate(spec)
        assert not any("unknown endpoint" in i.message for i in issues)

    def test_cycles_rejected(self) -> None:
        spec = _spec(
            edges=(
                EdgeSpec(source="a", target="b"),
                EdgeSpec(source="b", target="a"),
            )
        )
        issues = LangGraphEngine().validate(spec)
        assert any("cycle" in i.message for i in issues)

    def test_wrong_style_rejected(self) -> None:
        spec = _spec(topology=Topology(style=WorkflowStyle.DEBATE))
        issues = LangGraphEngine().validate(spec)
        assert any(i.field == "topology.style" for i in issues)

    def test_compile_raises_on_bad_spec(self) -> None:
        spec = _spec(agents=())
        with pytest.raises(SpecNotExpressible):
            LangGraphEngine().compile(spec, make_engine_context())


class TestGraphShape:
    def test_default_chain(self) -> None:
        adjacency = _adjacency(_spec())
        assert adjacency == {"a": ("b",), "b": ("c",)}

    def test_explicit_fanout(self) -> None:
        spec = _spec(
            edges=(
                EdgeSpec(source="a", target="b"),
                EdgeSpec(source="a", target="c"),
            )
        )
        adjacency = _adjacency(spec)
        assert adjacency["a"] == ("b", "c")
        assert not _has_cycle(spec)


class TestCompile:
    def test_deterministic_plan_hash(self) -> None:
        engine = LangGraphEngine()
        a = engine.compile(_spec(), make_engine_context())
        b = engine.compile(_spec(), make_engine_context())
        assert a.plan_hash == b.plan_hash

    def test_distinct_specs_distinct_hashes(self) -> None:
        engine = LangGraphEngine()
        a = engine.compile(_spec(), make_engine_context())
        b = engine.compile(_spec(run_name="other"), make_engine_context())
        assert a.plan_hash != b.plan_hash

    def test_descriptor_verified_tier(self) -> None:
        desc = LangGraphEngine().descriptor()
        assert desc.name == "langgraph"
        assert desc.capabilities.supports_graph_execution
        assert WorkflowStyle.DAG in desc.capabilities.workflow_styles


class TestDiscovery:
    def test_engine_registered_via_entry_point(self) -> None:
        from r4_teamengine_spi.loader import load

        engine = load("langgraph")
        assert engine.descriptor().name == "langgraph"
