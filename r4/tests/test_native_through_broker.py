"""Native role turns go through the AgentBroker (ADR-092 slice 9).

The one turn path: every native role turn is one ``AgentTurnRequest`` to the
run's ``AgentBroker`` (the plan engine's own), never a direct model call; the
broker charges every round to the run's ``BudgetMeter`` (the retired
``BudgetTracker`` has no residue). These run the real ``TeamService`` and
native engine on scripted turns (``MemoryAgentBroker``); the client refuses
any call, so a direct model call would fail the test.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from r4_contracts.team_engine.spec import (
    TEAM_SPEC_VERSION,
    AgentSpec,
    BudgetSpec,
    TaskSpec,
    TeamSpec,
    Topology,
    WorkflowStyle,
)
from r4_teamengine_spi.testing import MemoryAgentBroker

from r4.teams import native
from r4.teams import service as team_service
from r4.teams.service import TeamService

SRC = Path(native.__file__).resolve().parents[1]


class RefusingClient:
    """A client that fails the test on any model call."""

    config = SimpleNamespace(model="m-default", project_id="p")

    def chat(self, *_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("a native role turn called the model directly")

    def list_models(self):
        return []


def _spec(budget: int | None = None, iterations: int = 1) -> TeamSpec:
    return TeamSpec(
        spec_version=TEAM_SPEC_VERSION,
        run_name="native-broker",
        project="p",
        task=TaskSpec(objective="optimize fft"),
        agents=(
            AgentSpec(
                name="pm",
                role="pm",
                instructions="manage",
                model_ref="m-pm",
                max_iterations=iterations,
            ),
            AgentSpec(name="pg", role="pg", instructions="code", max_iterations=iterations),
        ),
        topology=Topology(style=WorkflowStyle.FILE_BASED),
        budgets=BudgetSpec(max_tokens=budget),
    )


@pytest.fixture()
def world(monkeypatch, tmp_path):
    state: dict[str, Any] = {"script": {"": "on it"}, "brokers": [], "none": False}

    def _broker(self, spec, run_id, meter):
        if state["none"]:
            return None
        broker = MemoryAgentBroker(dict(state["script"]), meter=meter, usage=40)
        state["brokers"].append(broker)
        return broker

    monkeypatch.setattr(team_service.TeamService, "_agent_broker", _broker)
    state["service"] = TeamService(RefusingClient(), run_root=tmp_path)
    return state


async def _run(world, spec):
    handle = await world["service"].submit(spec, engine="native")
    async for _event in handle.events():
        pass
    return await handle.result()


async def test_every_role_turn_is_an_agent_turn_request(world):
    result = await _run(world, _spec())
    (broker,) = world["brokers"]
    calls = {c.label: c for c in broker.calls}
    assert result.status == "success", result.error
    assert sorted(calls) == ["pg", "pm"]
    pm = calls["pm"]
    assert (pm.instructions, pm.model, pm.tools, pm.metadata["engine"]) == (
        "manage",
        "m-pm",  # the role's own model rides the request
        (),
        "native",
    )
    assert "Your Turn" in pm.prompt  # the session's context is the prompt
    assert len({c.call_key for c in broker.calls}) == len(broker.calls)
    # metrics are the broker's per-turn usage
    assert (result.metrics.llm_calls, result.metrics.input_tokens) == (2, 80)


async def test_the_broker_meters_every_round_and_a_spent_budget_fails_the_run(world):
    result = await _run(world, _spec(budget=50, iterations=3))
    assert result.status == "failed" and result.error.startswith("budget")
    assert len(world["brokers"][0].calls) == 2  # stopped at the iteration boundary


async def test_a_failed_turn_is_the_roles_named_error(world):
    world["script"] = {"pm": "on it"}  # pg unscripted: the broker refuses it
    result = await _run(world, _spec())
    assert result.status == "partial"
    assert result.error == "roles_failed: pg: agent turn failed: unscripted"


async def test_a_host_without_an_agent_broker_is_refused_by_name(world):
    world["none"] = True
    result = await _run(world, _spec())
    assert result.status == "failed"
    assert result.error.startswith("agent_broker_required")


def test_the_session_client_refuses_direct_model_calls():
    with pytest.raises(RuntimeError, match="bypassed the run's AgentBroker"):
        native._NoDirectModelCalls().chat(message="hi")


async def test_a_turn_from_inside_a_running_loop_runs_on_its_own_loop():
    async def answer():
        await asyncio.sleep(0)
        return 42

    assert native._run_coroutine(answer()) == 42


def test_no_native_turn_path_calls_the_model_directly():
    """Source pins: the native engine and the session's turn seam have no
    model call of their own; the chat-once factory left production."""
    text = (SRC / "teams" / "native.py").read_text()
    calls = re.findall(r"\.chat\(", text)
    assert calls == []  # the refusing stub defines chat(), it never calls one
    assert "chat_once_factory" not in (SRC / "teamup" / "role_agent.py").read_text()
    assert "_subcompanion_factory = chat_once" not in text


def test_the_retired_budget_tracker_has_no_residue():
    """Slice 4 retired it; slice 9 pins that nothing imports or aliases it:
    every team run meters through a BudgetMeter."""
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if re.search(
            r"^\s*(from|import)\b.*\bBudgetTracker\b|\bBudgetTracker\s*=", path.read_text(), re.M
        )
    ]
    assert offenders == []
