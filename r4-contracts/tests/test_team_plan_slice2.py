"""ADR-092 slice 2 grammar: ``repeat``, ``gate``, nested ``plan``, the new
reducers and the four slice-2 patterns.

Each construct is closed and statically bounded (D-4): its own keys checked,
its agent bound computed before any spend, and every count static once
params bind. Expansions are pinned as data, so a pattern's semantics live in
the IR the interpreter runs, not in prose.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from r4_contracts.team_engine.plan import (
    GATE_TOOL,
    MAX_REPEAT_ITERATIONS,
    PlanProblem,
    apply_reduce,
    count_steps,
    independence_warnings,
    iter_agent_parts,
    iter_gates,
    plan_summary,
    prepare_plan,
    static_agent_bound,
    validate_plan,
)
from r4_contracts.team_engine.plan_patterns import expand_patterns


def _plan(*steps: dict[str, Any], **top: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "plan_version": 1,
        "name": "t",
        "description": "slice 2",
        "budget": {"max_tokens": 1000},
        "phases": [
            {
                "id": "p",
                "title": "P",
                "steps": [{"id": "find", "kind": "agent", "prompt": "find"}, *steps],
            }
        ],
    }
    plan.update(top)
    return plan


def _codes(problems: list[PlanProblem]) -> set[tuple[str, str]]:
    return {(p.code, p.path) for p in problems}


def _has(problems: list[PlanProblem], code: str, path: str) -> bool:
    return (code, path) in _codes(problems)


S1 = "phases[0].steps[1]"


def _repeat(**over: Any) -> dict[str, Any]:
    step = {
        "id": "hunt",
        "kind": "repeat",
        "max_iterations": 3,
        "seed": "steps.find.output",
        "collect": "steps.more.output",
        "steps": [
            {
                "id": "more",
                "kind": "agent",
                "prompt": "known: {{seen|json}} round {{iteration}}",
                "fanout": {"count": 2},
            }
        ],
    }
    step.update(over)
    return step


# ── repeat ──────────────────────────────────────────────────────────────────


def test_a_repeat_validates_and_its_bound_is_iterations_times_its_body() -> None:
    program, problems = prepare_plan(_plan(_repeat()))
    assert problems == []
    assert static_agent_bound(program) == 1 + 3 * 2
    assert count_steps(program) == 3  # find, hunt and its one body step


@pytest.mark.parametrize("bad", [0, MAX_REPEAT_ITERATIONS + 1, "3", None, True])
def test_a_repeat_needs_a_static_iteration_bound(bad: Any) -> None:
    problems = prepare_plan(_plan(_repeat(max_iterations=bad)))[1]
    assert _has(problems, "required", f"{S1}.max_iterations")


def test_a_repeat_body_runs_primitives_only() -> None:
    gate = {"id": "ask", "kind": "gate", "class": "cost", "prompt": "ok?"}
    problems = prepare_plan(_plan(_repeat(steps=[gate])))[1]
    assert _has(problems, "bad_kind", f"{S1}.steps[0].kind")


def test_collect_must_read_a_step_of_the_repeat() -> None:
    problems = prepare_plan(_plan(_repeat(collect="steps.find.output")))[1]
    assert _has(problems, "bad_ref", f"{S1}.collect")


def test_seen_and_iteration_exist_only_inside_the_body() -> None:
    after = {"id": "after", "kind": "agent", "prompt": "{{seen}} {{iteration}}"}
    problems = prepare_plan(_plan(_repeat(), after))[1]
    assert _has(problems, "bad_ref", "phases[0].steps[2].prompt")


def test_a_body_step_is_invisible_after_the_repeat() -> None:
    after = {"id": "after", "kind": "agent", "prompt": "{{steps.more.output}}"}
    problems = prepare_plan(_plan(_repeat(), after))[1]
    assert _has(problems, "bad_ref", "phases[0].steps[2].prompt")
    ok = {"id": "after", "kind": "agent", "prompt": "{{steps.hunt.output.items}}"}
    assert prepare_plan(_plan(_repeat(), ok))[1] == []


def test_a_writer_in_a_repeat_body_fan_out_is_refused() -> None:
    raw = _plan(_repeat(), agents={"w": {"tools": ["file_write"]}})
    raw["phases"][0]["steps"][1]["steps"][0]["use"] = "w"
    assert _has(prepare_plan(raw)[1], "parallel_writer", f"{S1}.steps[0]")


# ── gate ────────────────────────────────────────────────────────────────────


def _gate(**over: Any) -> dict[str, Any]:
    step = {
        "id": "ask",
        "kind": "gate",
        "class": "cost",
        "prompt": "Spend more on {{steps.find.output|json}}?",
    }
    step.update(over)
    return step


def test_a_gate_validates_with_an_adr_066_class() -> None:
    program, problems = prepare_plan(_plan(_gate()))
    assert problems == []
    assert static_agent_bound(program) == 1
    assert list(iter_gates(program)) == [("ask", program["phases"][0]["steps"][1])]
    assert plan_summary(program)["gates"] == [{"gate": "ask", "class": "cost", "when": None}]


@pytest.mark.parametrize("cls", ["", "costly", None, "approve"])
def test_a_gate_class_outside_adr_066_is_refused(cls: Any) -> None:
    assert _has(prepare_plan(_plan(_gate(**{"class": cls})))[1], "bad_value", f"{S1}.class")


def test_a_gate_needs_a_question_and_reads_only_what_exists() -> None:
    problems = prepare_plan(_plan(_gate(prompt="", when="steps.ghost.output")))[1]
    assert _has(problems, "required", f"{S1}.prompt")
    assert _has(problems, "bad_ref", f"{S1}.when")


def test_a_gate_is_never_a_map_stage() -> None:
    stage_gate = {"kind": "gate", "class": "cost", "prompt": "x"}
    step = {"id": "m", "kind": "map", "over": "steps.find.output", "max_items": 2}
    step["stages"] = [stage_gate]
    assert _has(prepare_plan(_plan(step))[1], "bad_kind", f"{S1}.stages[0].kind")


def test_the_gate_approval_is_never_an_agent_tool() -> None:
    raw = _plan(agents={"a": {"tools": ["file_read", GATE_TOOL]}})
    assert _has(prepare_plan(raw)[1], "denied_tool", "agents.a.tools[1]")


# ── nested plan ─────────────────────────────────────────────────────────────

_CHILD = {
    "plan_version": 1,
    "name": "child",
    "description": "a template",
    "params": {"target": {"type": "string", "required": True}, "n": {"type": "integer"}},
    "agents": {
        "reader": {"tools": ["file_read"], "model": "m-child"},
        "judge": {"tools": ["file_read"], "reviews": ["reader"]},
    },
    "budget": {"max_tokens": 50, "max_agents": 3},
    "phases": [
        {
            "id": "read",
            "title": "Read",
            "steps": [
                {
                    "id": "read",
                    "kind": "agent",
                    "use": "reader",
                    "prompt": "read {{params.target}}",
                    "fanout": {"count": "{{params.n}}"},
                }
            ],
        }
    ],
    "output": {"found": "steps.read.output"},
}


def _loader(**templates: dict[str, Any]):
    library = {"child": _CHILD, **templates}

    def load(name: str) -> dict[str, Any]:
        if name not in library:
            raise KeyError(f"no plan template {name!r}")
        return copy.deepcopy(library[name])

    return load


def _nested(**over: Any) -> dict[str, Any]:
    step = {
        "id": "sub",
        "kind": "plan",
        "template": "child",
        "args": {"target": "src/{{params.where}}", "n": 2},
    }
    step.update(over)
    return step


def _nesting_plan(*steps: dict[str, Any]) -> dict[str, Any]:
    return _plan(*steps, params={"where": {"type": "string", "default": "lib"}})


def _inlined() -> dict[str, Any]:
    program, problems = prepare_plan(_nesting_plan(_nested()), templates=_loader())
    assert problems == []
    return program


def test_a_nested_plan_is_inlined_with_its_profiles_renamed() -> None:
    program = _inlined()
    node = program["phases"][0]["steps"][1]
    assert (node["args"], node["program"]["_bound_params"]) == (
        {"target": "src/lib", "n": 2},
        {"target": "src/lib", "n": 2},
    )
    keys = ["_bound_params", "description", "name", "output", "params", "phases", "plan_version"]
    assert sorted(node["program"]) == keys  # no budget, no agents: the parent's govern
    assert node["program"]["phases"][0]["steps"][0]["use"] == "sub__reader"
    assert (program["agents"]["sub__reader"]["model"], program["agents"]["sub__judge"]) == (
        "m-child",
        {"tools": ["file_read"], "reviews": ["sub__reader"]},
    )


def test_an_inlined_plan_counts_in_the_bound_the_steps_and_the_walks() -> None:
    program = _inlined()
    parts = [(p.name, p.part.get("use")) for p in iter_agent_parts(program)]
    assert (static_agent_bound(program), count_steps(program), parts) == (
        1 + 2,
        3,
        [("find", None), ("sub/read", "sub__reader")],
    )
    # a written program re-validates as it is (a resume)
    assert validate_plan(program) == []


def test_a_nested_plan_without_a_template_source_is_refused_by_name() -> None:
    assert _has(prepare_plan(_nesting_plan(_nested()))[1], "no_templates", f"{S1}.template")


def test_an_unknown_template_is_refused() -> None:
    raw = _nesting_plan(_nested(template="ghost"))
    assert _has(prepare_plan(raw, templates=_loader())[1], "unknown_template", f"{S1}.template")


def test_nested_plans_go_one_level_deep() -> None:
    grandchild = copy.deepcopy(_CHILD)
    grandchild["phases"][0]["steps"] = [{"id": "deeper", "kind": "plan", "template": "child"}]
    raw = _nesting_plan(_nested(template="middle"))
    problems = prepare_plan(raw, templates=_loader(middle=grandchild))[1]
    assert any(p.code == "nested_too_deep" for p in problems)


def test_args_are_static_a_runtime_value_is_refused() -> None:
    raw = _nesting_plan(_nested(args={"target": "{{steps.find.output}}", "n": 1}))
    assert _has(prepare_plan(raw, templates=_loader())[1], "dynamic_arg", f"{S1}.args.target")


def test_a_child_problem_is_reported_under_the_step() -> None:
    raw = _nesting_plan(_nested(args={"n": 1}))  # target is required
    problems = prepare_plan(raw, templates=_loader())[1]
    assert _has(problems, "required", f"{S1}.template[child].params.target")


def test_a_user_program_key_or_reserved_profile_is_refused() -> None:
    raw = _nesting_plan(_nested(program={"phases": []}))
    assert _has(prepare_plan(raw, templates=_loader())[1], "unknown_key", f"{S1}.program")
    raw = _nesting_plan(_nested())
    raw["agents"] = {"sub__reader": {}}
    problems = prepare_plan(raw, templates=_loader())[1]
    assert _has(problems, "reserved_profile", "agents.sub__reader")


def test_the_inlined_child_cannot_read_the_parents_steps() -> None:
    child = copy.deepcopy(_CHILD)
    child["phases"][0]["steps"][0]["prompt"] = "{{steps.find.output}}"
    problems = prepare_plan(_nesting_plan(_nested()), templates=_loader(child=child))[1]
    assert any(p.code == "bad_ref" for p in problems)


def test_the_child_bound_counts_against_the_parents_max_agents() -> None:
    roomy = copy.deepcopy(_CHILD)
    roomy["budget"]["max_agents"] = 100  # the child alone is within its own budget
    raw = _nesting_plan(_nested(args={"target": "x", "n": 9}))
    raw["budget"]["max_agents"] = 5
    problems = prepare_plan(raw, templates=_loader(child=roomy))[1]
    assert _has(problems, "bound_exceeded", "budget.max_agents")


# ── reducers ────────────────────────────────────────────────────────────────


def test_mean_needs_a_quorum_of_the_slots() -> None:
    step = {"op": "mean", "field": "score"}
    assert apply_reduce(step, [{"score": 4}, {"score": 8}, None], {}) == {
        "mean": 6.0,
        "n": 2,
        "of": 3,
    }
    assert apply_reduce(step, [{"score": 4}, None, None], {})["mean"] is None
    assert apply_reduce(step, [{"score": True}, {"score": "9"}], {})["mean"] is None


def test_rank_picks_a_clear_winner_and_never_a_near_tie() -> None:
    scope = {"c": ["a", "b", "c"]}
    step = {"op": "rank", "items": "c", "field": "mean", "margin": 1}
    clear = apply_reduce(step, [{"mean": 9}, {"mean": 5}, {"mean": None}], scope)
    assert (clear["status"], clear["winner"], clear["winner_index"]) == ("picked", "a", 0)
    assert clear["unscored"] == [2]
    tie = apply_reduce(step, [{"mean": 7.5}, {"mean": 8}, {"mean": 3}], scope)
    assert (tie["status"], tie["winner"], tie["competing"]) == (
        "competing_hypotheses",
        None,
        ["b", "a"],
    )
    none = apply_reduce(step, [None, {"mean": None}], scope)
    assert (none["status"], none["winner"]) == ("undecided", None)


def test_subtract_is_a_keyed_seen_set_difference() -> None:
    scope = {"old": [{"t": "a"}, {"t": "b"}]}
    step = {"op": "subtract", "items": "old", "key": "t"}
    value = [{"t": "b", "x": 1}, {"t": "c"}, {"t": "c", "x": 2}]
    assert apply_reduce(step, value, scope) == [{"t": "c"}]


def test_reduce_inputs_are_paths_never_beside_input() -> None:
    ok = {"id": "r", "kind": "reduce", "op": "concat", "inputs": ["steps.find.output"]}
    assert prepare_plan(_plan(ok))[1] == []
    both = {**ok, "input": "steps.find.output"}
    assert _has(prepare_plan(_plan(both))[1], "bad_value", f"{S1}.inputs")
    ghost = {**ok, "inputs": ["steps.ghost.output"]}
    assert _has(prepare_plan(_plan(ghost))[1], "bad_ref", f"{S1}.inputs[0]")


# ── patterns ────────────────────────────────────────────────────────────────


def _pattern(name: str, **over: Any) -> dict[str, Any]:
    return {"id": "q", "kind": "pattern", "pattern": name, **over}


def _expanded(step: dict[str, Any]) -> list[dict[str, Any]]:
    out, problems = expand_patterns(_plan(step))
    assert problems == []
    return out["phases"][0]["steps"][1:]


def test_judge_panel_expands_to_scores_a_rank_and_a_tie_gate() -> None:
    step = _pattern(
        "judge_panel", over="steps.find.output", prompt="score {{item}}", max_items=4, judges=3
    )
    scores, rank, tie = _expanded(step)
    assert scores["id"] == "q__scores" and scores["stages"][0]["fanout"] == {"count": 3}
    assert scores["stages"][1] == {"kind": "reduce", "op": "mean", "field": "score", "quorum": 2}
    assert scores["stages"][0]["schema"]["properties"]["score"]["enum"] == list(range(11))
    assert (rank["op"], rank["field"], rank["margin"]) == ("rank", "mean", 1)
    assert (tie["kind"], tie["class"], tie["when"]) == (
        "gate",
        "competing_hypotheses",
        "steps.q.output.competing",
    )
    program, problems = prepare_plan(_plan(step))
    assert problems == [] and static_agent_bound(program) == 1 + 4 * 3


def test_judge_panel_can_report_a_tie_without_a_gate() -> None:
    step = _pattern(
        "judge_panel", over="steps.find.output", prompt="s", max_items=2, on_tie="report"
    )
    assert [s["kind"] for s in _expanded(step)] == ["map", "reduce"]


@pytest.mark.parametrize(
    ("over", "path"),
    [
        ({"judges": 0}, "judges"),
        ({"margin": -1}, "margin"),
        ({"margin": 11}, "margin"),
        ({"on_tie": "pick"}, "on_tie"),
        ({"max_items": None}, "max_items"),
    ],
)
def test_judge_panel_refuses_bad_values_on_the_step_written(over: dict, path: str) -> None:
    step = _pattern("judge_panel", over="steps.find.output", prompt="s", max_items=2)
    step.update(over)
    problems = prepare_plan(_plan(step))[1]
    assert any(p.path == f"{S1}.{path}" for p in problems)


def test_perspective_verify_is_one_lens_per_voter_with_a_counted_majority() -> None:
    lenses = ["statistics", "methodology", "reproducibility"]
    step = _pattern(
        "perspective_verify",
        over="steps.find.output",
        prompt="As {{perspective}}, refute {{item}}",
        perspectives=lenses,
        max_items=5,
    )
    votes, split = _expanded(step)
    assert votes["stages"][0]["fanout"] == {"values": lenses, "as": "perspective"}
    assert votes["stages"][1]["quorum"] == 2
    assert split["buckets"] == {"survivors": False, "killed": True, "undecided": None}
    program, problems = prepare_plan(_plan(step))
    assert problems == [] and static_agent_bound(program) == 1 + 5 * 3


@pytest.mark.parametrize("lenses", [[], ["a", "a"], ["x"] * 10, [""], "stats", ["y" * 81], [1]])
def test_perspective_verify_needs_distinct_named_lenses(lenses: Any) -> None:
    step = _pattern(
        "perspective_verify",
        over="steps.find.output",
        prompt="{{perspective}}",
        perspectives=lenses,
        max_items=2,
    )
    assert _has(prepare_plan(_plan(step))[1], "bad_value", f"{S1}.perspectives")


def test_perspective_verify_prompt_must_read_the_perspective() -> None:
    step = _pattern(
        "perspective_verify",
        over="steps.find.output",
        prompt="refute {{item}}",
        perspectives=["a", "b"],
        max_items=2,
    )
    assert _has(prepare_plan(_plan(step))[1], "bad_value", f"{S1}.prompt")


def test_completeness_critic_is_a_seeded_repeat_until_dry() -> None:
    step = _pattern(
        "completeness_critic",
        over="steps.find.output",
        prompt="What is missing?",
        critics=2,
        rounds=3,
        key="title",
    )
    (loop,) = _expanded(step)
    assert (loop["kind"], loop["max_iterations"], loop["seed"], loop["key"]) == (
        "repeat",
        3,
        "steps.find.output",
        "title",
    )
    assert loop["collect"] == "steps.q__gaps.output"
    critic, gaps = loop["steps"]
    assert "{{seen|json}}" in critic["prompt"] and critic["fanout"] == {"count": 2}
    assert (gaps["op"], gaps["input"]) == ("concat", "steps.q__critic.output")
    program, problems = prepare_plan(_plan(step))
    assert problems == [] and static_agent_bound(program) == 1 + 3 * 2


def test_multi_modal_sweep_runs_each_mode_and_merges() -> None:
    modes = [
        {"name": "code", "use": "coder"},
        {"name": "kb", "prompt": "Ask the project's knowledge."},
        {"name": "big", "model": "m-large"},
    ]
    step = _pattern("multi_modal_sweep", prompt="Find X", modes=modes, key="id")
    raw = _plan(step, agents={"coder": {"tools": ["file_read"]}})
    expanded = _expanded(step)
    assert [s["id"] for s in expanded] == ["q__code", "q__kb", "q__big", "q__all", "q"]
    code, kb, big, merged, dedup = expanded
    assert (code["use"], kb["prompt"], big["model"]) == (
        "coder",
        "Ask the project's knowledge.",
        "m-large",
    )
    assert (merged["inputs"], dedup["op"], dedup["key"]) == (
        ["steps.q__code.output", "steps.q__kb.output", "steps.q__big.output"],
        "dedup_by",
        "id",
    )
    program, problems = prepare_plan(raw)
    assert (problems, static_agent_bound(program)) == ([], 1 + 3)


@pytest.mark.parametrize(
    "modes",
    [
        [{"name": "a", "use": "x"}],
        [{"name": "a", "use": "x"}, {"name": "a", "model": "m"}],
        [{"name": "a", "use": "x"}, {"name": "b"}],
        [{"name": "A", "use": "x"}, {"name": "b", "model": "m"}],
    ],
)
def test_multi_modal_sweep_modes_are_distinct_and_differ(modes: list) -> None:
    step = _pattern("multi_modal_sweep", prompt="p", modes=modes)
    problems = prepare_plan(_plan(step, agents={"x": {}}))[1]
    assert any(p.path.startswith(f"{S1}.modes") for p in problems)


# ── reviewer independence ───────────────────────────────────────────────────


def _review_plan(producer: Any, reviewer: Any) -> dict[str, Any]:
    raw = _plan(
        {"id": "rev", "kind": "agent", "use": "reviewer", "prompt": "{{steps.find.output}}"},
        agents={
            "maker": {"model": producer},
            "reviewer": {"model": reviewer, "reviews": ["maker"]},
        },
    )
    raw["phases"][0]["steps"][0]["use"] = "maker"
    return prepare_plan(raw)[0]


def test_a_reviewer_on_the_producers_model_is_warned_about() -> None:
    assert independence_warnings(_review_plan("m1", "m2")) == []
    same = independence_warnings(_review_plan("m1", "m1"))
    assert len(same) == 1 and "'reviewer'" in same[0] and "not independent" in same[0]
    # two unnamed models run on the one session default: the same model
    assert len(independence_warnings(_review_plan(None, None))) == 1
    assert plan_summary(_review_plan(None, None))["warnings"]
    # a default the reviewer names apart from is not a warning
    assert independence_warnings(_review_plan(None, "m2"), default_model="m1") == []


def test_reviews_name_other_profiles_only() -> None:
    raw = _plan(agents={"r": {"reviews": ["r", "ghost"]}})
    problems = prepare_plan(raw)[1]
    assert _has(problems, "unknown_profile", "agents.r.reviews[0]")
    assert _has(problems, "unknown_profile", "agents.r.reviews[1]")
