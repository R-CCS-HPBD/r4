"""ADR-101 D-3/D-4: every agent-driven spawn goes through ``wrap_spawn``.

The sites: ``shell_exec`` (captured, detached, streamed), ``python_exec``,
``compile_code``/``run_code``/``test_code`` (``coding._run_command``),
``test_fix_loop`` (``test_fix_loop._run``) and language servers
(``LSPManager.start``). A fake context proves each site uses the wrapped argv
and env; a refusing one proves nothing starts; with the sandbox off the Popen
arguments are the ones of before; with real bubblewrap the shell and Python
tools are confined end to end.
"""

from __future__ import annotations

import functools
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from r4.companion import coding, shell_detach, tools
from r4.companion import test_fix_loop as tfl
from r4.companion import tool_python as tool_python_mod
from r4.companion.agent_workspace import AgentWorkspace, agent_workspace
from r4.companion.sandbox import (
    UNAVAILABLE,
    SandboxContext,
    SandboxError,
    SandboxSetting,
    SpawnSpec,
    set_current_sandbox,
)
from r4.companion.sandbox import selftest as selftest_mod
from r4.companion.tool_python import tool_python
from r4.companion.web_access import make_web_access
from r4.lsp import LSPManager, LSPServerConfig

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
pytestmark = posix_only

MARKERS = "echo argv=$FAKE_SBX_ARGV env=$FAKE_SBX_ENV"
WRAPPED = "argv=prefixed env=wrapped"
REFUSAL = "Error: sandbox.unavailable: no backend in this test"


class FakeSandbox:
    """Duck-typed context: prefixes ``env FAKE_SBX_ARGV=prefixed`` and marks the env."""

    setting = SandboxSetting("on", "", "slash")

    def __init__(self, error: SandboxError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[object, bool, object]] = []

    def wrap(self, args, *, shell, cwd, env):
        self.calls.append((args, shell, cwd))
        if self.error is not None:
            raise self.error
        inner = ["/bin/sh", "-c", args] if shell else list(args)
        child_env = {**env, "FAKE_SBX_ENV": "wrapped"}
        return SpawnSpec(["env", "FAKE_SBX_ARGV=prefixed", *inner], False, child_env, True, "fake")


class PopenSpy:
    """Records every ``subprocess.Popen`` call, then runs it for real."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, dict]] = []
        real = subprocess.Popen
        spy = self

        class _Spy(real):  # type: ignore[misc, valid-type]
            def __init__(self, args, *a, **kw):
                spy.calls.append((args, kw))
                super().__init__(args, *a, **kw)

        self.cls = _Spy


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    for name in ("R4_SANDBOX", "R4_SANDBOXED", "R4_LAUNCH_SANDBOX"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(shell_detach.DETACH_ENV, "0")
    monkeypatch.setattr(tools, "_shell_chunk_sink", None)
    monkeypatch.setattr(coding, "_LSP_MANAGERS", {})
    set_current_sandbox(None)
    yield
    set_current_sandbox(None)
    shell_detach.terminate_detached(grace=2.0)


@pytest.fixture
def fake():
    sandbox = FakeSandbox()
    set_current_sandbox(sandbox)  # type: ignore[arg-type]
    return sandbox


@pytest.fixture
def refusing():
    sandbox = FakeSandbox(SandboxError(UNAVAILABLE, "no backend in this test"))
    set_current_sandbox(sandbox)  # type: ignore[arg-type]
    return sandbox


@pytest.fixture
def popen_spy(monkeypatch):
    spy = PopenSpy()
    monkeypatch.setattr(subprocess, "Popen", spy.cls)
    return spy


def _stream_on(monkeypatch) -> None:
    monkeypatch.setattr(tools, "_shell_chunk_sink", lambda *_a, **_k: None)


def _pid_gone(pid: int, wait_s: float = 5.0) -> bool:
    """True once ``pid`` no longer exists or is a zombie."""
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            return True
        if state == "Z":
            return True
        time.sleep(0.05)
    return False


def _marker_procs(marker: str) -> list[str]:
    found = []
    for entry in Path("/proc").iterdir():
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if marker.encode() in cmdline and entry.name.isdigit():
            found.append(entry.name)
    return found


def _no_marker_procs(marker: str, wait_s: float = 5.0) -> bool:
    deadline = time.monotonic() + wait_s
    while _marker_procs(marker) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _marker_procs(marker)


# ── the wrapped argv and env reach Popen ─────────────────────────────────────


def test_shell_exec_captured_runs_the_wrapped_argv(fake, tmp_path):
    out = tools._shell_exec(MARKERS, cwd=str(tmp_path))
    assert WRAPPED in out
    assert fake.calls == [(MARKERS, True, str(tmp_path))]


def test_shell_exec_detachable_runs_the_wrapped_argv(fake, tmp_path, monkeypatch):
    monkeypatch.setenv(shell_detach.DETACH_ENV, "1")
    out = tools._shell_exec(MARKERS, cwd=str(tmp_path))
    assert WRAPPED in out
    assert fake.calls == [(MARKERS, True, str(tmp_path))]


def test_shell_exec_streamed_runs_the_wrapped_argv(fake, tmp_path, monkeypatch):
    _stream_on(monkeypatch)
    out = tools._shell_exec(MARKERS, cwd=str(tmp_path))
    assert WRAPPED in out
    assert fake.calls == [(MARKERS, True, str(tmp_path))]


def test_python_exec_runs_the_wrapped_argv(fake):
    code = "import os; print(os.environ['FAKE_SBX_ARGV'], os.environ['FAKE_SBX_ENV'])"
    out = tools._python_exec(code)
    assert "prefixed wrapped" in out
    (args, shell, _cwd) = fake.calls[-1]  # earlier calls: the interpreter probes
    assert args == [tool_python(), "-c", code] and shell is False


def test_coding_step_runs_the_wrapped_argv(fake, tmp_path):
    argv = ["sh", "-c", MARKERS]
    out, code = coding._run_command(coding.CodeCommand(argv, tmp_path, "t", 30))
    assert (WRAPPED, 0) == (out.strip(), code)
    assert fake.calls == [(argv, False, str(tmp_path))]


def test_test_fix_loop_run_uses_the_wrapped_argv(fake, tmp_path):
    code, blob = tfl._run(MARKERS, tmp_path, 8000)
    assert (code, blob.strip()) == (0, WRAPPED)
    assert fake.calls == [(MARKERS, True, str(tmp_path))]


def test_language_server_starts_wrapped_and_is_recorded_sandboxed(fake, popen_spy):
    manager = LSPManager()
    manager.add_config(LSPServerConfig(name="fake", language_id="x", command=["sleep", "30"]))
    try:
        assert manager.start("fake")
        args, kw = popen_spy.calls[-1]
        assert args == ["env", "FAKE_SBX_ARGV=prefixed", "sleep", "30"]
        assert kw["env"]["FAKE_SBX_ENV"] == "wrapped"
        assert manager.unconfined() == []
    finally:
        manager.stop_all()


# ── a refusal: the tool error, and nothing started ───────────────────────────


@pytest.mark.parametrize("path", ["captured", "detached", "streamed"])
def test_shell_exec_refusal_is_the_tool_error(refusing, popen_spy, tmp_path, monkeypatch, path):
    if path == "detached":
        monkeypatch.setenv(shell_detach.DETACH_ENV, "1")
    if path == "streamed":
        _stream_on(monkeypatch)
    assert tools._shell_exec("echo ran", cwd=str(tmp_path)) == REFUSAL
    assert popen_spy.calls == []


def test_python_exec_refusal_is_the_tool_error(refusing, popen_spy):
    assert tools._python_exec("print('ran')") == REFUSAL
    assert popen_spy.calls == []


def test_run_code_refusal_is_the_tool_error(refusing, popen_spy, tmp_path):
    script = tmp_path / "main.py"
    script.write_text("print('ran')\n")
    assert tools._run_code(path=str(script)) == REFUSAL
    assert popen_spy.calls == []


def test_test_fix_loop_refusal_is_the_tool_error(refusing, popen_spy):
    handler = tools._make_test_fix_loop_handler(None)  # type: ignore[arg-type]
    assert handler("true", ["x.py"]) == REFUSAL
    assert popen_spy.calls == []


def _fake_pylsp(tmp_path: Path, monkeypatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "pylsp"
    exe.write_text("#!/bin/sh\nexec sleep 30\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (project / "m.py").write_text("x = 1\n")
    return project


def test_start_language_servers_refusal_is_the_tool_error(
    refusing, popen_spy, tmp_path, monkeypatch
):
    project = _fake_pylsp(tmp_path, monkeypatch)
    assert tools._start_language_servers(str(project)) == REFUSAL
    assert popen_spy.calls == []


# ── sandbox off: the Popen arguments of before ───────────────────────────────


def test_off_shell_exec_captured_passes_the_command_string(popen_spy, tmp_path):
    tools._shell_exec("echo ran", cwd=str(tmp_path))
    args, kw = popen_spy.calls[0]
    assert (args, kw["shell"], kw["cwd"], kw["start_new_session"]) == (
        "echo ran",
        True,
        str(tmp_path),
        True,
    )
    assert kw["env"]["R4_COMPANION_SESSION"] == "1" and "R4_SANDBOXED" not in kw["env"]


def test_off_shell_exec_detached_and_streamed_pass_the_command_string(
    popen_spy, tmp_path, monkeypatch
):
    monkeypatch.setenv(shell_detach.DETACH_ENV, "1")
    tools._shell_exec("echo ran", cwd=str(tmp_path))
    _stream_on(monkeypatch)
    tools._shell_exec("echo ran", cwd=str(tmp_path))
    (detached, kw_d), (streamed, kw_s) = popen_spy.calls
    assert (detached, kw_d["shell"], kw_d["start_new_session"]) == ("echo ran", True, True)
    assert (streamed, kw_s["shell"], kw_s["preexec_fn"]) == ("echo ran", True, os.setsid)


def test_off_python_exec_passes_the_interpreter_argv(popen_spy):
    tools._python_exec("print(1)")
    args, kw = popen_spy.calls[0]
    assert (args, kw["shell"]) == ([tool_python(), "-c", "print(1)"], False)


def test_off_language_server_passes_its_command(popen_spy):
    manager = LSPManager()
    manager.add_config(LSPServerConfig(name="s", language_id="x", command=["sleep", "30"]))
    try:
        manager.start("s")
        assert popen_spy.calls[-1][0] == ["sleep", "30"]
    finally:
        manager.stop_all()


# ── coding and test_fix_loop now run like shell_exec (D-4) ───────────────────


def test_coding_step_gets_the_tool_env(tmp_path):
    argv = ["sh", "-c", "echo session=$R4_COMPANION_SESSION"]
    out, _code = coding._run_command(coding.CodeCommand(argv, tmp_path, "t", 30))
    assert out.strip() == "session=1"


def test_test_fix_loop_run_gets_the_tool_env(tmp_path):
    _code, blob = tfl._run("echo session=$R4_COMPANION_SESSION", tmp_path, 8000)
    assert blob.strip() == "session=1"


def _background_pid(pidfile: Path) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        text = pidfile.read_text().strip() if pidfile.exists() else ""
        if text:
            return int(text)
        time.sleep(0.05)
    raise AssertionError("the background child never wrote its pid")


def test_coding_step_timeout_kills_the_whole_group(tmp_path):
    pidfile = tmp_path / "pid"
    argv = ["sh", "-c", f"sleep 20 & echo $! > {pidfile}; wait"]
    out, code = coding._run_command(coding.CodeCommand(argv, tmp_path, "t", 1))
    assert code == 124 and "timed out after 1s" in out
    assert _pid_gone(_background_pid(pidfile))


def test_test_fix_loop_timeout_kills_the_whole_group(tmp_path, monkeypatch):
    monkeypatch.setattr(tfl, "RUN_TIMEOUT_S", 1, raising=False)  # old code: no limit to lower
    pidfile = tmp_path / "pid"
    code, blob = tfl._run(f"sleep 20 & echo $! > {pidfile}; wait", tmp_path, 8000)
    assert code == 124 and "timed out after 1s" in blob
    assert _pid_gone(_background_pid(pidfile))


def test_coding_step_cwd_is_held_to_the_agent_workspace(tmp_path):
    ws, outside = tmp_path / "ws", tmp_path / "elsewhere"
    ws.mkdir()
    outside.mkdir()
    with agent_workspace(AgentWorkspace.of(ws)):
        out, code = coding._run_command(coding.CodeCommand(["true"], outside, "t", 30))
    assert code == 1 and out.startswith("Error: refused: write path")


def test_test_fix_loop_tool_runs_in_the_agent_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    handler = tools._make_test_fix_loop_handler(None)  # type: ignore[arg-type]
    with agent_workspace(AgentWorkspace.of(ws)):
        out = handler("pwd", ["x.py"])
    assert "status=fixed" in out
    assert out.rstrip().endswith(str(ws.resolve()))


# ── unconfined_running ───────────────────────────────────────────────────────


def _detach_sleep(tmp_path: Path, monkeypatch) -> shell_detach.DetachedShell:
    monkeypatch.setenv(shell_detach.DETACH_ENV, "1")
    out = tools._shell_exec("sleep 30", cwd=str(tmp_path), timeout=1)
    assert "was detached" in out
    (pid,) = shell_detach.detached_pids()
    return shell_detach._DETACHED[pid]


def test_unconfined_running_lists_an_off_mode_detached_shell(tmp_path, monkeypatch):
    handle = _detach_sleep(tmp_path, monkeypatch)
    assert handle.sandboxed is False
    assert tools.unconfined_running() == [
        f"detached shell pid {handle.pid} (output in {handle.stdout_path})"
    ]


def test_unconfined_running_skips_a_sandboxed_detached_shell(fake, tmp_path, monkeypatch):
    handle = _detach_sleep(tmp_path, monkeypatch)
    assert handle.sandboxed is True
    assert "Sandboxed (ADR-101)" not in handle.describe()  # not bwrap: the host hints hold
    assert tools.unconfined_running() == []


def test_inherited_sandbox_detached_handle_keeps_the_host_hints(tmp_path, monkeypatch):
    # Review finding 4: an inherited sandbox wraps nothing, so the child shares
    # r4's /tmp and pid namespace and the tail / kill -0 hints work.
    monkeypatch.setenv("R4_SANDBOXED", "bwrap")
    access = make_web_access("none", [], source="flag")
    ctx = SandboxContext(
        setting=lambda: SandboxSetting("on", "", "inherited"),
        web_access=lambda: access,
        workspace_root=tmp_path,
    )
    set_current_sandbox(ctx)
    handle = _detach_sleep(tmp_path, monkeypatch)
    assert handle.sandboxed is True
    assert "Sandboxed (ADR-101)" not in handle.describe()


def test_unconfined_running_lists_an_off_mode_language_server(tmp_path, monkeypatch):
    project = _fake_pylsp(tmp_path, monkeypatch)
    try:
        assert "Started: pylsp" in tools._start_language_servers(str(project))
        assert tools.unconfined_running() == [f"language server pylsp (root {project})"]
    finally:
        for manager in coding._LSP_MANAGERS.values():
            manager.stop_all()


# ── the interpreter probe (review finding 1) ─────────────────────────────────


def _foreign_venv(monkeypatch, tmp_path: Path) -> None:
    """R4 runs from a venv the user did not activate: tool_python() probes candidates."""
    for name in ("VIRTUAL_ENV", "CONDA_PREFIX", tool_python_mod.TOOL_PYTHON_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    tool_python_mod._usable.cache_clear()


def _workspace_interpreter(ws: Path, body: str) -> Path:
    """``ws/.venv/bin/python``: a file the sandboxed agent can write."""
    exe = ws / ".venv" / "bin" / "python"
    exe.parent.mkdir(parents=True)
    exe.write_text(f"#!/bin/sh\n{body}\necho True\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


def test_python_probe_runs_through_the_sandbox(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    exe = _workspace_interpreter(ws, ":")
    monkeypatch.chdir(ws)
    _foreign_venv(monkeypatch, tmp_path)
    assert tool_python() == str(exe)  # sandbox off: probed on the host, cached
    sandbox = FakeSandbox()
    set_current_sandbox(sandbox)  # type: ignore[arg-type]
    assert tool_python() == str(exe)  # on: the off-mode result is not reused
    assert sandbox.calls == [([str(exe), "-c", tool_python_mod._PROBE], False, None)]


def test_a_refused_probe_is_not_cached(tmp_path, monkeypatch, refusing):
    ws = tmp_path / "ws"
    exe = _workspace_interpreter(ws, ":")
    monkeypatch.chdir(ws)
    _foreign_venv(monkeypatch, tmp_path)
    assert tool_python() == sys.executable  # every probe refused, none cached
    set_current_sandbox(FakeSandbox())  # type: ignore[arg-type]
    assert tool_python() == str(exe)


# ── language servers stop whole (review findings 2 and 5) ────────────────────


def _wait_for_marker(marker: str, wait_s: float = 5.0) -> None:
    deadline = time.monotonic() + wait_s
    while not _marker_procs(marker) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _marker_procs(marker), "the server never started"


def test_stopping_a_language_server_stops_its_children():
    marker = f"r4lspmark-{uuid.uuid4().hex[:12]}"
    manager = LSPManager()
    command = ["sh", "-c", f"sh -c 'sleep 300; :' {marker} & wait"]
    manager.add_config(LSPServerConfig(name="s", language_id="x", command=command))
    assert manager.start("s")
    _wait_for_marker(marker)
    manager.stop("s")
    assert _no_marker_procs(marker)


class _IdleProc:
    pid = 0

    def poll(self):
        return None


class _GrowingProc(_IdleProc):
    """Its poll registers another server, as a concurrent ``start`` would."""

    def __init__(self, processes: dict) -> None:
        self.processes = processes

    def poll(self):
        self.processes.setdefault("late", _IdleProc())
        return None


def test_unconfined_tolerates_a_server_started_meanwhile():
    manager = LSPManager()
    manager._processes["first"] = _GrowingProc(manager._processes)  # type: ignore[assignment]
    try:
        assert manager.unconfined() == ["first"]
    finally:
        manager._processes.clear()


# ── a plan agent's coding tools (review finding 3) ───────────────────────────


def test_run_code_in_a_plan_agent_worktree_runs_the_worktree_file(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    (project / "main.py").write_text("print('main ran')\n")
    worktree = project / ".r4" / "wt-a"
    (worktree / ".git").mkdir(parents=True)
    (worktree / "main.py").write_text("print('worktree ran')\n")
    monkeypatch.chdir(project)  # the bridge's cwd is the project root
    with agent_workspace(AgentWorkspace.of(worktree)):
        out = tools._run_code(path="main.py")
        tested = tools._test_code()
    assert "worktree ran" in out, out
    assert "refused" not in tested and str(worktree.resolve()) in tested, tested


# ── real bubblewrap, end to end ──────────────────────────────────────────────


@functools.lru_cache(maxsize=1)
def bwrap_works() -> bool:
    """bwrap can make a user + network namespace here (cached)."""
    exe = shutil.which("bwrap")
    if not exe or not sys.platform.startswith("linux"):
        return False
    argv = [exe, "--ro-bind", "/", "/", "--unshare-user", "--unshare-net", "true"]
    try:
        return subprocess.run(argv, capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


needs_bwrap = pytest.mark.skipif(
    not bwrap_works(), reason="bubblewrap with user namespaces unavailable"
)


@pytest.fixture
def sandboxed_ws(tmp_path, monkeypatch):
    """A real bwrap context on a workspace; the scratch HOME (with ~/.ssh) outside it."""
    ws = tmp_path / "ws"
    ws.mkdir()
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_x").write_text("PRIVATE-KEY")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(ws)
    selftest_mod.clear_cache()
    access = make_web_access("none", [], source="flag")
    ctx = SandboxContext(
        setting=lambda: SandboxSetting("on", "", "slash"),
        web_access=lambda: access,
        workspace_root=ws,
    )
    set_current_sandbox(ctx)
    yield ws
    set_current_sandbox(None)
    shell_detach.terminate_detached(grace=2.0)
    ctx.close()


@needs_bwrap
def test_bwrap_shell_exec_writes_only_inside_the_workspace(sandboxed_ws, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    out = tools._shell_exec(f"echo in > inside.txt; echo out > {outside}/x.txt; echo rc=$?")
    assert (sandboxed_ws / "inside.txt").read_text() == "in\n"
    assert not (outside / "x.txt").exists()
    assert "rc=0" not in out


@needs_bwrap
def test_bwrap_python_exec_cannot_read_ssh_keys(sandboxed_ws):
    code = (
        "import os\n"
        "try:\n    print(open(os.path.expanduser('~/.ssh/id_x')).read())\n"
        "except OSError as e:\n    print('denied', type(e).__name__)\n"
    )
    out = tools._python_exec(code)
    assert "PRIVATE-KEY" not in out and "denied" in out, out


@needs_bwrap
def test_bwrap_timed_out_shell_exec_leaves_no_survivor(sandboxed_ws):
    marker = f"r4sbxmark-{uuid.uuid4().hex[:12]}"
    out = tools._shell_exec(f"sh -c 'sleep 30; :' {marker} & sleep 30; :", timeout=1)
    assert out.startswith("Error: command timed out after 1 s"), out
    assert _no_marker_procs(marker)


@needs_bwrap
def test_bwrap_detached_shell_is_reported_sandboxed(sandboxed_ws, monkeypatch):
    handle = _detach_sleep(sandboxed_ws, monkeypatch)
    assert handle.sandboxed is True
    assert "Sandboxed (ADR-101)" in handle.describe()
    assert tools.unconfined_running() == []


@needs_bwrap
def test_bwrap_python_probe_cannot_write_outside_the_workspace(sandboxed_ws, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    mark = outside / "pwned"
    _workspace_interpreter(sandboxed_ws, f"touch {mark}")
    _foreign_venv(monkeypatch, tmp_path)
    tools._python_exec("print(1)")
    assert not mark.exists()


@needs_bwrap
def test_bwrap_stopping_a_language_server_leaves_no_survivor(sandboxed_ws):
    marker = f"r4lspmark-{uuid.uuid4().hex[:12]}"
    manager = LSPManager()
    command = ["sh", "-c", f"sleep 300; : {marker}"]
    manager.add_config(LSPServerConfig(name="s", language_id="x", command=command))
    assert manager.start("s")
    _wait_for_marker(marker)
    manager.stop("s")
    assert _no_marker_procs(marker)
