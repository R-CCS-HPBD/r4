"""Bridge RPC result contract, teams family (plan H-4.5).

Each verb in ``rpc_schema_teams.TEAMS_RESULT_TYPES`` runs through its real
handler (``_bridge_rpc_bindings``) on the shared harness
(``bridge_rpc_harness``: real store and session, network faked); its output
must validate against the verb's exported JSON Schema.

Teamup sessions and plan runs live under ``tmp_path``. Background work the
RPCs start (a teamup iteration, a team bootstrap) is replaced by a no-op: the
contract covers the reply, not the run. Plan runs use scripted
``MemoryAgentBroker`` turns (LLM-free), as ``test_team_plan_surfaces`` does.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from bridge_rpc_harness import (
    Case,
    Harness,
    Setup,
    case_ids,
    make_harness,
    plain,
    run_verb,
    schema_errors,
    uncovered,
)
from r4_teamengine_spi.testing import MemoryAgentBroker

from r4.companion import team_plan_tools
from r4.companion.ui.rpc_schema_teams import TEAMS_RESULT_TYPES
from r4.teams import service as team_service

_SCRIPT = {"gatherer": {"facts": ["a", "b"]}}
_PLAN = {
    "plan_version": 1,
    "name": "mini",
    "description": "Gather facts",
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
        }
    ],
    "output": {"facts": "steps.gather.output"},
}
_RUN = {"plan": _PLAN, "args": {"topic": "x"}, "durable": False}


class _GatedBroker(MemoryAgentBroker):
    """Holds every turn until released, so a run stays live for the read."""

    def __init__(self, script: dict[str, Any]) -> None:
        super().__init__(script)
        self.release = threading.Event()
        self.entered = threading.Event()

    async def run_turn(self, request: Any) -> Any:
        self.entered.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return await super().run_turn(request)


_GATES: list[_GatedBroker] = []


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    import r4.project
    import r4.teamup.session as teamup_session

    monkeypatch.setattr(teamup_session, "TEAMUP_SESSIONS_DIR", tmp_path / "teams")
    monkeypatch.setattr(team_service, "TEAM_RUNS_DIR", tmp_path / "runs")
    current = SimpleNamespace(get_current=lambda: "proj-lead")
    monkeypatch.setattr(r4.project, "get_project_manager", lambda: current)
    h = make_harness(tmp_path, monkeypatch)
    h.session.permissions.set_approval_callback(lambda *_a: True)
    yield h
    for gate in _GATES:
        gate.release.set()
    _GATES.clear()


def _team(h: Harness) -> str:
    """One stored teamup session (the real teamup.create handler)."""
    return str(run_verb(h, "teamup.create", plain({"name": "alpha", "goal": "g"}))["session_id"])


def _with_team(params: dict[str, Any]) -> Setup:
    return lambda h: {"session_id": _team(h), **params}


def _no_iteration(h: Harness) -> dict[str, Any]:
    h.server._teamup_iteration_thread = lambda _session: None  # type: ignore[method-assign]
    return {"session_id": _team(h)}


def _no_bootstrap(h: Harness) -> dict[str, Any]:
    h.server._teamup_bootstrap_thread = lambda **_kw: None  # type: ignore[method-assign]
    return {"goal": "prove alloy X", "crew": "hpc"}


def _paused(h: Harness) -> dict[str, Any]:
    sid = _team(h)
    run_verb(h, "teamup.create", plain({"name": "beta", "start": True}))
    return {"session_id": sid}


def _tailor_answer(h: Harness) -> dict[str, Any]:
    h.client.chat = lambda **_kw: SimpleNamespace(content="")  # type: ignore[attr-defined]
    return {"session_id": _team(h)}


def _broker(h: Harness, broker: MemoryAgentBroker) -> None:
    _ = h
    team_service.TeamService._agent_broker = lambda *_a, **_k: broker  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _restore_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        team_service.TeamService, "_agent_broker", team_service.TeamService._agent_broker
    )


def _wait_done(run_id: str) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        live = team_plan_tools._snapshot(run_id)
        if live is None or live.done.is_set():
            return
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} still running")


def _finished_run(h: Harness) -> str:
    _broker(h, MemoryAgentBroker(_SCRIPT))
    run_id = str(run_verb(h, "team.plan_run", plain(dict(_RUN)))["run_id"])
    _wait_done(run_id)
    return run_id


def _live_run(h: Harness) -> str:
    gate = _GatedBroker(_SCRIPT)
    _GATES.append(gate)
    _broker(h, gate)
    run_id = str(run_verb(h, "team.plan_run", plain(dict(_RUN)))["run_id"])
    assert gate.entered.wait(10)
    return run_id


def _run_started(h: Harness) -> dict[str, Any]:
    _broker(h, MemoryAgentBroker(_SCRIPT))
    return dict(_RUN)


def _run_rejected(h: Harness) -> dict[str, Any]:
    h.session.permissions.set_approval_callback(lambda *_a: False)
    return dict(_RUN)


def _result_finished(h: Harness) -> dict[str, Any]:
    return {"run_id": _finished_run(h)}


def _result_slice(h: Harness) -> dict[str, Any]:
    return {"run_id": _finished_run(h), "path": "output.facts"}


def _result_running(h: Harness) -> dict[str, Any]:
    return {"run_id": _live_run(h)}


def _cancel_running(h: Harness) -> dict[str, Any]:
    return {"run_id": _live_run(h), "reason": "Work pane c"}


def _runs_all(h: Harness) -> dict[str, Any]:
    _team(h)
    _finished_run(h)
    _live_run(h)
    return {}


CASES: list[Case] = [
    ("teamup.list", "empty", plain({})),
    ("teamup.list", "one", lambda h: (_team(h), {})[1]),
    ("teamup.status", "created", _with_team({})),
    ("teamup.run", "started", _no_iteration),
    ("teamup.start", "accepted", _no_bootstrap),
    ("teamup.pause", "pause", _paused),
    ("teamup.resume", "resume", _with_team({})),
    ("teamup.cancel", "cancel", _with_team({})),
    ("teamup.create", "created", plain({"name": "alpha", "roles": "pm,se"})),
    ("teamup.tailor", "degraded", _with_team({})),
    ("teamup.tailor", "answered", _tailor_answer),
    ("teamup.crews", "all", plain({})),
    ("teamup.engines", "registry", plain({})),
    ("teamup.logs", "entries", _with_team({"limit": 5})),
    ("teamup.report", "report", _with_team({})),
    ("teamup.hardware", "profiles", plain({})),
    ("teamup.spawn_agent_session", "pm", _with_team({"role": "pm"})),
    ("team.plan_templates", "builtin", plain({})),
    ("team.plan_run", "started", _run_started),
    ("team.plan_run", "invalid", plain({"plan": {"plan_version": 1}, "durable": False})),
    ("team.plan_run", "unknown_arg", plain({"wait": 3})),
    ("team.plan_run", "rejected", _run_rejected),
    ("team.plan_result", "finished", _result_finished),
    ("team.plan_result", "slice", _result_slice),
    ("team.plan_result", "running", _result_running),
    ("team.plan_result", "unknown", plain({"run_id": "no-such-run"})),
    ("team.plan_result", "bad_id", plain({"run_id": "../escape"})),
    ("team.plan_cancel", "running", _cancel_running),
    ("team.plan_cancel", "finished", _result_finished),
    ("team.runs", "empty", plain({})),
    ("team.runs", "all_kinds", _runs_all),
]


@pytest.mark.parametrize(("method", "label", "setup"), CASES, ids=case_ids(CASES))
def test_handler_output_matches_schema(
    harness: Harness, method: str, label: str, setup: Setup
) -> None:
    _ = label
    assert schema_errors(harness, method, setup) == []


def test_every_teams_verb_has_a_case() -> None:
    assert uncovered(TEAMS_RESULT_TYPES, CASES) == set()


def test_the_cases_reach_the_branches_they_name(harness: Harness) -> None:
    """The plan views differ by branch; each case lands where its label says."""
    started = run_verb(harness, "team.plan_run", _run_started)
    _wait_done(str(started["run_id"]))
    running = run_verb(harness, "team.plan_result", _result_running)
    runs = run_verb(harness, "team.runs", plain({}))
    assert {
        "started": started["ok"],
        "running": (running["status"], "progress" in running),
        "live_row": [row.get("live") for row in runs["runs"] if row.get("live")],
        "rejected": run_verb(harness, "team.plan_run", _run_rejected).get("status"),
        "degraded": "degraded" in run_verb(harness, "teamup.tailor", _with_team({})),
    } == {
        "started": True,
        "running": ("running", True),
        "live_row": [True],
        "rejected": "rejected",
        "degraded": True,
    }
