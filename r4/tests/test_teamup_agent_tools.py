"""Pins for agent-initiated teamup_start/teamup_run (B-family, 2026-09-27).

Confirmed rules: agent starts a team when the task class deserves one
(crew picked by the agent), prompt tailoring lands once before the first
turn, run starts async, gated by RISKY_TOOLS.

ADR-092 slice 4 (MD-8): only the ``hpc`` crew opens a teamup session; every
other crew is its plan template (``test_crew_templates.py`` pins that run).
The session pins below therefore start the ``hpc`` crew, whose default roles
are pm, se, pg; they started the ``research`` crew's re-skinned slots before.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

import r4.companion.tools as tools_mod
from r4.companion.permissions import RISKY_TOOLS


def _find_tool(name: str, client: SimpleNamespace | None = None) -> dict:
    tools = tools_mod._teamup_tools(client or client_default())
    match = [t for t in tools if t["name"] == name]
    assert match, f"tool {name} not registered"
    return match[0]


def client_default():
    return SimpleNamespace(
        chat=lambda **kw: SimpleNamespace(
            content="### ROLE: pm\n" + "generic pm. " + "a" * 160 + "\n",
            tool_calls=[],
            usage={},
        )
    )


@pytest.fixture()
def blocked_team(tmp_path, monkeypatch):
    import threading

    import r4.teamup.session as session_module
    from r4.companion.ui.bridge_server import CompanionBridgeServer

    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(5), "test did not release the model call"
        return SimpleNamespace(content="test reply")

    client = SimpleNamespace(chat=chat)
    team = session_module.TeamupSession.create(
        session_module.TeamupSessionConfig(name="controlled", max_iterations=5), client
    )
    # Peer suite semantics pre-date the agentic role engine: each role = ONE chat.
    # The chat-once factory rides the CLASS-level seam so freshly-loaded
    # objects (bridge/tool paths) run the same shape; monkeypatch reverts it.
    from role_fixtures import chat_once_factory

    monkeypatch.setattr(
        session_module.TeamupSession, "_subcompanion_factory", chat_once_factory(client)
    )
    bridge = object.__new__(CompanionBridgeServer)
    bridge._session = SimpleNamespace(client=client)
    bridge._agent_model_value = lambda: "test-model"
    bridge._emit_line = lambda _payload: None
    try:
        yield team, bridge, client, entered, release, calls
    finally:
        release.set()
        assert _await_idle(), "team worker did not exit"


@pytest.mark.parametrize("control", ["pause", "cancel"])
def test_bridge_controls_reach_running_team(blocked_team, control):
    from r4.teamup.session import TeamupSession

    team, bridge, client, entered, release, calls = blocked_team
    bridge.teamup_run({"session_id": team.session_id})
    assert entered.wait(5)
    active = TeamupSession.load(team.session_id, client)
    reply = getattr(bridge, f"teamup_{control}")({"session_id": team.session_id})
    assert TeamupSession.load(team.session_id, client) is active
    assert active.status.value == reply["status"]
    release.set()
    assert _await_idle()
    assert len(calls) == 1
    assert TeamupSession.load(team.session_id, client).status.value == reply["status"]


@pytest.mark.parametrize("first", ["bridge", "agent"])
def test_team_run_guard_shared_across_entry_points(blocked_team, first):
    team, bridge, client, entered, release, calls = blocked_team
    agent_run = _find_tool("teamup_run", client)["execute"]
    if first == "bridge":
        bridge.teamup_run({"session_id": team.session_id})
    else:
        assert json.loads(agent_run(team.session_id))["ok"]
    assert entered.wait(5)
    with pytest.raises(ValueError, match="already running"):
        bridge.teamup_run({"session_id": team.session_id})
    assert json.loads(agent_run(team.session_id))["ok"] is False
    release.set()
    assert _await_idle()
    assert len(calls) == len(team.config.roles)


def test_cancelled_team_cannot_be_silently_restarted(blocked_team):
    team, bridge, client, _entered, _release, calls = blocked_team
    bridge.teamup_cancel({"session_id": team.session_id})
    with pytest.raises(ValueError, match="cancelled"):
        bridge.teamup_run({"session_id": team.session_id})
    reply = json.loads(_find_tool("teamup_run", client)["execute"](team.session_id))
    assert reply["ok"] is False
    assert "cancelled" in reply["error"]
    assert not calls


def test_teamup_start_and_run_are_risky_gated():
    assert "teamup_start" in RISKY_TOOLS
    assert "teamup_run" in RISKY_TOOLS


@pytest.fixture()
def client_with_answer():
    return SimpleNamespace(
        chat=lambda **kw: SimpleNamespace(
            content=(
                "### ROLE: pm\n" + "custom pm for this goal. " + "q" * 200 + "\n"
                "### ROLE: pg\n" + "custom pg for this goal. " + "q" * 200 + "\n"
                "### ROLE: se\n" + "custom se. " + "q" * 200 + "\n"
                "### ROLE: cd\n" + "custom cd. " + "q" * 200 + "\n"
            ),
            tool_calls=[],
            usage={},
        )
    )


def _await_idle(timeout: float = 5.0) -> bool:
    """Wait until the companion async guard is empty again."""
    from r4.teamup.session import TeamupSession

    guard = TeamupSession._active_runs
    deadline = time.time() + timeout
    while time.time() < deadline and guard:
        time.sleep(0.02)
    return not guard


def _assert_hpc_start(out: dict) -> None:
    assert out["ok"] is True
    assert out["crew"] == "hpc"
    assert out["roles"] == ["pm", "se", "pg"]
    assert out["tailor"]["tailored"] == ["pg", "pm", "se"]
    assert out["iterations_started"] == 1


def _assert_hpc_persisted(loaded) -> None:
    assert loaded.config.role_prompts["pm"].startswith("custom pm for this goal.")
    assert loaded.config.optimization_goal == "characterise ridge defects missed by classifiers"
    assert loaded.config.max_iterations == 4
    assert loaded.iteration >= 1  # one iteration really ran


def test_teamup_start_creates_tailors_and_fires(tmp_path, client_with_answer):
    import r4.teamup.session as sessmod
    from r4.teamup.session import TeamupSession

    tmp_sessions = tmp_path / "teams"
    monkey_orig = sessmod.TEAMUP_SESSIONS_DIR
    sessmod.TEAMUP_SESSIONS_DIR = tmp_sessions
    tool = _find_tool("teamup_start", client_with_answer)
    try:
        out = json.loads(
            tool["execute"](
                goal="characterise ridge defects missed by classifiers",
                crew="hpc",
                max_iterations=4,
            )
        )
        _assert_hpc_start(out)
        assert _await_idle()
        loaded = TeamupSession.load(out["session_id"], client_with_answer)
        _assert_hpc_persisted(loaded)
    finally:
        sessmod.TEAMUP_SESSIONS_DIR = monkey_orig


def test_teamup_start_hpc_crew_and_refusals(tmp_path, client_with_answer):
    import r4.teamup.session as sessmod

    tmp_sessions = tmp_path / "teams"
    monkey_orig = sessmod.TEAMUP_SESSIONS_DIR
    sessmod.TEAMUP_SESSIONS_DIR = tmp_sessions
    tool = _find_tool("teamup_start", client_with_answer)
    try:
        out = json.loads(
            tool["execute"](
                goal="reduce walltime of kernel_x",
                crew="hpc",
                run=False,
                hardware_target="rccs-gb10",
            )
        )
        refused = json.loads(tool["execute"](goal="x", crew="banana"))
    finally:
        sessmod.TEAMUP_SESSIONS_DIR = monkey_orig
    assert out["ok"] is True
    assert out["crew"] == "hpc"
    assert out["iterations_started"] == 0
    assert refused["ok"] is False
    assert "banana" in refused["error"]


def test_teamup_run_refuses_parallel_and_runs_iterations(tmp_path, client_with_answer):
    import r4.teamup.session as sessmod

    tmp_sessions = tmp_path / "teams"
    monkey_orig = sessmod.TEAMUP_SESSIONS_DIR
    sessmod.TEAMUP_SESSIONS_DIR = tmp_sessions
    start = _find_tool("teamup_start", client_with_answer)
    run = _find_tool("teamup_run", client_with_answer)
    try:
        out = json.loads(start["execute"](goal="paired hypotheses", crew="hpc"))
        assert _await_idle()
        again = json.loads(run["execute"](session_id=out["session_id"], iterations=1))
        assert again["ok"] is True and again["iterations_started"] == 1
        refused = json.loads(run["execute"](session_id=out["session_id"]))
        assert refused["ok"] is False and "already running" in refused["error"]
        assert _await_idle()
    finally:
        sessmod.TEAMUP_SESSIONS_DIR = monkey_orig


def test_a_plan_crew_runs_through_the_plan_executor_never_a_session(tmp_path):
    """Slice 4: ``code`` (and every crew but hpc) is its plan template; the
    tool hands it to the session's plan executor and opens no teamup session."""
    import r4.teamup.session as sessmod

    calls = []
    executor = SimpleNamespace(
        start_crew=lambda *a, **kw: calls.append((a, kw)) or {"ok": True, "crew": "code"}
    )
    tools = tools_mod._teamup_tools(client_default(), plan_executor=executor)
    tool = next(t for t in tools if t["name"] == "teamup_start")
    monkey_orig = sessmod.TEAMUP_SESSIONS_DIR
    sessmod.TEAMUP_SESSIONS_DIR = tmp_path / "teams"
    try:
        out = json.loads(tool["execute"](goal="add unit tests", crew="dev", run=False))
    finally:
        sessmod.TEAMUP_SESSIONS_DIR = monkey_orig
    assert out == {"ok": True, "crew": "code"}
    assert calls == [(("code", "add unit tests"), {"models": None, "run": False})]
    assert not (tmp_path / "teams").exists()


def test_a_plan_crew_without_a_plan_executor_is_refused_by_name():
    out = json.loads(_find_tool("teamup_start")["execute"](goal="x", crew="code"))
    assert out["ok"] is False and "team plans need a connected client" in out["error"]


def test_unknown_crew_still_refused_by_name():
    tool = _find_tool("teamup_start")
    out = json.loads(tool["execute"](goal="x", crew="banana"))
    assert out["ok"] is False and "known: code, hpc, kakenhi-report" in out["error"]
