"""Pins for shell_exec detach-on-timeout and the pipe-buffer note (plan item H18).

TB 4.0 harness-efficiency plan 2026-10-24: a timed-out ``shell_exec`` killed
its process group, so long builds were lost, and ``… | tail`` returned nothing
because ``tail`` prints at EOF. With ``R4_SHELL_DETACH_ON_TIMEOUT=1`` (default
on only under ``R4_AUTONOMOUS=1``) the command keeps running with its output
in files the result names.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import re
import signal
import sys
import time
from pathlib import Path

import pytest

from r4.companion.tools import _shell_exec, set_shell_chunk_sink, set_shell_status_sink


def _shell_detach_module():
    """The H18 module; None only when this file runs against pre-H18 code (old-code proof)."""
    try:
        return importlib.import_module("r4.companion.shell_detach")
    except ImportError:
        return None


shell_detach = _shell_detach_module()

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")

_HANDLE_RE = re.compile(r"pid (\d+) .*?stdout -> (\S+); stderr -> (\S+)")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """No stream sink, a private spill dir, no detach env, no leftover processes."""
    set_shell_chunk_sink(None)
    set_shell_status_sink(None)
    (tmp_path / "spill").mkdir()
    if shell_detach is not None:
        monkeypatch.setattr(shell_detach, "_SPILL_DIR", tmp_path / "spill")
    monkeypatch.delenv("R4_SHELL_DETACH_ON_TIMEOUT", raising=False)
    monkeypatch.delenv("R4_AUTONOMOUS", raising=False)
    yield
    if shell_detach is not None:
        shell_detach.terminate_detached()


def _alive(pid: int) -> bool:
    """True while *pid* exists and is not a zombie awaiting its reaper."""
    if Path("/proc/self/stat").exists():
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except (FileNotFoundError, ProcessLookupError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for(predicate, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _handle(result: str) -> tuple[int, Path, Path]:
    match = _HANDLE_RE.search(result)
    assert match, result
    return int(match.group(1)), Path(match.group(2)), Path(match.group(3))


def test_timeout_detaches_and_the_file_receives_the_rest(monkeypatch, tmp_path):
    """Old code: 'Error: command timed out', the group killed, 'done' never written."""
    monkeypatch.setenv("R4_SHELL_DETACH_ON_TIMEOUT", "1")
    marker = tmp_path / "finished"
    out = _shell_exec(f"echo start; sleep 2; echo done; touch {marker}", timeout=1)
    assert "timed out after 1 s and was detached, not killed" in out
    pid, stdout_path, _stderr_path = _handle(out)
    assert "start" in out.split("[partial output before timeout]\n", 1)[1]
    assert f"kill -- -{pid}" in out
    assert _wait_for(marker.exists), "the detached command must keep running"
    assert _wait_for(lambda: "done" in stdout_path.read_text())
    proc = shell_detach._DETACHED[pid].proc
    assert _wait_for(lambda: proc.poll() is not None)
    assert proc.returncode == 0


def test_next_call_reports_how_the_detached_command_ended(monkeypatch):
    monkeypatch.setenv("R4_SHELL_DETACH_ON_TIMEOUT", "1")
    out = _shell_exec("sleep 1.5; exit 4", timeout=1)
    pid, _out, _err = _handle(out)
    proc = shell_detach._DETACHED[pid].proc
    assert _wait_for(lambda: proc.poll() is not None)
    follow = _shell_exec("echo next", timeout=10)
    assert follow.startswith(f"[detached pid {pid} finished with exit code 4;")
    assert follow.rstrip().endswith("next")
    assert pid not in shell_detach.detached_pids()


def test_autonomous_runs_detach_by_default(monkeypatch):
    monkeypatch.setenv("R4_AUTONOMOUS", "1")
    out = _shell_exec("sleep 30", timeout=1)
    pid, _out, _err = _handle(out)
    assert _alive(pid)


def test_off_switch_under_autonomous_keeps_the_kill(monkeypatch):
    monkeypatch.setenv("R4_AUTONOMOUS", "1")
    monkeypatch.setenv("R4_SHELL_DETACH_ON_TIMEOUT", "0")
    out = _shell_exec("sleep 30", timeout=1)
    assert out.startswith("Error: command timed out after 1 s\n")
    assert "detached" not in out
    assert shell_detach.detached_pids() == []


def test_interactive_default_keeps_the_kill():
    out = _shell_exec("sleep 30", timeout=1)
    assert out.startswith("Error: command timed out after 1 s\n")
    assert shell_detach.detached_pids() == []


def test_completed_command_gives_the_pipe_paths_result(monkeypatch, tmp_path):
    command = "echo out; echo err >&2; exit 3"
    piped = _shell_exec(command, timeout=10)
    monkeypatch.setenv("R4_SHELL_DETACH_ON_TIMEOUT", "1")
    filed = _shell_exec(command, timeout=10)
    assert filed == piped == "out\n\n[stderr]\nerr\n\n[exit code: 3]"
    assert list((tmp_path / "spill").iterdir()) == [], "a finished run leaves no spill files"


def test_completed_command_output_keeps_the_cap(monkeypatch):
    monkeypatch.setenv("R4_SHELL_DETACH_ON_TIMEOUT", "1")
    out = _shell_exec("head -c 50000 /dev/zero | tr '\\0' z", timeout=10)
    assert "…[truncated 22000 chars]…" in out


def test_session_end_terminates_detached_commands(monkeypatch):
    monkeypatch.setenv("R4_SHELL_DETACH_ON_TIMEOUT", "1")
    out = _shell_exec("sleep 60", timeout=1)
    pid, _out, _err = _handle(out)
    proc = shell_detach._DETACHED[pid].proc
    shell_detach.terminate_detached()
    assert _wait_for(lambda: proc.poll() is not None)
    assert proc.returncode == -signal.SIGTERM


def test_tail_pipe_timeout_explains_the_empty_output():
    """Old code: an empty timeout result with no reason (retry4 layout p24bDM4)."""
    out = _shell_exec("for i in 1 2 3; do echo $i; sleep 1; done | tail -n 2", timeout=1)
    assert "piped to `tail` appears only when the command ends" in out
    assert "> job.log 2>&1" in out


def test_tail_pipe_note_also_on_a_detached_timeout(monkeypatch):
    monkeypatch.setenv("R4_SHELL_DETACH_ON_TIMEOUT", "1")
    out = _shell_exec("sleep 5 | tail -n 5", timeout=1)
    assert "piped to `tail`" in out
    assert "detached" in out


def test_no_pipe_note_without_an_eof_filter():
    out = _shell_exec("sleep 30", timeout=1)
    assert "piped to" not in out


@pytest.mark.parametrize(
    ("value", "autonomous", "expected"),
    [
        ("", "", False),
        ("", "1", True),
        ("0", "1", False),
        ("off", "true", False),
        ("1", "", True),
        ("yes", "0", True),
    ],
)
def test_detach_switch(monkeypatch, value, autonomous, expected):
    monkeypatch.setenv("R4_SHELL_DETACH_ON_TIMEOUT", value)
    monkeypatch.setenv("R4_AUTONOMOUS", autonomous)
    assert shell_detach.detach_enabled() is expected


def test_spill_dir_is_private(monkeypatch):
    monkeypatch.setattr(shell_detach, "_SPILL_DIR", None)
    directory = shell_detach.spill_dir()
    try:
        assert directory.is_dir()
        assert (directory.stat().st_mode & 0o777) == 0o700
    finally:
        with contextlib.suppress(OSError):
            directory.rmdir()


def test_capped_find_is_killed_not_detached(monkeypatch, tmp_path):
    """Review 2026-10-24: with detach on, a `find` past its 25 s cap was left
    crawling in the background and lost its -maxdepth hint."""
    from r4.companion import tools

    monkeypatch.setenv("R4_SHELL_DETACH_ON_TIMEOUT", "1")
    monkeypatch.setattr(tools, "FIND_TIMEOUT_CAP_S", 1)
    (tmp_path / "x").write_text("")
    out = _shell_exec("find . -name x -exec sleep 5 \\;", cwd=str(tmp_path), timeout=60)
    assert "timed out after 1" in out
    assert "detached" not in out
    assert "-maxdepth" in out
    assert shell_detach.detached_pids() == []


def test_exit_cleanup_removes_the_spill_dir(monkeypatch, tmp_path):
    """Review 2026-10-24: every unattended process left one r4-shell-* dir behind."""
    monkeypatch.setenv("R4_SHELL_DETACH_ON_TIMEOUT", "1")
    spill = tmp_path / "own-spill"
    spill.mkdir()
    monkeypatch.setattr(shell_detach, "_SPILL_DIR", spill)
    pid, _out, _err = _handle(_shell_exec("sleep 60", timeout=1))
    shell_detach.cleanup_at_exit()
    assert _wait_for(lambda: not _alive(pid))
    assert not spill.exists()


def test_exit_cleanup_keeps_the_dir_of_a_command_that_survives(monkeypatch, tmp_path):
    monkeypatch.setenv("R4_SHELL_DETACH_ON_TIMEOUT", "1")
    pid, out_path, _err = _handle(_shell_exec("trap '' TERM; sleep 30", timeout=1))
    try:
        shell_detach.cleanup_at_exit()
        assert _alive(pid) and out_path.parent.exists()
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)


_SIGNAL_CHILD = """
import os, sys, time
from pathlib import Path
from r4.companion import shell_detach
from r4.companion.tools import _shell_exec, set_shell_chunk_sink
set_shell_chunk_sink(None)
shell_detach.install_signal_cleanup()
os.environ["R4_SHELL_DETACH_ON_TIMEOUT"] = "1"
print(_shell_exec("sleep 60", timeout=1), flush=True)
time.sleep(60)
"""


def test_sigterm_stops_detached_commands(tmp_path):
    """Review 2026-10-24: SIGTERM skips atexit, so a bridge stopped that way left
    its detached commands running (they live in their own sessions)."""
    import subprocess

    env = {**os.environ, "TMPDIR": str(tmp_path)}
    child = subprocess.Popen(
        [sys.executable, "-c", _SIGNAL_CHILD], stdout=subprocess.PIPE, text=True, env=env
    )
    try:
        lines = []
        while not _HANDLE_RE.search("".join(lines)):
            line = child.stdout.readline()
            assert line, "child exited before detaching"
            lines.append(line)
        pid, _out, _err = _handle("".join(lines))
        assert _alive(pid)
        child.send_signal(signal.SIGTERM)
        assert child.wait(timeout=30) == -signal.SIGTERM
        assert _wait_for(lambda: not _alive(pid))
        assert not list(tmp_path.glob("r4-shell-*"))
    finally:
        child.kill()
        child.wait()
