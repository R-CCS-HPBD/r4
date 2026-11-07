"""The plan journal and resume (ADR-092 D-8).

The journal is an attempt-local memo keyed by what a call sends. A new
attempt over the same run directory replays every unchanged ``ok`` call at
zero model cost; changed, failed and invalidated calls run live.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from r4_contracts.team_engine.events import EventKind
from r4_teamengine_spi.testing import MemoryAgentBroker, make_engine_context, make_services

from r4.teams.plan import PlanEngine, PlanJournal, spec_from_plan


def _plan(stage_two_prompt: str = "second {{prev}}") -> dict[str, Any]:
    """Three phases: a lone agent, a two-stage map over its output, a final agent."""
    return {
        "plan_version": 1,
        "name": "resume",
        "description": "a plan to resume",
        "budget": {"max_tokens": 100_000},
        "phases": [
            {
                "id": "list",
                "title": "List",
                "steps": [{"id": "src", "kind": "agent", "prompt": "list"}],
            },
            {
                "id": "work",
                "title": "Work",
                "steps": [
                    {
                        "id": "m",
                        "kind": "map",
                        "over": "steps.src.output",
                        "max_items": 10,
                        "stages": [
                            {"kind": "agent", "label": "a", "prompt": "first {{item}}"},
                            {"kind": "agent", "label": "b", "prompt": stage_two_prompt},
                        ],
                    }
                ],
            },
            {
                "id": "sum",
                "title": "Sum",
                "steps": [{"id": "fin", "kind": "agent", "prompt": "sum {{steps.m.output|json}}"}],
            },
        ],
        "output": {"result": "steps.fin.output", "items": "steps.m.output"},
    }


def _script(**overrides: Any) -> dict[str, Any]:
    script: dict[str, Any] = {
        "src": ["x", "y", "z"],
        "a": lambda r: f"a{r.metadata['item_index']}",
        "b": lambda r: f"b{r.metadata['item_index']}",
        "fin": "done",
    }
    script.update(overrides)
    return script


async def _attempt(tmp_path: Path, raw: dict[str, Any], broker: MemoryAgentBroker, n: int):
    """Attempt ``n`` of the run ``run-1`` in ``tmp_path``."""
    spec, problems = spec_from_plan(raw, {}, project="p")
    assert not problems, problems
    services = make_services(run_id="run-1", attempt_id=f"attempt-{n}", agents=broker)
    services.run = replace(services.run, working_dir=str(tmp_path))
    run = await PlanEngine().start(PlanEngine().compile(spec, make_engine_context()), services)
    events = [event async for event in run.events()]
    return run, events, json.loads((tmp_path / "result.json").read_text())


async def test_unchanged_resume_makes_zero_broker_calls(tmp_path):
    first = MemoryAgentBroker(_script())
    _run1, _events1, result1 = await _attempt(tmp_path, _plan(), first, 1)
    assert len(first.calls) == 1 + 3 * 2 + 1

    second = MemoryAgentBroker({})  # unscripted: any live call would fail
    run2, events2, result2 = await _attempt(tmp_path, _plan(), second, 2)
    completed = [e.payload for e in events2 if e.kind is EventKind.AGENT_COMPLETED]

    assert {
        "live_calls": second.calls,
        "outputs": (result1["output"], result2["output"]),
        "status": (result2["status"], result2["attempt"]),
        "budget": (result2["budget"]["replayed"], result2["budget"]["agents_started"]),
        "replayed_events": [p["replayed"] for p in completed],
        "tokens": (await run2.metrics()).total_tokens,
    } == {
        "live_calls": [],
        "outputs": ({"result": "done", "items": ["b0", "b1", "b2"]},) * 2,
        "status": ("success", "attempt-2"),
        "budget": (8, 0),
        "replayed_events": [True] * 8,
        "tokens": 0,
    }


async def test_a_replayed_object_renders_downstream_prompts_unchanged(tmp_path):
    """A replayed output is the live output, key order included.

    Downstream prompts render it with ``|json`` and their journal keys hash
    the rendered prompt, so a journal that reordered an object's keys would
    make every reader of a structured step run live again on resume.
    """
    finding = {"title": "off-by-one", "evidence": "line 3", "severity": "high"}
    raw = _plan()
    raw["phases"][1]["steps"][0]["stages"][0]["prompt"] = "first {{item|json}}"
    first = MemoryAgentBroker(_script(src=[finding, dict(finding, title="nullable")]))
    _run1, _events1, result1 = await _attempt(tmp_path, raw, first, 1)

    second = MemoryAgentBroker({})  # unscripted: any live call would fail
    _run2, _events2, result2 = await _attempt(tmp_path, raw, second, 2)

    assert ([c.label for c in second.calls], result2["output"]) == ([], result1["output"])


async def test_an_edited_last_stage_prompt_reruns_only_the_changed_calls(tmp_path):
    await _attempt(tmp_path, _plan(), MemoryAgentBroker(_script()), 1)

    second = MemoryAgentBroker(_script(b=lambda r: f"B{r.metadata['item_index']}"))
    _run2, _events2, result2 = await _attempt(
        tmp_path, _plan("second, harder: {{prev}}"), second, 2
    )

    # stage two changed, and the final agent reads its output, so it reruns too
    assert sorted(c.label for c in second.calls) == ["b[0]", "b[1]", "b[2]", "fin"]
    assert result2["output"]["items"] == ["B0", "B1", "B2"]
    assert result2["steps"]["m"]["replayed"] == 3  # stage one replayed for every item


async def test_null_records_rerun_on_resume(tmp_path):
    flaky = _script(
        a=lambda r: None if r.metadata["item_index"] == 1 else f"a{r.metadata['item_index']}"
    )
    _run1, _events1, result1 = await _attempt(tmp_path, _plan(), MemoryAgentBroker(flaky), 1)
    assert result1["output"]["items"] == ["b0", None, "b2"]
    assert result1["status"] == "partial"

    second = MemoryAgentBroker(_script())
    _run2, _events2, result2 = await _attempt(tmp_path, _plan(), second, 2)

    # item 1 runs both stages live; the summary's input changed, so it reruns
    assert sorted(c.label for c in second.calls) == ["a[1]", "b[1]", "fin"]
    assert result2["output"]["items"] == ["b0", "b1", "b2"]
    assert result2["status"] == "success"


async def test_invalidate_from_phase_drops_that_phase_and_every_later_one(tmp_path):
    raw = _plan()
    await _attempt(tmp_path, raw, MemoryAgentBroker(_script()), 1)
    journal = PlanJournal(tmp_path)
    spec, _ = spec_from_plan(raw, {}, project="p")
    assert spec is not None

    dropped = journal.invalidate_from_phase(spec.plan, "work")

    assert dropped == 7  # six map calls and the summary
    assert {r["phase"] for r in journal.records()} == {"list"}
    second = MemoryAgentBroker(_script())
    await _attempt(tmp_path, raw, second, 2)
    assert sorted(c.label for c in second.calls) == [
        "a[0]",
        "a[1]",
        "a[2]",
        "b[0]",
        "b[1]",
        "b[2]",
        "fin",
    ]
    with pytest.raises(KeyError, match="no phase 'nope'"):
        journal.invalidate_from_phase(spec.plan, "nope")


def test_journal_load_skips_torn_lines_and_keeps_ok_over_a_later_failure(tmp_path):
    journal = PlanJournal(tmp_path)
    journal.append({"call_key": "k1", "ok": True, "output": 1, "phase": "p"})
    journal.append({"call_key": "k1", "ok": False, "output": None, "phase": "p"})
    journal.append({"call_key": "k2", "ok": False, "output": None, "phase": "p"})
    journal.append({"call_key": "k2", "ok": True, "output": 2, "phase": "p"})
    with journal.path.open("a") as fh:
        fh.write('{"call_key": "k3", "ok": tr')  # a crash mid-append

    memo = journal.load()

    assert memo["k1"]["output"] == 1 and memo["k2"]["output"] == 2
    assert "k3" not in memo
    assert len(journal.records()) == 4


@pytest.mark.parametrize(
    "torn",
    ['{"call_key": "k2", "ok": tr', '{"call_key": "k2", "ok": true, "output": 2}'],
    ids=["mid-record", "before-the-newline"],
)
def test_a_record_after_a_torn_tail_starts_on_its_own_line(tmp_path, torn):
    """A crash mid-append leaves no newline; the next attempt's first record
    must not join the torn line and be lost with it (D-8: a crash loses at
    most the call in flight)."""
    journal = PlanJournal(tmp_path)
    journal.append({"call_key": "k1", "ok": True, "output": 1, "phase": "p"})
    with journal.path.open("a") as fh:
        fh.write(torn)  # the crash

    journal.append({"call_key": "k3", "ok": True, "output": 3, "phase": "p"})
    journal.append({"call_key": "k4", "ok": True, "output": 4, "phase": "p"})

    memo = journal.load()
    assert {key: memo[key]["output"] for key in ("k1", "k3", "k4")} == {"k1": 1, "k3": 3, "k4": 4}
    assert ("k2" in memo) is torn.endswith("}")  # a complete record survives its tear too


def _resumed_spec(tmp_path: Path, raw: dict[str, Any]):
    """Attempt 1's spec, and the spec TeamService.resume rebuilds from what
    attempt 1 wrote: plan.json and params.json, with the manifest's
    ``max_tokens`` and ``concurrency`` (the lowered values) as overrides."""
    first, problems = spec_from_plan(raw, {}, project="p")
    assert not problems and first is not None
    written = json.loads(json.dumps(first.plan))  # plan.json
    again, problems = spec_from_plan(
        written,
        dict(first.plan["_bound_params"]),  # params.json
        project="p",
        max_tokens=first.budgets.max_tokens,
        concurrency=first.topology.max_parallel,
    )
    assert not problems and again is not None
    return first, again


def test_a_resume_rebuilds_the_same_plan_hash_when_the_plan_named_no_concurrency(tmp_path):
    raw = _plan()
    assert "concurrency" not in raw
    first, again = _resumed_spec(tmp_path, raw)

    hashes = [PlanEngine().compile(s, make_engine_context()).plan_hash for s in (first, again)]
    assert hashes[0] == hashes[1]
    assert first.plan == again.plan


async def test_a_replayed_answer_still_names_the_tools_refused_when_it_was_given(tmp_path):
    """D-6: the refusal is named in the result, on the attempt that paid and
    on every attempt that replays that answer."""

    class _Refusing(MemoryAgentBroker):
        async def run_turn(self, request):
            result = await super().run_turn(request)
            note = "[refused tools: shell_exec; no approval lane: risky tools fail closed]"
            return replace(result, text=f"{result.text}\n\n{note}")

    _run1, _events1, result1 = await _attempt(tmp_path, _plan(), _Refusing(_script()), 1)
    second = MemoryAgentBroker({})
    _run2, events2, result2 = await _attempt(tmp_path, _plan(), second, 2)
    fin = next(
        e.payload
        for e in events2
        if e.kind is EventKind.AGENT_COMPLETED and e.payload["label"] == "fin"
    )

    assert second.calls == []
    assert result1["steps"]["fin"]["refused_tools"] == ["shell_exec"]
    assert result2["steps"]["fin"]["refused_tools"] == ["shell_exec"]
    assert (fin["replayed"], fin["refused_tools"]) == (True, ["shell_exec"])


async def test_the_journal_records_every_live_call_with_its_attempt(tmp_path):
    await _attempt(tmp_path, _plan(), MemoryAgentBroker(_script()), 1)
    records = PlanJournal(tmp_path).records()

    keys = {"call_key", "label", "step", "phase", "ok", "output", "reason", "usage", "model"}
    b1 = next(r for r in records if r["label"] == "b[1]")

    assert {
        "count": len(records),
        "missing_keys": [
            sorted(keys | {"attempt"} - set(r)) for r in records if not keys <= set(r)
        ],
        "attempts": {r["attempt"] for r in records},
        "b1": (b1["step"], b1["phase"], b1["output"]),
    } == {"count": 8, "missing_keys": [], "attempts": {"attempt-1"}, "b1": ("m", "work", "b1")}


async def test_resume_accepts_this_runs_checkpoint_and_refuses_another_run(tmp_path):
    from r4_teamengine_spi.errors import IncompatibleCheckpoint

    run1, _events, _result = await _attempt(tmp_path, _plan(), MemoryAgentBroker(_script()), 1)
    ref = await run1.checkpoint()

    spec, _ = spec_from_plan(_plan(), {}, project="p")
    services = make_services(run_id="run-1", attempt_id="attempt-2", agents=MemoryAgentBroker({}))
    services.run = replace(services.run, working_dir=str(tmp_path))
    run2 = await PlanEngine().start(PlanEngine().compile(spec, make_engine_context()), services)
    await run2.resume(ref)
    events = [e async for e in run2.events()]

    assert events[0].payload["resumed_from"] == "attempt-1"
    assert events[-1].kind is EventKind.RUN_COMPLETED
    with pytest.raises(IncompatibleCheckpoint, match="run"):
        await run2.resume(replace(ref, run_id="run-2"))


async def test_a_null_record_keeps_a_bounded_excerpt_of_what_the_agent_said(tmp_path):
    """A schema_invalid null carried no trace of the answer it rejected, which
    made a failed run undiagnosable (live on the stand-alone stack, 2026-10-09)."""
    from r4_contracts.team_engine.services import AgentTurnResult

    class _Rambles(MemoryAgentBroker):
        async def run_turn(self, request):
            self.calls.append(request)
            reason = "schema_invalid:$.findings: required"
            return AgentTurnResult(ok=False, reason=reason, text="I found " + "x" * 5000)

    await _attempt(tmp_path, _plan(), _Rambles({}), 1)
    records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    src = next(r for r in records if r["step"] == "src")
    assert src["ok"] is False and src["text"].startswith("I found")
    assert len(src["text"]) == 2000
