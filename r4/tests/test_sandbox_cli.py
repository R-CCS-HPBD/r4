"""The local tool sandbox on the command line (ADR-101 D-1, D-2, D-12).

``r4 --sandbox``, ``r4 -p`` and the REPL's notice line, ``r4 config sandbox``,
the doctor line and the web question's local note. The backend is faked; the
profile store is the scratch config dir of ``conftest``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from bridge_rpc_harness import Harness, McpHooks, install_mcp_hooks, make_harness

from r4.companion.sandbox import (
    SANDBOX_LAUNCH_ENV,
    SANDBOX_NOTICE_OFF,
    SANDBOX_NOTICE_OFF_CLI,
    SandboxSetting,
    current_sandbox,
    profile_sandbox_default,
    set_current_sandbox,
)
from r4.companion.sandbox import context as context_mod
from r4.companion.sandbox import selftest as selftest_mod


class FakeBackend:
    def __init__(self, ok: bool = True, reason: str = "", name: str = "bwrap") -> None:
        self.ok, self.reason, self.name = ok, reason, name

    def probe(self) -> tuple[bool, str]:
        return self.ok, self.reason

    def build_argv(self, command: Any, policy: Any, cwd: Any) -> list[str]:
        return ["FAKE", *command]


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    from r4._cli import sandbox_prompt

    fake = FakeBackend()
    install_mcp_hooks(monkeypatch)  # hermetic: never the host's MCP daemon
    monkeypatch.setattr(context_mod, "backend_for_platform", lambda: fake)
    monkeypatch.setattr(sandbox_prompt, "backend_for_platform", lambda: fake)
    monkeypatch.setattr(selftest_mod, "run_self_test", lambda ctx, policy=None: (True, ""))
    for name in ("R4_SANDBOX", "R4_SANDBOXED", SANDBOX_LAUNCH_ENV, "R4_VENUE_SESSION_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    yield fake
    set_current_sandbox(None)


@pytest.fixture
def harness(backend: FakeBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    monkeypatch.chdir(tmp_path)
    return make_harness(tmp_path, monkeypatch)


def _stderr_lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [line for line in capsys.readouterr().err.splitlines() if line.strip()]


# ── the root flag ───────────────────────────────────────────────────────────


def test_root_flag_parses_and_becomes_the_launch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from r4.cli import _add_root_parser_args, _apply_sandbox_flag

    parser = argparse.ArgumentParser()
    _add_root_parser_args(parser)
    args = parser.parse_args(["--sandbox", "on"])
    assert args.root_sandbox == "on"
    monkeypatch.delenv(SANDBOX_LAUNCH_ENV, raising=False)
    _apply_sandbox_flag(args)
    assert os.environ[SANDBOX_LAUNCH_ENV] == "on"
    monkeypatch.delenv(SANDBOX_LAUNCH_ENV)
    _apply_sandbox_flag(parser.parse_args([]))
    assert SANDBOX_LAUNCH_ENV not in os.environ


# ── r4 -p / REPL ────────────────────────────────────────────────────────────


def test_apply_local_sandbox_prints_one_off_line(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    from r4._cli.sandbox_prompt import apply_local_sandbox

    state = apply_local_sandbox(harness.session, launch_env={})
    assert state["mode"] == "off" and state["notice"] == SANDBOX_NOTICE_OFF
    # r4 -p and the REPL have no /sandbox: their line names what they do have.
    assert _stderr_lines(capsys) == [SANDBOX_NOTICE_OFF_CLI]
    assert "/sandbox" not in SANDBOX_NOTICE_OFF_CLI
    for way in ("r4 --sandbox on", "R4_SANDBOX=on", "r4 config sandbox on"):
        assert way in SANDBOX_NOTICE_OFF_CLI
    assert current_sandbox() is harness.session.sandbox_context


def test_the_launch_flag_is_popped_applied_and_silent(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    from r4._cli.sandbox_prompt import apply_local_sandbox

    env = {SANDBOX_LAUNCH_ENV: "on", "PATH": "/bin"}
    state = apply_local_sandbox(harness.session, launch_env=env)
    assert SANDBOX_LAUNCH_ENV not in env
    assert (state["mode"], state["source"]) == ("on", "flag")
    assert SandboxSetting.from_metadata(harness.session.metadata).mode == "on"
    assert _stderr_lines(capsys) == []


def test_an_unusable_backend_prints_its_reason_once(
    harness: Harness, backend: FakeBackend, capsys: pytest.CaptureFixture[str]
) -> None:
    from r4._cli.sandbox_prompt import apply_local_sandbox

    backend.ok, backend.reason = False, "bwrap not found (install bubblewrap)"
    apply_local_sandbox(harness.session, launch_env={SANDBOX_LAUNCH_ENV: "on"})
    lines = _stderr_lines(capsys)
    assert len(lines) == 1 and "bwrap not found (install bubblewrap)" in lines[0]


def test_a_venue_worker_prints_nothing(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import r4.venue as venue
    from r4._cli.sandbox_prompt import apply_local_sandbox

    monkeypatch.setattr(venue, "in_venue", lambda: True)
    state = apply_local_sandbox(harness.session, launch_env={})
    assert (state["mode"], state["source"]) == ("off", "venue")
    assert _stderr_lines(capsys) == []


def test_r4_p_prints_exactly_one_sandbox_line(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from r4.companion.ui import _launch_headless
    from r4.companion.ui import repl as repl_mod

    harness.session.send = lambda prompt, **_kw: "done"
    monkeypatch.setattr(repl_mod, "open_cli_session", lambda client, **_kw: harness.session)
    with patch("r4.companion.session_store.CompanionSessionStore") as store_cls:
        _launch_headless(
            client=harness.client,
            prompt="hi",
            session_id=None,
            model=None,
            skill=None,
            system=None,
        )
        store_cls.return_value.save.assert_called_once()
    captured = capsys.readouterr()
    assert "done" in captured.out
    sandbox_lines = [line for line in captured.err.splitlines() if "andbox" in line]
    assert sandbox_lines == [SANDBOX_NOTICE_OFF_CLI]


def test_r4_p_with_a_mock_session_installs_no_mock_context(
    backend: FakeBackend, capsys: pytest.CaptureFixture[str]
) -> None:
    from unittest.mock import MagicMock

    from r4._cli.sandbox_prompt import apply_local_sandbox

    set_current_sandbox(None)
    state = apply_local_sandbox(MagicMock(), launch_env={})
    assert state["mode"] == "off"
    assert not isinstance(current_sandbox(), MagicMock)


# ── session-less agent runs (team plan, teamup, recipe) ─────────────────────


def _cli_agent_sandbox(args: Any, config: Any) -> Any:
    """``cli_agent_sandbox_and_web`` with web ``none``; the installed context."""
    from r4._cli.sandbox_prompt import cli_agent_sandbox_and_web
    from r4.companion.web_access import make_web_access

    web = make_web_access("none", source="flag")
    cli_agent_sandbox_and_web(args, config, lambda: lambda: web)
    return current_sandbox()


def test_cli_agent_sandbox_follows_the_root_flag(
    backend: FakeBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    profile = SimpleNamespace(profile="default")
    context = _cli_agent_sandbox(SimpleNamespace(root_sandbox="on"), profile)
    assert (context.setting.mode, context.setting.source) == ("on", "flag")
    off = _cli_agent_sandbox(SimpleNamespace(), profile)
    assert off.setting.source == "default_off"


def test_cli_agent_sandbox_says_when_on_cannot_work(
    backend: FakeBackend, capsys: pytest.CaptureFixture[str]
) -> None:
    backend.ok, backend.reason = False, "no user namespaces"
    _cli_agent_sandbox(SimpleNamespace(root_sandbox="on"), None)
    lines = _stderr_lines(capsys)
    assert len(lines) == 1 and "no user namespaces" in lines[0]


# ── r4 config sandbox ───────────────────────────────────────────────────────


def _config_args(argv: list[str]) -> argparse.Namespace:
    from r4._cli.config import ConfigCommand

    parser = argparse.ArgumentParser()
    ConfigCommand().register(parser.add_subparsers(dest="command"))
    return parser.parse_args(argv)


def test_config_sandbox_on_and_off_write_the_profile_default(
    backend: FakeBackend, capsys: pytest.CaptureFixture[str]
) -> None:
    from r4._cli.sandbox_prompt import handle_config_sandbox

    config = SimpleNamespace(profile="default")
    assert handle_config_sandbox(_config_args(["config", "sandbox", "on"]), config) == 0
    assert profile_sandbox_default("default") == "on"
    out = capsys.readouterr().out
    assert "sandbox default: on" in out and "backend: bwrap available" in out
    assert handle_config_sandbox(_config_args(["config", "sandbox", "off"]), config) == 0
    assert profile_sandbox_default("default") == "off"


def test_config_sandbox_show_prints_default_backend_and_policy(
    backend: FakeBackend, capsys: pytest.CaptureFixture[str]
) -> None:
    from r4._cli.sandbox_prompt import handle_config_sandbox

    backend.ok, backend.reason = False, "bwrap not found"
    args = _config_args(["config", "sandbox"])
    assert args.sandbox_action == "show"
    assert handle_config_sandbox(args, SimpleNamespace(profile="default")) == 0
    out = capsys.readouterr().out
    assert "sandbox default: off (not set)" in out
    assert "backend: unavailable (bwrap not found)" in out
    assert "extra_write_paths: (none)" in out


def test_config_sandbox_on_is_refused_without_a_working_backend(
    backend: FakeBackend, capsys: pytest.CaptureFixture[str]
) -> None:
    # Review LC-8: it wrote the default (every session then refused every
    # command, every MCP server exited 78) and exited 0.
    from r4._cli.sandbox_prompt import handle_config_sandbox
    from r4.companion.mcp_exposure import mcp_sandbox_desired

    backend.ok, backend.reason = False, "bubblewrap (bwrap) is not installed"
    config = SimpleNamespace(profile="default")
    assert handle_config_sandbox(_config_args(["config", "sandbox", "on"]), config) == 1
    assert profile_sandbox_default("default") == ""
    assert mcp_sandbox_desired() is False
    err = capsys.readouterr().err
    assert "not changed" in err and "bwrap) is not installed" in err
    assert handle_config_sandbox(_config_args(["config", "sandbox", "off"]), config) == 0


def test_config_sandbox_on_is_refused_when_the_self_test_fails(
    backend: FakeBackend, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from r4._cli.sandbox_prompt import handle_config_sandbox

    monkeypatch.setattr(selftest_mod, "run_self_test", lambda ctx, policy=None: (False, "leak"))
    config = SimpleNamespace(profile="default")
    assert handle_config_sandbox(_config_args(["config", "sandbox", "on"]), config) == 1
    assert profile_sandbox_default("default") == ""


def test_config_command_dispatches_sandbox(
    backend: FakeBackend, capsys: pytest.CaptureFixture[str]
) -> None:
    from r4._cli.config import ConfigCommand

    with patch("r4.config.get_config", return_value=SimpleNamespace(profile="default")):
        assert ConfigCommand().handle(_config_args(["config", "sandbox", "on"])) == 0
    assert profile_sandbox_default("default") == "on"


# ── r4 doctor ───────────────────────────────────────────────────────────────


def test_doctor_reports_the_backend(backend: FakeBackend) -> None:
    from r4._cli.doctor import _check_sandbox

    assert _check_sandbox()["message"] == "sandbox: bwrap available"
    backend.ok, backend.reason = False, "bwrap not found (install bubblewrap)"
    line = _check_sandbox()
    assert line["ok"] is True and line["available"] is False
    assert line["message"] == "sandbox: unavailable (bwrap not found (install bubblewrap))"


def test_plain_doctor_output_has_the_sandbox_line(
    backend: FakeBackend, capsys: pytest.CaptureFixture[str]
) -> None:
    # Follow-up 2: only --json showed local.checks.sandbox.
    from r4._cli.diagnostics import emit_full_diagnostics
    from r4._cli.doctor import _check_sandbox

    checks = {"local": {"ok": True, "checks": {"sandbox": _check_sandbox()}}}
    emit_full_diagnostics(checks, as_json=False, title="R4 Doctor")
    out = capsys.readouterr().out
    assert "sandbox: bwrap available · default: off" in out


# ── the web question's local note ───────────────────────────────────────────


def test_the_local_note_follows_the_sandbox(harness: Harness) -> None:
    from r4._cli import web_access_prompt as wap

    assert wap.local_note(False) == wap._LOCAL_NOTE
    assert "sandbox proxy" in wap.local_note(True)
    assert wap.local_note() == wap._LOCAL_NOTE  # the session's sandbox is off
    harness.session.set_sandbox(SandboxSetting("on", "", "slash"))
    assert "sandbox proxy" in wap.local_note()
    assert "sandbox proxy" in wap._question_lines(["none", "public"], remote=False)[-1]
    assert wap._question_lines(["none"], remote=True)[-1] == wap._REMOTE_NOTE


# ── review fixes (ADR-101 bridge lane, 2026-10-30) ──────────────────────────


@pytest.fixture
def mcp(backend: FakeBackend, monkeypatch: pytest.MonkeyPatch) -> McpHooks:
    return install_mcp_hooks(monkeypatch)


def test_a_cli_session_opening_on_moves_mcp_under_the_sandbox(
    harness: Harness, mcp: McpHooks, capsys: pytest.CaptureFixture[str]
) -> None:
    # Review #1: r4 --sandbox on left local MCP servers unconfined.
    from r4._cli.sandbox_prompt import apply_local_sandbox

    apply_local_sandbox(harness.session, launch_env={SANDBOX_LAUNCH_ENV: "on"})
    assert mcp.calls == ["require_mcp_sandbox"]
    mcp.error = OSError("ratchet not writable")
    apply_local_sandbox(harness.session, launch_env={})
    assert any("ratchet not writable" in line for line in _stderr_lines(capsys))


def test_a_cli_agent_run_on_moves_mcp_and_off_does_not(
    mcp: McpHooks, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _cli_agent_sandbox(SimpleNamespace(), None)
    assert mcp.calls == []
    _cli_agent_sandbox(SimpleNamespace(root_sandbox="on"), SimpleNamespace(profile="work"))
    assert mcp.calls == ["require_mcp_sandbox"]
    assert mcp.profiles == ["work"]  # review Q-3: the run's profile, not R4_PROFILE


def test_config_sandbox_moves_mcp_with_the_default(
    mcp: McpHooks, capsys: pytest.CaptureFixture[str]
) -> None:
    # Review #5: r4 config sandbox on|off never touched MCP (no CLI way back).
    from r4._cli.sandbox_prompt import handle_config_sandbox

    config = SimpleNamespace(profile="default")
    mcp.restarted = ["fs"]
    assert handle_config_sandbox(_config_args(["config", "sandbox", "on"]), config) == 0
    assert handle_config_sandbox(_config_args(["config", "sandbox", "off"]), config) == 0
    assert mcp.calls == ["require_mcp_sandbox", "release_mcp_sandbox_default"]
    assert "restarted MCP servers: fs" in capsys.readouterr().out
    mcp.error = OSError("ratchet not writable")
    assert handle_config_sandbox(_config_args(["config", "sandbox", "off"]), config) == 1
    assert profile_sandbox_default("default") == "off"
    assert any("ratchet not writable" in line for line in _stderr_lines(capsys))


def test_the_web_question_sees_the_run_sandbox(
    backend: FakeBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review #7: the question's note was computed before the sandbox existed.
    from r4._cli import web_access_prompt as wap
    from r4._cli.sandbox_prompt import cli_agent_sandbox_and_web
    from r4.companion.web_access import make_web_access

    monkeypatch.chdir(tmp_path)
    web = make_web_access("none", source="flag")
    notes: list[str] = []

    def ask() -> Any:
        notes.append(wap.local_note())
        return lambda: web

    getter = cli_agent_sandbox_and_web(SimpleNamespace(root_sandbox="on"), None, ask)
    assert notes == [wap._LOCAL_NOTE_SANDBOXED]
    assert getter() is web
    assert current_sandbox().setting.enabled
    assert "web: " in current_sandbox().status()["network"]


def test_team_plan_and_teamup_ask_after_the_sandbox(
    backend: FakeBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from r4._cli import team_plan, teamup
    from r4._cli import web_access_prompt as wap
    from r4.companion.web_access import make_web_access

    monkeypatch.chdir(tmp_path)
    web = make_web_access("none", source="flag")
    notes: list[str] = []

    def getter(args: Any, config: Any, **kwargs: Any) -> Any:
        notes.append(wap.local_note())
        return lambda: web

    monkeypatch.setattr(wap, "cli_agent_web_getter", getter)
    monkeypatch.setattr(team_plan, "_service", lambda client, web_access: web_access)
    args = SimpleNamespace(root_sandbox="on")
    assert team_plan._local_service(args, SimpleNamespace(config=None))() is web
    assert teamup._web_getter(args, SimpleNamespace(config=None))() is web
    assert notes == [wap._LOCAL_NOTE_SANDBOXED] * 2
