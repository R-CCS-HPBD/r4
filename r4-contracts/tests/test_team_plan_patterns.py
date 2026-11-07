"""Plan patterns (ADR-092 D-4): ``adversarial_verify`` expands at compile time
into a counted vote and a partition, so the interpreter runs only primitives,
votes are counted in code, and a skeptic that fails its schema can never pass
a finding by default (the quorum rule).

The shipped ``adversarial-review`` template is pinned here too: it is package
data of ``r4`` (D-10) but its semantics are this grammar's.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import pytest

from r4_contracts.team_engine.plan import (
    MAX_FANOUT,
    MAX_MAP_ITEMS,
    PlanProblem,
    apply_reduce,
    prepare_plan,
    resolve_path,
    schema_problems,
    static_agent_bound,
    step_agent_bound,
    validate_output,
    validate_plan,
)
from r4_contracts.team_engine.plan_patterns import PATTERNS, expand_patterns

_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "r4"
    / "src"
    / "r4"
    / "teams"
    / "plan"
    / "templates"
    / "adversarial-review.json"
)

_VOTE_SCHEMA = {
    "type": "object",
    "properties": {"refuted": {"type": "boolean"}, "reason": {"type": "string", "maxLength": 800}},
    "required": ["refuted", "reason"],
    "additionalProperties": False,
}
_PATTERN_PATH = "phases[1].steps[0]"


def _pattern_plan(**pattern: Any) -> dict[str, Any]:
    """Phase ``find`` produces findings; phase ``verify`` holds one pattern step."""
    step = {
        "id": "verify",
        "kind": "pattern",
        "pattern": "adversarial_verify",
        "over": "steps.find.output.findings",
        "prompt": "Refute: {{item|json}}",
        "max_items": 4,
        **pattern,
    }
    return {
        "plan_version": 1,
        "name": "t",
        "description": "a pattern plan",
        "budget": {"max_tokens": 1000},
        "agents": {"skeptic": {"tools": ["file_read"]}},
        "phases": [
            {
                "id": "find",
                "title": "Find",
                "steps": [{"id": "find", "kind": "agent", "prompt": "find"}],
            },
            {"id": "verify", "title": "Verify", "steps": [step]},
        ],
    }


def _expanded_steps(**pattern: Any) -> list[dict[str, Any]]:
    expanded, problems = expand_patterns(_pattern_plan(**pattern))
    assert problems == []
    return expanded["phases"][1]["steps"]


def _has(problems: list[PlanProblem], code: str, path: str) -> bool:
    return any(p.code == code and p.path == path for p in problems)


def _template() -> dict[str, Any]:
    return json.loads(_TEMPLATE.read_text(encoding="utf-8"))


# ── expansion ───────────────────────────────────────────────────────────────


def test_adversarial_verify_expands_to_a_vote_map_and_a_partition() -> None:
    steps = _expanded_steps(use="skeptic", skeptics=3, label="skeptic", log="verifying")
    votes = {
        "id": "verify__votes",
        "kind": "map",
        "over": "steps.find.output.findings",
        "max_items": 4,
        "label": "skeptic",
        "log": "verifying",
        "stages": [
            {
                "kind": "agent",
                "prompt": "Refute: {{item|json}}",
                "fanout": {"count": 3},
                "schema": _VOTE_SCHEMA,
                "use": "skeptic",
            },
            {"kind": "reduce", "op": "majority", "field": "refuted", "quorum": 2},
        ],
    }
    split = {
        "id": "verify",
        "kind": "reduce",
        "op": "partition",
        "input": "steps.verify__votes.output",
        "items": "steps.find.output.findings",
        "field": "verdict",
        "buckets": {"survivors": False, "killed": True, "undecided": None},
    }
    assert steps == [votes, split]


def test_expansion_leaves_the_input_and_other_steps_untouched() -> None:
    raw = _pattern_plan()
    before = copy.deepcopy(raw)
    expanded, _problems = expand_patterns(raw)
    assert raw == before
    assert expanded["phases"][0] == raw["phases"][0]


def test_the_vote_schema_is_in_the_supported_subset() -> None:
    voter = _expanded_steps()[0]["stages"][0]
    assert schema_problems(voter["schema"], "$") == []
    assert validate_output({"refuted": True, "reason": "read it"}, voter["schema"]) == []
    assert validate_output({"refuted": "yes", "reason": "x", "extra": 1}, voter["schema"]) != []


@pytest.mark.parametrize(("skeptics", "quorum"), [(1, 1), (2, 1), (3, 2), (4, 2), (5, 3), (9, 5)])
def test_the_quorum_is_ceil_half_of_the_skeptics(skeptics: int, quorum: int) -> None:
    voter, majority = _expanded_steps(skeptics=skeptics)[0]["stages"]
    assert majority["quorum"] == quorum == math.ceil(skeptics / 2)
    assert voter["fanout"] == {"count": skeptics}


def test_defaults_are_three_skeptics_a_refuted_field_and_no_profile() -> None:
    votes = _expanded_steps()[0]
    voter, majority = votes["stages"]
    assert voter["fanout"] == {"count": 3}
    assert "use" not in voter
    assert majority == {"kind": "reduce", "op": "majority", "field": "refuted", "quorum": 2}
    assert votes["label"] == "verify"
    assert "log" not in votes


def test_a_custom_vote_field_flows_into_the_schema_and_the_majority() -> None:
    votes, split = _expanded_steps(field="broken")
    voter, majority = votes["stages"]
    assert voter["schema"]["required"] == ["broken", "reason"]
    assert voter["schema"]["properties"]["broken"] == {"type": "boolean"}
    assert majority["field"] == "broken"
    assert split["field"] == "verdict"


@pytest.mark.parametrize(("items", "skeptics"), [(1, 1), (4, 3), (50, 2), (10, MAX_FANOUT)])
def test_the_pattern_bound_is_items_times_skeptics(items: int, skeptics: int) -> None:
    raw = _pattern_plan(max_items=items, skeptics=skeptics)
    raw["budget"]["max_agents"] = 1000
    program, problems = prepare_plan(raw)
    assert problems == []
    votes, split = program["phases"][1]["steps"]
    assert step_agent_bound(votes) == items * skeptics
    assert step_agent_bound(split) == 0
    assert static_agent_bound(program) == 1 + items * skeptics


def test_the_pattern_bound_counts_against_max_agents() -> None:
    raw = _pattern_plan(max_items=10, skeptics=5)
    raw["budget"]["max_agents"] = 50
    assert _has(prepare_plan(raw)[1], "bound_exceeded", "budget.max_agents")


def test_an_expanded_plan_validates_cleanly() -> None:
    program, problems = prepare_plan(_pattern_plan(use="skeptic"))
    assert problems == []
    assert validate_plan(program) == []


# ── refusals ────────────────────────────────────────────────────────────────


def test_the_patterns_are_slice_1_and_slice_2s_closed_set() -> None:
    # ADR-092 D-4: slice 2 adds the four named patterns, and only those.
    assert set(PATTERNS) == {
        "adversarial_verify",
        "perspective_verify",
        "judge_panel",
        "completeness_critic",
        "multi_modal_sweep",
    }


@pytest.mark.parametrize(
    "name", ["loop_until_dry", "", None, ["adversarial_verify"], {"name": "x"}, 3]
)
def test_an_unknown_pattern_is_refused(name: Any) -> None:
    program, problems = prepare_plan(_pattern_plan(pattern=name))
    assert program == {}
    assert _has(problems, "unknown_pattern", f"{_PATTERN_PATH}.pattern")


@pytest.mark.parametrize("skeptics", [0, MAX_FANOUT + 1, -1, True, "3", 2.0, None])
def test_bad_skeptics_are_refused(skeptics: Any) -> None:
    assert _has(
        prepare_plan(_pattern_plan(skeptics=skeptics))[1],
        "out_of_range",
        f"{_PATTERN_PATH}.skeptics",
    )


@pytest.mark.parametrize("max_items", [0, MAX_MAP_ITEMS + 1, "4", None, True])
def test_bad_max_items_are_refused(max_items: Any) -> None:
    assert _has(
        prepare_plan(_pattern_plan(max_items=max_items))[1],
        "required",
        f"{_PATTERN_PATH}.max_items",
    )


@pytest.mark.parametrize("key", ["id", "over", "prompt"])
def test_id_over_and_prompt_are_required(key: str) -> None:
    raw = _pattern_plan()
    del raw["phases"][1]["steps"][0][key]
    assert _has(prepare_plan(raw)[1], "required", f"{_PATTERN_PATH}.{key}")


def test_unknown_pattern_keys_are_refused() -> None:
    problems = prepare_plan(_pattern_plan(quorum=1))[1]
    assert _has(problems, "unknown_key", f"{_PATTERN_PATH}.quorum")


def test_every_pattern_problem_is_reported_together() -> None:
    problems = prepare_plan(_pattern_plan(skeptics=0, max_items=0, quorum=1, prompt=""))[1]
    found = {(p.path, p.code) for p in problems}
    assert found == {
        (f"{_PATTERN_PATH}.skeptics", "out_of_range"),
        (f"{_PATTERN_PATH}.max_items", "required"),
        (f"{_PATTERN_PATH}.quorum", "unknown_key"),
        (f"{_PATTERN_PATH}.prompt", "required"),
    }


@pytest.mark.parametrize("field", [["refuted"], {"a": 1}, 3, "", "two words", "reason"])
def test_a_bad_vote_field_is_refused_not_raised(field: Any) -> None:
    assert _has(prepare_plan(_pattern_plan(field=field))[1], "bad_value", f"{_PATTERN_PATH}.field")


@pytest.mark.parametrize("use", [["skeptic"], {"a": 1}, 3])
def test_a_non_string_profile_is_refused_not_raised(use: Any) -> None:
    assert "unknown_profile" in {p.code for p in prepare_plan(_pattern_plan(use=use))[1]}


@pytest.mark.parametrize("step_id", ["Verify", "9x", "a-b", "a__b"])
def test_a_pattern_id_must_be_a_plain_step_id(step_id: str) -> None:
    assert _has(prepare_plan(_pattern_plan(id=step_id))[1], "bad_id", f"{_PATTERN_PATH}.id")


@pytest.mark.parametrize("step_id", ["my__step", "verify__votes"])
def test_user_step_ids_may_not_use_the_reserved_double_underscore(step_id: str) -> None:
    raw = _pattern_plan()
    raw["phases"][0]["steps"].append({"id": step_id, "kind": "agent", "prompt": "x"})
    assert _has(prepare_plan(raw)[1], "bad_id", "phases[0].steps[1].id")


def test_expansion_tolerates_a_malformed_phase_list() -> None:
    raw = _pattern_plan()
    raw["phases"] = 5
    assert expand_patterns(raw) == (raw, [])
    assert _has(prepare_plan(raw)[1], "required", "phases")


# ── the shipped template (ADR-092 D-10) ─────────────────────────────────────


def test_the_shipped_template_prepares_to_a_bound_of_seven() -> None:
    program, problems = prepare_plan(
        _template(), {"target": "src/", "max_findings": "3", "skeptics": "2"}
    )
    assert problems == []
    assert static_agent_bound(program) == 1 + 3 * 2
    assert program["_bound_params"] == {
        "target": "src/",
        "focus": "",
        "max_findings": 3,
        "skeptics": 2,
        "reviewer_model": None,
    }


def test_the_shipped_template_binds_counts_and_the_reviewer_model() -> None:
    program, _problems = prepare_plan(
        _template(), {"target": "src/", "max_findings": "3", "skeptics": "2"}
    )
    votes = program["phases"][1]["steps"][0]
    assert votes["max_items"] == 3
    assert votes["stages"][0]["fanout"] == {"count": 2}
    assert votes["stages"][1]["quorum"] == 1
    assert program["agents"]["skeptic"]["model"] is None


def test_the_shipped_template_defaults_bound_sixteen() -> None:
    program, problems = prepare_plan(_template(), {"target": "src/"})
    assert problems == []
    assert static_agent_bound(program) == 1 + 5 * 3


@pytest.mark.parametrize(
    ("args", "code", "path"),
    [
        ({}, "required", "params.target"),
        ({"target": "src/", "max_findings": "9"}, "out_of_range", "params.max_findings"),
        ({"target": "src/", "skeptics": "6"}, "out_of_range", "params.skeptics"),
        ({"target": "src/", "skeptics": "many"}, "bad_value", "params.skeptics"),
        ({"target": "src/", "model": "x"}, "unknown_param", "args.model"),
    ],
)
def test_the_shipped_template_refuses_bad_args(args: dict[str, Any], code: str, path: str) -> None:
    assert _has(prepare_plan(_template(), args)[1], code, path)


def test_the_shipped_template_routes_skeptics_to_the_reviewer_model() -> None:
    program, problems = prepare_plan(
        _template(), {"target": "src/", "reviewer_model": "qwen3-coder:30b"}
    )
    assert problems == []
    assert program["agents"]["skeptic"]["model"] == "qwen3-coder:30b"


def test_the_shipped_template_refuses_a_url_reviewer_model() -> None:
    problems = prepare_plan(_template(), {"target": "src/", "reviewer_model": "http://h:8000/v1"})[
        1
    ]
    assert _has(problems, "url_model", "agents.skeptic.model")


# ── the expanded pattern, run by its own reducers ───────────────────────────


def _vote(refuted: bool) -> dict[str, Any]:
    return {"refuted": refuted, "reason": "read the code"}


def test_votes_kill_keep_and_leave_findings_undecided() -> None:
    program, _problems = prepare_plan(
        _template(), {"target": "src/", "max_findings": "4", "skeptics": "3"}
    )
    votes_step, split = program["phases"][1]["steps"]
    findings = [{"title": f"f{i}"} for i in range(4)]
    ballots = [  # three skeptics per finding; None is a skeptic that failed its schema
        [_vote(True), _vote(True), _vote(False)],
        [_vote(False), _vote(False), None],
        [_vote(True), None, None],
        [_vote(True), _vote(False), None],
    ]
    per_item = [apply_reduce(votes_step["stages"][1], ballot, {}) for ballot in ballots]
    scope = {
        "steps": {"find": {"output": {"findings": findings}}, "verify__votes": {"output": per_item}}
    }
    result = apply_reduce(split, resolve_path(scope, split["input"]), scope)
    assert result == {
        "survivors": [findings[1]],
        "killed": [findings[0]],
        "undecided": findings[2:],
    }


def test_a_panel_of_failed_skeptics_never_passes_a_finding() -> None:
    program, _problems = prepare_plan(_template(), {"target": "src/", "skeptics": "5"})
    votes_step, split = program["phases"][1]["steps"]
    per_item = [apply_reduce(votes_step["stages"][1], [None] * 4 + [_vote(False)], {})]
    scope = {
        "steps": {"find": {"output": {"findings": ["f"]}}, "verify__votes": {"output": per_item}}
    }
    result = apply_reduce(split, resolve_path(scope, split["input"]), scope)
    assert result == {"survivors": [], "killed": [], "undecided": ["f"]}
