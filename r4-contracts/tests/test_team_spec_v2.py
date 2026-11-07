"""TeamSpec v2 (ADR-092 D-1): a ``PLAN`` workflow style and an optional
``TeamSpec.plan`` carrying the bound, expanded program. The change is
additive: a v1 roster spec constructs exactly as before (positionally too),
so native and LangGraph callers are untouched.
"""

from __future__ import annotations

import dataclasses

import pytest

from r4_contracts.team_engine import (
    TEAM_SPEC_VERSION,
    AgentSpec,
    BudgetSpec,
    ResourceHints,
    TaskSpec,
    TeamSpec,
    Topology,
    WorkflowStyle,
)
from r4_contracts.team_engine import spec as spec_module
from r4_contracts.team_engine.plan import TEAM_PLAN_VERSION, prepare_plan

_V1_FIELDS = (
    "spec_version",
    "run_name",
    "project",
    "task",
    "agents",
    "topology",
    "edges",
    "budgets",
    "policy_ref",
    "resources",
    "context",
)
_V1_STYLES = {"sequential", "dag", "graph", "hierarchical", "debate", "swarm", "file_based"}


def _roster_spec(version: int = 1) -> TeamSpec:
    return TeamSpec(
        spec_version=version,
        run_name="r",
        project="p",
        task=TaskSpec(objective="o"),
        agents=(AgentSpec(name="pm", role="pm", instructions="lead"),),
        topology=Topology(style=WorkflowStyle.SEQUENTIAL),
    )


def test_the_team_spec_version_is_2() -> None:
    assert TEAM_SPEC_VERSION == 2
    assert spec_module.TEAM_SPEC_VERSION == TEAM_SPEC_VERSION


def test_plan_is_a_workflow_style() -> None:
    assert WorkflowStyle.PLAN.value == "plan"
    assert WorkflowStyle("plan") is WorkflowStyle.PLAN
    assert WorkflowStyle.PLAN == "plan"


def test_the_v1_styles_are_unchanged() -> None:
    assert {style.value for style in WorkflowStyle} == _V1_STYLES | {"plan"}


def test_plan_is_the_last_field_and_defaults_to_none() -> None:
    assert tuple(f.name for f in dataclasses.fields(TeamSpec)) == (*_V1_FIELDS, "plan")
    assert _roster_spec().plan is None


def test_a_v1_roster_spec_constructs_unchanged() -> None:
    spec = _roster_spec(1)
    assert spec.spec_version == 1
    assert spec.edges == ()
    assert spec.budgets == BudgetSpec()
    assert spec.policy_ref == "default"
    assert spec.resources == ResourceHints()
    assert spec.context == {}
    assert spec.plan is None


def test_a_v1_positional_construction_still_works() -> None:
    spec = TeamSpec(
        1,
        "r",
        "p",
        TaskSpec("o"),
        (),
        Topology(WorkflowStyle.DAG),
        (),
        BudgetSpec(),
        "default",
        ResourceHints(),
        {},
    )
    assert spec.topology.style is WorkflowStyle.DAG
    assert spec.plan is None


def test_a_plan_spec_carries_the_prepared_program() -> None:
    raw = {
        "plan_version": TEAM_PLAN_VERSION,
        "name": "one",
        "description": "one agent",
        "budget": {"max_tokens": 1000},
        "phases": [
            {"id": "p", "title": "P", "steps": [{"id": "a", "kind": "agent", "prompt": "hi"}]}
        ],
    }
    program, problems = prepare_plan(raw)
    assert problems == []
    spec = TeamSpec(
        spec_version=TEAM_SPEC_VERSION,
        run_name="r",
        project="p",
        task=TaskSpec(objective="o"),
        agents=(),
        topology=Topology(style=WorkflowStyle.PLAN),
        budgets=BudgetSpec(max_tokens=1000),
        plan=program,
    )
    assert spec.plan is program
    assert spec.topology.style is WorkflowStyle.PLAN


def test_the_spec_stays_frozen() -> None:
    spec = _roster_spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.plan = {}  # type: ignore[misc]
