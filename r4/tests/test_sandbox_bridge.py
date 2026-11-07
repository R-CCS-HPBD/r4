"""The local tool sandbox on the session and the console bridge (ADR-101 D-1, D-2, D-11, D-12).

The backend is faked (no bwrap runs); the session, its store and the bridge
are real (``bridge_rpc_harness``), on the scratch HOME of ``conftest``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from bridge_rpc_harness import VENUE_ROW, Harness, McpHooks, install_mcp_hooks, make_harness

from r4.companion.sandbox import (
    SANDBOX_LAUNCH_ENV,
    SANDBOX_NOTICE_OFF,
    SELF_TEST_FAILED,
    UNAVAILABLE,
    SandboxSetting,
    current_sandbox,
    profile_sandbox_default,
    remember_sandbox_default,
    set_current_sandbox,
)
from r4.companion.sandbox import context as context_mod
from r4.companion.sandbox import selftest as selftest_mod
from r4.companion.ui import sandbox_bridge
from r4.companion.ui.bridge_server import (
    CompanionBridgeServer,
    _bridge_error_response,
    _parse_args,
    _project_runtime,
    _venue_runtime_projection,
)
from r4.companion.ui.sandbox_bridge import (
    SandboxBridgeError,
    opening_sandbox,
    sandbox_notice,
    sandbox_state,
)


class FakeBackend:
    """A backend whose probe answers what the test says; nothing runs under it."""

    def __init__(self, ok: bool = True, reason: str = "", name: str = "bwrap") -> None:
        self.ok, self.reason, self.name = ok, reason, name

    def probe(self) -> tuple[bool, str]:
        return self.ok, self.reason

    def build_argv(self, command: Any, policy: Any, cwd: Any) -> list[str]:
        return ["FAKE", *command]


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> McpHooks:
    return install_mcp_hooks(monkeypatch)


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    fake = FakeBackend()
    install_mcp_hooks(monkeypatch)  # hermetic: never the host's MCP daemon
    monkeypatch.setattr(context_mod, "backend_for_platform", lambda: fake)
    monkeypatch.setattr(selftest_mod, "run_self_test", lambda ctx, policy=None: (True, ""))
    for name in ("R4_SANDBOX", "R4_SANDBOXED", SANDBOX_LAUNCH_ENV, "R4_VENUE_SESSION_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    yield fake
    set_current_sandbox(None)


@pytest.fixture
def harness(backend: FakeBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    monkeypatch.chdir(tmp_path)
    return make_harness(tmp_path, monkeypatch)


def _setting(mode: str, source: str) -> SandboxSetting:
    return SandboxSetting(mode, "2026-10-30T00:00:00+00:00", source)


def _go_remote(h: Harness) -> None:
    h.session.metadata["venue"] = "frontend"
    _project_runtime(h.session, _venue_runtime_projection(VENUE_ROW))


# ── resolution (D-2, D-11) ──────────────────────────────────────────────────


def test_a_session_without_any_choice_is_off(harness: Harness) -> None:
    setting = harness.session.sandbox_setting()
    assert (setting.mode, setting.source) == ("off", "default_off")


def test_env_then_profile_default_apply_to_a_session_without_its_own(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.session._sandbox_profile_default = "on"
    assert harness.session.sandbox_setting().source == "profile"
    monkeypatch.setenv("R4_SANDBOX", "off")
    assert (harness.session.sandbox_setting().mode, harness.session.sandbox_setting().source) == (
        "off",
        "env",
    )


def test_the_profile_default_is_read_when_the_session_is_built(
    backend: FakeBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remember_sandbox_default("on", profile="default")
    monkeypatch.chdir(tmp_path)
    h = make_harness(tmp_path, monkeypatch)
    assert h.session.sandbox_setting().source == "profile"


def test_the_sessions_own_choice_beats_env_and_profile(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.session.set_sandbox(_setting("on", "slash"))
    harness.session._sandbox_profile_default = "off"
    monkeypatch.setenv("R4_SANDBOX", "off")
    assert harness.session.sandbox_setting().mode == "on"


def test_a_venue_worker_resolves_to_off_and_shows_no_notice(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import r4.venue as venue

    monkeypatch.setattr(venue, "in_venue", lambda: True)
    harness.session.set_sandbox(_setting("on", "slash"))
    setting = harness.session.sandbox_setting()
    assert (setting.mode, setting.source) == ("off", "venue")
    assert sandbox_state(harness.session)["notice"] == ""


@pytest.mark.parametrize(
    ("own", "launch", "resolved", "expected"),
    [
        (None, None, ("off", "default_off"), None),
        (None, ("off", "flag"), ("off", "default_off"), ("off", "flag")),
        (None, None, ("on", "env"), ("on", "env")),  # pinned: a resume keeps it
        (None, None, ("on", "profile"), ("on", "profile")),
        (None, None, ("off", "env"), None),  # an off is never pinned
        (None, None, ("on", "inherited"), None),
        (("on", "slash"), ("off", "flag"), ("on", "slash"), None),  # strictest wins
        (("off", "flag"), ("on", "flag"), ("off", "flag"), ("on", "flag")),
        (("off", "slash"), None, ("off", "slash"), None),
    ],
)
def test_opening_sandbox_order(own: Any, launch: Any, resolved: Any, expected: Any) -> None:
    to = lambda pair: _setting(*pair) if pair else None  # noqa: E731
    chosen = opening_sandbox(to(own), to(launch), _setting(*resolved))
    assert (None if chosen is None else (chosen.mode, chosen.source)) == expected


def test_an_env_on_is_kept_on_resume_without_it(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("R4_SANDBOX", "on")
    opened = harness.server.initialize({"venue": "local"})
    assert opened["session"]["sandbox"]["source"] == "env"
    harness.store.save(harness.server.session)
    monkeypatch.setenv("R4_SANDBOX", "off")
    state = harness.server.load_session({"session_id": opened["session"]["id"]})
    assert state["session"]["sandbox"]["mode"] == "on"


def test_a_resumed_on_session_is_not_loosened_by_a_launch_off(harness: Harness) -> None:
    harness.session.set_sandbox(_setting("on", "slash"))
    harness.store.save(harness.session)
    harness.server._sandbox_preset = _setting("off", "flag")
    state = harness.server.load_session({"session_id": harness.sid})
    assert (state["session"]["sandbox"]["mode"], state["session"]["sandbox"]["source"]) == (
        "on",
        "slash",
    )


# ── the launch preset ───────────────────────────────────────────────────────


def test_the_launch_env_is_taken_out_of_the_environment(
    backend: FakeBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SANDBOX_LAUNCH_ENV, "on")
    server = CompanionBridgeServer(client=None, store=None)
    assert SANDBOX_LAUNCH_ENV not in os.environ  # children never inherit it
    assert server._sandbox_preset is not None and server._sandbox_preset.source == "flag"


def test_the_bridge_argument_wins_and_still_pops_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SANDBOX_LAUNCH_ENV, "on")
    preset = sandbox_bridge.launch_sandbox("off")
    assert preset is not None and preset.mode == "off"
    assert SANDBOX_LAUNCH_ENV not in os.environ
    assert _parse_args(["--sandbox", "on"]).sandbox == "on"
    assert _parse_args([]).sandbox is None


def test_initialize_applies_the_launch_preset(harness: Harness) -> None:
    harness.server._sandbox_preset = _setting("on", "flag")
    state = harness.server.initialize({"venue": "local"})
    sandbox = state["session"]["sandbox"]
    assert (sandbox["mode"], sandbox["source"], sandbox["notice"]) == ("on", "flag", "")


# ── the notice (D-1) ────────────────────────────────────────────────────────


def test_a_new_local_session_carries_the_off_notice(harness: Harness) -> None:
    state = harness.server.initialize({"venue": "local"})
    assert state["session"]["sandbox"]["notice"] == SANDBOX_NOTICE_OFF


def test_a_resumed_off_session_carries_the_notice(harness: Harness) -> None:
    harness.store.save(harness.session)
    state = harness.server.load_session({"session_id": harness.sid})
    assert state["session"]["sandbox"]["notice"] == SANDBOX_NOTICE_OFF


def test_an_on_session_has_no_notice(harness: Harness) -> None:
    harness.session.set_sandbox(_setting("on", "slash"))
    assert harness.server.snapshot()["session"]["sandbox"]["notice"] == ""


def test_a_missing_backend_says_why_instead(harness: Harness, backend: FakeBackend) -> None:
    backend.ok, backend.reason = False, "bwrap not found (install bubblewrap)"
    notice = harness.server.snapshot()["session"]["sandbox"]["notice"]
    assert "bwrap not found (install bubblewrap)" in notice
    assert notice != SANDBOX_NOTICE_OFF


def test_a_frontend_session_reports_remote_and_no_notice(harness: Harness) -> None:
    _go_remote(harness)
    sandbox = harness.server.snapshot()["session"]["sandbox"]
    assert (sandbox["mode"], sandbox["notice"]) == ("remote", "")


def test_notice_rule() -> None:
    assert sandbox_notice({"mode": "off", "available": True}) == SANDBOX_NOTICE_OFF
    assert sandbox_notice({"mode": "on", "available": True}) == ""
    assert sandbox_notice({"mode": "on", "source": "inherited", "available": True}) == ""
    assert "Sandbox unavailable" in sandbox_notice({"mode": "on", "available": False})


# ── session.sandbox_set ─────────────────────────────────────────────────────


def test_set_on_with_a_missing_backend_is_refused_and_changes_nothing(
    harness: Harness, backend: FakeBackend
) -> None:
    backend.ok, backend.reason = False, "bwrap not found"
    with pytest.raises(SandboxBridgeError) as err:
        harness.server.session_sandbox_set({"mode": "on"})
    assert err.value.code == UNAVAILABLE
    assert "sandbox" not in harness.session.metadata
    reply = _bridge_error_response(7, err.value)
    assert reply["error"]["data"]["code"] == UNAVAILABLE


def test_set_on_with_a_failing_self_test_is_refused(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        selftest_mod, "run_self_test", lambda ctx, policy=None: (False, "write escaped")
    )
    with pytest.raises(SandboxBridgeError) as err:
        harness.server.session_sandbox_set({"mode": "on"})
    assert err.value.code == SELF_TEST_FAILED
    assert harness.session.sandbox_setting().mode == "off"


def test_set_on_stores_saves_and_moves_mcp_under_the_sandbox(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import r4.companion.mcp_exposure as mcp_exposure
    import r4.companion.tools as tools

    calls: list[Any] = []
    monkeypatch.setattr(
        mcp_exposure, "require_mcp_sandbox", lambda profile: calls.append(profile) or ["fs"]
    )
    monkeypatch.setattr(tools, "unconfined_running", lambda: ["shell job 12"])
    result = harness.server.session_sandbox_set({"mode": "on"})
    assert calls == ["default"]  # the session's profile reaches the hook (review Q-3)
    assert result["restarted_mcp"] == ["fs"]
    assert result["still_running"] == ["shell job 12"]
    assert (result["sandbox"]["mode"], result["sandbox"]["source"]) == ("on", "slash")
    assert result["state"]["session"]["sandbox"]["mode"] == "on"
    reloaded = harness.store.load(harness.sid, harness.client)
    assert SandboxSetting.from_metadata(reloaded.metadata).mode == "on"


def test_set_off_neither_restarts_mcp_nor_lists_processes(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import r4.companion.mcp_exposure as mcp_exposure

    monkeypatch.setattr(mcp_exposure, "require_mcp_sandbox", lambda _p: pytest.fail("not on"))
    harness.session.set_sandbox(_setting("on", "slash"))
    result = harness.server.session_sandbox_set({"mode": "off"})
    assert (result["sandbox"]["mode"], result["restarted_mcp"], result["still_running"]) == (
        "off",
        [],
        [],
    )


def test_set_default_writes_the_profile_and_off_releases_mcp(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import r4.companion.mcp_exposure as mcp_exposure

    monkeypatch.setattr(mcp_exposure, "require_mcp_sandbox", lambda _p: [])
    monkeypatch.setattr(mcp_exposure, "release_mcp_sandbox_default", lambda: ["fs"])
    harness.server.session_sandbox_set({"mode": "on", "default": True})
    assert profile_sandbox_default("default") == "on"
    config_json = Path(os.environ["XDG_CONFIG_HOME"]) / "r4" / "config.json"
    assert '"sandbox_default": "on"' in config_json.read_text()
    result = harness.server.session_sandbox_set({"mode": "off", "default": True})
    assert profile_sandbox_default("default") == "off"
    assert result["restarted_mcp"] == ["fs"]


def test_a_missing_mcp_hook_is_an_mcp_error_never_silent(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review Q-2: lane-isolation tolerance turned a renamed hook into a quiet
    # success while local MCP servers kept running unconfined.
    import r4.companion.mcp_exposure as mcp_exposure

    monkeypatch.delattr(mcp_exposure, "require_mcp_sandbox")
    result = harness.server.session_sandbox_set({"mode": "on"})
    assert "require_mcp_sandbox" in result["mcp_error"]
    assert result["sandbox"]["mcp_error"] == result["mcp_error"]


def test_the_snapshot_carries_the_detail_lines_and_a_separate_mcp_error(
    harness: Harness, mcp: McpHooks
) -> None:
    # Review Q-5: the console renders the bridge's own lines, never a copy.
    from r4.companion.sandbox import SANDBOX_DETAIL_LINES

    sandbox = harness.server.session_sandbox_get({})["sandbox"]
    assert sandbox["detail"] == list(SANDBOX_DETAIL_LINES)
    assert sandbox["mcp_error"] == ""
    mcp.error = OSError("ratchet not writable")
    sandbox = harness.server.session_sandbox_set({"mode": "on"})["sandbox"]
    assert "ratchet not writable" in sandbox["mcp_error"]
    assert "MCP" not in sandbox["notice"]


def test_set_on_a_frontend_session_is_refused(harness: Harness) -> None:
    _go_remote(harness)
    with pytest.raises(SandboxBridgeError) as err:
        harness.server.session_sandbox_set({"mode": "on"})
    assert err.value.code == "sandbox.remote"


def test_set_an_unknown_mode_is_refused(harness: Harness) -> None:
    with pytest.raises(SandboxBridgeError) as err:
        harness.server.session_sandbox_set({"mode": "maybe"})
    assert err.value.code == "sandbox.refused"


def test_get_reports_the_state(harness: Harness) -> None:
    sandbox = harness.server.session_sandbox_get({})["sandbox"]
    assert (sandbox["mode"], sandbox["backend"], sandbox["available"]) == ("off", "bwrap", True)
    assert sandbox["notice"] == SANDBOX_NOTICE_OFF


# ── the process-global context ──────────────────────────────────────────────


def test_the_active_session_owns_the_current_context(harness: Harness) -> None:
    assert current_sandbox() is harness.session.sandbox_context
    harness.session.set_sandbox(_setting("on", "slash"))
    other = harness.store.create_session(harness.client, model="gpt-x")
    # Built (a fork, an export) but not active: the on session keeps the spawns.
    assert current_sandbox() is harness.session.sandbox_context
    assert current_sandbox().setting.enabled
    harness.server._configure_session(other)
    assert current_sandbox() is other.sandbox_context


def test_the_first_session_of_a_process_installs_its_context(
    backend: FakeBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bridge_rpc_harness import FakeClient

    from r4.companion.session import CompanionSession

    set_current_sandbox(None)
    monkeypatch.chdir(tmp_path)
    session = CompanionSession(session_id="s1", name="first", client=FakeClient())
    assert current_sandbox() is session.sandbox_context


def test_the_context_follows_the_session_setting(harness: Harness) -> None:
    context = harness.session.sandbox_context
    assert not context.setting.enabled
    harness.session.set_sandbox(_setting("on", "slash"))
    assert context.setting.enabled
    assert context.status()["write_roots"] == [os.path.realpath(os.getcwd())]


# ── review fixes (ADR-101 bridge lane, 2026-10-30) ──────────────────────────


@pytest.mark.parametrize("how", ["preset", "env", "profile"])
def test_a_session_opening_on_moves_mcp_under_the_sandbox(
    harness: Harness, mcp: McpHooks, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    # Review #1: only /sandbox on reached require_mcp_sandbox.
    if how == "preset":
        harness.server._sandbox_preset = _setting("on", "flag")
    elif how == "env":
        monkeypatch.setenv("R4_SANDBOX", "on")
    else:
        harness.session._sandbox_profile_default = "on"
    harness.store.save(harness.session)
    harness.server._apply_initial_sandbox(harness.session)
    assert harness.session.sandbox_setting().mode == "on"
    assert mcp.calls == ["require_mcp_sandbox"]


def test_initialize_on_moves_mcp_and_off_leaves_it(harness: Harness, mcp: McpHooks) -> None:
    harness.server.initialize({"venue": "local"})
    assert mcp.calls == []
    harness.server._sandbox_preset = _setting("on", "flag")
    harness.server.initialize({"venue": "local"})
    assert mcp.calls == ["require_mcp_sandbox"]


def test_an_mcp_failure_at_open_is_in_the_notice(harness: Harness, mcp: McpHooks) -> None:
    mcp.error = OSError("read-only config dir")
    harness.server._sandbox_preset = _setting("on", "flag")
    sandbox = harness.server.initialize({"venue": "local"})["session"]["sandbox"]
    assert "read-only config dir" in sandbox["mcp_error"]


def _switch_away(harness: Harness, other_mode: str) -> Any:
    other = harness.store.create_session(harness.client, model="gpt-x")
    other.set_sandbox(_setting(other_mode, "slash"))
    harness.server._configure_session(other)
    assert current_sandbox() is other.sandbox_context  # the switch itself still applies
    return other


@pytest.mark.parametrize("other_mode", ["off", "on"])
def test_a_background_subagent_keeps_the_context_it_started_under(
    harness: Harness, other_mode: str
) -> None:
    # Review ESC-2: a background subagent outlived its turn and, after a
    # switch to an off session, ran its shell commands unconfined.
    import threading

    from r4.companion.async_agents import AsyncAgentPool

    harness.session.set_sandbox(_setting("on", "slash"))
    started_under = harness.session.sandbox_context
    gate, seen = threading.Event(), []

    def runner(_record: Any) -> str:
        gate.wait(5)
        seen.append(current_sandbox())
        return "done"

    pool = AsyncAgentPool(max_running=2)
    pool.spawn("task", runner)
    other = _switch_away(harness, other_mode)
    gate.set()
    assert pool.wait_idle(5) and seen == [started_under]
    other.set_sandbox(_setting("off", "slash"))  # review ESC-3: /sandbox off elsewhere
    assert started_under.setting.enabled


@pytest.mark.parametrize("other_mode", ["off", "on"])
def test_a_background_team_plan_keeps_the_context_it_started_under(
    harness: Harness, tmp_path: Path, other_mode: str
) -> None:
    # Review ESC-3, LC-5: an on->on switch (or a later /sandbox off there)
    # moved a running plan to another session's sandbox and web access.
    import threading

    from r4.companion import team_plan_tools

    harness.session.set_sandbox(_setting("on", "slash"))
    started_under = harness.session.sandbox_context
    gate, seen = threading.Event(), []

    async def start() -> Any:
        gate.wait(5)
        seen.append(current_sandbox())
        raise RuntimeError("stop here")

    live = team_plan_tools._LiveRun(run_id="r1", run_dir=tmp_path, plan="p")
    team_plan_tools._spawn(live, start)
    _switch_away(harness, other_mode)
    gate.set()
    assert live.done.wait(5) and seen == [started_under]


def test_a_second_session_in_one_process_turns_under_its_own_context(
    harness: Harness,
) -> None:
    # Review ESC-4: ACP holds many sessions; only the first installed its
    # context, so a later on session spawned under the first (off) one.
    from r4.companion import session as session_mod
    from r4.companion.sandbox import installed_sandbox

    first = harness.session
    second = harness.store.create_session(harness.client, model="gpt-x")
    second.set_sandbox(_setting("on", "slash"))
    assert installed_sandbox() is first.sandbox_context  # the process-global stays
    seen: list[Any] = []

    def turn(self: Any, message: str) -> str:
        seen.append(current_sandbox())
        return message

    pinned_turn = session_mod._own_sandbox_pinned(turn)
    pinned_turn(second, "hi")
    pinned_turn(first, "hi")
    assert seen == [second.sandbox_context, first.sandbox_context]
    assert seen[0].setting.enabled and not seen[1].setting.enabled
    assert getattr(session_mod.CompanionSession.send, "__wrapped__", None) is not None


def test_a_turn_inside_a_pinned_run_keeps_the_runs_context(harness: Harness) -> None:
    from r4.companion import session as session_mod
    from r4.companion.sandbox import pin_sandbox

    other = harness.store.create_session(harness.client, model="gpt-x")
    seen: list[Any] = []
    pinned_turn = session_mod._own_sandbox_pinned(lambda self: seen.append(current_sandbox()))
    with pin_sandbox(harness.session.sandbox_context):
        pinned_turn(other)
    assert seen == [harness.session.sandbox_context]


def test_an_acp_session_opening_on_moves_mcp_under_the_sandbox(
    harness: Harness, mcp: McpHooks, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review ESC-4: ACP never settled a session's sandbox (no MCP follow).
    from r4.companion.acp_session import CompanionAcpSession

    monkeypatch.setenv("R4_SANDBOX", "on")
    session = harness.store.create_session(harness.client, model="gpt-x")
    CompanionAcpSession(session, store=harness.store)
    assert mcp.calls == ["require_mcp_sandbox"]
    assert SandboxSetting.from_metadata(session.metadata).source == "env"  # pinned on


def test_an_on_session_replaces_an_off_context_while_a_plan_runs(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from r4.companion import team_plan_tools

    monkeypatch.setattr(team_plan_tools, "live_runs", lambda: [{"run_id": "r1"}])
    other = harness.store.create_session(harness.client, model="gpt-x")
    other.set_sandbox(_setting("on", "slash"))
    harness.server._configure_session(other)
    assert current_sandbox() is other.sandbox_context


def test_a_failed_default_write_changes_nothing(
    harness: Harness, mcp: McpHooks, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review #3: the session was set and saved before the default write raised.
    def disk_full(mode: str, *, profile: Any = None) -> None:
        raise OSError("disk full")

    harness.session.set_sandbox(_setting("on", "slash"))
    harness.store.save(harness.session)
    monkeypatch.setattr(sandbox_bridge, "remember_sandbox_default", disk_full)
    with pytest.raises(SandboxBridgeError) as err:
        harness.server.session_sandbox_set({"mode": "off", "default": True})
    assert "disk full" in err.value.reason
    assert harness.session.sandbox_setting().mode == "on"
    reloaded = harness.store.load(harness.sid, harness.client)
    assert SandboxSetting.from_metadata(reloaded.metadata).mode == "on"
    assert mcp.calls == []


def test_a_failed_save_puts_the_previous_profile_default_back(
    harness: Harness, mcp: McpHooks, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review LC-6: the default stayed written while the TUI said "not changed".
    def broken(session: Any) -> None:
        raise OSError("store gone")

    monkeypatch.setattr(harness.store, "save", broken)
    with pytest.raises(OSError):
        harness.server.session_sandbox_set({"mode": "on", "default": True})
    assert profile_sandbox_default("default") == ""
    remember_sandbox_default("off", profile="default")
    with pytest.raises(OSError):
        harness.server.session_sandbox_set({"mode": "on", "default": True})
    assert profile_sandbox_default("default") == "off"
    assert mcp.calls == [] and "sandbox" not in harness.session.metadata


def test_a_failed_save_leaves_the_live_session_as_it_was(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(session: Any) -> None:
        raise OSError("store gone")

    monkeypatch.setattr(harness.store, "save", broken)
    with pytest.raises(OSError):
        harness.server.session_sandbox_set({"mode": "on"})
    assert "sandbox" not in harness.session.metadata


def test_an_mcp_failure_on_set_is_reported_not_swallowed(harness: Harness, mcp: McpHooks) -> None:
    # Review #4: an OSError from require_mcp_sandbox was logged and dropped.
    mcp.error = OSError("ratchet not writable")
    result = harness.server.session_sandbox_set({"mode": "on"})
    assert result["sandbox"]["mode"] == "on"  # the shell/Python sandbox still applies
    assert "ratchet not writable" in result["mcp_error"]
    assert "ratchet not writable" in result["sandbox"]["mcp_error"]


def test_a_daemon_left_out_of_sync_is_reported(harness: Harness, mcp: McpHooks) -> None:
    mcp.in_sync = False
    result = harness.server.session_sandbox_set({"mode": "on"})
    assert result["mcp_error"] == sandbox_bridge.MCP_STALE
    mcp.in_sync = True
    assert harness.server.session_sandbox_set({"mode": "on"})["mcp_error"] == ""


def test_the_unavailable_notice_says_what_happens_in_each_mode() -> None:
    # Review #6: on, commands are refused; they do not run with full permissions.
    on = sandbox_notice({"mode": "on", "available": False, "reason": "bwrap not found"})
    off = sandbox_notice({"mode": "off", "available": False, "reason": "bwrap not found"})
    assert "refused" in on and "full permissions" not in on
    assert "full permissions" in off
