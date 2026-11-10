"""ADR-114 S2.3: interactive REPL presence; one-shot headless refusal."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from r4.companion.session_bus import SessionBus
from r4.companion.session_bus_tools import build_session_bus_tools, inbox_reminder
from r4.companion.tool_availability import unavailable_reason


@pytest.fixture(autouse=True)
def _bus_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("R4_SESSION_BUS_DIR", str(tmp_path / "bus"))
    monkeypatch.delenv("R4_SESSION_BUS", raising=False)


def _session(sid: str = "repl-session-1") -> Any:
    return SimpleNamespace(
        session_id=sid,
        name="REPL Session",
        metadata={},
        agent=SimpleNamespace(_effective_model=lambda: "test-model"),
        client=MagicMock(),
        bus_presence=None,
        send=MagicMock(return_value="answer"),
    )


def _quiet_terminal_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("r4._cli.sandbox_prompt.apply_local_sandbox", lambda _session: None)
    monkeypatch.setattr(
        "r4._cli.web_access_prompt.apply_repl_web_access",
        lambda _session, _config, flag=None: None,
    )
    monkeypatch.setattr(
        "r4._cli.web_access_prompt.apply_local_web_access",
        lambda _session, _config: None,
    )


def _run_repl(monkeypatch: pytest.MonkeyPatch, session: Any, run: Any) -> None:
    from r4.companion.ui import repl

    _quiet_terminal_setup(monkeypatch)
    monkeypatch.setattr(repl, "open_cli_session", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(repl.RichREPL, "run", run)
    repl.launch_repl(MagicMock())


def test_repl_advertises_lead_presence_then_withdraws(monkeypatch):
    session = _session()
    bus = SessionBus()

    def inspect_running(repl):
        assert repl.session is session
        entry = bus.get(session.session_id)
        assert entry is not None
        assert (entry.session_id, entry.pid, entry.host) == (
            session.session_id,
            os.getpid(),
            bus.host,
        )
        tools = build_session_bus_tools(session)
        assert all(unavailable_reason(tool, session.client) is None for tool in tools)

    _run_repl(monkeypatch, session, inspect_running)

    assert session.bus_presence is None
    assert bus.get(session.session_id) is None
    assert bus.live_sessions() == []


def test_repl_uses_the_same_durable_binding_and_withdraws_it(monkeypatch):
    session = _session("durable-repl-2")
    remote = MagicMock()
    factory = MagicMock(return_value=remote)
    monkeypatch.setattr("r4.companion.session_bus_remote.for_owner_client", factory)

    def inspect_running(_repl):
        factory.assert_called_once()
        bound_session, entry = factory.call_args.args
        assert bound_session is session
        assert entry.session_id == session.session_id
        assert session.bus_presence.remote is remote
        remote.bind.assert_called_once_with()

    _run_repl(monkeypatch, session, inspect_running)

    remote.withdraw.assert_called_once_with()


def test_repl_withdraws_presence_when_the_loop_raises(monkeypatch):
    session = _session("repl-error-3")
    bus = SessionBus()

    def fail_running(_repl):
        assert bus.get(session.session_id) is not None
        raise RuntimeError("terminal failed")

    with pytest.raises(RuntimeError, match="terminal failed"):
        _run_repl(monkeypatch, session, fail_running)

    assert session.bus_presence is None
    assert bus.live_sessions() == []


def test_repl_bus_optout_creates_no_presence_or_remote_binding(monkeypatch):
    session = _session("repl-optout-4")
    monkeypatch.setenv("R4_SESSION_BUS", "0")
    register = MagicMock(side_effect=AssertionError("opt-out registered local presence"))
    remote = MagicMock(side_effect=AssertionError("opt-out bound remote presence"))
    monkeypatch.setattr(SessionBus, "register", register)
    monkeypatch.setattr("r4.companion.session_bus_remote.for_owner_client", remote)

    def inspect_running(_repl):
        assert session.bus_presence is None
        tools = build_session_bus_tools(session)
        assert all("session bus" in (unavailable_reason(t, None) or "") for t in tools)

    _run_repl(monkeypatch, session, inspect_running)

    register.assert_not_called()
    remote.assert_not_called()
    assert not SessionBus().root.exists()


def test_repl_unread_notice_never_starts_a_hidden_turn(monkeypatch):
    from r4.companion.ui.session_bus_bridge import BusAttachment

    session = _session("repl-no-wake-5")
    bus = SessionBus()
    attachments = []
    original_start = BusAttachment.start

    def capture_start(attachment):
        attachments.append(attachment)
        return original_start(attachment)

    monkeypatch.setattr(BusAttachment, "start", capture_start)

    def inspect_running(_repl):
        bus.send(
            sender_id="other-session",
            sender_name="other",
            recipient_id=session.session_id,
            body="SENDER-CONTROLLED-BODY",
        )
        attachments[0].tick(now=100.0)
        reminder = inbox_reminder(session)
        assert "1 unread message(s)" in reminder
        assert "SENDER-CONTROLLED-BODY" not in reminder

    _run_repl(monkeypatch, session, inspect_running)

    session.send.assert_not_called()


def test_one_shot_headless_never_advertises_and_direct_calls_refuse(monkeypatch, capsys):
    from r4.companion.session_store import CompanionSessionStore
    from r4.companion.ui import _launch_headless, repl

    session = _session("headless-one-shot-6")
    _quiet_terminal_setup(monkeypatch)
    monkeypatch.setattr(repl, "open_cli_session", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(CompanionSessionStore, "save", lambda _store, _session: None)
    register = MagicMock(side_effect=AssertionError("headless registered ghost presence"))
    monkeypatch.setattr(SessionBus, "register", register)

    _launch_headless(
        client=session.client,
        prompt="one prompt",
        session_id=session.session_id,
        model=None,
        skill=None,
        system=None,
    )

    assert capsys.readouterr().out.strip() == "answer"
    session.send.assert_called_once_with("one prompt")
    register.assert_not_called()
    assert session.bus_presence is None
    assert not SessionBus().root.exists()
    tools = {tool["name"]: tool["execute"] for tool in build_session_bus_tools(session)}
    refusal = tools["session_list"]()
    assert refusal.startswith("Error: this session is not on the local session bus")
    assert "one-shot r4 -p" in refusal
