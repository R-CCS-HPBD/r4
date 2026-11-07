"""Pins for live team_event streaming on the JSONL bridge (2026-09-27).

A running ``teamup.run`` iteration streams out-of-band
``{"team_event": {...}}`` lines (iteration_started → agent_status /
agent_message → iteration_complete) before the unchanged RPC reply lands.
Clients that ignore the shape see identical behaviour (additive only).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from r4.companion.permissions import PermissionManager
from r4.companion.session_models import UsageSummary
from r4.companion.skills import SkillRegistry
from r4.companion.ui.bridge_server import CompanionBridgeServer
from r4.teamup.roles import AgentRole
from r4.teamup.session import SessionStatus, TeamupSession, TeamupSessionConfig


class _FakeStore:
    def __init__(self, session):
        self.session = session

    def create_session(self, client, name=None, *, model=None):
        self.session.client = client
        return self.session

    def load(self, session_id, client):
        self.session.client = client
        return self.session

    def save(self, session):
        return None

    def list_sessions(self, limit=50):
        return []


def _fake_companion_session():
    client = SimpleNamespace(
        config=SimpleNamespace(
            profile="research",
            gateway_url="http://gateway.test",
            project_id="demo",
            model="config-model",
            endpoint_url=None,
            endpoint_provider=None,
        ),
        readiness=MagicMock(return_value={"ready": True}),
        list_endpoints=MagicMock(return_value={"endpoints": []}),
    )
    return SimpleNamespace(
        session_id="comp-1",
        name="Demo",
        client=client,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        agent=SimpleNamespace(model="test-model"),
        messages=[],
        skills=SkillRegistry(),
        permissions=PermissionManager(),
        context=SimpleNamespace(_memory={}),
        usage_summary=UsageSummary(),
        active_endpoints=[],
        metadata={},
        _harness=None,
        send=lambda text, **kwargs: "answer",
        request_turn_cancel=MagicMock(),
        usage_report=MagicMock(return_value={"total_tokens": 0}),
        export_markdown=MagicMock(return_value="# Demo"),
    )


def _fake_companion_factory(response_text: str):
    """Factory honoring the role-agent engine without a real LLM call."""

    class _FakeCompanion:
        def __init__(self, *a, **kw):
            self._listeners = []

        def on_event(self, cb) -> None:
            self._listeners.append(cb)

        def send(self, context, *, extra_system, tools, stream):
            return response_text

    def _factory(*a, **kw):
        return _FakeCompanion()

    return _factory


def _fake_teamup(tmp_path, *, response_text: str = "PM says hi") -> TeamupSession:
    def _chat(**kwargs):
        return SimpleNamespace(content=response_text, tool_calls=[], usage={})

    cfg = TeamupSessionConfig(name="rocket", roles=[AgentRole.PM, AgentRole.SE])
    session = TeamupSession(cfg, SimpleNamespace(chat=_chat), session_dir=tmp_path / "team")
    session._subcompanion_factory = _fake_companion_factory(response_text)
    session.status = SessionStatus.RUNNING
    return session


def _await_event_kind(lines, kind: str, timeout: float = 5.0):
    """Poll for one team_event kind; fail by name on timeout."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        found = [line["team_event"] for line in lines if line["team_event"]["kind"] == kind]
        if found:
            return found[0]
        time.sleep(0.02)
    raise AssertionError(f"no {kind} event within {timeout}s")


def _make_tailor_answer() -> str:
    body = "x" * 200
    return (
        f"### ROLE: pm\ncustom pm. {body}\n"
        f"### ROLE: pg\ncustom pg. {body}\n"
        f"### ROLE: se\ncustom se. {body}\n"
        f"### ROLE: cd\ncustom cd. {body}\n"
    )


def _wire_tailor_client(bridge, answer: str):
    bridge.session.client = SimpleNamespace(
        chat=lambda **kw: SimpleNamespace(content=answer, tool_calls=[], usage={})
    )
    return bridge


def _await_run_finished(lines, timeout: float = 5.0) -> None:
    """Poll until the background iteration's run_finished line lands."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(line["team_event"]["kind"] == "run_finished" for line in lines):
            return
        time.sleep(0.02)


def _bridge(monkeypatch) -> tuple[CompanionBridgeServer, list[dict[str, Any]]]:
    session = _fake_companion_session()
    bridge = CompanionBridgeServer(client=session.client, store=_FakeStore(session))
    lines: list[dict[str, Any]] = []
    monkeypatch.setattr(bridge, "_emit_line", lambda payload: lines.append(payload))
    return bridge, lines


def _expected_kind_sequence() -> list[str]:
    per_role = ["agent_status", "agent_status", "agent_message"]
    return ["iteration_started"] + per_role * 2 + ["iteration_complete"]


def _assert_reply_unchanged(reply: dict[str, Any], team: TeamupSession) -> None:
    assert (
        reply["session_id"] == team.session_id
        and reply["continued"] is True
        and reply["iteration"] == 1
        and reply["status"] == "running"
        and "budget" in reply
    )


def _assert_start_ack(reply: dict[str, Any], team: TeamupSession) -> None:
    assert reply["ok"] is True
    assert reply["started"] is True
    assert reply["session_id"] == team.session_id
    assert reply["expected_iteration"] == 1


def _assert_live_arc(lines: list[dict[str, Any]]) -> None:
    kinds = [line["team_event"]["kind"] for line in lines]
    assert kinds[0] == "iteration_started"
    assert kinds[-1] == "run_finished"
    for want in ("agent_status", "agent_message", "iteration_complete"):
        assert want in kinds


def test_teamup_run_is_async_start_then_live_events_then_run_finished(monkeypatch, tmp_path):
    bridge, lines = _bridge(monkeypatch)
    team = _fake_teamup(tmp_path)
    monkeypatch.setattr(bridge, "_teamup_session", lambda sid: team)

    reply = bridge.teamup_run({})
    _assert_start_ack(reply, team)
    _await_run_finished(lines)
    _assert_live_arc(lines)
    finished = next(
        line["team_event"] for line in lines if line["team_event"]["kind"] == "run_finished"
    )
    assert finished["continued"] is True
    assert "errors" not in finished  # clean run — honest absence
    # Guard released: a follow-up START is legal again.
    assert bridge.teamup_run({})["started"] is True


def _preview_is_honest(event: dict[str, Any], team: TeamupSession) -> bool:
    return (
        len(event["preview"]) == 1201
        and event["preview"].endswith("…")
        and event["role"] in {"pm", "se"}
        and event["session_name"] == "rocket"
        and event["iteration"] == 1
        and event["max_iterations"] == team.config.max_iterations
    )


def test_agent_message_preview_is_capped_and_honest(monkeypatch, tmp_path):
    bridge, lines = _bridge(monkeypatch)
    team = _fake_teamup(tmp_path, response_text="y " * 5000)
    monkeypatch.setattr(bridge, "_teamup_session", lambda sid: team)
    bridge.teamup_run({})
    _await_run_finished(lines)

    messages = [
        line["team_event"] for line in lines if line["team_event"]["kind"] == "agent_message"
    ]
    assert messages and all(_preview_is_honest(event, team) for event in messages)


def test_agent_status_order_is_thinking_then_idle_per_role(monkeypatch, tmp_path):
    bridge, lines = _bridge(monkeypatch)
    monkeypatch.setattr(bridge, "_teamup_session", lambda sid: _fake_teamup(tmp_path))
    bridge.teamup_run({})
    _await_run_finished(lines)

    statuses = [
        (e.get("role"), e.get("status"))
        for e in (line["team_event"] for line in lines)
        if e["kind"] == "agent_status"
    ]
    assert statuses == [
        ("pm", "thinking"),
        ("pm", "idle"),
        ("se", "thinking"),
        ("se", "idle"),
    ]


def test_compose_keeps_the_previous_callback_chain_and_restores(monkeypatch, tmp_path):
    bridge, lines = _bridge(monkeypatch)
    team = _fake_teamup(tmp_path)
    prev: list[str] = []
    team._on_agent_message = lambda role, content: prev.append(content)

    bridge._compose_teamup_live_callbacks(team)
    team._on_agent_message(AgentRole.PM, "text")
    assert prev == ["text"]
    assert any(
        line["team_event"]["kind"] == "agent_message"
        and line["team_event"].get("preview") == "text"
        for line in lines
    )

    bridge._restore_teamup_live_callbacks(team)
    before = len(lines)
    team._on_agent_message(AgentRole.PM, "after-restore")
    assert prev == ["text", "after-restore"]
    assert len(lines) == before  # no more bridge lines after restore


def test_preview_is_full_when_under_the_cap(monkeypatch, tmp_path):
    bridge, lines = _bridge(monkeypatch)
    team = _fake_teamup(tmp_path, response_text="short")
    monkeypatch.setattr(bridge, "_teamup_session", lambda sid: team)
    bridge.teamup_run({})
    _await_run_finished(lines)
    previews = [
        line["team_event"]["preview"]
        for line in lines
        if line["team_event"]["kind"] == "agent_message"
    ]
    assert previews == ["short", "short"]


def test_teamup_tailor_calls_root_agent_and_returns_result(monkeypatch, tmp_path):
    from r4.teamup.session import TeamupSession, TeamupSessionConfig

    pm_prompt = "Custom pm. " + "x" * 200
    cfg = TeamupSessionConfig(name="rocket", roles=[AgentRole.PM])
    session = TeamupSession(
        cfg,
        SimpleNamespace(
            chat=lambda **kw: SimpleNamespace(
                content=f"### ROLE: pm\n{pm_prompt}", tool_calls=[], usage={}
            )
        ),
        session_dir=tmp_path / "t3",
    )

    bridge, _lines = _bridge(monkeypatch)
    bridge.session.client = SimpleNamespace(
        chat=lambda **kw: SimpleNamespace(
            content=f"### ROLE: pm\n{pm_prompt}", tool_calls=[], usage={}
        )
    )
    monkeypatch.setattr(bridge, "_teamup_session", lambda sid: session)

    out = bridge.teamup_tailor({})
    assert out["session_id"] == session.session_id
    assert out["root"] == "pm"
    assert out["tailored"] == ["pm"]
    assert session.config.role_prompts["pm"] == pm_prompt


def test_teamup_run_while_running_refuses_by_name(monkeypatch, tmp_path):
    import asyncio
    import time

    bridge, _lines = _bridge(monkeypatch)
    team = _fake_teamup(tmp_path)

    async def _slow_iteration():
        await asyncio.sleep(0.5)
        return True

    team.run_iteration = _slow_iteration  # type: ignore[method-assign]
    monkeypatch.setattr(bridge, "_teamup_session", lambda sid: team)
    reply = bridge.teamup_run({})
    assert reply["started"] is True
    import pytest

    with pytest.raises(ValueError, match="teamup.conflict"):
        bridge.teamup_run({})
    # Let the background iteration finish so the guard is clean for other tests.
    deadline = time.time() + 5
    while time.time() < deadline and team.session_dir.resolve() in TeamupSession._active_runs:
        time.sleep(0.02)


def test_teamup_agent_start_is_async_and_posts_team_started(monkeypatch, tmp_path):
    import r4.teamup.session as sessmod

    orig = sessmod.TEAMUP_SESSIONS_DIR
    sessmod.TEAMUP_SESSIONS_DIR = tmp_path / "teams"
    bridge, lines = _bridge(monkeypatch)
    _wire_tailor_client(bridge, _make_tailor_answer())
    try:
        # ADR-092 slice 4: only the hpc crew opens a teamup session (research
        # is its plan template: test_crew_templates pins that path).
        out = bridge.teamup_agent_start({"goal": "prove alloy X", "crew": "hpc"})
        assert out == {"ok": True, "accepted": True, "goal": "prove alloy X", "crew": "hpc"}
        started = _await_event_kind(lines, "team_started")
        _assert_team_started_payload(started)
        _await_run_finished(lines)
    finally:
        sessmod.TEAMUP_SESSIONS_DIR = orig


def _assert_team_started_payload(started) -> None:
    assert started["roles"] == ["pm", "se", "pg"]
    assert started["crew"] == "hpc"
    assert started["tailor"]["tailored"] == ["pg", "pm", "se"]
    assert started["session_id"]
    kinds_meta = started["kind"]
    assert kinds_meta == "team_started"


def test_agent_model_value_mirrors_effective_model_not_raw_none():
    """2026-09-29 (user's rivault/kimi-k3 repro): session with agent.model
    unset must hand teams the model the agent's own turn uses (client config,
    e.g. public kimi-k3) — never None, which fell through to the gateway's
    dead default deployment and 503'd the tailor + every role turn."""
    from r4.companion.agent import ResearchCompanion

    client = SimpleNamespace(_config=SimpleNamespace(model="kimi-k3"))
    agent = ResearchCompanion(client, model=None)
    bridge = object.__new__(CompanionBridgeServer)
    bridge._session = SimpleNamespace(agent=agent, client=client)
    assert bridge._agent_model_value() == "kimi-k3"


def test_agent_model_value_prefers_the_set_session_model():
    from r4.companion.agent import ResearchCompanion

    client = SimpleNamespace(_config=SimpleNamespace(model="rivault-gpt-oss-static"))
    agent = ResearchCompanion(client, model="kimi-k3")
    bridge = object.__new__(CompanionBridgeServer)
    bridge._session = SimpleNamespace(agent=agent, client=client)
    assert bridge._agent_model_value() == "kimi-k3"


def test_agent_model_value_falls_back_for_duck_agents():
    bridge = object.__new__(CompanionBridgeServer)
    bridge._session = SimpleNamespace(agent=SimpleNamespace(model="duck-model"))
    assert bridge._agent_model_value() == "duck-model"


def _exploding_chat(**_kw):
    raise RuntimeError("API error: 503: No active endpoint for model 'rivault-gpt-oss-static'")


def _assert_degraded_started(started) -> None:
    assert "503" in started["tailor"]["degraded"]
    assert started["tailor"]["tailored"] == []
    assert sorted(started["tailor"]["kept_default"]) == ["pg", "pm", "se"]


def _role_error_events(lines) -> list:
    return [
        line["team_event"]
        for line in lines
        if line["team_event"].get("kind") == "agent_status"
        and line["team_event"].get("status") == "error"
    ]


def test_teamup_agent_start_degrades_when_tailor_endpoint_is_down(monkeypatch, tmp_path):
    """2026-09-29: a 503-class tailor failure must NOT kill the start — the
    team runs crew-spec baselines, the team_started payload names the
    degradation, and dead-endpoint role errors land per agent, not as a
    start error (repro of the rivault-gpt-oss-static outage)."""
    import r4.teamup.session as sessmod

    orig = sessmod.TEAMUP_SESSIONS_DIR
    sessmod.TEAMUP_SESSIONS_DIR = tmp_path / "teams"
    bridge, lines = _bridge(monkeypatch)
    bridge.session.client = SimpleNamespace(chat=_exploding_chat)
    try:
        out = bridge.teamup_agent_start({"goal": "probe alloy", "crew": "hpc"})
        assert out["accepted"] is True
        _assert_degraded_started(_await_event_kind(lines, "team_started"))
        assert not any(line["team_event"].get("kind") == "team_start_error" for line in lines)
        _await_run_finished(lines)
        assert len(_role_error_events(lines)) == 3  # each role error visible by itself
    finally:
        sessmod.TEAMUP_SESSIONS_DIR = orig


def test_teamup_agent_start_second_bootstrap_is_a_named_conflict(monkeypatch, tmp_path):
    bridge, _lines = _bridge(monkeypatch)
    bridge._teamup_bootstrapping = True
    import pytest

    with pytest.raises(ValueError, match="teamup.conflict"):
        bridge.teamup_agent_start({"goal": "x", "crew": "research"})


def test_guarded_start_flips_created_to_running_and_iterates(monkeypatch, tmp_path):
    """The 'i0 run_finished' bug (2026-09-28): a chat-flow-started session was
    CREATED, so run_iteration returned False in the same tick. The guarded
    start must transition to RUNNING (persisted), then the iteration runs."""

    from r4.teamup.session import TeamupSession, TeamupSessionConfig

    cfg = TeamupSessionConfig(name="fresh", roles=[AgentRole.PM])
    team = TeamupSession(
        cfg,
        SimpleNamespace(chat=lambda **kw: SimpleNamespace(content="ok", tool_calls=[], usage={})),
        session_dir=tmp_path / "t",
    )
    bridge, lines = _bridge(monkeypatch)
    monkeypatch.setattr(bridge, "_teamup_session", lambda sid: team)

    bridge._teamup_start_guarded(team)

    from r4.teamup.session import SessionStatus

    assert team.status == SessionStatus.RUNNING
    snapshot = (tmp_path / "t" / "session.json").read_text()
    assert '"status": "running"' in snapshot
    _await_run_finished(lines)
    finished = next(
        line["team_event"] for line in lines if line["team_event"]["kind"] == "run_finished"
    )
    assert finished["continued"] is True
    assert finished["iteration"] == 1
