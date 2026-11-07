"""ADR-092 slice 6: the local loop runs a governed iteration (D-29 tail, D-32,
D-33, D-36).

A loop iteration executor declares its action; the one local driver gates it
by the loop's ADR-066 ask policy (an ask-mode loop parks on its own approvals
list until a human decides through the existing decide path, never
self-approved), hands it the loop's remaining ADR-067 wall clock, and acts on
the continue / publish / stop decision it returns. These tests drive the real
``LocalRuntime`` with a scripted executor; ``test_loop_plan_executor.py``
drives the plan executor itself.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from r4.runtime import LocalRuntime, LoopSpec
from r4.runtime import local_policy as lp
from r4.runtime.api import IterationContext
from r4.runtime.hybrid import HybridRuntime
from r4.runtime.umrs import PlanIterationRefused, UmrsRuntime

TERMINAL = ("stopped", "completed", "error")


def _spec(**kw: Any) -> LoopSpec:
    base = {"project_id": "proj", "research_goal": "find the catalyst", "max_iterations": 2}
    return LoopSpec(**{**base, **kw})


class _Executor:
    """A scripted loop iteration executor: records what ran and with what."""

    def __init__(self, decisions: list | None = None, action: dict | None = None) -> None:
        self.decisions = list(decisions or [])
        self.action = action if action is not None else {"kind": "plan_run", "template": "t"}
        self.ran: list[int] = []
        self.contexts: list[IterationContext] = []

    def __call__(self, *_a: Any) -> dict:
        raise AssertionError("a governed executor is never called as a plain one")

    def iteration_action(self, ctx: IterationContext) -> dict:
        return {**self.action, "iteration": ctx.iteration_number}

    def run_iteration(self, ctx: IterationContext) -> dict:
        self.ran.append(ctx.iteration_number)
        self.contexts.append(ctx)
        out: dict = {"iteration": ctx.iteration_number}
        if self.decisions:
            out["loop_decision"] = self.decisions.pop(0)
        return out


def _runtime(tmp_path) -> LocalRuntime:
    return LocalRuntime(tmp_path / "rt.db", poll_seconds=0.01)


def _wait(predicate, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def _terminal(runtime: LocalRuntime, loop_id: str):
    return _wait(lambda: (s := runtime.get_loop(loop_id)).status in TERMINAL and s)


def _pending(runtime: LocalRuntime, loop_id: str) -> dict:
    return _wait(lambda: (rows := runtime.list_approvals(loop_id)) and rows[0])


# ── the policy vocabulary ───────────────────────────────────────────────────


def test_the_local_loop_reads_the_shared_action_table():
    # The table is pinned once, in r4-contracts (test_ask_policy.py).
    from r4_contracts.ask_policy import ACTION_KIND_CLASSES, PLAN_RUN, TRIGGER_CLASSES

    assert lp.ACTION_CLASSES is ACTION_KIND_CLASSES
    assert lp.PLAN_RUN == PLAN_RUN
    assert lp.TRIGGER_CLASSES == frozenset(TRIGGER_CLASSES)
    assert lp.action_classes({"kind": PLAN_RUN}) == ACTION_KIND_CLASSES[PLAN_RUN]
    assert lp.action_classes({"kind": "plan_gate"}) == frozenset()


def test_a_plan_loop_without_a_mode_asks_and_a_plain_loop_is_unchanged():
    assert lp.policy_metadata("", (), plan_loop=True)["mode"] == "ask"
    assert lp.policy_metadata("", (), plan_loop=False) == {}
    assert lp.policy_metadata("auto", (), plan_loop=True)["mode"] == "auto"
    profile = lp.policy_metadata("profile", ["cost", "cost"], plan_loop=True)
    assert profile["pause_profile"] == {"triggers": ["cost"]}


@pytest.mark.parametrize(
    ("mode", "triggers"), [("sometimes", ()), ("profile", ["money"]), ("ask", ["vibes"])]
)
def test_unknown_modes_and_classes_are_refused_by_name(mode, triggers):
    with pytest.raises(ValueError, match="unknown"):
        lp.policy_metadata(mode, triggers, plan_loop=True)


@pytest.mark.parametrize(
    ("metadata", "asks"),
    [
        ({"mode": "ask"}, True),
        ({"mode": "auto"}, False),
        ({}, False),
        ({"mode": "profile", "pause_profile": {"triggers": ["cost"]}}, True),
        ({"mode": "profile", "pause_profile": {"triggers": ["physical_action"]}}, False),
    ],
)
def test_needs_human_follows_the_mode(metadata, asks):
    assert lp.needs_human(metadata, {"kind": "plan_run"}) is asks


def test_the_remaining_wall_clock():
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    assert lp.remaining_wallclock_sec({}, now) is None
    left = lp.remaining_wallclock_sec({"max_wall_clock_hours": 1}, now)
    assert 3590 < left <= 3600
    assert lp.remaining_wallclock_sec({"max_wall_clock_hours": 1}, "2020-01-01T00:00:00") < 0


# ── ask mode parks; only a human answer lets the plan run ───────────────────


def _parked_view(runtime, loop_id, pending) -> dict:
    """What a parked plan loop shows while its question waits."""
    loop = runtime.get_loop(loop_id)
    return {
        "mode": loop.metadata["mode"],
        "kind": pending["action"]["kind"],
        "status": pending["status"],
        "ours": pending["id"].startswith(lp.APPROVAL_PREFIX),
        "phase": loop.phase,
    }


def test_ask_mode_parks_at_the_plan_run_until_a_human_decides(tmp_path):
    runtime, executor = _runtime(tmp_path), _Executor()
    state = runtime.start_loop(_spec(iteration_plan="research-round"), executor=executor)
    pending = _pending(runtime, state.loop_id)
    parked = _parked_view(runtime, state.loop_id, pending)
    time.sleep(0.15)  # many polls: the loop never answers its own question
    ran_while_parked = list(executor.ran)
    listed = runtime.list_approvals(state.loop_id)[0]["id"]

    # The human answers through another runtime instance (a fresh tool call).
    decided = _runtime(tmp_path).decide_approval(pending["id"], "once")
    _wait(lambda: executor.ran == [1])
    second = _pending(runtime, state.loop_id)  # the next plan run asks again
    runtime.decide_approval(second["id"], "once")
    final = _terminal(runtime, state.loop_id)
    log = final.metadata["approvals_log"]
    assert {
        "parked": parked,
        "ran_while_parked": ran_while_parked,
        "listed": listed == pending["id"],
        "decided": (decided["status"], decided["decided_by"]),
        "second": (second["id"] != pending["id"], second["iteration"]),
        "final": (final.status, executor.ran),
        "log": [(r["status"], r["decision"]) for r in log],
    } == {
        "parked": {
            "mode": "ask",
            "kind": "plan_run",
            "status": "pending",
            "ours": True,
            "phase": "awaiting_approval",
        },
        "ran_while_parked": [],
        "listed": True,
        "decided": ("approved", "human"),
        "second": (True, 2),
        "final": ("completed", [1, 2]),
        "log": [("approved", "once")] * 2,
    }


def test_a_human_no_stops_the_loop_and_the_plan_never_runs(tmp_path):
    runtime, executor = _runtime(tmp_path), _Executor()
    state = runtime.start_loop(_spec(mode="ask"), executor=executor)
    runtime.decide_approval(_pending(runtime, state.loop_id)["id"], "no")
    final = _terminal(runtime, state.loop_id)
    assert final.status == "stopped"
    assert final.metadata["state_reason"] == "approval_rejected:plan_run"
    assert executor.ran == []
    (iteration,) = runtime.list_iterations(state.loop_id)
    assert iteration.status == "rejected"


def test_always_turns_the_loop_auto_and_later_runs_do_not_ask(tmp_path):
    runtime, executor = _runtime(tmp_path), _Executor()
    state = runtime.start_loop(_spec(mode="ask", max_iterations=3), executor=executor)
    runtime.decide_approval(_pending(runtime, state.loop_id)["id"], "always")
    final = _terminal(runtime, state.loop_id)
    assert final.status == "completed" and executor.ran == [1, 2, 3]
    assert final.metadata["mode"] == "auto"
    assert len(final.metadata["approvals_log"]) == 1


def test_auto_mode_never_asks(tmp_path):
    runtime, executor = _runtime(tmp_path), _Executor()
    state = runtime.start_loop(_spec(mode="auto"), executor=executor)
    final = _terminal(runtime, state.loop_id)
    assert final.status == "completed" and executor.ran == [1, 2]
    assert "approvals_log" not in final.metadata


@pytest.mark.parametrize(("triggers", "asks"), [(["cost"], True), (["physical_action"], False)])
def test_a_profile_parks_only_when_it_names_cost(tmp_path, triggers, asks):
    runtime, executor = _runtime(tmp_path), _Executor()
    state = runtime.start_loop(
        _spec(mode="profile", pause_triggers=tuple(triggers), max_iterations=1), executor=executor
    )
    if asks:
        _pending(runtime, state.loop_id)
        assert executor.ran == []
        runtime.stop_loop(state.loop_id)
    else:
        assert _terminal(runtime, state.loop_id).status == "completed"
        assert executor.ran == [1]


def test_a_stop_while_parked_withdraws_the_question_and_answers_nothing(tmp_path):
    runtime, executor = _runtime(tmp_path), _Executor()
    state = runtime.start_loop(_spec(mode="ask"), executor=executor)
    pending = _pending(runtime, state.loop_id)
    _runtime(tmp_path).stop_loop(state.loop_id)  # another instance: the store says stopped
    _wait(lambda: runtime.get_loop(state.loop_id).metadata.get("approvals_log"))
    (record,) = runtime.get_loop(state.loop_id).metadata["approvals_log"]
    assert record["id"] == pending["id"]
    assert record["status"] == "withdrawn" and "decided_by" not in record
    assert executor.ran == []
    assert runtime.list_approvals() == []
    with pytest.raises(LookupError):
        runtime.decide_approval(pending["id"], "once")
    _wait(lambda: runtime.list_iterations(state.loop_id))
    assert runtime.list_iterations(state.loop_id)[0].status == "halted"


def test_decisions_are_validated(tmp_path):
    with pytest.raises(ValueError, match="decision must be one of"):
        _runtime(tmp_path).decide_approval("local-appr-x", "yes please")


# ── the loop's own decision ─────────────────────────────────────────────────


def test_a_publish_decision_completes_the_loop_with_its_reason(tmp_path):
    runtime = _runtime(tmp_path)
    executor = _Executor([{"decision": "continue"}, {"decision": "publish", "reason": "picked"}])
    state = runtime.start_loop(_spec(mode="auto", max_iterations=5), executor=executor)
    final = _terminal(runtime, state.loop_id)
    assert final.status == "completed" and executor.ran == [1, 2]
    assert final.metadata["state_reason"] == "published:picked"
    assert final.metadata["decided_at_iteration"] == 2


def test_a_stop_decision_stops_the_loop(tmp_path):
    runtime = _runtime(tmp_path)
    executor = _Executor([{"decision": "stop", "reason": "plan_failed"}])
    state = runtime.start_loop(_spec(mode="auto", max_iterations=5), executor=executor)
    final = _terminal(runtime, state.loop_id)
    assert final.status == "stopped" and executor.ran == [1]
    assert final.metadata["state_reason"] == "plan_failed"


def test_a_plain_executor_never_ends_the_loop_early(tmp_path):
    runtime = _runtime(tmp_path)

    def plain(_loop_id, number, _goal):
        return {"loop_decision": {"decision": "stop", "reason": "not a control signal"}}

    state = runtime.start_loop(_spec(max_iterations=3), executor=plain)
    final = _terminal(runtime, state.loop_id)
    assert final.status == "completed" and final.current_iteration == 3
    assert "state_reason" not in final.metadata
    assert "mode" not in final.metadata  # a plain loop's policy is unchanged


# ── ADR-067: the wall clock ─────────────────────────────────────────────────


def test_the_remaining_wall_clock_reaches_the_iteration(tmp_path):
    runtime, executor = _runtime(tmp_path), _Executor()
    state = runtime.start_loop(
        _spec(mode="auto", max_iterations=1, max_wall_clock_hours=0.5), executor=executor
    )
    _terminal(runtime, state.loop_id)
    remaining = executor.contexts[0].remaining_wallclock_sec
    assert 1790 < remaining <= 1800
    uncapped = _Executor()
    state = runtime.start_loop(_spec(mode="auto", max_iterations=1), executor=uncapped)
    _terminal(runtime, state.loop_id)
    assert uncapped.contexts[0].remaining_wallclock_sec is None


def test_a_spent_wall_clock_stops_the_loop_at_the_boundary(tmp_path):
    runtime, executor = _runtime(tmp_path), _Executor()

    # 2026-10-24: the cap was 20 ms with a 50 ms iteration. On a loaded host the
    # time from start_loop to the first boundary alone passed 20 ms, so the loop
    # stopped before iteration 1 (failed on b7e7b4c5 too). A 0.5 s cap leaves room
    # for start-up; the 0.6 s iteration still spends it at the second boundary.
    def slow_then_record(ctx):
        time.sleep(0.6)
        return _Executor.run_iteration(executor, ctx)

    executor.run_iteration = slow_then_record  # type: ignore[method-assign]
    hours = 0.5 / 3600  # 500 ms
    state = runtime.start_loop(
        _spec(mode="auto", max_iterations=5, max_wall_clock_hours=hours), executor=executor
    )
    final = _terminal(runtime, state.loop_id)
    assert final.status == "stopped"
    assert final.metadata["state_reason"] == "budget_exhausted:wall_clock"
    assert executor.ran == [1]


def test_a_stop_made_by_another_instance_is_honoured_at_the_boundary(tmp_path):
    runtime = _runtime(tmp_path)
    seen: list[int] = []

    def slow(_loop_id, number, _goal):
        seen.append(number)
        time.sleep(0.1)
        return {}

    state = runtime.start_loop(_spec(max_iterations=50), executor=slow)
    _wait(lambda: seen)
    _runtime(tmp_path).stop_loop(state.loop_id)
    time.sleep(0.3)
    assert len(seen) <= 2


# ── venues ──────────────────────────────────────────────────────────────────


def test_the_umrs_runtime_refuses_a_plan_loop_without_a_source_before_any_call():
    # slice 8b: UMRS runs plan loops on its server venue; the runs' models
    # need a venue source, so a loop without one is refused before sending
    client = SimpleNamespace(start_loop=lambda *a, **k: pytest.fail("nothing is sent"))
    with pytest.raises(PlanIterationRefused) as exc:
        UmrsRuntime(client).start_loop(_spec(iteration_plan="research-round"))
    assert exc.value.code == "plan_iteration_needs_server_venue"
    assert "venue source" in str(exc.value)


def test_hybrid_lists_and_routes_local_approvals(tmp_path):
    local = _runtime(tmp_path)
    umrs_calls: list = []
    umrs = SimpleNamespace(
        list_approvals=lambda loop_id=None: [{"id": "appr-srv"}],
        decide_approval=lambda i, d: umrs_calls.append((i, d)) or {"id": i},
        capabilities=lambda: SimpleNamespace(backends=("slurm",)),
    )
    hybrid = HybridRuntime(local, umrs)
    executor = _Executor()
    state = hybrid.start_loop(_spec(mode="ask"), executor=executor)
    pending = _pending(local, state.loop_id)
    ids = [row["id"] for row in hybrid.list_approvals()]
    assert ids == [pending["id"], "appr-srv"]
    assert [row["id"] for row in hybrid.list_approvals(state.loop_id)] == [pending["id"]]
    hybrid.decide_approval(pending["id"], "no")
    assert umrs_calls == []
    hybrid.decide_approval("appr-srv", "once")
    assert umrs_calls == [("appr-srv", "once")]
    assert _terminal(local, state.loop_id).status == "stopped"


def test_hybrid_sends_a_cluster_plan_loop_to_umrs_which_refuses(tmp_path):
    umrs = UmrsRuntime(SimpleNamespace(start_loop=lambda *a, **k: pytest.fail("sent")))
    hybrid = HybridRuntime(_runtime(tmp_path), umrs)
    with pytest.raises(PlanIterationRefused):
        hybrid.start_loop(_spec(iteration_plan="research-round", requires={"gpu": 1}))


# ── nothing but a human decides ─────────────────────────────────────────────


def test_only_the_human_paths_call_decide():
    """The decide verb is reached from the human's surfaces only: the
    never-auto ``research_loop_decide`` tool and the ``r4 project loop
    decide`` CLI (plus the runtimes that route it). The loop, the plan
    executor and the gate lane never call it."""
    import re
    from pathlib import Path

    import r4

    src = Path(r4.__file__).parent
    callers = set()
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\.decide_approval\(|_approvals\.decide\(", text):
            callers.add(str(path.relative_to(src)))
    assert callers == {
        "runtime/local.py",
        "runtime/hybrid.py",
        "_cli/project_knowledge.py",
        "companion/research_loop_tools.py",
    }
    plan_executor = (src / "runtime" / "plan_executor.py").read_text(encoding="utf-8")
    assert not re.search(r"\b(decide|graph_decide|decide_approval)\s*\(", plan_executor)


def test_the_decide_tool_is_never_auto_and_never_offered_to_plan_agents():
    from r4_contracts.team_engine.plan import TEAM_AGENT_DENY

    from r4.companion.permissions import NEVER_AUTO_APPROVE

    assert "research_loop_decide" in NEVER_AUTO_APPROVE
    assert "research_loop_decide" in TEAM_AGENT_DENY
    assert "research_loop_start" in TEAM_AGENT_DENY


def test_the_iteration_context_can_ask_but_not_decide():
    fields = set(IterationContext.__dataclass_fields__)
    assert fields == {
        "loop_id",
        "project_id",
        "iteration_number",
        "research_goal",
        "remaining_wallclock_sec",
        "ask_human",
        "stop_requested",
    }
