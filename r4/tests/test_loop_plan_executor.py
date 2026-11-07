"""ADR-092 slice 6: a team plan as the research loop's per-iteration executor.

End to end on the real path: ``LocalRuntime`` (the one driver) runs
``PlanIterationExecutor``, which submits the ``research-round`` template
through ``TeamService`` and the plan engine. Agent turns are scripted
(``MemoryAgentBroker``); a plan gate is asked through the real
``RoleTurnAgentBroker`` gate path over the loop's own gate lane, so a gate
lands on the loop's approvals list and the human's answer comes back to it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from r4_teamengine_spi.testing import MemoryAgentBroker

from r4.runtime import LocalRuntime, LoopSpec
from r4.runtime import plan_executor as pe
from r4.runtime.api import IterationContext
from r4.teams import service as team_service

TERMINAL = ("stopped", "completed", "error")
HYPS = [{"statement": f"H{i}", "rationale": "r", "test": "t"} for i in range(2)]
DESIGN = {
    "hypothesis": "MOF-5 binds CO2 at 1 bar",
    "protocol": "p",
    "controls": "c",
    "resources": "r",
    "refuting_outcome": "o",
}


def _script(reviewer) -> dict[str, Any]:
    return {
        "planner": {"hypotheses": HYPS},
        "experimenter": DESIGN,
        "analyst": {"test": "t", "power": "p", "threats": "none"},
        "reviewer": reviewer,
    }


PICKS_FIRST = _script(lambda req: {"score": 9 - 5 * req.metadata["item_index"], "reason": "r"})
UNDECIDED = _script(None)  # every judge fails: no quorum, no pick
TIE = _script({"score": 7, "reason": "r"})  # a near-tie raises the competing gate


class _Knowledge:
    def __init__(self, graphs=("g-proj",)) -> None:
        self._graphs = list(graphs)
        self.proposals: list[dict] = []
        self.decisions: list = []
        self.ingests: list = []

    def graphs(self, project_id=None, **_kw):
        return [{"graph_id": g, "project_id": project_id} for g in self._graphs]

    def graph_propose(self, graph_id, **kw):
        self.proposals.append({"graph_id": graph_id, **kw})
        return {"proposal_id": f"prop-{len(self.proposals)}", "status": "pending"}

    def graph_decide(self, *a, **k):
        self.decisions.append((a, k))  # the loop never decides a proposal

    def ingest(self, *a, **k):
        self.ingests.append((a, k))  # the loop never ingests


class _Client(SimpleNamespace):
    catalog_writes: list

    def create_scientific_catalog(self, **kw):
        self.catalog_writes.append(kw)  # a catalog candidate is never written
        return {"catalog_id": "c"}

    def list_models(self):
        return []


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Scripted turns, the loop's real gate lane, runs under tmp_path."""
    monkeypatch.setattr(team_service, "TEAM_RUNS_DIR", tmp_path / "runs")
    from r4.companion import knowledge_write_gate

    monkeypatch.setattr(
        knowledge_write_gate, "acquisition_constraints", lambda _c: {"mode": "open"}
    )
    state: dict[str, Any] = {"script": PICKS_FIRST, "brokers": [], "specs": []}

    def _broker(self, spec, run_id, meter):
        from r4.teams.agent_broker import RoleTurnAgentBroker

        broker = MemoryAgentBroker(dict(state["script"]))
        lane = RoleTurnAgentBroker(None, meter=meter, run_id=run_id, gate_lane=self._gate_lane)
        broker.ask_gate = lane.ask_gate  # the real gate path over the loop's lane
        state["brokers"].append(broker)
        state["specs"].append(spec)
        return broker

    monkeypatch.setattr(team_service.TeamService, "_agent_broker", _broker)
    client = _Client(
        config=SimpleNamespace(model=None, project_id="proj"),
        knowledge=_Knowledge(),
        catalog_writes=[],
    )
    state["client"] = client
    state["runtime"] = LocalRuntime(tmp_path / "rt.db", poll_seconds=0.01)
    return state


def _start(world, *, mode="ask", max_iterations=2, args=None, hours=0.0):
    executor = pe.PlanIterationExecutor(
        world["client"], "proj", args=args or {"judges": 2}, poll_seconds=0.02
    )
    spec = LoopSpec(
        project_id="proj",
        research_goal="capture CO2 with MOF-5",
        max_iterations=max_iterations,
        iteration_plan="research-round",
        mode=mode,
        max_wall_clock_hours=hours,
    )
    return world["runtime"].start_loop(spec, executor=executor), executor


def _wait(predicate, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def _pending(world, loop_id, kind):
    runtime = world["runtime"]
    return _wait(
        lambda: next(
            (r for r in runtime.list_approvals(loop_id) if r["action"]["kind"] == kind), None
        )
    )


def _terminal(world, loop_id):
    runtime = world["runtime"]
    return _wait(lambda: (s := runtime.get_loop(loop_id)).status in TERMINAL and s)


def _summary(world, loop_id, number=None):
    rows = world["runtime"].list_iterations(loop_id)
    row = rows[0] if number is None else next(r for r in rows if r.iteration_number == number)
    return row.summary


# ── the plan runs only after the human's yes; the loop decides ─────────────


def _role_labels(world) -> set:
    """The roles the first run's broker ran (labels without item or model)."""
    return {call.label.split("[")[0].split("@")[0] for call in world["brokers"][0].calls}


def test_ask_mode_parks_then_runs_the_plan_and_publishes_a_proposal(world):
    state, _executor = _start(world)
    approval = _pending(world, state.loop_id, "plan_run")
    time.sleep(0.1)
    before = list(world["brokers"])  # no plan was submitted before the human's answer
    world["runtime"].decide_approval(approval["id"], "once")
    final = _terminal(world, state.loop_id)
    summary = _summary(world, state.loop_id)
    assert {
        "template": approval["action"]["template"],
        "before": before,
        "status": final.status,
        "reason": final.metadata["state_reason"],
        "plan": summary["plan"]["status"],
        "review": summary["review"]["status"],
        "decision": summary["loop_decision"]["decision"],
        # The broker ran the research round's four roles.
        "roles": {"planner", "experimenter", "analyst", "reviewer"} <= _role_labels(world),
    } == {
        "template": "research-round",
        "before": [],
        "status": "completed",
        "reason": "published:the reviewers picked a design",
        "plan": "success",
        "review": "picked",
        "decision": "publish",
        "roles": True,
    }


def test_a_picked_design_is_only_proposed_never_established(world):
    state, _executor = _start(world, mode="auto")
    _terminal(world, state.loop_id)
    (proposal,) = world["client"].knowledge.proposals
    (operation,) = proposal["proposed_operations"]
    knowledge = world["client"].knowledge
    assert {
        "graph": proposal["graph_id"],
        "status": operation["status"],
        "confidence": operation["confidence"],
        "statement": operation["statement"],
        "untrusted": operation["untrusted"],
        "producer": operation["producer"].startswith(
            f"research_loop:{state.loop_id}/iteration:1/plan:"
        ),
        "finding": _summary(world, state.loop_id)["finding"],
        "decided": knowledge.decisions,
        "ingested": knowledge.ingests,
    } == {
        "graph": "g-proj",
        "status": "proposed",
        "confidence": 0.0,
        "statement": DESIGN["hypothesis"],
        "untrusted": True,
        "producer": True,
        "finding": {"status": "proposed", "graph_id": "g-proj", "proposal_id": "prop-1"},
        "decided": [],
        "ingested": [],
    }


def test_a_published_round_records_a_catalog_candidate_and_writes_nothing(world):
    state, _executor = _start(world, mode="auto")
    _terminal(world, state.loop_id)
    candidate = _summary(world, state.loop_id)["catalog_candidate"]
    assert candidate["status"] == "candidate" and candidate["written"] is False
    assert candidate["conclusion"] == DESIGN["hypothesis"]
    assert candidate["evidence_refs"][1] == "knowledge_proposal:prop-1"
    assert candidate["evidence_refs"][0].startswith("team_run:")
    assert world["client"].catalog_writes == []


def test_no_pick_proposes_nothing_and_the_loop_continues(world):
    world["script"] = UNDECIDED
    state, executor = _start(world, mode="auto", max_iterations=2)
    final = _terminal(world, state.loop_id)
    summary = _summary(world, state.loop_id, 1)
    assert {
        "status": (final.status, final.current_iteration),
        "proposals": world["client"].knowledge.proposals,
        "review": summary["review"]["status"],
        "staged": sorted({"finding", "catalog_candidate"} & set(summary)),
        "decision": summary["loop_decision"],
        # Round 2 was told round 1's verdict, as the loop's own words only.
        "told": "Round 1: review undecided" in json.dumps(world["specs"][1].plan),
        "rounds": len(executor.rounds),
    } == {
        "status": ("completed", 2),
        "proposals": [],
        "review": "undecided",
        "staged": [],
        "decision": {"decision": "continue", "reason": "no design passed review"},
        "told": True,
        "rounds": 2,
    }


def test_the_loop_never_writes_to_a_plan_param_what_an_agent_wrote(world):
    world["script"] = UNDECIDED
    state, executor = _start(world, mode="auto", max_iterations=1)
    _terminal(world, state.loop_id)
    context = executor._context_text()
    assert "H0" not in context and DESIGN["hypothesis"] not in context


# ── plan gates go to the loop's inbox ───────────────────────────────────────


def _gate_answers(summary) -> list:
    """The engine's journalled gate answers (D-8) of the round's run."""
    journal = Path(summary["plan"]["result_path"]).with_name("journal.jsonl")
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    return [r for r in records if r.get("kind") == "gate"]


def test_a_plan_gate_parks_on_the_loop_and_the_human_answer_flows_back(world):
    world["script"] = TIE
    state, _executor = _start(world, mode="auto", max_iterations=1)
    gate = _pending(world, state.loop_id, "plan_gate")
    world["runtime"].decide_approval(gate["id"], "once")
    final = _terminal(world, state.loop_id)
    summary = _summary(world, state.loop_id)
    (answer,) = _gate_answers(summary)
    (logged,) = final.metadata["approvals_log"]
    assert {
        "asked": (gate["action"]["class"], gate["action"]["gate"], gate["action"]["untrusted"]),
        "plan": summary["plan"]["status"],
        "review": summary["review"]["status"],
        "decision": summary["loop_decision"]["decision"],
        "status": final.status,
        # A near-tie the human let pass is not a pick: nothing is proposed.
        "proposals": world["client"].knowledge.proposals,
        "staged": sorted({"finding", "catalog_candidate"} & set(summary)),
        # Journalled twice: the engine's gate record (D-8) and the loop's log.
        "answer": (answer["ok"], answer["answered"], answer["decided_by"], answer["class"]),
        "logged": (logged["action"]["kind"], logged["decided_by"]),
    } == {
        "asked": ("competing_hypotheses", "review__tie", True),
        "plan": "success",
        "review": "competing_hypotheses",
        "decision": "continue",
        "status": "completed",
        "proposals": [],
        "staged": [],
        "answer": (True, True, "human", "competing_hypotheses"),
        "logged": ("plan_gate", "human"),
    }


def test_a_gate_asks_even_in_auto_mode_and_a_no_stops_the_loop(world):
    world["script"] = TIE
    state, _executor = _start(world, mode="auto", max_iterations=3)
    gate = _pending(world, state.loop_id, "plan_gate")
    world["runtime"].decide_approval(gate["id"], "no")

    def _settled():
        loop = world["runtime"].get_loop(state.loop_id)
        return loop if loop.current_iteration > 1 or loop.status in TERMINAL else None

    final = _wait(_settled)
    if final.status not in TERMINAL:  # a second round started: stop it, then fail
        world["runtime"].stop_loop(state.loop_id)
    assert final.status == "stopped"
    assert final.metadata["state_reason"].startswith("plan_cancelled")
    assert final.current_iteration == 1


def test_always_on_a_gate_is_a_yes_to_that_gate_only(world):
    world["script"] = TIE
    state, _executor = _start(world, mode="ask", max_iterations=1)
    world["runtime"].decide_approval(_pending(world, state.loop_id, "plan_run")["id"], "once")
    world["runtime"].decide_approval(_pending(world, state.loop_id, "plan_gate")["id"], "always")
    final = _terminal(world, state.loop_id)
    assert final.metadata["mode"] == "ask"


def test_a_host_exit_withdraws_a_waiting_gate(world, monkeypatch):
    from r4.runtime import local

    world["script"] = TIE
    state, _executor = _start(world, mode="auto", max_iterations=1)
    gate = _pending(world, state.loop_id, "plan_gate")
    monkeypatch.setattr(local, "_host_exiting", lambda: True)
    log = _wait(lambda: world["runtime"].get_loop(state.loop_id).metadata.get("approvals_log"))
    assert [(r["id"], r["status"], r["reason"]) for r in log] == [
        (gate["id"], "withdrawn", "host_exit")
    ]
    world["runtime"].stop_loop(state.loop_id)


def test_a_stop_while_a_gate_waits_refuses_it_and_records_no_answer(world):
    world["script"] = TIE
    state, _executor = _start(world, mode="auto", max_iterations=1)
    gate = _pending(world, state.loop_id, "plan_gate")
    world["runtime"].stop_loop(state.loop_id)
    _wait(lambda: world["brokers"] and world["runtime"].list_iterations(state.loop_id))
    summary = _summary(world, state.loop_id)
    journal = Path(summary["plan"]["result_path"]).with_name("journal.jsonl")
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    assert not [r for r in records if r.get("kind") == "gate"]  # a refusal is not an answer
    log = world["runtime"].get_loop(state.loop_id).metadata["approvals_log"]
    assert [(r["id"], r["status"]) for r in log] == [(gate["id"], "withdrawn")]
    assert summary["plan"]["status"] == "cancelled"


# ── ADR-067: the plan's wall clock is min(its own, the loop's remaining) ────


@pytest.mark.parametrize(
    ("own", "remaining", "expected"),
    [
        (3600, 120.7, 120),
        (3600, 7200.0, 3600),
        (None, 90.0, 90),
        (3600, None, 3600),
        (3600, 0.4, 1),
    ],
)
def test_plan_within_loop_budget_takes_the_smaller(own, remaining, expected):
    budget = {"max_tokens": 10} if own is None else {"max_tokens": 10, "max_wallclock_sec": own}
    raw = {"budget": budget}
    out = pe.plan_within_loop_budget(raw, remaining)
    assert out["budget"].get("max_wallclock_sec") == expected
    assert raw["budget"] == budget  # the template itself is untouched


def test_the_submitted_plan_carries_the_loop_s_remaining_wall_clock(world):
    state, _executor = _start(world, mode="auto", max_iterations=1, hours=0.25)
    _terminal(world, state.loop_id)
    (spec,) = world["specs"]
    assert 890 <= spec.budgets.max_wallclock_sec <= 900  # template's own is 3600
    assert _summary(world, state.loop_id)["plan"]["max_wallclock_sec"] == (
        spec.budgets.max_wallclock_sec
    )


def test_an_uncapped_loop_keeps_the_plan_s_own_wall_clock(world):
    state, _executor = _start(world, mode="auto", max_iterations=1)
    _terminal(world, state.loop_id)
    assert world["specs"][0].budgets.max_wallclock_sec == 3600


def test_a_spent_loop_clock_runs_no_plan():
    executor = pe.PlanIterationExecutor(SimpleNamespace(), "proj")
    ctx = IterationContext("L", "proj", 1, "g", 0.5, lambda _a: True, lambda: False)
    summary = executor.run_iteration(ctx)
    assert summary["loop_decision"] == {
        "decision": "stop",
        "reason": "budget_exhausted:wall_clock",
    }


# ── the executor refuses outside the gate, and refuses what it cannot drive ──


def test_called_as_a_plain_executor_it_refuses():
    executor = pe.PlanIterationExecutor(SimpleNamespace(), "proj")
    with pytest.raises(RuntimeError, match="gates it"):
        executor("L", 1, "goal")


def test_only_a_template_with_a_reviewer_verdict_is_a_loop_template():
    with pytest.raises(ValueError, match="cannot run as a loop iteration"):
        pe.PlanIterationExecutor(SimpleNamespace(), "proj", template="code")
    with pytest.raises(ValueError, match="belong to the loop"):
        pe.PlanIterationExecutor(SimpleNamespace(), "proj", args={"objective": "x"})


def test_the_action_the_loop_gates_is_a_plan_run():
    executor = pe.PlanIterationExecutor(SimpleNamespace(), "proj", args={"judges": 2})
    ctx = IterationContext("L", "proj", 3, "g", None, lambda _a: True, lambda: False)
    action = executor.iteration_action(ctx)
    assert action["kind"] == "plan_run" and action["template"] == "research-round"
    assert action["iteration"] == 3 and action["args"] == {"judges": 2}


@pytest.mark.parametrize(
    ("status", "review", "decision"),
    [
        ("success", {"status": "picked"}, "publish"),
        ("partial", {"status": "picked"}, "publish"),
        ("success", {"status": "competing_hypotheses"}, "continue"),
        ("success", {"status": "undecided"}, "continue"),
        ("success", {}, "continue"),
        ("failed", {"status": "picked"}, "stop"),
        ("cancelled", {}, "stop"),
        (None, {}, "stop"),
    ],
)
def test_the_loop_decision_table(status, review, decision):
    assert pe.loop_decision_for(status, review)["decision"] == decision


def test_no_graph_or_a_refused_proposal_is_recorded_not_forced(world, monkeypatch):
    ctx = IterationContext("L", "proj", 1, "g", None, lambda _a: True, lambda: False)
    client = _Client(knowledge=_Knowledge(graphs=()))
    out = pe.propose_finding(client, ctx, {"run_id": "r"}, DESIGN)
    assert out["status"] == "not_proposed" and "no knowledge graph" in out["reason"]
    from r4.companion import knowledge_write_gate

    monkeypatch.setattr(
        knowledge_write_gate, "acquisition_constraints", lambda _c: {"mode": "disabled"}
    )
    client = _Client(knowledge=_Knowledge())
    out = pe.propose_finding(client, ctx, {"run_id": "r"}, DESIGN)
    assert out["status"] == "not_proposed" and "DISABLED" in out["reason"]
    assert client.knowledge.proposals == []


# ── the chat tool ───────────────────────────────────────────────────────────


def _start_tool(client):
    from r4.companion.research_loop_tools import build_research_loop_tools

    return next(t for t in build_research_loop_tools(client) if t["name"] == "research_loop_start")[
        "execute"
    ]


def test_the_chat_tool_starts_a_local_plan_loop_in_ask_mode(world, monkeypatch, tmp_path):
    monkeypatch.setenv("R4_RUNTIME_MODE", "local")
    monkeypatch.setenv("R4_RUNTIME_DB", str(tmp_path / "tool.db"))
    client = world["client"]
    client.get_platform_project = lambda _p: (_ for _ in ()).throw(RuntimeError("offline"))
    client.get = lambda path, **_k: {"budget": {"max_wall_clock_hours": 2}}
    reply = json.loads(
        _start_tool(client)(research_goal="g", project_id="proj", plan="research-round")
    )
    assert reply["type"] == "research_loop_started"
    assert reply["iteration_plan"] == "research-round" and reply["mode"] == "ask"
    runtime = LocalRuntime(tmp_path / "tool.db")
    meta = runtime.get_loop(reply["loop_id"]).metadata
    assert meta["max_wall_clock_hours"] == 2.0
    runtime.stop_loop(reply["loop_id"])


def test_the_chat_tool_inherits_the_project_policy_and_the_tighter_cap(
    world, monkeypatch, tmp_path
):
    monkeypatch.setenv("R4_RUNTIME_MODE", "local")
    monkeypatch.setenv("R4_RUNTIME_DB", str(tmp_path / "tool.db"))
    client = world["client"]
    client.get_platform_project = lambda _p: {
        "project": {"ask_mode": "profile", "pause_triggers": ["cost"]}
    }
    client.get = lambda path, **_k: {"budget": {"max_wall_clock_hours": 2}}
    reply = json.loads(
        _start_tool(client)(
            research_goal="g", project_id="proj", plan="research-round", max_wall_clock_hours=1
        )
    )
    meta = LocalRuntime(tmp_path / "tool.db").get_loop(reply["loop_id"]).metadata
    assert meta["mode"] == "profile" and meta["pause_profile"] == {"triggers": ["cost"]}
    assert meta["max_wall_clock_hours"] == 1.0
    LocalRuntime(tmp_path / "tool.db").stop_loop(reply["loop_id"])


def test_the_chat_tool_refuses_a_server_plan_loop_without_a_source(monkeypatch):
    monkeypatch.setenv("R4_RUNTIME_MODE", "umrs")
    client = _Client(
        config=SimpleNamespace(project_id="proj"),
        start_loop=lambda *a, **k: pytest.fail("nothing is sent to UMRS"),
        get_platform_project=lambda _p: {},
        get=lambda *_a, **_k: {},
    )
    reply = json.loads(
        _start_tool(client)(research_goal="g", project_id="proj", plan="research-round")
    )
    assert reply["type"] == "research_loop_error"
    assert reply["code"] == "plan_iteration_needs_server_venue"
    # slice 8b: UMRS runs the loop's rounds on its server venue; the runs'
    # models need a venue source, refused by name before anything is sent
    assert "venue source" in reply["message"]
