"""The effective view of a plan's agents (ADR-092 D-9).

The approval card and the CLI dry run read ``effective_agents``: one row per
agent step or map stage with the model and tools it will actually run with,
so a step-level model or a default-profile step is never hidden behind a
per-profile table.
"""

from __future__ import annotations

from r4_contracts.team_engine.plan import DEFAULT_PROFILE_TOOLS

from r4.teams.plan import load_template, spec_from_plan
from r4.teams.plan.summary import effective_agents


def _program() -> dict:
    raw = {
        "plan_version": 1,
        "name": "s",
        "description": "a plan to summarise",
        "budget": {"max_tokens": 1000},
        "agents": {
            "rev": {"instructions": "review", "tools": ["file_read"], "model": "m-rev"},
            "plain": {"instructions": "plain"},
        },
        "params": {"items": {"type": "array", "default": [1, 2]}},
        "phases": [
            {
                "id": "p1",
                "steps": [
                    {
                        "id": "fan",
                        "kind": "agent",
                        "use": "rev",
                        "label": "fanned",
                        "prompt": "x",
                        "fanout": {"count": 3},
                    },
                    {"id": "solo", "kind": "agent", "prompt": "y", "model": "m-step"},
                    {
                        "id": "m",
                        "kind": "map",
                        "over": "params.items",
                        "max_items": 7,
                        "label": "mapped",
                        "stages": [
                            {"kind": "agent", "use": "rev", "prompt": "a", "model": "m-stage"},
                            {"kind": "reduce", "op": "flatten"},
                            {"kind": "agent", "use": "plain", "label": "last", "prompt": "b"},
                        ],
                    },
                    {"id": "r", "kind": "reduce", "op": "flatten", "input": "steps.m.output"},
                ],
            }
        ],
    }
    spec, problems = spec_from_plan(raw, {}, project="p")
    assert problems == [] and spec is not None
    return spec.plan


def test_every_agent_row_names_its_effective_model_tools_and_multiplicity():
    rows = effective_agents(_program(), default_model="m-default")

    assert rows == [
        {
            "step": "fan",
            "stage": None,
            "label": "fanned",
            "profile": "rev",
            "model": "m-rev",
            "tools": ["file_read"],
            "replicas": 3,
            "max_items": None,
            "iterations": None,  # slice 2: set only inside a repeat
            "isolation": "none",  # slice 5: where its agents work
        },
        {
            # no ``use``: the default profile's tools, and the step's own model wins
            "step": "solo",
            "stage": None,
            "label": "solo",
            "profile": "(default)",
            "model": "m-step",
            "tools": list(DEFAULT_PROFILE_TOOLS),
            "replicas": 1,
            "max_items": None,
            "iterations": None,  # slice 2: set only inside a repeat
            "isolation": "none",  # slice 5: where its agents work
        },
        {
            # a stage's model wins over its profile's; the label falls back to the map's
            "step": "m",
            "stage": 0,
            "label": "mapped",
            "profile": "rev",
            "model": "m-stage",
            "tools": ["file_read"],
            "replicas": 1,
            "max_items": 7,
            "iterations": None,  # slice 2: set only inside a repeat
            "isolation": "none",  # slice 5: where its agents work
        },
        {
            # the profile names no model: the default model runs it
            "step": "m",
            "stage": 2,
            "label": "last",
            "profile": "plain",
            "model": "m-default",
            "tools": list(DEFAULT_PROFILE_TOOLS),
            "replicas": 1,
            "max_items": 7,
            "iterations": None,  # slice 2: set only inside a repeat
            "isolation": "none",  # slice 5: where its agents work
        },
    ]


def test_without_a_default_model_an_unnamed_model_is_none():
    rows = effective_agents(_program())

    assert [(r["step"], r["stage"], r["model"]) for r in rows] == [
        ("fan", None, "m-rev"),
        ("solo", None, "m-step"),
        ("m", 0, "m-stage"),
        ("m", 2, None),
    ]


def test_an_expanded_template_lists_its_pattern_minted_agents():
    spec, problems = spec_from_plan(
        load_template("adversarial-review"), {"target": "src", "skeptics": 3}, project="p"
    )
    assert problems == [] and spec is not None

    rows = effective_agents(spec.plan, default_model="m-small")

    # the pattern's skeptics are a map stage: one per finding, three replicas each
    assert [(r["step"], r["stage"], r["profile"], r["replicas"], r["max_items"]) for r in rows] == [
        ("find", None, "finder", 1, None),
        ("verify__votes", 0, "skeptic", 3, 5),
    ]
    assert {r["model"] for r in rows} == {"m-small"}
