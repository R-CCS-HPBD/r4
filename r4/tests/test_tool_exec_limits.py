"""Pins for the shell_exec / python_exec execution limits (W2, rows H6 + H7).

Harness repair plan 2026-10-05, gap row D-26: default timeout 180 s, a
model-supplied ``timeout`` clamped to 1800 s (and the clamp stated), the tail
of the output so far returned on timeout, the whole process group killed on
timeout, and output over 32,000 chars cut to 20,000 head + 8,000 tail.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import pytest

from r4.companion import tools
from r4.companion.tools import (
    EXEC_DEFAULT_TIMEOUT_S,
    EXEC_MAX_TIMEOUT_S,
    EXEC_OUTPUT_CAP_CHARS,
    EXEC_OUTPUT_HEAD_CHARS,
    EXEC_OUTPUT_TAIL_CHARS,
    EXEC_TIMEOUT_TAIL_CHARS,
    _cap_exec_output,
    _python_exec,
    _shell_exec,
    effective_timeout_for_command,
    get_builtin_tools,
    resolve_exec_timeout,
    set_shell_chunk_sink,
    set_shell_status_sink,
)

PARTIAL_MARKER = "[partial output before timeout]\n"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")


def _partial_section(result: str) -> str:
    """The text after the partial-output marker of a timeout result."""
    assert PARTIAL_MARKER in result, result
    return result.split(PARTIAL_MARKER, 1)[1]


def _alive(pid: int) -> bool:
    """True while *pid* exists and is not a zombie awaiting its reaper."""
    if Path("/proc/self/stat").exists():
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            return False
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _gone_within(pid: int, seconds: float = 10.0) -> bool:  # generous under a loaded host
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _read_pid(path: Path) -> int:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        text = path.read_text().strip() if path.exists() else ""
        if text:
            return int(text)
        time.sleep(0.05)
    raise AssertionError(f"no pid written to {path}")


def _spy_timeouts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the timeout each _run_captured call receives (still runs it)."""
    seen: list[int] = []
    real = tools._run_captured

    def spy(args, *, shell, cwd, timeout, env):
        seen.append(timeout)
        return real(args, shell=shell, cwd=cwd, timeout=timeout, env=env)

    monkeypatch.setattr(tools, "_run_captured", spy)
    return seen


@pytest.fixture
def _no_stream_sink():
    set_shell_chunk_sink(None)
    set_shell_status_sink(None)
    yield
    set_shell_chunk_sink(None)
    set_shell_status_sink(None)


# ---------------------------------------------------------------------------
# Default timeout and clamp
# ---------------------------------------------------------------------------


def test_default_timeout_is_180_seconds_for_both_tools():
    assert EXEC_DEFAULT_TIMEOUT_S == 180
    assert EXEC_MAX_TIMEOUT_S == 1800
    for fn in (_shell_exec, _python_exec):
        assert inspect.signature(fn).parameters["timeout"].default == 180


def test_default_timeout_reaches_the_child(monkeypatch, _no_stream_sink):
    seen = _spy_timeouts(monkeypatch)
    assert "hi" in _shell_exec("echo hi")
    assert "py" in _python_exec("print('py')")
    assert seen == [180, 180]


def test_tool_schemas_state_the_limits():
    by_name = {t["name"]: t for t in get_builtin_tools()}
    for name in ("shell_exec", "python_exec"):
        tool = by_name[name]
        timeout_schema = tool["parameters"]["properties"]["timeout"]
        assert timeout_schema["default"] == 180
        assert "1800" in timeout_schema["description"]
        description = tool["description"]
        for needle in ("180 s", "1800 s", "32,000", "20,000", "8,000", "background", "nohup"):
            assert needle in description, (name, needle)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 180),
        (1, 1),
        (600, 600),
        (1800, 1800),
        ("300", 300),
        (90.7, 90),
    ],
)
def test_resolve_timeout_accepts_values_within_limits(raw, expected):
    assert resolve_exec_timeout(raw) == (expected, "")


def test_resolve_timeout_clamps_above_maximum_and_says_so():
    seconds, note = resolve_exec_timeout(7200)
    assert seconds == 1800
    assert "7200 s exceeds the 1800 s maximum" in note
    assert "clamped to 1800 s" in note


@pytest.mark.parametrize("raw", [0, -5, "soon", [1]])
def test_resolve_timeout_replaces_unusable_values_with_default(raw):
    seconds, note = resolve_exec_timeout(raw)
    assert seconds == 180
    assert "used the default 180 s" in note


def test_clamp_is_applied_and_stated_in_both_results(monkeypatch, _no_stream_sink):
    seen = _spy_timeouts(monkeypatch)
    shell_out = _shell_exec("echo clamped-shell", timeout=5000)
    py_out = _python_exec("print('clamped-py')", timeout=99999)
    assert seen == [1800, 1800]
    assert shell_out.startswith("[timeout 5000 s exceeds the 1800 s maximum; clamped to 1800 s]")
    assert "clamped-shell" in shell_out
    assert py_out.startswith("[timeout 99999 s exceeds the 1800 s maximum; clamped to 1800 s]")
    assert "clamped-py" in py_out


def test_find_keeps_its_shorter_crawl_cap(monkeypatch, _no_stream_sink):
    assert effective_timeout_for_command("find . -name x", 180) == 25
    assert effective_timeout_for_command("find . -name x", 10) == 10
    seen = _spy_timeouts(monkeypatch)
    _shell_exec("find . -maxdepth 0", timeout=900)
    assert seen == [25]


# ---------------------------------------------------------------------------
# Timeout: partial output
# ---------------------------------------------------------------------------


def test_shell_timeout_returns_partial_output(_no_stream_sink):
    out = _shell_exec("echo before-timeout; echo on-stderr >&2; sleep 30", timeout=1)
    assert out.startswith("Error: command timed out after 1 s\n")
    partial = _partial_section(out)
    assert "before-timeout" in partial
    assert "[stderr]\non-stderr" in partial


def test_python_timeout_returns_partial_output(_no_stream_sink):
    out = _python_exec("import time\nprint('py-before', flush=True)\ntime.sleep(30)", timeout=1)
    assert out.startswith("Error: command timed out after 1 s\n")
    assert "py-before" in _partial_section(out)


def test_python_timeout_keeps_unflushed_prints(_no_stream_sink):
    """python_exec runs unbuffered, so a plain print survives the kill."""
    out = _python_exec("import time\nprint('no-flush-print')\ntime.sleep(30)", timeout=1)
    assert "no-flush-print" in _partial_section(out)


def test_timeout_partial_output_is_the_last_8000_chars(_no_stream_sink):
    out = _shell_exec(
        "head -c 12000 /dev/zero | tr '\\0' a; printf END; sleep 30",
        timeout=1,
    )
    partial = _partial_section(out)
    assert EXEC_TIMEOUT_TAIL_CHARS == 8_000
    assert len(partial) == 8_000
    assert partial.endswith("aEND")


def test_timeout_without_output_says_so(_no_stream_sink):
    out = _shell_exec("sleep 30", timeout=1)
    assert _partial_section(out) == "(no output)"


def test_timeout_hint_suggests_timeout_or_background(_no_stream_sink):
    out = _shell_exec("sleep 30", timeout=1)
    assert "larger `timeout` (max 1800 s)" in out
    assert "nohup" in out


def test_find_timeout_keeps_the_find_hint(monkeypatch, _no_stream_sink):
    monkeypatch.setattr(tools, "effective_timeout_for_command", lambda command, t: 1)
    out = _shell_exec("find . -maxdepth 0 ; sleep 30")
    assert "narrow the root" in out
    assert PARTIAL_MARKER in out


def test_streamed_shell_timeout_returns_partial_output():
    statuses: list[str] = []
    set_shell_chunk_sink(lambda stream_id, chunk: None)
    set_shell_status_sink(lambda stream_id, status: statuses.append(status))
    try:
        out = _shell_exec("echo streamed-early; sleep 9", timeout=1)
    finally:
        set_shell_chunk_sink(None)
        set_shell_status_sink(None)
    assert statuses == ["timed_out"]
    assert out.startswith("Error: command timed out after 1 s\n")
    assert "streamed-early" in _partial_section(out)
    assert "(stream shell-" in out


def test_streamed_shell_states_the_clamp():
    set_shell_chunk_sink(lambda stream_id, chunk: None)
    try:
        out = _shell_exec("echo streamed-clamp", timeout=4000)
    finally:
        set_shell_chunk_sink(None)
    assert out.startswith("[timeout 4000 s exceeds the 1800 s maximum; clamped to 1800 s]")
    assert "streamed-clamp" in out


# ---------------------------------------------------------------------------
# Timeout: process-group kill
# ---------------------------------------------------------------------------


@posix_only
def test_timeout_kills_backgrounded_child(tmp_path, _no_stream_sink):
    pidfile = tmp_path / "bg.pid"
    out = _shell_exec(f"sleep 300 & echo $! > {pidfile}; echo started; wait", timeout=1)
    assert "timed out" in out
    pid = _read_pid(pidfile)
    try:
        assert _gone_within(pid), f"backgrounded sleep {pid} survived the timeout"
    finally:
        if _alive(pid):
            with contextlib.suppress(ProcessLookupError):  # it may die between check and kill
                os.kill(pid, signal.SIGKILL)


@posix_only
def test_timeout_kills_every_part_of_a_compound_command(tmp_path, _no_stream_sink):
    pidfile = tmp_path / "inner.pid"
    marker = tmp_path / "after.txt"
    command = f"sh -c 'echo $$ > {pidfile}; exec sleep 300' && touch {marker}"
    out = _shell_exec(command, timeout=1)
    assert "timed out" in out
    pid = _read_pid(pidfile)
    try:
        assert _gone_within(pid), f"inner sleep {pid} survived the timeout"
    finally:
        if _alive(pid):
            with contextlib.suppress(ProcessLookupError):  # it may die between check and kill
                os.kill(pid, signal.SIGKILL)
    time.sleep(0.2)
    assert not marker.exists(), "the second half of `a && b` ran after the kill"


@posix_only
def test_backgrounded_child_without_redirect_no_longer_blocks_past_timeout(
    tmp_path, _no_stream_sink
):
    """`cmd &` holding the output pipe used to hang the tool until cmd exited."""
    pidfile = tmp_path / "bg.pid"
    started = time.monotonic()
    out = _shell_exec(f"sleep 300 & echo $! > {pidfile}; echo bg-nowait", timeout=1)
    elapsed = time.monotonic() - started
    pid = _read_pid(pidfile)
    try:
        assert elapsed < 10
        assert "bg-nowait" in _partial_section(out)
        assert _gone_within(pid)
    finally:
        if _alive(pid):
            with contextlib.suppress(ProcessLookupError):  # it may die between check and kill
                os.kill(pid, signal.SIGKILL)


@posix_only
def test_python_timeout_kills_its_subprocesses(tmp_path, _no_stream_sink):
    pidfile = tmp_path / "py-child.pid"
    code = (
        "import subprocess, time\n"
        "p = subprocess.Popen(['sleep', '300'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(300)\n"
    )
    out = _python_exec(code, timeout=1)
    assert "timed out" in out
    pid = _read_pid(pidfile)
    try:
        assert _gone_within(pid), f"python child {pid} survived the timeout"
    finally:
        if _alive(pid):
            with contextlib.suppress(ProcessLookupError):  # it may die between check and kill
                os.kill(pid, signal.SIGKILL)


@posix_only
@pytest.mark.skipif(shutil.which("setsid") is None, reason="needs setsid(1)")
def test_escaped_grandchild_cannot_hang_the_tool(tmp_path, monkeypatch, _no_stream_sink):
    """A child that left the group keeps the pipe open; the drain is bounded."""
    monkeypatch.setattr(tools, "_EXEC_KILL_DRAIN_S", 0.5)
    pidfile = tmp_path / "escaped.pid"
    started = time.monotonic()
    out = _shell_exec(
        f"setsid sh -c 'echo $$ > {pidfile}; exec sleep 300' & echo escaped-start; wait",
        timeout=1,
    )
    elapsed = time.monotonic() - started
    pid = _read_pid(pidfile)
    with contextlib.suppress(ProcessLookupError):  # it may die between check and kill
        os.kill(pid, signal.SIGKILL)
    assert elapsed < 10
    assert "escaped-start" in _partial_section(out)


def test_background_job_with_redirect_survives_a_normal_return(tmp_path, _no_stream_sink):
    """The documented long-job pattern: nohup + redirect returns at once and keeps running."""
    pidfile = tmp_path / "job.pid"
    log = tmp_path / "job.log"
    started = time.monotonic()
    out = _shell_exec(f"nohup sleep 300 > {log} 2>&1 & echo $! > {pidfile}", timeout=30)
    assert time.monotonic() - started < 10
    assert "timed out" not in out
    pid = _read_pid(pidfile)
    try:
        assert _alive(pid), "a completed command must not kill its background job"
    finally:
        with contextlib.suppress(ProcessLookupError):  # it may die between check and kill
            os.kill(pid, signal.SIGKILL)


# ---------------------------------------------------------------------------
# Output cap
# ---------------------------------------------------------------------------


def test_cap_constants():
    assert (EXEC_OUTPUT_CAP_CHARS, EXEC_OUTPUT_HEAD_CHARS, EXEC_OUTPUT_TAIL_CHARS) == (
        32_000,
        20_000,
        8_000,
    )


@pytest.mark.parametrize("size", [0, 1, 8_001, 31_999, 32_000])
def test_output_at_or_under_cap_is_untouched(size):
    text = "x" * size
    assert _cap_exec_output(text) == text


def test_output_over_cap_keeps_20000_head_and_8000_tail():
    text = "h" * 20_000 + "m" * 4_001 + "t" * 8_000
    assert len(text) == 32_001
    capped = _cap_exec_output(text)
    head, rest = capped.split("\n…[truncated 4001 chars]…\n")
    assert head == "h" * 20_000
    assert rest == "t" * 8_000


def test_shell_output_over_cap_is_cut(_no_stream_sink):
    out = _shell_exec("head -c 50000 /dev/zero | tr '\\0' z")
    assert len(out) == 20_000 + 8_000 + len("\n…[truncated 22000 chars]…\n")
    assert "…[truncated 22000 chars]…" in out


def test_python_output_over_cap_is_cut(_no_stream_sink):
    out = _python_exec("print('a' * 20000 + 'b' * 5000 + 'c' * 8000, end='')")
    head, tail = out.split("\n…[truncated 5000 chars]…\n")
    assert head == "a" * 20_000
    assert tail == "c" * 8_000


def test_python_output_at_cap_is_kept_whole(_no_stream_sink):
    out = _python_exec("print('q' * 32000, end='')")
    assert out == "q" * 32_000


def test_streamed_output_over_cap_is_cut():
    set_shell_chunk_sink(lambda stream_id, chunk: None)
    try:
        out = _shell_exec("head -c 40000 /dev/zero | tr '\\0' s", timeout=30)
    finally:
        set_shell_chunk_sink(None)
    assert "…[truncated 12000 chars]…" in out
    assert out.startswith("s" * 20_000)


def test_exit_code_survives_the_cap(_no_stream_sink):
    out = _shell_exec("head -c 40000 /dev/zero | tr '\\0' e; exit 7")
    assert out.endswith("[exit code: 7]")
