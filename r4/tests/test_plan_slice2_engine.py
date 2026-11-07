"""ADR-092 slice 2 in the engine: ``repeat``, ``gate`` and nested ``plan``
runs, their journal and resume behaviour (D-8), budgets (D-7), the
judge-panel tie, retrieval refs on journal records, and the ``research-round``
template.

Every run is a real PlanRun over scripted turns (``MemoryAgentBroker``): no
model, no network.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from r4_contracts.team_engine.events import EventKind, TeamEvent
from r4_contracts.team_engine.plan import plan_summary
from r4_contracts.team_engine.services import AgentTurnResult, GateDecision
from r4_teamengine_spi.testing import MemoryAgentBroker, make_engine_context, make_services

from r4.teams.plan import PlanEngine, spec_from_plan
from r4.teams.plan.journal import PlanJournal
from r4.teams.plan_sources import load_template

HUMAN = EventKind.HUMAN_INPUT_REQUESTED


def _plan(*steps: dict[str, Any], phases: list | None = None, **top: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "plan_version": 1,
        "name": "t",
        "description": "slice 2",
        "budget": {"max_tokens": 100_000},
        "phases": phases or [{"id": "p1", "title": "One", "steps": list(steps)}],
    }
    plan.update(top)
    return plan


def _services(tmp_path: Path, broker: Any, **kwargs: Any):
    services = make_services(agents=broker, **kwargs)
    services.run = replace(services.run, working_dir=str(tmp_path))
    return services


def _compile(raw: dict[str, Any], args: dict[str, Any] | None = None, **kw: Any):
    spec, problems = spec_from_plan(raw, args or {}, project="p", templates=load_template, **kw)
    assert not problems, problems
    return PlanEngine().compile(spec, make_engine_context())


async def _run(raw: dict[str, Any], services, args: dict[str, Any] | None = None):
    run = await PlanEngine().start(_compile(raw, args), services)
    events = [event async for event in run.events()]
    return run, events


def _result(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "result.json").read_text())


def _messages(events: list[TeamEvent], kind: str) -> list[dict[str, Any]]:
    return [
        e.payload
        for e in events
        if e.kind is EventKind.AGENT_MESSAGE and e.payload.get("type") == kind
    ]


def _completed(events: list[TeamEvent]) -> list[str]:
    return [e.payload["label"] for e in events if e.kind is EventKind.AGENT_COMPLETED]


# ── repeat: loop-until-dry ──────────────────────────────────────────────────


def _hunt(max_iterations: int = 4, **over: Any) -> dict[str, Any]:
    step = {
        "id": "hunt",
        "kind": "repeat",
        "max_iterations": max_iterations,
        "seed": "params.known",
        "collect": "steps.more.output",
        "steps": [{"id": "more", "kind": "agent", "prompt": "known: {{seen|json}}"}],
    }
    step.update(over)
    return step


_KNOWN = {"known": {"type": "array", "default": ["a"]}}


async def test_a_repeat_stops_when_an_iteration_finds_nothing_new(tmp_path: Path) -> None:
    answers = iter([["a", "b"], ["b", "c", "c"], ["a", "c"], ["z"]])
    broker = MemoryAgentBroker({"more": lambda req: next(answers)})
    raw = _plan(_hunt(), params=_KNOWN)
    run, events = await _run(raw, _services(tmp_path, broker))

    out = _result(tmp_path)["output"]["hunt"]
    assert ((await run.result()).status, out) == (
        "success",
        {"items": ["b", "c"], "iterations": 3, "dry": True, "stopped": None},
    )
    # the third iteration is dry: the fourth never starts
    assert _completed(events) == ["more@0", "more@1", "more@2"]
    # each iteration sees everything seen so far, inside the untrusted envelope
    first, second, third = [call.prompt for call in broker.calls]
    shown = ('"b"' in first, '"b"' in second, '"c"' in third, '"a"' in first)
    assert shown == (False, True, True, True)
    assert 'source="seen" untrusted="true"' in second


async def test_a_repeat_is_bounded_by_its_iterations(tmp_path: Path) -> None:
    counter = iter(range(100))
    broker = MemoryAgentBroker({"more": lambda req: [f"x{next(counter)}"]})
    _run_, events = await _run(_plan(_hunt(2), params=_KNOWN), _services(tmp_path, broker))
    out = _result(tmp_path)["output"]["hunt"]
    assert (out["iterations"], out["dry"], len(broker.calls)) == (2, False, 2)


async def test_a_failed_collect_stops_the_loop_with_a_named_reason(tmp_path: Path) -> None:
    broker = MemoryAgentBroker({"more": None})
    run, _events = await _run(_plan(_hunt(), params=_KNOWN), _services(tmp_path, broker))
    doc = _result(tmp_path)
    assert doc["output"]["hunt"]["stopped"] == "collect_null"
    assert "repeat_stopped:collect_null" in doc["steps"]["hunt"]["null_reasons"]
    assert len(broker.calls) == 1  # never loops on the same failure


async def test_a_resumed_repeat_replays_its_iterations(tmp_path: Path) -> None:
    raw = _plan(_hunt(), params=_KNOWN)
    script = {"more": lambda req: ["b"] if '"b"' not in req.prompt else ["b"]}
    first = MemoryAgentBroker(script)
    await _run(raw, _services(tmp_path, first))
    second = MemoryAgentBroker(script)
    _run2, events = await _run(raw, _services(tmp_path, second, attempt_id="attempt-2"))
    assert second.calls == []  # both iterations replayed at zero model cost (D-8)
    assert _result(tmp_path)["output"]["hunt"]["items"] == ["b"]
    assert all(e.payload["replayed"] for e in events if e.kind is EventKind.AGENT_COMPLETED)


async def test_the_body_outputs_stay_inside_the_iteration(tmp_path: Path) -> None:
    broker = MemoryAgentBroker({"more": ["b"], "after": "done"})
    after = {"id": "after", "kind": "agent", "prompt": "{{steps.hunt.output.items|json}}"}
    await _run(_plan(_hunt(1), after, params=_KNOWN), _services(tmp_path, broker))
    record = [r for r in PlanJournal(tmp_path).records() if r["step"] == "hunt"][0]
    assert record["node"] == "hunt.iterations[0].more"
    assert '"b"' in broker.calls[-1].prompt


# ── gate ────────────────────────────────────────────────────────────────────


def _gated(**gate: Any) -> dict[str, Any]:
    ask = {"id": "ask", "kind": "gate", "class": "cost", "prompt": "Go on with {{steps.a.output}}?"}
    ask.update(gate)
    return _plan(
        {"id": "a", "kind": "agent", "prompt": "first"},
        ask,
        {"id": "b", "kind": "agent", "prompt": "second"},
    )


async def test_an_approved_gate_lets_the_run_go_on(tmp_path: Path) -> None:
    broker = MemoryAgentBroker({"a": "x", "b": "y"}, gate_answers={"ask": True})
    run, events = await _run(_gated(), _services(tmp_path, broker))
    result = await run.result()
    assert (result.status, _completed(events)) == ("success", ["a", "b"])
    (asked,) = [e.payload for e in events if e.kind is HUMAN]
    # the agent's part of the question is marked untrusted
    marked = 'untrusted="true"' in asked["prompt"]
    assert (asked["gate"], asked["class"], marked) == ("ask", "cost", True)
    assert (broker.gate_calls[0].trigger_class, result.metrics.human_interventions) == ("cost", 1)
    assert _result(tmp_path)["gates"]["ask"]["approved"] is True


async def test_a_rejected_gate_stops_the_run(tmp_path: Path) -> None:
    broker = MemoryAgentBroker({"a": "x", "b": "y"}, gate_answers={"ask": False})
    run, events = await _run(_gated(), _services(tmp_path, broker))
    result = await run.result()
    assert result.status == "cancelled"
    assert events[-1].kind is EventKind.RUN_CANCELLED
    assert events[-1].payload["reason"] == "gate_rejected:ask"
    assert [c.label for c in broker.calls] == ["a"]  # b never started
    doc = _result(tmp_path)
    assert doc["cancel_reason"] == "gate_rejected:ask"
    assert doc["steps"]["b"]["null_reasons"] == ["halted_by_gate"]


async def test_a_gate_with_no_human_refuses_by_name(tmp_path: Path) -> None:
    broker = MemoryAgentBroker({"a": "x", "b": "y"})  # answers no gate: no human
    run, events = await _run(_gated(), _services(tmp_path, broker))
    assert (await run.result()).status == "cancelled"
    assert events[-1].payload["reason"] == "gate_refused:ask:no_human"
    assert [c.label for c in broker.calls] == ["a"]


async def test_a_gate_is_never_passed_without_a_human_answer(tmp_path: Path) -> None:
    class SaysYesWithoutAHuman(MemoryAgentBroker):
        async def ask_gate(self, request):
            return GateDecision(approved=True, answered=False)

    broker = SaysYesWithoutAHuman({"a": "x", "b": "y"})
    run, _events = await _run(_gated(), _services(tmp_path, broker))
    assert (await run.result()).status == "cancelled"
    assert [c.label for c in broker.calls] == ["a"]


async def test_a_broker_without_gates_refuses_them(tmp_path: Path) -> None:
    class Old(MemoryAgentBroker):
        ask_gate = None  # type: ignore[assignment]

    run, events = await _run(_gated(), _services(tmp_path, Old({"a": "x"})))
    assert events[-1].payload["reason"] == "gate_refused:ask:no_human"


async def test_a_human_answer_is_journalled_and_resume_does_not_ask_again(tmp_path: Path) -> None:
    first = MemoryAgentBroker({"a": "x", "b": "y"}, gate_answers={"ask": True})
    await _run(_gated(), _services(tmp_path, first))
    (record,) = [r for r in PlanJournal(tmp_path).records() if r.get("kind") == "gate"]
    assert (record["answered"], record["ok"], record["class"]) == (True, True, "cost")

    second = MemoryAgentBroker({"a": "x", "b": "y"})  # no human now
    run, events = await _run(_gated(), _services(tmp_path, second, attempt_id="attempt-2"))
    asked_again = [e for e in events if e.kind is HUMAN]
    assert ((await run.result()).status, second.gate_calls, asked_again) == ("success", [], [])
    assert _messages(events, "gate")[0]["replayed"] is True


async def test_a_rejection_is_an_answer_too_until_from_phase_drops_it(tmp_path: Path) -> None:
    raw = _gated()
    await _run(raw, _services(tmp_path, MemoryAgentBroker({"a": "x"}, gate_answers={"ask": False})))
    again = MemoryAgentBroker({"a": "x", "b": "y"}, gate_answers={"ask": True})
    run, _ = await _run(raw, _services(tmp_path, again, attempt_id="attempt-2"))
    assert (await run.result()).status == "cancelled" and again.gate_calls == []
    PlanJournal(tmp_path).invalidate_from_phase(_compile(raw).program, "p1")
    run, _ = await _run(raw, _services(tmp_path, again, attempt_id="attempt-3"))
    assert (await run.result()).status == "success" and len(again.gate_calls) == 1


async def test_a_refusal_is_not_an_answer_so_resume_asks(tmp_path: Path) -> None:
    _run1, events = await _run(_gated(), _services(tmp_path, MemoryAgentBroker({"a": "x"})))
    assert not [r for r in PlanJournal(tmp_path).records() if r.get("kind") == "gate"]
    # only a human's answer replays: a record that is not one is asked again
    (asked,) = [e.payload for e in events if e.kind is HUMAN]
    forged = {"call_key": asked["call_key"], "kind": "gate", "ok": True, "answered": False}
    PlanJournal(tmp_path).append(forged)
    human = MemoryAgentBroker({"a": "x", "b": "y"}, gate_answers={"ask": True})
    run, _ = await _run(_gated(), _services(tmp_path, human, attempt_id="attempt-2"))
    assert (await run.result()).status == "success" and len(human.gate_calls) == 1


async def test_a_changed_question_asks_again(tmp_path: Path) -> None:
    await _run(
        _gated(), _services(tmp_path, MemoryAgentBroker({"a": "x"}, gate_answers={"ask": True}))
    )
    changed = _gated()
    changed["phases"][0]["steps"][0]["prompt"] = "first, again"  # a reruns live
    human = MemoryAgentBroker({"a": "OTHER", "b": "y"}, gate_answers={"ask": True})
    await _run(changed, _services(tmp_path, human, attempt_id="attempt-2"))
    assert len(human.gate_calls) == 1  # the rendered question changed: content-addressed


async def test_a_gate_whose_when_is_empty_is_not_raised(tmp_path: Path) -> None:
    broker = MemoryAgentBroker({"a": [], "b": "y"})
    run, events = await _run(_gated(when="steps.a.output"), _services(tmp_path, broker))
    assert (await run.result()).status == "success"
    assert broker.gate_calls == [] and _messages(events, "gate")[0]["raised"] is False


async def test_a_spent_budget_refuses_the_gate_without_asking(tmp_path: Path) -> None:
    broker = MemoryAgentBroker({"a": "x"}, gate_answers={"ask": True}, usage=500)
    raw = _gated()
    raw["budget"]["max_tokens"] = 500
    _run_, events = await _run(raw, _services(tmp_path, broker))
    assert broker.gate_calls == []
    assert events[-1].payload["reason"] == "gate_refused:ask:budget_exhausted:tokens"


async def test_a_gate_wait_is_not_charged_to_the_wall_clock(tmp_path: Path) -> None:
    class SlowHuman(MemoryAgentBroker):
        async def ask_gate(self, request):
            await asyncio.sleep(1.2)
            return GateDecision(approved=True, answered=True, decided_by="h")

    broker = SlowHuman({"a": "x", "b": "y"})
    raw = _gated()
    raw["budget"]["max_wallclock_sec"] = 1
    run, events = await _run(raw, _services(tmp_path, broker))
    assert (await run.result()).status == "success"
    assert [c.label for c in broker.calls] == ["a", "b"]


async def test_cancel_while_a_gate_waits_ends_the_run(tmp_path: Path) -> None:
    hold = asyncio.Event()

    class Waiting(MemoryAgentBroker):
        async def ask_gate(self, request):
            await hold.wait()
            return GateDecision(approved=True, answered=True)

    run = await PlanEngine().start(_compile(_gated()), _services(tmp_path, Waiting({"a": "x"})))
    events = []
    async for event in run.events():
        events.append(event)
        if event.kind is HUMAN:
            await run.cancel("user stop")
    assert events[-1].kind is EventKind.RUN_CANCELLED
    assert (await run.result()).status == "cancelled"


# ── nested plan ─────────────────────────────────────────────────────────────


async def test_a_nested_template_runs_in_its_own_frame(tmp_path: Path) -> None:
    raw = _plan(
        {
            "id": "rev",
            "kind": "plan",
            "template": "adversarial-review",
            "args": {"target": "src", "max_findings": 2, "skeptics": 1},
        }
    )
    broker = MemoryAgentBroker(
        {
            "rev/finder": {"findings": [{"title": "t", "evidence": "e", "severity": "high"}]},
            "rev/skeptic": {"refuted": False, "reason": "real"},
        }
    )
    run, events = await _run(raw, _services(tmp_path, broker))
    assert (await run.result()).status == "success"
    assert _completed(events) == ["rev/finder", "rev/skeptic[0]#0"]
    out = _result(tmp_path)["output"]["rev"]
    assert out["survivors"] == [{"title": "t", "evidence": "e", "severity": "high"}]
    nodes = [r["node"] for r in PlanJournal(tmp_path).records()]
    assert nodes == ["rev/find", "rev/verify__votes.stages[0]"]
    phases = [m["id"] for m in _messages(events, "phase")]
    assert phases == ["p1", "rev/find", "rev/verify"]
    assert broker.calls[0].instructions.startswith("You review code")


# ── judge_panel: a near-tie raises competing_hypotheses ────────────────────


def _panel(margin: float = 1) -> dict[str, Any]:
    return _plan(
        {"id": "cands", "kind": "agent", "prompt": "candidates"},
        {
            "id": "pick",
            "kind": "pattern",
            "pattern": "judge_panel",
            "over": "steps.cands.output",
            "prompt": "score {{item}}",
            "max_items": 3,
            "judges": 2,
            "margin": margin,
        },
        output={"pick": "steps.pick.output", "tie": "steps.pick__tie.output"},
    )


def _scores(by_item: dict[int, int]):
    return lambda req: {"score": by_item[req.metadata["item_index"]], "reason": "r"}


async def test_a_clear_winner_is_picked_without_asking(tmp_path: Path) -> None:
    broker = MemoryAgentBroker({"cands": ["A", "B"], "pick": _scores({0: 9, 1: 4})})
    run, _events = await _run(_panel(), _services(tmp_path, broker))
    out = _result(tmp_path)["output"]
    assert (await run.result()).status == "success" and broker.gate_calls == []
    assert (out["pick"]["status"], out["pick"]["winner"]) == ("picked", "A")
    assert out["tie"] == {"raised": False, "approved": None, "class": "competing_hypotheses"}


async def test_a_near_tie_picks_nothing_and_asks_a_human(tmp_path: Path) -> None:
    broker = MemoryAgentBroker(
        {"cands": ["A", "B"], "pick": _scores({0: 7, 1: 8})}, gate_answers={"pick__tie": True}
    )
    run, events = await _run(_panel(), _services(tmp_path, broker))
    assert (await run.result()).status == "success"
    (asked,) = [e.payload for e in events if e.kind is HUMAN]
    assert asked["class"] == "competing_hypotheses" and '"B"' in asked["prompt"]
    doc = _result(tmp_path)
    assert (doc["output"]["pick"]["status"], doc["output"]["pick"]["winner"]) == (
        "competing_hypotheses",
        None,
    )
    assert doc["output"]["pick"]["competing"] == ["B", "A"]
    assert doc["gates"]["pick__tie"]["approved"] is True


async def test_a_near_tie_with_no_human_stops_the_run(tmp_path: Path) -> None:
    broker = MemoryAgentBroker({"cands": ["A", "B"], "pick": _scores({0: 8, 1: 8})})
    run, events = await _run(_panel(), _services(tmp_path, broker))
    assert (await run.result()).status == "cancelled"
    assert events[-1].payload["reason"] == "gate_refused:pick__tie:no_human"


# ── K3: retrieval refs on journal records ───────────────────────────────────


async def test_retrieval_refs_are_journalled_and_replayed_with_the_call(tmp_path: Path) -> None:
    ref = {"tool": "knowledge_query", "query": "q", "project": "p", "passages": []}

    class Reading(MemoryAgentBroker):
        def _result(self, request):
            base = super()._result(request)
            return replace(base, retrieval=(ref,))

    raw = _plan({"id": "a", "kind": "agent", "prompt": "read"})
    await _run(raw, _services(tmp_path, Reading({"a": "x"})))
    (record,) = PlanJournal(tmp_path).records()
    assert record["retrieval"] == [ref]


def test_agent_turn_result_carries_retrieval() -> None:
    assert AgentTurnResult(ok=True).retrieval == ()


# ── research-round template ─────────────────────────────────────────────────


def test_research_round_reads_the_research_role_prompts_not_copies() -> None:
    from r4.research_loop.team import RESEARCH_ROLES
    from r4.teams.plan.templates import load_template as raw_template

    raw = raw_template("research-round")
    assert raw["agents"]["reviewer"]["instructions"] == {"ref": "research_role:PR"}
    resolved = load_template("research-round")
    for name, abbr in (("planner", "RL"), ("experimenter", "ED"), ("analyst", "DA")):
        assert resolved["agents"][name]["instructions"] == RESEARCH_ROLES[abbr]["system_prompt"]
    assert resolved["agents"]["reviewer"]["instructions"] == RESEARCH_ROLES["PR"]["system_prompt"]


def test_an_unresolved_reference_never_runs() -> None:
    from r4.teams.plan.templates import load_template as raw_template

    spec, problems = spec_from_plan(raw_template("research-round"), {"objective": "x"}, project="p")
    assert spec is None and any(p.path.endswith("instructions") for p in problems)


@pytest.mark.parametrize(
    ("args", "warned"),
    [
        ({"objective": "x"}, True),
        ({"objective": "x", "producer_model": "m1", "reviewer_model": "m1"}, True),
        ({"objective": "x", "producer_model": "m1", "reviewer_model": "m2"}, False),
    ],
)
def test_research_round_warns_when_the_reviewer_model_is_the_producers(
    args: dict, warned: bool
) -> None:
    program = _compile(load_template("research-round"), args)
    warnings = [w.message for w in program.warnings if "not independent" in w.message]
    assert bool(warnings) is warned
    assert bool(plan_summary(program.program)["warnings"]) is warned


def test_research_round_is_bounded_and_gates_a_near_tie() -> None:
    program = _compile(load_template("research-round"), {"objective": "x"})
    assert program.agent_bound == 1 + 3 + 3 + 3 * 3
    assert [g["class"] for g in plan_summary(program.program)["gates"]] == ["competing_hypotheses"]


async def test_research_round_runs_end_to_end_on_scripted_turns(tmp_path: Path) -> None:
    hyps = [{"statement": f"H{i}", "rationale": "r", "test": "t"} for i in range(2)]
    design = {
        "hypothesis": "H",
        "protocol": "p",
        "controls": "c",
        "resources": "1 GPU",
        "refuting_outcome": "o",
    }
    broker = MemoryAgentBroker(
        {
            "planner": {"hypotheses": hyps},
            "experimenter": design,
            "analyst": {"test": "t", "power": "p", "threats": "none"},
            "reviewer": _scores({0: 9, 1: 3}),
        }
    )
    services = _services(tmp_path, broker)
    run = await PlanEngine().start(
        _compile(load_template("research-round"), {"objective": "why", "judges": 2}), services
    )
    _events = [event async for event in run.events()]
    assert (await run.result()).status == "success"
    decision = _result(tmp_path)["output"]["decision"]
    assert (decision["status"], decision["winner_index"]) == ("picked", 0)
    assert broker.gate_calls == []
