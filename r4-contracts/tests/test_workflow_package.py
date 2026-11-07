"""The workflow upload contract (r4_contracts.workflow_package, 2026-10-02).

UMRS refuses uploads with these errors and r4 checks them before sending,
so one function decides for both. Mandatory: a description and an explicit
flow graph (every step's depends_on). Advice comes as warnings.
"""

from __future__ import annotations

import copy

import pytest

from r4_contracts.workflow_package import (
    errors_of,
    format_problems,
    warnings_of,
    workflow_package_problems,
)

LLM = "umrs.pipeline_sdk.templates.llm_step:LLMStep"
GOOD = {
    "name": "seg",
    "description": "Segments an image, then measures each region.",
    "source": {"type": "local_path", "path": "."},
    "steps": [
        {"name": "segment", "entrypoint": "steps:Segment", "depends_on": []},
        {"name": "measure", "entrypoint": "steps:Measure", "depends_on": ["segment"]},
        {"name": "approve", "gate": True, "depends_on": ["measure"]},
    ],
}


def wf(**changes):
    data = copy.deepcopy(GOOD)
    data.update(changes)
    return data


def codes(data):
    return [p.code for p in workflow_package_problems(data)]


def test_a_described_explicit_graph_passes():
    assert workflow_package_problems(GOOD) == []


@pytest.mark.parametrize("description", [None, "", "   ", 42])
def test_description_is_mandatory(description):
    data = wf(description=description)
    if description is None:
        del data["description"]
    (problem,) = workflow_package_problems(data)
    assert problem.code == "missing_description" and problem.severity == "error"
    assert "what it produces" in problem.message


def test_every_step_declares_depends_on_and_the_hint_names_the_step_before():
    data = wf()
    del data["steps"][1]["depends_on"]
    (problem,) = workflow_package_problems(data)
    assert problem.code == "missing_depends_on"
    assert problem.where == 'steps[1] "measure".depends_on'
    assert '["segment"]' in problem.message and "[] if it starts the run" in problem.message


def test_the_old_implicit_chain_needs_every_edge_spelled_out():
    data = wf()
    for step in data["steps"]:
        del step["depends_on"]
    assert codes(data) == ["missing_depends_on"] * 3


@pytest.mark.parametrize(
    "deps, code",
    [
        (["nope"], "unknown_dependency"),
        (["measure"], "self_dependency"),
        ("segment", "bad_depends_on"),
        ([1], "bad_depends_on"),
    ],
)
def test_broken_edges(deps, code):
    data = wf()
    data["steps"][1]["depends_on"] = deps
    assert codes(data) == [code]


def test_a_cycle_is_named_by_its_path():
    data = wf()
    data["steps"][0]["depends_on"] = ["approve"]
    (problem,) = workflow_package_problems(data)
    assert problem.code == "dependency_cycle"
    assert "cycle segment → approve → measure → segment" in problem.message


def test_structural_keys_are_reported_in_plain_words():
    data = {"steps": [{"name": "s", "depends_on": []}]}
    assert codes(data) == [
        "missing_name",
        "missing_description",
        "missing_source",
        "missing_entrypoint",
    ]
    assert codes(
        {
            "name": "x",
            "description": "d",
            "source": {"type": "local_path", "path": "."},
            "steps": [],
        }
    ) == ["no_steps"]
    assert codes([]) == ["not_an_object"]


def test_gate_steps_need_no_entrypoint():
    assert "missing_entrypoint" not in codes(GOOD)


def test_legacy_parallel_shorthand_gets_a_hint_beside_the_errors():
    data = wf(execution={"parallel": ["segment", "measure"]})
    for step in data["steps"]:
        del step["depends_on"]
    problems = workflow_package_problems(data)
    assert [p.code for p in warnings_of(problems)] == ["legacy_parallel"]
    assert len(errors_of(problems)) == 3


def _llm_step(config, **extra):
    return {"name": "ask", "entrypoint": LLM, "depends_on": [], "config": config, **extra}


def test_placeholders_nothing_fills_are_advice():
    data = wf(
        steps=[
            _llm_step(
                {
                    "model": "{{model}}",
                    "system_prompt": "Hi {{ params.who }}",
                    "prompt_template": "{{ steps.a.b }} {{ params.q }} {{ x }}",
                }
            )
        ],
        parameter_schema={"type": "object", "properties": {"q": {}, "who": {}}},
    )
    problems = workflow_package_problems(data)
    assert errors_of(problems) == []
    wheres = sorted(p.where for p in problems)
    assert wheres == [
        'steps[0] "ask".config.model',
        'steps[0] "ask".config.prompt_template',
        'steps[0] "ask".config.system_prompt',
    ]
    model = next(p for p in problems if p.where.endswith(".model"))
    assert "leave model out" in model.message


def test_a_custom_class_renders_its_own_placeholders():
    data = wf(
        steps=[
            {
                "name": "s",
                "entrypoint": "mine:Step",
                "depends_on": [],
                "config": {"x": "{{ anything }}"},
            }
        ]
    )
    assert workflow_package_problems(data) == []


def test_prompt_parameters_should_be_declared():
    step = _llm_step(
        {
            "temperature": 0,
            "prompt_template": "{{ params.question }} {{ params.temperature }} {{ params.project_id }}",
        }
    )
    (problem,) = workflow_package_problems(wf(steps=[step]))
    assert problem.code == "undeclared_params" and problem.severity == "warning"
    assert (
        "use question but" in problem.message
    )  # own config key and platform-filled project_id excluded
    declared = wf(
        steps=[step],
        parameter_schema={"type": "object", "properties": {"question": {"type": "string"}}},
    )
    assert workflow_package_problems(declared) == []
    as_items = wf(
        steps=[step], parameters=[{"name": "question", "type": "string", "required": True}]
    )
    assert workflow_package_problems(as_items) == []


def test_errors_come_first_and_format_numbers_them():
    data = wf(description="", steps=[_llm_step({"model": "{{model}}", "prompt_template": "hi"})])
    del data["steps"][0]["depends_on"]
    problems = workflow_package_problems(data)
    assert [p.severity for p in problems] == ["error", "error", "warning"]
    text = format_problems(errors_of(problems))
    assert (
        text.startswith("(1) description: missing")
        and '; (2) steps[0] "ask".depends_on: missing' in text
    )


def test_enum_values_are_lowercase_and_known():
    data = wf(source={"type": "LOCAL_PATH", "path": "."}, execution_mode="IN_PROCESS")
    assert codes(data) == ["bad_source_type", "bad_execution_mode"]
    assert codes(wf(execution_mode="slurm")) == []
