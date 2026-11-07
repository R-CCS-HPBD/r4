"""ADR-092 slice 5 grammar: ``isolation`` and ``merge_back``.

- ``isolation`` is a closed set (``none``, ``worktree``, ``copy``) on an
  ``agent`` or ``map`` step, checked statically; a map stage takes its map's.
- D-6 updated: a map or a fan-out above one may hold a writer only when it
  is isolated, and only writers that honour a per-agent workspace
  (``WORKSPACE_TOOLS``); any other writer stays refused (fail closed).
- ``merge_back`` names earlier isolated steps of the plan's own phases; it
  starts no agent and is refused inside a repeat or an inlined template.
"""

from __future__ import annotations

from typing import Any

import pytest

from r4_contracts.team_engine.plan import (
    ISOLATION_MODES,
    MAX_MERGE_FROM,
    PARALLEL_UNSAFE_TOOLS,
    WORKSPACE_TOOLS,
    PlanProblem,
    count_steps,
    discard_warnings,
    plan_summary,
    prepare_plan,
    static_agent_bound,
    step_isolation,
    validate_plan,
)


def _plan(*steps: dict[str, Any], **top: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "plan_version": 1,
        "name": "t",
        "description": "slice 5",
        "budget": {"max_tokens": 1000},
        "agents": {
            "w": {"tools": ["file_read", "file_write", "file_edit", "shell_exec"]},
            "r": {"tools": ["file_read"]},
        },
        "phases": [{"id": "p", "title": "P", "steps": list(steps)}],
    }
    plan.update(top)
    return plan


def _codes(problems: list[PlanProblem]) -> set[tuple[str, str]]:
    return {(p.code, p.path) for p in problems}


def _has(problems: list[PlanProblem], code: str, path: str) -> bool:
    return (code, path) in _codes(problems)


def _fan(isolation: str | None = None, use: str = "w", count: int = 3) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "impl",
        "kind": "agent",
        "use": use,
        "prompt": "do",
        "fanout": {"count": count},
    }
    if isolation is not None:
        step["isolation"] = isolation
    return step


def _list() -> dict[str, Any]:
    return {"id": "a", "kind": "agent", "use": "r", "prompt": "list"}


def _map(isolation: str | None = None, use: str = "w") -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "m",
        "kind": "map",
        "over": "steps.a.output",
        "max_items": 4,
        "stages": [{"kind": "agent", "use": use, "prompt": "{{item}}"}],
    }
    if isolation is not None:
        step["isolation"] = isolation
    return step


S0 = "phases[0].steps[0]"
S1 = "phases[0].steps[1]"
S2 = "phases[0].steps[2]"


# ── isolation: a closed grammar ─────────────────────────────────────────────


def test_isolation_modes_are_the_three_the_adr_names() -> None:
    assert ISOLATION_MODES == ("none", "worktree", "copy")


@pytest.mark.parametrize("mode", ISOLATION_MODES)
def test_every_isolation_mode_validates_on_an_agent_and_a_map(mode: str) -> None:
    _, problems = prepare_plan(_plan(_fan(mode, use="r")))
    assert problems == []
    _, problems = prepare_plan(_plan(_list(), _map(mode, use="r")))
    assert problems == []


@pytest.mark.parametrize("value", ["sandbox", "", "WORKTREE", 1, True, ["copy"], None])
def test_an_isolation_outside_the_set_is_refused_by_name(value: Any) -> None:
    step = _fan(use="r")
    step["isolation"] = value
    problems = prepare_plan(_plan(step))[1]
    if value is None:  # an explicit null is "none", as an absent key is
        assert problems == []
        return
    assert _has(problems, "bad_value", f"{S0}.isolation")


def test_a_map_stage_may_not_carry_its_own_isolation() -> None:
    mapped = _map("copy", use="r")
    mapped["stages"][0]["isolation"] = "worktree"
    problems = prepare_plan(_plan(_list(), mapped))[1]
    assert _has(problems, "unknown_key", f"{S1}.stages[0].isolation")


def test_step_isolation_reads_none_by_default() -> None:
    assert step_isolation({"kind": "agent"}) == "none"
    assert step_isolation({"kind": "agent", "isolation": "copy"}) == "copy"


# ── the D-6 rule, updated ───────────────────────────────────────────────────


@pytest.mark.parametrize("isolation", [None, "none"])
def test_parallel_writers_stay_refused_without_isolation(isolation: str | None) -> None:
    assert _has(prepare_plan(_plan(_fan(isolation)))[1], "parallel_writer", S0)
    mapped = prepare_plan(_plan(_list(), _map(isolation)))[1]
    assert _has(mapped, "parallel_writer", f"{S1}.stages[0]")


@pytest.mark.parametrize("mode", ["worktree", "copy"])
def test_parallel_writers_are_allowed_when_isolated(mode: str) -> None:
    assert prepare_plan(_plan(_fan(mode)))[1] == []
    assert prepare_plan(_plan(_list(), _map(mode)))[1] == []


def test_the_isolated_writers_are_exactly_the_workspace_aware_ones() -> None:
    assert {
        "file_write",
        "file_edit",
        "apply_patch",
        "shell_exec",
        "python_exec",
    } == PARALLEL_UNSAFE_TOOLS & WORKSPACE_TOOLS


@pytest.mark.parametrize("tool", sorted(PARALLEL_UNSAFE_TOOLS - WORKSPACE_TOOLS))
def test_a_writer_that_ignores_the_workspace_stays_refused_even_isolated(tool: str) -> None:
    """``compile_code``, ``test_fix_loop``, ``project_use``… write wherever
    they write: an isolated step cannot contain them (fail closed)."""
    agents = {"w": {"tools": ["file_write", tool]}, "r": {"tools": ["file_read"]}}
    problems = prepare_plan(_plan(_fan("worktree"), agents=agents))[1]
    assert _has(problems, "parallel_writer", S0)
    assert tool in next(p.message for p in problems if p.code == "parallel_writer")
    assert "file_write" not in next(p.message for p in problems if p.code == "parallel_writer")


@pytest.mark.parametrize("tool", ["mcp_invoke", "mcp.srv.write_thing"])
def test_an_mcp_writer_stays_refused_even_isolated(tool: str) -> None:
    agents = {"w": {"tools": [tool]}, "r": {"tools": ["file_read"]}}
    assert _has(prepare_plan(_plan(_fan("copy"), agents=agents))[1], "parallel_writer", S0)


def test_isolation_of_a_map_is_not_isolation_of_a_later_fanout() -> None:
    raw = _plan(_list(), _map("copy"), {**_fan(), "id": "after"})
    assert _has(prepare_plan(raw)[1], "parallel_writer", S2)


# ── merge_back ──────────────────────────────────────────────────────────────


def _merge(*sources: str, **extra: Any) -> dict[str, Any]:
    return {"id": "merge", "kind": "merge_back", "from": list(sources), **extra}


def test_a_merge_back_of_isolated_steps_validates_and_starts_no_agent() -> None:
    raw = _plan(_list(), _map("worktree"), _fan("copy"), _merge("m", "impl"))
    program, problems = prepare_plan(raw)
    assert problems == []
    assert static_agent_bound(program) == 1 + 4 + 3
    assert count_steps(program) == 4
    assert plan_summary(program)["merge_backs"] == [{"step": "merge", "from": ["m", "impl"]}]


def test_a_merge_back_may_not_name_a_step_that_is_not_isolated() -> None:
    raw = _plan(_list(), _fan("none", use="r"), _merge("a", "impl"))
    problems = prepare_plan(raw)[1]
    assert _has(problems, "bad_ref", f"{S2}.from[0]")
    assert _has(problems, "bad_ref", f"{S2}.from[1]")


def test_a_merge_back_may_not_name_a_later_or_unknown_step() -> None:
    raw = _plan(_merge("impl"), _fan("copy"))
    assert _has(prepare_plan(raw)[1], "bad_ref", f"{S0}.from[0]")
    assert _has(prepare_plan(_plan(_fan("copy"), _merge("nope")))[1], "bad_ref", f"{S1}.from[0]")


@pytest.mark.parametrize(
    "sources",
    [[], "impl", ["impl", "impl"], [1], ["impl"] * (MAX_MERGE_FROM + 1)],
)
def test_a_merge_back_from_is_a_short_list_of_distinct_ids(sources: Any) -> None:
    step = {"id": "merge", "kind": "merge_back", "from": sources}
    assert _has(prepare_plan(_plan(_fan("copy"), step))[1], "bad_value", f"{S1}.from")


def test_a_merge_back_has_closed_keys() -> None:
    raw = _plan(_fan("copy"), _merge("impl", prompt="apply?", auto=True))
    problems = prepare_plan(raw)[1]
    assert _has(problems, "unknown_key", f"{S1}.prompt")
    assert _has(problems, "unknown_key", f"{S1}.auto")


def test_a_merge_back_is_refused_inside_a_repeat() -> None:
    repeat = {
        "id": "loop",
        "kind": "repeat",
        "max_iterations": 2,
        "collect": "steps.impl.output",
        "steps": [_fan("copy", use="r"), _merge("impl")],
    }
    problems = prepare_plan(_plan(repeat))[1]
    assert _has(problems, "bad_kind", f"{S0}.steps[1].kind")


def test_a_merge_back_is_refused_inside_an_inlined_template() -> None:
    program = {
        "plan_version": 1,
        "name": "inner",
        "description": "inner",
        "_bound_params": {},
        "phases": [{"id": "q", "title": "Q", "steps": [_fan("copy", use="r"), _merge("impl")]}],
    }
    step = {"id": "sub", "kind": "plan", "template": "x", "program": program}
    problems = validate_plan(_plan(step))
    assert _has(problems, "bad_kind", f"{S0}.program.phases[0].steps[1].kind")


# ── isolated writes with no merge-back are discarded: warned ────────────────

DISCARDED = "changes made in {}'s isolated workspace are discarded at run end"


def _warned(raw: dict[str, Any]) -> list[str]:
    program, problems = prepare_plan(raw)
    assert problems == []
    return [w for w in plan_summary(program)["warnings"] if "discarded" in w]


@pytest.mark.parametrize("step", [_fan("copy"), _map("worktree")])
def test_an_isolated_writer_no_merge_back_names_is_warned(step: dict[str, Any]) -> None:
    raw = _plan(_list(), step) if step["kind"] == "map" else _plan(step)
    (warning,) = _warned(raw)
    assert warning.startswith(DISCARDED.format(step["id"]))


def test_a_merge_back_that_names_the_step_silences_the_warning() -> None:
    assert _warned(_plan(_list(), _map("copy"), _fan("worktree"), _merge("m", "impl"))) == []


def test_only_the_steps_no_merge_back_names_are_warned() -> None:
    warned = _warned(_plan(_list(), _map("copy"), _fan("worktree"), _merge("impl")))
    assert [w.split("'")[0] for w in warned] == ["changes made in m"]


@pytest.mark.parametrize("isolation", ["none", None])
def test_a_writer_in_the_project_root_is_not_warned(isolation: str | None) -> None:
    assert _warned(_plan(_fan(isolation, count=1))) == []


def test_an_isolated_reader_is_not_warned() -> None:
    assert _warned(_plan(_fan("copy", use="r"))) == []


def test_an_isolated_mcp_tool_counts_as_a_writer() -> None:
    agents = {"w": {"tools": ["mcp.srv.tool"]}, "r": {"tools": ["file_read"]}}
    step = {**_fan("copy", count=1), "id": "mcp_step"}
    assert len(discard_warnings(prepare_plan(_plan(step, agents=agents))[0])) == 1


def test_an_isolated_writer_inside_a_repeat_is_always_warned() -> None:
    """A merge-back cannot name a step inside a repeat's body."""
    repeat = {
        "id": "loop",
        "kind": "repeat",
        "max_iterations": 2,
        "collect": "steps.impl.output",
        "steps": [{**_fan("copy", count=1)}],
    }
    (warning,) = _warned(_plan(repeat))
    assert warning.startswith(DISCARDED.format("loop.impl"))


def test_a_multi_stage_isolated_map_is_warned_once() -> None:
    mapped = _map("copy")
    mapped["stages"].append({"kind": "agent", "use": "w", "prompt": "{{prev}}"})
    assert len(_warned(_plan(_list(), mapped))) == 1
