"""Pins for r4.companion.mcp_exposure (pane-toggle auto-exposure)."""

from __future__ import annotations

import json
import signal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.companion import mcp_exposure


@pytest.fixture()
def public_url(monkeypatch):
    monkeypatch.setenv("R4_REMOTE_PROXY_PUBLIC_URL", "http://umrs-host.example:50800")
    return "http://umrs-host.example:50800"


def test_register_uses_public_url_slice(public_url):
    client = SimpleNamespace(mcp=MagicMock())
    client.mcp.register_server.return_value = {"server_id": "srv-1"}
    result = mcp_exposure.register_for_agent(client, "fetch", "fetches urls")
    call = client.mcp.register_server.call_args.kwargs
    assert call["url"] == "http://umrs-host.example:50800/fetch"
    assert result["state"] == "registered"


def test_register_blocked_reports_reason(public_url):
    client = SimpleNamespace(mcp=MagicMock())
    client.mcp.register_server.side_effect = RuntimeError("API error: 500")
    result = mcp_exposure.register_for_agent(client, "fetch")
    assert result["state"] == "blocked"
    assert "500" in result["reason"]


def test_ensure_proxy_running_short_circuits_when_healthy(monkeypatch):
    monkeypatch.setattr(mcp_exposure, "_http_probe_ok", lambda *a, **k: True)
    monkeypatch.setattr(mcp_exposure, "_proxy_identity", lambda *a, **k: {})
    result = mcp_exposure.ensure_proxy_running()
    assert result["state"] == "running"


def test_ensure_proxy_running_fails_without_config(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_exposure, "_http_probe_ok", lambda *a, **k: False)
    monkeypatch.setattr(mcp_exposure, "_user_config_file", lambda: tmp_path / "absent.json")
    result = mcp_exposure.ensure_proxy_running()
    assert result["state"] == "failed"
    assert "no mcp config" in result["reason"]


def test_expose_skips_registration_when_proxy_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_exposure, "ensure_proxy_running", lambda: {"state": "failed", "reason": "nope"}
    )
    client = SimpleNamespace(mcp=MagicMock())
    result = mcp_exposure.expose_local_mcp(client, "fetch")
    assert result["registration"]["state"] == "blocked"
    client.mcp.register_server.assert_not_called()


# ── ADR-101 D-9: the per-user MCP sandbox ──────────────────────────────────

_DAEMON_PID = 424242


class _FakeProc:
    def __init__(self, pid, exited=False):
        self.pid = pid
        self.exited = exited

    def poll(self):
        return 1 if self.exited else None


class _FakeDaemon:
    """Fakes the port, ``GET /``, Popen and signals; never touches a real process."""

    def __init__(self, monkeypatch, tmp_path, *, running=False, identity=None, servers=None):
        self.running = running
        self.identity = identity or {}
        self.spawned: list[list[str]] = []
        self.spawn_env: list[dict] = []
        self.signals: list[tuple[int, int]] = []
        self.next_pid = _DAEMON_PID
        config = tmp_path / "mcp-servers.json"
        config.write_text(json.dumps({"mcpServers": servers or {}}))
        self.config = config
        monkeypatch.setattr(mcp_exposure, "_user_config_file", lambda: config)
        monkeypatch.setattr(mcp_exposure, "_http_probe_ok", lambda *a, **k: self.running)
        monkeypatch.setattr(mcp_exposure, "_proxy_identity", lambda *a, **k: self.identity)
        monkeypatch.setattr(mcp_exposure.subprocess, "Popen", self._popen)
        monkeypatch.setattr(mcp_exposure, "_signal_daemon", self._signal)
        monkeypatch.setattr(mcp_exposure, "_port_freed", lambda _t: True)
        monkeypatch.setattr(
            mcp_exposure, "_command_line", lambda pid: "python -m r4_mcp.runners.remote_proxy"
        )
        monkeypatch.setattr(mcp_exposure, "_proc_pids", lambda: [])  # never scan the real /proc
        monkeypatch.setattr(mcp_exposure, "_START_WAIT", 0.5)

    def _popen(self, command, **kwargs):
        self.spawned.append(list(command))
        self.spawn_env.append(dict(kwargs.get("env") or {}))
        self.running = True
        self.identity = _identity(_flag(command, "--sandbox-digest") or "", pid=self.next_pid)
        return _FakeProc(self.next_pid)

    def _signal(self, pid, signum):
        self.signals.append((pid, signum))
        return True


def _identity(digest="", pid=_DAEMON_PID, service="r4-mcp-remote-proxy"):
    return {
        "service": service,
        "pid": pid,
        "sandbox_digest": digest,
        "servers": {"fetch": {"kind": "stdio"}, "ctx": {"kind": "http"}},
    }


def _sandbox_default(mode: str) -> None:
    from r4.companion.sandbox import remember_sandbox_default

    remember_sandbox_default(mode)


def _flag(command: list[str], name: str) -> str | None:
    return command[command.index(name) + 1] if name in command else None


def test_daemon_starts_without_wrap_flags_when_sandbox_off(monkeypatch, tmp_path):
    fake = _FakeDaemon(monkeypatch, tmp_path)
    result = mcp_exposure.ensure_proxy_running()
    assert result["state"] == "started"
    (command,) = fake.spawned
    assert "--stdio-wrap" not in command and "--sandbox-digest" not in command
    assert command[-6:] == [
        "--config",
        str(tmp_path / "mcp-servers.json"),
        "--host",
        "0.0.0.0",
        "--port",
        "50800",
    ]
    recorded = json.loads(mcp_exposure._pid_path().read_text())
    assert recorded["pid"] == _DAEMON_PID


def test_daemon_starts_with_launcher_wrap_when_profile_default_on(monkeypatch, tmp_path):
    from r4.companion.sandbox import mcp_launcher_prefix

    _sandbox_default("on")
    fake = _FakeDaemon(monkeypatch, tmp_path)
    assert mcp_exposure.ensure_proxy_running()["state"] == "started"
    (command,) = fake.spawned
    policy = mcp_exposure._policy_path()
    assert json.loads(_flag(command, "--stdio-wrap")) == mcp_launcher_prefix(policy)
    document = json.loads(policy.read_text())
    assert document["enabled"] is True
    assert _flag(command, "--sandbox-digest") == document["digest"]
    assert oct(policy.stat().st_mode & 0o777) == "0o600"


def test_running_daemon_with_matching_digest_is_reused(monkeypatch, tmp_path):
    fake = _FakeDaemon(monkeypatch, tmp_path, running=True, identity=_identity(""))
    assert mcp_exposure.ensure_proxy_running()["state"] == "running"
    assert fake.spawned == [] and fake.signals == []


def test_digest_mismatch_restarts_the_identified_daemon(monkeypatch, tmp_path):
    fake = _FakeDaemon(monkeypatch, tmp_path, running=True, identity=_identity(""))
    mcp_exposure._write_pid_file(_DAEMON_PID, 50800, tmp_path / "mcp-servers.json")
    restarted = mcp_exposure.require_mcp_sandbox()
    assert restarted == ["fetch"]  # the stdio servers, not the http upstream
    assert fake.signals == [(_DAEMON_PID, signal.SIGTERM)]
    (command,) = fake.spawned
    assert "--stdio-wrap" in command and _flag(command, "--sandbox-digest")


@pytest.mark.parametrize(
    "identity, pid_file_pid, command_line",
    [
        ({}, _DAEMON_PID, "python -m r4_mcp.runners.remote_proxy"),  # not JSON / pre-digest
        (_identity(service="something-else"), _DAEMON_PID, "python -m r4_mcp.runners.remote_proxy"),
        (_identity(), 1111, "python -m r4_mcp.runners.remote_proxy"),  # not the pid we started
        (_identity(), _DAEMON_PID, "/usr/bin/nginx"),  # pid reused by another program
    ],
)
def test_unidentified_listener_is_never_killed(
    monkeypatch, tmp_path, identity, pid_file_pid, command_line
):
    _sandbox_default("on")
    fake = _FakeDaemon(monkeypatch, tmp_path, running=True, identity=identity)
    monkeypatch.setattr(mcp_exposure, "_command_line", lambda pid: command_line)
    mcp_exposure._write_pid_file(pid_file_pid, 50800, tmp_path / "mcp-servers.json")
    result = mcp_exposure.ensure_proxy_running()
    assert result["state"] == "running" and result["sandbox"] == "stale"
    assert "not restarted" in result["reason"]
    assert fake.signals == [] and fake.spawned == []


def test_daemon_that_will_not_stop_is_reported(monkeypatch, tmp_path):
    fake = _FakeDaemon(monkeypatch, tmp_path, running=True, identity=_identity(""))
    monkeypatch.setattr(mcp_exposure, "_port_freed", lambda _t: False)
    mcp_exposure._write_pid_file(_DAEMON_PID, 50800, tmp_path / "mcp-servers.json")
    _sandbox_default("on")
    result = mcp_exposure.ensure_proxy_running()
    assert result["sandbox"] == "stale" and "did not stop" in result["reason"]
    assert [s for _p, s in fake.signals] == [signal.SIGTERM, signal.SIGKILL]
    assert fake.spawned == []


def test_require_without_a_daemon_restarts_nothing(monkeypatch, tmp_path):
    fake = _FakeDaemon(monkeypatch, tmp_path, running=False)
    assert mcp_exposure.require_mcp_sandbox() == []
    assert fake.spawned == []
    assert mcp_exposure.mcp_sandbox_desired() is True


def test_ratchet_outlives_a_session_turning_the_sandbox_off(monkeypatch, tmp_path):
    _FakeDaemon(monkeypatch, tmp_path)
    assert mcp_exposure.mcp_sandbox_desired() is False
    mcp_exposure.require_mcp_sandbox()
    # Another session (or this one) goes off: env, profile default off. MCP stays on.
    monkeypatch.setenv("R4_SANDBOX", "off")
    _sandbox_default("off")
    assert mcp_exposure.mcp_sandbox_desired() is True
    assert mcp_exposure.release_mcp_sandbox_default() == []
    assert mcp_exposure.mcp_sandbox_desired() is False
    assert mcp_exposure.release_mcp_sandbox_default() == []  # idempotent


def test_release_restarts_a_sandboxed_daemon_without_the_wrap(monkeypatch, tmp_path):
    fake = _FakeDaemon(monkeypatch, tmp_path, running=True, identity=_identity("abc123"))
    mcp_exposure._write_pid_file(_DAEMON_PID, 50800, tmp_path / "mcp-servers.json")
    mcp_exposure.require_mcp_sandbox()  # identity digest differs from ours: one restart
    assert len(fake.spawned) == 1
    fake.spawned.clear()
    fake.signals.clear()
    fake.identity = _identity("abc123")
    assert mcp_exposure.release_mcp_sandbox_default() == ["fetch"]
    (command,) = fake.spawned
    assert "--stdio-wrap" not in command


def test_status_reports_the_running_daemon(monkeypatch, tmp_path):
    identity = _identity("abc")
    identity["servers"] = {
        "a": {"kind": "stdio", "sandboxed": True, "exempt": False},
        "b": {"kind": "stdio", "sandboxed": False, "exempt": True},
    }
    _FakeDaemon(monkeypatch, tmp_path, running=True, identity=identity)
    status = mcp_exposure.mcp_sandbox_status()
    assert status["desired"] is False and status["running_digest"] == "abc"
    assert status["sandboxed_servers"] == ["a"] and status["exempt_servers"] == ["b"]
    assert status["in_sync"] is False


def test_status_without_a_daemon(monkeypatch, tmp_path):
    _FakeDaemon(monkeypatch, tmp_path, running=False)
    status = mcp_exposure.mcp_sandbox_status()
    assert status["running_digest"] is None and status["in_sync"] is True
    assert status["sandboxed_servers"] == [] and status["exempt_servers"] == []


# ── review fixes (ADR-101 D-9) ──────────────────────────────────────────────

_LEGACY = {"status": "ok", "upstreams": ["fetch"]}  # a daemon from before ADR-101


def _legacy_argv(port="50800", *extra):
    return ["python", "-m", "r4_mcp.runners.remote_proxy", "--config", "c", "--port", port, *extra]


def test_stale_daemon_is_not_handed_to_the_agent_while_the_sandbox_is_wanted(monkeypatch, tmp_path):
    _sandbox_default("on")
    _FakeDaemon(monkeypatch, tmp_path, running=True, identity=dict(_LEGACY))
    client = SimpleNamespace(mcp=MagicMock())
    result = mcp_exposure.expose_local_mcp(client, "fetch")
    assert result["proxy"]["sandbox"] == "stale"
    assert result["registration"]["state"] == "blocked"
    assert "not under it" in result["registration"]["reason"]
    client.mcp.register_server.assert_not_called()


def test_stale_daemon_stricter_than_wanted_still_registers(monkeypatch, tmp_path):
    """Sandbox off, an unidentified daemon runs sandboxed: stricter than wanted, not blocked."""
    _FakeDaemon(monkeypatch, tmp_path, running=True, identity=_identity("abc", pid=7))
    client = SimpleNamespace(mcp=MagicMock())
    client.mcp.register_server.return_value = {"server_id": "s"}
    result = mcp_exposure.expose_local_mcp(client, "fetch")
    assert result["proxy"]["sandbox"] == "stale"
    assert result["registration"]["state"] == "registered"


def test_legacy_daemon_is_identified_by_its_command_line_and_restarted(monkeypatch, tmp_path):
    _sandbox_default("on")
    fake = _FakeDaemon(monkeypatch, tmp_path, running=True, identity=dict(_LEGACY))
    argv = {4242: _legacy_argv(), 4343: ["python", "-m", "something"]}
    monkeypatch.setattr(mcp_exposure, "_proc_pids", lambda: list(argv))
    monkeypatch.setattr(mcp_exposure, "_proc_argv", lambda pid: argv[pid])
    result = mcp_exposure.ensure_proxy_running()
    assert result["state"] == "started"
    assert fake.signals == [(4242, signal.SIGTERM)]
    assert "--stdio-wrap" in fake.spawned[0]


@pytest.mark.parametrize(
    "argvs",
    [
        [_legacy_argv("50801")],  # another port
        [_legacy_argv("50800", "--stdio-wrap", "[]")],  # not a pre-ADR-101 daemon
        [_legacy_argv(), _legacy_argv()],  # ambiguous
        [],  # not ours (another uid) or no procfs
    ],
)
def test_legacy_daemon_not_matched_is_never_killed(monkeypatch, tmp_path, argvs):
    _sandbox_default("on")
    fake = _FakeDaemon(monkeypatch, tmp_path, running=True, identity=dict(_LEGACY))
    table = dict(enumerate(argvs, start=5000))
    monkeypatch.setattr(mcp_exposure, "_proc_pids", lambda: list(table))
    monkeypatch.setattr(mcp_exposure, "_proc_argv", lambda pid: table[pid])
    assert mcp_exposure.ensure_proxy_running()["sandbox"] == "stale"
    assert fake.signals == [] and fake.spawned == []


def test_proc_argv_is_empty_for_another_users_process(monkeypatch):
    monkeypatch.setattr(mcp_exposure.os, "getuid", lambda: 4_000_000)
    assert mcp_exposure._proc_argv(1) == []


def test_desired_state_and_digest_do_not_depend_on_the_active_profile(monkeypatch, tmp_path):
    from r4.companion.sandbox import remember_sandbox_default

    config = tmp_path / "mcp-servers.json"
    config.write_text('{"mcpServers": {}}')
    remember_sandbox_default("on", "a")
    remember_sandbox_default("off", "b")
    monkeypatch.setenv("R4_PROFILE", "a")
    on_a = (mcp_exposure.mcp_sandbox_desired(), mcp_exposure.mcp_policy_document(config))
    monkeypatch.setenv("R4_PROFILE", "b")
    on_b = (mcp_exposure.mcp_sandbox_desired(), mcp_exposure.mcp_policy_document(config))
    assert on_a == on_b and on_a[0] is True


def test_session_on_another_profile_keeps_the_wrapped_daemon(monkeypatch, tmp_path):
    from r4.companion.sandbox import remember_sandbox_default

    remember_sandbox_default("on", "a")
    fake = _FakeDaemon(monkeypatch, tmp_path)
    monkeypatch.setenv("R4_PROFILE", "a")
    assert mcp_exposure.ensure_proxy_running()["state"] == "started"
    monkeypatch.setenv("R4_PROFILE", "b")
    assert mcp_exposure.ensure_proxy_running() == {"state": "running", "port": 50800}
    assert fake.signals == [] and len(fake.spawned) == 1


def test_profiles_with_different_policies_agree_on_the_strictest(monkeypatch, tmp_path):
    from r4.config import ProfileConfigStore

    store = ProfileConfigStore()
    store.save("a", sandbox_default="on", sandbox_policy={"env_keep": ["X", "Y"]})
    deny = {"env_keep": ["Y"], "extra_deny_read_paths": ["/d"]}
    store.save("b", sandbox_default="on", sandbox_policy=deny)
    config = tmp_path / "mcp-servers.json"
    config.write_text('{"mcpServers": {}}')
    monkeypatch.setenv("R4_PROFILE", "a")
    doc_a = mcp_exposure.mcp_policy_document(config)
    monkeypatch.setenv("R4_PROFILE", "b")
    assert mcp_exposure.mcp_policy_document(config) == doc_a
    assert doc_a["user_policy"]["env_keep"] == ["Y"]
    assert doc_a["user_policy"]["extra_deny_read_paths"] == ["/d"]


def test_ratchet_snapshots_the_policy_and_only_tightens(monkeypatch, tmp_path):
    from r4.config import ProfileConfigStore

    _FakeDaemon(monkeypatch, tmp_path)
    store = ProfileConfigStore()
    store.save("a", sandbox_policy={"env_keep": ["X", "Y"]})
    store.save("b", sandbox_policy={"env_keep": ["Y"]})
    mcp_exposure.require_mcp_sandbox("a")
    assert mcp_exposure.per_user_policy().env_keep == ("X", "Y")
    mcp_exposure.require_mcp_sandbox("b")
    assert mcp_exposure.per_user_policy().env_keep == ("Y",)


_SERVERS = {
    "github": {
        "type": "stdio",
        "command": "gh-mcp",
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GH}", "PLAIN": "1"},
    },
    "brave": {"type": "stdio", "command": "brave", "env": {"BRAVE_API_KEY": "k"}},
    "free": {"type": "stdio", "command": "f", "sandbox": False, "env": {"FREE_TOKEN": "t"}},
    "ctx": {"type": "http", "url": "https://ctx.example/mcp", "env": {"HTTP_TOKEN": "x"}},
}


def test_policy_keeps_the_env_names_wrapped_entries_declare(monkeypatch, tmp_path):
    from r4.companion.sandbox import UserSandboxPolicy, scrub_env

    _sandbox_default("on")
    fake = _FakeDaemon(monkeypatch, tmp_path, servers=_SERVERS)
    document = mcp_exposure.mcp_policy_document(fake.config)
    keep = UserSandboxPolicy.from_mapping(document["user_policy"]).env_keep
    assert keep == ("BRAVE_API_KEY", "GITHUB_PERSONAL_ACCESS_TOKEN", "PLAIN")
    env = {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp", "BRAVE_API_KEY": "bk", "OPENAI_API_KEY": "o"}
    kept = scrub_env(env, keep=keep)
    assert kept == {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp", "BRAVE_API_KEY": "bk"}
    assert document["exempt"] == ["free"]


def test_editing_an_exemption_changes_the_digest(monkeypatch, tmp_path):
    _sandbox_default("on")
    fake = _FakeDaemon(monkeypatch, tmp_path, servers={"s": {"type": "stdio", "command": "x"}})
    before = mcp_exposure.mcp_policy_document(fake.config)["digest"]
    exempt = {"s": {"type": "stdio", "command": "x", "sandbox": False}}
    fake.config.write_text(json.dumps({"mcpServers": exempt}))
    assert mcp_exposure.mcp_policy_document(fake.config)["digest"] != before


def test_daemon_is_started_without_r4_mcp_config(monkeypatch, tmp_path):
    monkeypatch.setenv("R4_MCP_CONFIG", str(tmp_path / "other.json"))
    fake = _FakeDaemon(monkeypatch, tmp_path)
    assert mcp_exposure.ensure_proxy_running()["state"] == "started"
    assert "R4_MCP_CONFIG" not in fake.spawn_env[0] and "HOME" in fake.spawn_env[0]


def test_pid_file_is_written_only_for_the_daemon_that_answers(monkeypatch, tmp_path):
    """Session B's daemon lost the port to A's: B must not record its (dead) pid."""
    fake = _FakeDaemon(monkeypatch, tmp_path)
    winner = _identity("", pid=100)

    def losing_popen(command, **_kwargs):
        fake.spawned.append(list(command))
        fake.running, fake.identity = True, winner
        return _FakeProc(200, exited=True)

    monkeypatch.setattr(mcp_exposure.subprocess, "Popen", losing_popen)
    mcp_exposure._write_pid_file(100, 50800, fake.config)
    result = mcp_exposure.ensure_proxy_running()
    assert result == {"state": "running", "port": 50800}
    assert json.loads(mcp_exposure._pid_path().read_text())["pid"] == 100
    _sandbox_default("on")  # the winner now runs the wrong digest: stale, never restarted here
    fake.running = False
    result = mcp_exposure.ensure_proxy_running()
    assert result["sandbox"] == "stale" and "took port" in result["reason"]
    assert fake.signals == []


def test_start_holds_the_daemon_lock(monkeypatch, tmp_path):
    fcntl = pytest.importorskip("fcntl")
    fake = _FakeDaemon(monkeypatch, tmp_path)
    held = []
    real_popen = fake._popen

    def probing_popen(command, **kwargs):
        with mcp_exposure._pid_path().with_name("remote-proxy.lock").open("a") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except BlockingIOError:
                held.append(True)
        return real_popen(command, **kwargs)

    monkeypatch.setattr(mcp_exposure.subprocess, "Popen", probing_popen)
    assert mcp_exposure.ensure_proxy_running()["state"] == "started"
    assert held == [True]


def test_failed_restart_reports_nothing_restarted(monkeypatch, tmp_path):
    fake = _FakeDaemon(monkeypatch, tmp_path, running=True, identity=_identity(""))
    mcp_exposure._write_pid_file(_DAEMON_PID, 50800, fake.config)

    def broken_popen(*_a, **_k):
        raise OSError("no python")

    monkeypatch.setattr(mcp_exposure.subprocess, "Popen", broken_popen)
    assert mcp_exposure.require_mcp_sandbox() == []
    assert fake.signals == [(_DAEMON_PID, signal.SIGTERM)]


def test_release_clears_the_ratchet_and_never_writes_a_default(monkeypatch, tmp_path):
    # Review Q-3: the caller writes the default for the session's profile;
    # release wrote it again for R4_PROFILE/default, a profile nobody named.
    from r4.companion.sandbox import profile_sandbox_default, remember_sandbox_default

    _FakeDaemon(monkeypatch, tmp_path)
    _sandbox_default("on")
    mcp_exposure.require_mcp_sandbox()
    assert mcp_exposure.release_mcp_sandbox_default() == []
    assert profile_sandbox_default() == "on"  # untouched: still wanted by the profile
    assert mcp_exposure.mcp_sandbox_desired() is True
    remember_sandbox_default("off")
    assert mcp_exposure.mcp_sandbox_desired() is False


def test_a_fresh_pre_adr101_daemon_counts_as_started_with_the_sandbox_off(monkeypatch, tmp_path):
    # Review OFF-2: an older r4-mcp answers {status, upstreams} without a pid;
    # the first /mcp toggle waited 8 s and failed although it was up.
    proc = SimpleNamespace(pid=4242, poll=lambda: None)
    legacy = {"status": "ok", "upstreams": []}
    monkeypatch.setattr(mcp_exposure, "_proxy_identity", lambda *a, **k: legacy)
    monkeypatch.setattr(mcp_exposure, "_START_WAIT", 2.0)
    off = mcp_exposure.McpSandbox()
    result = mcp_exposure._await_daemon(proc, 50800, off, tmp_path / "mcp-servers.json")
    assert result["state"] == "started"
    assert not mcp_exposure._pid_path().exists()  # the answer may not be ours: no pid file
    monkeypatch.setattr(mcp_exposure, "_START_WAIT", 0.4)
    on = mcp_exposure.McpSandbox("abc", ("launcher",))
    result = mcp_exposure._await_daemon(proc, 50800, on, tmp_path / "mcp-servers.json")
    assert result["state"] == "failed"  # a sandboxed start must confirm its digest


def test_status_names_hatch_runtimes_left_unconfined(monkeypatch, tmp_path):
    from r4.integrations import hatchling

    _FakeDaemon(monkeypatch, tmp_path)
    _sandbox_default("on")
    packages = tmp_path / "pk"
    for name, state in (
        ("old", {"pid": 11}),
        ("new", {"pid": 11, "sandboxed": True}),
        ("dead", {"pid": 12}),
    ):
        (packages / name).mkdir(parents=True)
        (packages / name / hatchling.HATCH_RUNTIME_STATE_FILE).write_text(json.dumps(state))
    monkeypatch.setattr(hatchling, "HATCH_PACKAGES_DIR", packages)
    alive = staticmethod(lambda pid: pid == 11)
    monkeypatch.setattr(hatchling.HatchlingBridge, "_is_process_alive", alive)
    assert mcp_exposure.mcp_sandbox_status()["unsandboxed_hatch_runtimes"] == ["old"]


def test_labels_follow_the_running_daemon(monkeypatch, tmp_path):
    fake = _FakeDaemon(
        monkeypatch,
        tmp_path,
        running=True,
        identity=_identity(""),
        servers={"free": {"type": "stdio", "command": "x", "sandbox": False}},
    )
    assert mcp_exposure.mcp_sandbox_labels() == {"state": "off", "exempt": ["free"]}
    _sandbox_default("on")
    assert mcp_exposure.mcp_sandbox_labels()["state"] == "pending"
    fake.identity = _identity(mcp_exposure.mcp_policy_document(fake.config)["digest"])
    assert mcp_exposure.mcp_sandbox_labels()["state"] == "sandboxed"
    fake.running = False
    assert mcp_exposure.mcp_sandbox_labels()["state"] == "sandboxed"
