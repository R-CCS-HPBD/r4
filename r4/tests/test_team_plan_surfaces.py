"""ADR-092 slice 3 ("surfaces"): the console reaches team plans.

Pins, LLM-free (scripted ``MemoryAgentBroker`` turns, fake companions):

- the ``team_event`` stream of a plan run, keyed by run id and agent id:
  started / round / tool / result / failed / cancelled, gate requested /
  answered, finished (``team_plan_events``);
- the broker reports every round and tool call of an agent to the run's
  observer, never the terminal channel, and a broken observer never fails a
  turn;
- an agent's completion event carries a bounded preview of its answer;
- the bridge's ``team.plan_*`` RPCs run the session's own chat-tool
  executor under the chat tools' permission rules: ``team.plan_run`` is
  validated first, then asked in every mode (MD-5) and a human yes grants the
  card's SAFE tools to that run only (MD-6); ``team.plan_cancel`` is the
  console user's own cancel and asks nobody, while the agent-called
  ``team_plan_cancel`` tool keeps its risky rule and no agent reaches the
  console path;
- ``team.runs`` lists every TeamService run with status, agents and cost;
- cancelling the chat turn that started a plan cancels the plan through the
  engine's own cancel, and the journal records ``cancelled``;
- the bridge's approval line says when an approval is never-auto, so the
  console neither remembers it for the session nor answers it by rule.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from r4_contracts.team_engine.events import ActorRef, EventKind, TeamEvent
from r4_contracts.team_engine.services import AgentTurnResult
from r4_teamengine_spi.testing import MemoryAgentBroker

from r4.companion import team_plan_events as plan_events
from r4.companion import team_plan_tools
from r4.companion.cancellation import TurnCancellation
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.team_plan_tools import build_team_plan_tools, executor_of
from r4.companion.ui.bridge_server import (
    _DEFERRED_METHODS,
    BRIDGE_RPC_NAMES,
    CompanionBridgeServer,
)
from r4.companion.ui.team_plan_bridge import TeamPlanBridgeMixin
from r4.teams import service as team_service
from r4.teams.agent_broker import _Turn
from r4.teamup.role_agent import RoleActivityEvent

PLAN_RPCS = ("team.plan_templates", "team.plan_run", "team.plan_result", "team.plan_cancel")


def _plan(**overrides: Any) -> dict[str, Any]:
    """One agent, then a two-replica fan-out over its output."""
    plan: dict[str, Any] = {
        "plan_version": 1,
        "name": "mini",
        "description": "Gather facts, then check them",
        "params": {"topic": {"type": "string", "required": True}},
        "agents": {"worker": {"instructions": "Work carefully.", "tools": ["file_read"]}},
        "budget": {"max_tokens": 100_000},
        "phases": [
            {
                "id": "gather",
                "title": "Gather",
                "steps": [
                    {
                        "id": "gather",
                        "kind": "agent",
                        "use": "worker",
                        "label": "gatherer",
                        "prompt": "Gather facts on {{params.topic}}",
                    }
                ],
            },
            {
                "id": "check",
                "title": "Check",
                "steps": [
                    {
                        "id": "check",
                        "kind": "agent",
                        "use": "worker",
                        "label": "checker",
                        "prompt": "Check {{steps.gather.output|json}}",
                        "fanout": {"count": 2},
                    }
                ],
            },
        ],
        "output": {"facts": "steps.gather.output", "checks": "steps.check.output"},
    }
    plan.update(overrides)
    return plan


class _GatedBroker(MemoryAgentBroker):
    """Holds every turn until ``release`` is set; a cancelled turn settles as
    the real broker's does (a ``cancelled`` null)."""

    def __init__(self, script: dict[str, Any]) -> None:
        super().__init__(script)
        self.release = threading.Event()
        self.entered = threading.Event()

    async def run_turn(self, request):
        self.entered.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return await super().run_turn(request)


class _Listener:
    """Collects published plan events for one test, removed afterwards."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def __call__(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.events.append(event)

    def kinds(self, run_id: str | None = None) -> list[str]:
        with self._lock:
            return [e["kind"] for e in self.events if run_id is None or e["run_id"] == run_id]

    def of(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self.events if e["kind"] == kind]

    def wait_for(self, kind: str, timeout: float = 10.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self.of(kind)
            if found:
                return found[0]
            time.sleep(0.02)
        raise AssertionError(f"no {kind} event within {timeout}s: {self.kinds()}")


@pytest.fixture
def listener():
    collected = _Listener()
    plan_events.add_run_listener(collected)
    yield collected
    plan_events.remove_run_listener(collected)


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """The lead's plan tools over ``tmp_path``, ``broker`` answering every turn."""
    import r4.project

    current = SimpleNamespace(get_current=lambda: "proj-lead")
    monkeypatch.setattr(r4.project, "get_project_manager", lambda: current)
    run_root = tmp_path / "runs"
    seen: dict[str, Any] = {}

    def _broker_for(broker):
        def _agent_broker(self, spec, run_id, meter):
            seen["grants"] = self._run_grants
            seen["observer"] = self._observer
            return broker

        return _agent_broker

    def _wire(broker, manager=None, turn_cancel=None):
        monkeypatch.setattr(team_service.TeamService, "_agent_broker", _broker_for(broker))
        manager = manager or PermissionManager(mode=PermissionMode.AUTO_SAFE)
        tools = build_team_plan_tools(
            SimpleNamespace(config=SimpleNamespace(project_id="")),
            session_model="lead-model",
            permission_manager=manager,
            run_root=run_root,
            turn_cancel=turn_cancel,
        )
        return tools, manager, run_root, seen

    return _wire


def _script() -> dict[str, Any]:
    return {"gatherer": {"facts": ["a", "b"]}, "checker": {"ok": True}}


def _wait_done(run_id: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        live = team_plan_tools._snapshot(run_id)
        if live is None or live.done.is_set():
            return
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} still running")


def _journal(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "journal.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── the event mapping ───────────────────────────────────────────────────────


def _event(kind: EventKind, payload: dict[str, Any], agent: str = "") -> TeamEvent:
    actor = ActorRef(kind="agent", name=agent) if agent else ActorRef("engine", "plan")
    return TeamEvent(
        seq=1, run_id="r", ts="2026-10-11T00:00:00Z", kind=kind, actor=actor, payload=payload
    )


def test_agent_completion_maps_to_result_failed_or_cancelled_keyed_by_agent() -> None:
    def kind_of(payload):
        event = plan_events.from_engine_event(
            "r-1", "mini", _event(EventKind.AGENT_COMPLETED, payload, "scan[0]#1")
        )
        assert event["run_id"] == "r-1" and event["agent"] == "scan[0]#1"
        return event["kind"]

    assert kind_of({"ok": True, "preview": "x"}) == "agent_result"
    assert kind_of({"ok": False, "reason": "cancelled"}) == "agent_cancelled"
    assert kind_of({"ok": False, "reason": "schema_invalid: missing findings"}) == "agent_failed"
    assert kind_of({"ok": False, "reason": "budget_exhausted:tokens"}) == "agent_failed"


def test_gates_map_to_requested_and_answered_and_an_unraised_gate_is_silent() -> None:
    asked = plan_events.from_engine_event(
        "r",
        "p",
        _event(
            EventKind.HUMAN_INPUT_REQUESTED,
            {"gate": "g", "class": "cost", "prompt": "x" * 900, "step": "g", "phase": "p1"},
        ),
    )
    assert asked["kind"] == "gate_requested" and asked["class"] == "cost"
    assert len(asked["prompt"]) == plan_events.MAX_PROMPT_CHARS + 1  # bounded, marked
    answered = plan_events.from_engine_event(
        "r",
        "p",
        _event(
            EventKind.AGENT_MESSAGE,
            {
                "type": "gate",
                "gate": "g",
                "raised": True,
                "approved": False,
                "answered": True,
                "reason": None,
            },
        ),
    )
    assert answered["kind"] == "gate_answered" and answered["approved"] is False
    skipped = _event(EventKind.AGENT_MESSAGE, {"type": "gate", "gate": "g", "raised": False})
    assert plan_events.from_engine_event("r", "p", skipped) is None


def test_engine_only_events_without_a_surface_meaning_are_not_streamed() -> None:
    assert plan_events.from_engine_event("r", "p", _event(EventKind.CHECKPOINT_CREATED, {})) is None


def test_rounds_and_tools_map_to_agent_round_and_agent_tool() -> None:
    rnd = plan_events.from_agent_fact(
        "r",
        "p",
        "round",
        "a#1",
        {"round": 2, "input_tokens": 5, "output_tokens": 7, "model": "m"},
    )
    assert (rnd["kind"], rnd["agent"], rnd["round"], rnd["output_tokens"]) == (
        "agent_round",
        "a#1",
        2,
        7,
    )
    call = plan_events.from_agent_fact("r", "p", "tool", "a#1", {"phase": "call", "name": "grep"})
    done = plan_events.from_agent_fact(
        "r",
        "p",
        "tool",
        "a#1",
        {"phase": "result", "name": "grep", "status": "error", "duration_ms": 3},
    )
    assert (call["tool"], call["state"]) == ("grep", "started")
    assert (done["state"], done["duration_ms"]) == ("error", 3)
    assert plan_events.from_agent_fact("r", "p", "other", "a", {}) is None


def test_a_failing_listener_never_reaches_the_publisher(listener) -> None:
    def broken(_event):
        raise RuntimeError("boom")

    plan_events.add_run_listener(broken)
    try:
        plan_events.publish({"run_id": "r", "kind": "phase"})
    finally:
        plan_events.remove_run_listener(broken)
    assert listener.kinds() == ["phase"]


# ── the broker's observer ───────────────────────────────────────────────────


def test_the_turn_reports_rounds_and_tool_calls_but_not_the_terminal_channel() -> None:
    facts: list[tuple[str, str, dict[str, Any]]] = []
    turn = _Turn(label="scan[0]#1", observer=lambda *fact: facts.append(fact))
    turn.on_activity(RoleActivityEvent(role="scan[0]#1", kind="tool_call", name="grep_search"))
    turn.on_activity(
        RoleActivityEvent(role="scan[0]#1", kind="tool_result", status="ok", duration_ms=12)
    )
    turn.add_round(100, 20, {"model": "m1"})
    turn.on_activity(RoleActivityEvent(role="scan[0]#1", kind="tool_call", name="final_output"))
    turn.on_activity(RoleActivityEvent(role="scan[0]#1", kind="tool_result", status="ok"))

    assert [(kind, agent) for kind, agent, _ in facts] == [
        ("tool", "scan[0]#1"),
        ("tool", "scan[0]#1"),
        ("round", "scan[0]#1"),
    ]
    assert facts[0][2] == {"phase": "call", "name": "grep_search"}
    assert facts[1][2]["name"] == "grep_search" and facts[1][2]["duration_ms"] == 12
    assert facts[2][2] == {"round": 1, "input_tokens": 100, "output_tokens": 20, "model": "m1"}
    assert turn.tool_calls == 1  # the terminal channel is not a tool call


def test_a_broken_observer_never_fails_the_turn() -> None:
    def broken(*_fact):
        raise RuntimeError("watcher died")

    turn = _Turn(label="a", observer=broken)
    turn.on_activity(RoleActivityEvent(role="a", kind="tool_call", name="file_read"))
    turn.add_round(1, 1, {})
    assert (turn.tool_calls, turn.llm_calls) == (1, 1)


def test_the_service_hands_its_observer_to_the_run_broker(tmp_path) -> None:
    from r4.teams.brokers import TokenBudgetMeter
    from r4.teams.plan import spec_from_plan

    def watcher(*_fact):
        return None

    spec, problems = spec_from_plan(_plan(), {"topic": "t"}, project="p")
    assert not problems
    service = team_service.TeamService(SimpleNamespace(), run_root=tmp_path, observer=watcher)
    broker = service._agent_broker(spec, "run-1", TokenBudgetMeter(spec.budgets))
    assert broker._observer is watcher
    assert (
        broker._new_turn(SimpleNamespace(label="x#1", model="m", max_tool_rounds=2)).observer
        is watcher
    )


# ── a run streams, keyed by run id and agent id ─────────────────────────────


def _execute(tools, name):
    """A plan tool's executor by name."""
    return next(t for t in tools if t["name"] == name)["execute"]


def _results_by_agent(listener) -> dict:
    return {e["agent"]: e for e in listener.of("agent_result")}


def test_a_chat_started_run_streams_every_agent_with_its_result(wire, listener) -> None:
    tools, _manager, _root, seen = wire(MemoryAgentBroker(_script()))
    run = _execute(tools, "team_plan_run")
    answer = json.loads(run(plan=_plan(), args={"topic": "x"}, wait_sec=30))
    run_id = answer["run_id"]
    listener.wait_for("plan_finished")

    kinds = listener.kinds(run_id)
    results = _results_by_agent(listener)
    gatherer = results["gatherer"]
    finished = listener.of("plan_finished")[0]
    assert {
        "status": answer["status"],
        "ends": (kinds[0], kinds[-1]),
        "counts": (kinds.count("agent_started"), kinds.count("agent_result"), "phase" in kinds),
        "agents": set(results),
        "gatherer": (json.loads(gatherer["preview"]), gatherer["step"], gatherer["ok"]),
        "finished": (finished["status"], finished["result_path"].endswith("result.json")),
        "observer": callable(seen["observer"]),  # the broker is told rounds and tools
    } == {
        "status": "success",
        "ends": ("plan_started", "plan_finished"),
        "counts": (3, 3, True),
        "agents": {"gatherer", "checker#0", "checker#1"},
        "gatherer": ({"facts": ["a", "b"]}, "gather", True),
        "finished": ("success", True),
        "observer": True,
    }, answer


def test_a_long_answer_is_previewed_bounded_and_marked(wire, listener) -> None:
    from r4.teams.plan.run import MAX_EVENT_PREVIEW_CHARS

    long = "z" * (MAX_EVENT_PREVIEW_CHARS * 3)
    tools, *_ = wire(MemoryAgentBroker({"gatherer": long, "checker": "fine"}))
    run = next(t for t in tools if t["name"] == "team_plan_run")["execute"]
    json.loads(run(plan=_plan(), args={"topic": "x"}, wait_sec=30))
    listener.wait_for("plan_finished")
    preview = next(e for e in listener.of("agent_result") if e["agent"] == "gatherer")["preview"]
    assert len(preview) == MAX_EVENT_PREVIEW_CHARS + 1 and preview.endswith("…")


def test_a_failed_agent_streams_as_agent_failed_with_its_reason(wire, listener) -> None:
    class _NullBroker(MemoryAgentBroker):
        async def run_turn(self, request):
            if request.label == "gatherer":
                return AgentTurnResult(ok=False, reason="schema_invalid: no facts", text="nope")
            return await super().run_turn(request)

    tools, *_ = wire(_NullBroker(_script()))
    run = next(t for t in tools if t["name"] == "team_plan_run")["execute"]
    json.loads(run(plan=_plan(), args={"topic": "x"}, wait_sec=30))
    listener.wait_for("plan_finished")
    failed = listener.of("agent_failed")
    assert failed and failed[0]["agent"] == "gatherer"
    assert failed[0]["reason"].startswith("schema_invalid") and failed[0]["preview"] == "nope"


# ── cancelling the chat turn reaches the plan ───────────────────────────────


def _cancelled_records(records) -> dict:
    """Whether the run's journal holds its agents cancelled, and only so."""
    return {
        "journalled": bool(records),
        "every_null_cancelled": all(r["reason"] == "cancelled" for r in records if not r["ok"]),
        "some_cancelled": any(r["reason"] == "cancelled" for r in records),
    }


def test_cancelling_the_turn_cancels_the_plan_it_started(wire, listener) -> None:
    broker = _GatedBroker(_script())
    token = TurnCancellation()
    tools, _manager, root, _seen = wire(broker, turn_cancel=lambda: token)
    run = _execute(tools, "team_plan_run")
    answer = json.loads(run(plan=_plan(), args={"topic": "x"}, wait_sec=0))
    run_id = answer["run_id"]
    entered = broker.entered.wait(10)

    token.request()  # the user stops the chat turn
    _wait_done(run_id)

    doc = json.loads((root / run_id / "result.json").read_text())
    assert {
        "answer": answer["status"],
        "entered": entered,
        "doc": (doc["status"], doc["cancel_reason"]),
        "records": _cancelled_records(_journal(root / run_id)),
        "streamed": "agent_cancelled" in listener.kinds(run_id),
        "finished": listener.wait_for("plan_finished")["status"],
    } == {
        "answer": "running",
        "entered": True,
        "doc": ("cancelled", team_plan_tools.TURN_CANCEL_REASON),
        "records": {"journalled": True, "every_null_cancelled": True, "some_cancelled": True},
        "streamed": True,
        "finished": "cancelled",
    }


def test_a_turn_cancelled_before_the_plan_started_cancels_it_at_start(wire) -> None:
    broker = _GatedBroker(_script())
    token = TurnCancellation()
    token.request()
    tools, _manager, root, _seen = wire(broker, turn_cancel=lambda: token)
    run = next(t for t in tools if t["name"] == "team_plan_run")["execute"]
    answer = json.loads(run(plan=_plan(), args={"topic": "x"}, wait_sec=15))
    assert answer["status"] == "cancelled"
    assert not broker.calls  # no agent ran


def test_another_turn_cancelling_does_not_touch_a_plan_it_did_not_start(wire) -> None:
    broker = _GatedBroker(_script())
    started_in, later = TurnCancellation(), TurnCancellation()
    current = {"token": started_in}
    tools, _manager, root, _seen = wire(broker, turn_cancel=lambda: current["token"])
    run = next(t for t in tools if t["name"] == "team_plan_run")["execute"]
    run_id = json.loads(run(plan=_plan(), args={"topic": "x"}, wait_sec=0))["run_id"]
    current["token"] = later
    later.request()  # a later turn is cancelled
    broker.release.set()
    _wait_done(run_id)
    assert json.loads((root / run_id / "result.json").read_text())["status"] == "success"


def test_a_plan_started_outside_a_turn_has_no_turn_to_follow(wire) -> None:
    broker = MemoryAgentBroker(_script())
    tools, *_ = wire(broker, turn_cancel=lambda: None)
    run = next(t for t in tools if t["name"] == "team_plan_run")["execute"]
    assert json.loads(run(plan=_plan(), args={"topic": "x"}, wait_sec=30))["status"] == "success"


def test_the_session_hands_its_turn_token_accessor_to_the_plan_tools(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from r4.companion import session as session_module
    from r4.companion.tools import get_builtin_tools

    def accessor():
        return None

    executor = executor_of(get_builtin_tools(client=None, turn_cancel=accessor))
    assert executor is not None and executor._turn_cancel is accessor

    seen: dict[str, Any] = {}
    real = session_module.get_builtin_tools

    def _capture(*args: Any, **kwargs: Any):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(session_module, "get_builtin_tools", _capture)
    chat = session_module.CompanionSession(
        session_id="turn-token", name="t", client=MagicMock(), max_llm_calls_per_session=0
    )
    assert seen["turn_cancel"] == chat._current_turn_cancel
    assert chat._current_turn_cancel() is None  # between turns
    chat._turn_cancel = token = TurnCancellation()
    assert executor_of(chat.tools)._turn_cancel() is token


# ── the bridge RPCs ─────────────────────────────────────────────────────────


class _Host(TeamPlanBridgeMixin):
    """The mixin over a minimal session: its tools, its permission manager."""

    def __init__(self, tools: list[dict[str, Any]], manager: PermissionManager) -> None:
        self.session = SimpleNamespace(tools=tools, permissions=manager)
        self.lines: list[dict[str, Any]] = []
        self.client = SimpleNamespace(config=SimpleNamespace(project_id=""))

    def _emit_line(self, payload: dict[str, Any]) -> None:
        self.lines.append(payload)

    def _teamup_client(self) -> Any:
        return self.client


def _asking_manager(mode: PermissionMode, answer: bool, asked: list) -> PermissionManager:
    def _callback(tool, description, arguments):
        asked.append((tool, description, dict(arguments)))
        return answer

    return PermissionManager(mode=mode, approval_callback=_callback)


def test_the_bridge_serves_the_plan_rpcs_and_answers_slow_ones_off_the_loop() -> None:
    from r4.companion.ui.bridge_server import _bridge_rpc_bindings

    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    bindings = _bridge_rpc_bindings(server)
    for name in (*PLAN_RPCS, "team.runs"):
        assert name in BRIDGE_RPC_NAMES and name in bindings
    assert {"team.plan_run", "team.plan_cancel"} <= _DEFERRED_METHODS
    assert issubclass(CompanionBridgeServer, TeamPlanBridgeMixin)


def test_plan_run_asks_in_chat_and_a_yes_grants_the_card_tools(wire) -> None:
    asked: list = []
    manager = _asking_manager(PermissionMode.AUTO_SAFE, True, asked)
    tools, manager, root, seen = wire(MemoryAgentBroker(_script()), manager)
    host = _Host(tools, manager)

    reply = host.team_plan_run({"plan": _plan(), "args": {"topic": "x"}})
    assert reply["ok"] is True and reply["run_id"]
    assert len(asked) == 1 and asked[0][0] == "team_plan_run"
    assert "mini" in asked[0][1]  # the plan's card, not a bare tool name
    assert asked[0][2]["wait_sec"] == 0  # the RPC never blocks on the run
    _wait_done(reply["run_id"])
    assert seen["grants"] == frozenset({"file_read"})  # MD-6: the card's SAFE tools


def test_autopilot_runs_a_plan_without_asking_and_grants_nothing(wire) -> None:
    """ADR-104 amendment 2: no card in autopilot, so no MD-6 grant either."""
    asked: list = []
    manager = _asking_manager(PermissionMode.TRUST, False, asked)
    tools, manager, root, seen = wire(MemoryAgentBroker(_script()), manager)
    reply = _Host(tools, manager).team_plan_run({"plan": _plan(), "args": {"topic": "x"}})
    assert reply["ok"] is True and asked == []
    _wait_done(reply["run_id"])
    assert seen["grants"] == frozenset()


def test_plan_run_without_a_human_never_runs(wire) -> None:
    tools, manager, root, _seen = wire(
        MemoryAgentBroker(_script()), PermissionManager(mode=PermissionMode.AUTO_SAFE)
    )
    reply = _Host(tools, manager).team_plan_run({"plan": _plan(), "args": {"topic": "x"}})
    assert reply == {
        "ok": False,
        "status": "rejected",
        "error": "team_plan_run was not approved; nothing ran and nothing was spent",
    }
    assert not root.exists() or not any(root.iterdir())


def test_plan_run_rejected_by_the_human_never_runs(wire) -> None:
    asked: list = []
    manager = _asking_manager(PermissionMode.ASK_ALWAYS, False, asked)
    broker = MemoryAgentBroker(_script())
    tools, manager, root, _seen = wire(broker, manager)
    reply = _Host(tools, manager).team_plan_run({"plan": _plan(), "args": {"topic": "x"}})
    assert reply["status"] == "rejected" and len(asked) == 1
    assert broker.calls == []


def test_an_invalid_plan_is_refused_before_the_human_is_asked(wire) -> None:
    asked: list = []
    manager = _asking_manager(PermissionMode.ASK_ALWAYS, True, asked)
    tools, manager, _root, _seen = wire(MemoryAgentBroker({}), manager)
    host = _Host(tools, manager)
    reply = host.team_plan_run({"plan": _plan(budget={}), "args": {"topic": "x"}})
    assert reply["ok"] is False and any("max_tokens" in p for p in reply["problems"])
    unknown = host.team_plan_run({"plan": _plan(), "wait": 3})
    assert unknown["problems"] == ["team.plan_run does not take wait"]
    assert asked == []


def test_a_goal_binds_to_the_templates_free_text_parameter(wire) -> None:
    asked: list = []
    manager = _asking_manager(PermissionMode.ASK_ALWAYS, False, asked)
    tools, manager, _root, _seen = wire(MemoryAgentBroker({}), manager)
    host = _Host(tools, manager)

    host.team_plan_run({"template": "adversarial-review", "goal": "src/pkg/mod.py"})
    assert asked[-1][2]["args"] == {"target": "src/pkg/mod.py"}
    host.team_plan_run({"template": "research-round", "goal": "why does X fail"})
    assert asked[-1][2]["args"] == {"objective": "why does X fail"}
    no_template = host.team_plan_run({"plan": _plan(), "goal": "x"})
    assert "needs a template" in no_template["problems"][0]
    full = host.team_plan_run(
        {"template": "adversarial-review", "args": {"target": "a"}, "goal": "b"}
    )
    assert "no free-text parameter" in full["problems"][0]
    unknown = host.team_plan_run({"template": "no-such", "goal": "b"})
    assert unknown["ok"] is False


def _running_plan(wire, manager):
    broker = _GatedBroker(_script())
    tools, manager, root, _seen = wire(broker, manager)
    host = _Host(tools, manager)
    run_id = host.team_plan_run({"plan": _plan(), "args": {"topic": "x"}})["run_id"]
    assert broker.entered.wait(10)
    return host, tools, root, run_id, broker


def test_a_console_cancel_is_the_humans_own_action_and_asks_nobody(wire) -> None:
    """Maintainer, 2026-10-11: the Work pane's `c` is the human's decision."""
    asked: list = []
    manager = _asking_manager(PermissionMode.ASK_ALWAYS, False, asked)
    manager._approval_callback = lambda *_a: True  # the plan's own card: yes
    host, _tools, root, run_id, _broker = _running_plan(wire, manager)
    manager._approval_callback = lambda *a: asked.append(a) or False  # any card: no

    view = host.team_plan_cancel({"run_id": run_id, "reason": "wrong target"})
    assert asked == []  # no card raised
    assert view["status"] == "cancelled"
    doc = json.loads((root / run_id / "result.json").read_text())
    assert doc["cancel_reason"] == "cancelled by the user from the console: wrong target"


def test_the_agent_called_cancel_tool_keeps_its_approval_rule(wire) -> None:
    from r4.companion.permissions import RISKY_TOOLS

    asked: list = []
    manager = _asking_manager(PermissionMode.ASK_ALWAYS, True, asked)
    _host, tools, root, run_id, broker = _running_plan(wire, manager)
    asked.clear()
    arguments = {"run_id": run_id, "reason": "agent decided"}
    manager._approval_callback = lambda *a: asked.append(a) or False
    gated = manager.check("team_plan_cancel", arguments)  # the agent loop's gate
    unattended = PermissionManager(mode=PermissionMode.AUTO_SAFE)  # nobody to ask
    fork = manager.fork_for_agent("v#1", run_id="r")  # plan agents never see it
    # The lead's approved tool call cancels as the lead, not as the user.
    cancelled = json.loads(_execute(tools, "team_plan_cancel")(**arguments))
    doc = json.loads((root / run_id / "result.json").read_text())
    assert {
        "risky": "team_plan_cancel" in RISKY_TOOLS,
        "gated": gated,
        "asked": [a[0] for a in asked],
        "unattended": unattended.check("team_plan_cancel", arguments),
        "fork": fork.refusal("team_plan_cancel"),
        "cancelled": cancelled["status"],
        "reason": doc["cancel_reason"],
    } == {
        "risky": True,
        "gated": False,
        "asked": ["team_plan_cancel"],
        "unattended": False,
        "fork": "team_agent_deny",
        "cancelled": "cancelled",
        "reason": "cancelled by the lead: agent decided",
    }


def _callers_of(attribute: str) -> set:
    """Every module of the r4 package that reads ``.<attribute>``."""
    import ast

    import r4

    src = Path(r4.__file__).resolve().parent
    callers = set()
    for path in src.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr == attribute:
                callers.add(path.relative_to(src).as_posix())
    return callers


def _bridge_verb_tools() -> set:
    from r4.companion.tools import get_builtin_tools

    return {t["name"] for t in get_builtin_tools(client=None) if "." in t["name"]}


def _refused_in_agent_context(host, run_id) -> dict:
    from r4.companion.permissions import _APPROVING_AGENT

    token = _APPROVING_AGENT.set("finder[0]#1")
    try:
        return host.team_plan_cancel({"run_id": run_id})
    finally:
        _APPROVING_AGENT.reset(token)


def test_an_agent_cannot_reach_the_console_cancel(wire) -> None:
    asked: list = []
    manager = _asking_manager(PermissionMode.TRUST, True, asked)
    host, tools, root, run_id, broker = _running_plan(wire, manager)
    # (a) inside an agent's approval context the console verb refuses.
    refused = _refused_in_agent_context(host, run_id)
    still_running = not team_plan_tools._snapshot(run_id).done.is_set()
    # (b) the chat tool runs the asking cancel, and takes nothing that could
    # switch it to the console path.
    tool = next(t for t in tools if t["name"] == "team_plan_cancel")
    # (c) no tool is a bridge verb, and nothing but the bridge RPC calls the
    # no-card cancel.
    callers = _callers_of("cancel_by_user")
    broker.release.set()
    _wait_done(run_id)
    # The refused console verb never touched the run: it ends on its own.
    final = json.loads((root / run_id / "result.json").read_text())["status"]
    assert {
        "refused": (refused["status"], "team_plan_cancel tool" in refused["error"]),
        "still_running": still_running,
        "tool": tool["execute"].__func__ is team_plan_tools._PlanTools.cancel,
        "params": set(tool["parameters"]["properties"]),
        "bridge_verbs": _bridge_verb_tools(),
        "callers": callers,
        "final": final,
    } == {
        "refused": ("rejected", True),
        "still_running": True,
        "tool": True,
        "params": {"run_id", "reason"},
        "bridge_verbs": set(),
        "callers": {"companion/ui/team_plan_bridge.py"},
        "final": "success",
    }


def test_plan_templates_and_result_need_no_approval(wire) -> None:
    asked: list = []
    manager = _asking_manager(PermissionMode.ASK_ALWAYS, True, asked)
    tools, manager, _root, _seen = wire(MemoryAgentBroker(_script()), manager)
    host = _Host(tools, manager)
    names = {row["name"] for row in host.team_plan_templates({})["templates"]}
    assert {"adversarial-review", "research-round"} <= names
    run_id = host.team_plan_run({"plan": _plan(), "args": {"topic": "x"}})["run_id"]
    _wait_done(run_id)
    asked.clear()
    result = host.team_plan_result({"run_id": run_id, "path": "output.facts"})
    assert result["value"] == {"facts": ["a", "b"]} and asked == []


def test_a_session_without_plan_tools_is_refused_by_name() -> None:
    host = _Host([], PermissionManager())
    with pytest.raises(ValueError, match="no team plan tools"):
        host.team_plan_templates({})


def test_team_runs_lists_plan_runs_and_teams_with_status_agents_and_cost(
    wire, monkeypatch, tmp_path
) -> None:
    import r4.teamup.session as session_module
    from r4.teams.service import TeamService
    from r4.teamup.session import TeamupSessionConfig

    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path / "teams")
    team = TeamService(SimpleNamespace()).open_team_session(
        TeamupSessionConfig(name="crew", project_id="proj-lead")
    )
    asked: list = []
    tools, manager, _root, _seen = wire(
        MemoryAgentBroker(_script()), _asking_manager(PermissionMode.TRUST, True, asked)
    )
    host = _Host(tools, manager)
    run_id = host.team_plan_run({"plan": _plan(), "args": {"topic": "x"}})["run_id"]
    _wait_done(run_id)

    rows = {row["run_id"]: row for row in host.team_runs({})["runs"]}
    plan_row, team_row = rows[run_id], rows[team.session_id]
    assert (plan_row["kind"], plan_row["status"], plan_row["engine"]) == ("plan", "success", "plan")
    assert plan_row["agents"] == {"started": 3, "ok": 3, "null": 0}
    assert plan_row["cost"]["tokens"] == 300  # the scripted broker's 100 per turn
    assert plan_row["project_id"] == "proj-lead"
    assert (team_row["kind"], team_row["engine"]) == ("team", "native")
    assert team_row["agents"]["roles"] == ["pm", "se", "pg"]
    assert "cost" not in team_row  # ADR-092 slice 4: the team's compute budget is retired


def test_team_runs_overlays_a_live_run(wire) -> None:
    broker = _GatedBroker(_script())
    tools, manager, *_ = wire(
        broker, PermissionManager(mode=PermissionMode.TRUST, approval_callback=lambda *_a: True)
    )
    host = _Host(tools, manager)
    run_id = host.team_plan_run({"plan": _plan(), "args": {"topic": "x"}})["run_id"]
    assert broker.entered.wait(10)
    try:
        row = next(r for r in host.team_runs({})["runs"] if r["run_id"] == run_id)
        assert row["status"] == "running" and row["live"] is True
    finally:
        broker.release.set()
        _wait_done(run_id)


def _finished_line_seen(host) -> bool:
    return any(line["team_event"]["kind"] == "plan_finished" for line in list(host.lines))


def _await_finished_line(host, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and not _finished_line_seen(host):
        time.sleep(0.02)


def _streamed(host) -> dict:
    """What the bridge wrote: only team_event lines, their run ids and agents."""
    events = [line["team_event"] for line in host.lines]
    return {
        "shape": all(set(line) == {"team_event"} for line in host.lines),
        "runs": {e["run_id"] for e in events},
        "agents": {e["agent"] for e in events if e["kind"] == "agent_result"},
    }


def test_the_bridge_streams_plan_events_as_run_keyed_team_event_lines(wire) -> None:
    tools, manager, *_ = wire(
        MemoryAgentBroker(_script()),
        PermissionManager(mode=PermissionMode.TRUST, approval_callback=lambda *_a: True),
    )
    host = _Host(tools, manager)
    host._start_plan_event_stream()
    try:
        run_id = host.team_plan_run({"plan": _plan(), "args": {"topic": "x"}})["run_id"]
        _wait_done(run_id)
        _await_finished_line(host)
    finally:
        host._stop_plan_event_stream()
    assert _streamed(host) == {
        "shape": True,
        "runs": {run_id},
        "agents": {"gatherer", "checker#0", "checker#1"},
    }


def test_a_discarded_bridge_stops_listening() -> None:
    import gc

    host = _Host([], PermissionManager())
    host._start_plan_event_stream()
    listener = host._plan_listener
    del host
    gc.collect()
    plan_events.publish({"run_id": "r", "kind": "phase"})
    assert listener not in plan_events._LISTENERS


# ── the approval line says never-auto ───────────────────────────────────────


def _approval_server(answer: str = "yes") -> CompanionBridgeServer:
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._emitted = []
    server._emit_line = server._emitted.append
    server._register_approval_intent = lambda *a: None
    server._decide_approval_intent = lambda *a: None
    server._await_approval = lambda request_id, _q: answer
    server._session_id_str = lambda: "s-1"
    server._approval_seq = 0
    return server


@pytest.mark.parametrize(
    "tool,tier,remember",
    [("team_plan_run", "never_auto", False), ("shell_exec", "risky", True)],
)
def test_the_approval_line_tells_the_console_whether_it_may_remember(tool, tier, remember):
    server = _approval_server()
    server._on_tool_approval(tool, "card", {})
    legacy = next(
        line["approval_request"] for line in server._emitted if "approval_request" in line
    )
    assert legacy["risk_tier"] == tier
    assert legacy["session_approval_allowed"] is remember
