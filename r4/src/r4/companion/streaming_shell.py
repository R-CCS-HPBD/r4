"""Streaming shell execution for the companion (catch-up W5/G7 flow-side).

Competitor target (Copilot): command output streams into the chat/view in
real time, and users can cancel mid-execution from a visible control. The
companion previously ran ``subprocess.run`` and surfaced a single final
text block after process exit — no streaming, no granular cancel.

This module provides the Python-side core the ACP frame layer and the
webview render layer build on:

- :func:`run_shell_streaming` — runs the command line-by-line via
  ``Popen`` (process-group owned), invoking ``on_chunk(stream_id, chunk)``
  with each stdout/stderr slice as it arrives; accumulates and returns
  the full body on exit. ``timeout`` applies to the whole run. Anti-buffer
  discipline: text mode, ``bufsize=1`` (line-buffered).
- :func:`new_stream_id` — short opaque id (uuid4[:8]) identifying a live
  stream, so UIs can render/cancel independently.
- :func:`cancel_stream` — kills an in-flight stream's **process group**
  (``os.setsid`` + SIGKILL via ``pgid``), not just the leader. Reuses the
  exact same semantics the shell ``timeout`` failsafe would — one process
  ID namespace, no orphaned children.
- :func:`active_streams` — diagnostic map of currently-running stream
  handles (for Bun/extension health chips later).

Everything here is local and deterministic for tests: no events emitted
from this module; the emitter linkage happens via the agent's
``tools_execute`` shim binding a ``stream_id + on_chunk`` closure in a
``contextvars.ContextVar`` before dispatching the tool call.
"""

from __future__ import annotations

import logging
import os
import select
import signal
import subprocess  # nosec B404 — shell streaming is the explicit tool feature
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .sandbox import wrap_spawn

logger = logging.getLogger(__name__)

ChunkCallback = Callable[[str, str], None]

# Event_type carried on the wire for per-line shell chunks. Lives here rather
# than in agent.py's CompanionEventType enum (adding an enum value forces an
# agent.py edit, which trips pre-existing complexity debt in that module).
SHELL_OUTPUT_EVENT_TYPE = "shell_output"

# Terminal lifecycle event (done | timed_out | cancelled) so UIs can move a
# stream chip out of the running state — fixes chips stuck at `running`
# whose late Cancel click surfaced as `not-found`.
SHELL_STREAM_END_EVENT_TYPE = "shell_stream_end"


@dataclass
class _ActiveStream:
    stream_id: str
    process: subprocess.Popen[str]
    started: float
    command: str
    cancelled: bool = False


_active: dict[str, _ActiveStream] = {}
_active_lock = threading.Lock()


def new_stream_id(prefix: str = "shell") -> str:
    """Short opaque stream identifier (uuid4 first 8 hex)."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def active_streams() -> dict[str, dict[str, object]]:
    """Snapshot of currently-running streams (stream_id -> metadata)."""
    with _active_lock:
        return {
            sid: {
                "command": entry.command,
                "started": entry.started,
                "cancelled": entry.cancelled,
            }
            for sid, entry in list(_active.items())
        }


@dataclass
class StreamResult:
    status: str  # "done" | "timed_out" | "cancelled"
    stream_id: str
    command: str
    output: str
    returncode: int | None
    cancelled: bool = False
    elapsed_sec: float = 0.0
    traversed_lines: int = 0
    extras: dict[str, object] = field(default_factory=dict)


def _interpret_exit(code: int | None, cancelled: bool, timed_out: bool) -> str:
    if cancelled:
        return "cancelled"
    if timed_out:
        return "timed_out"
    return "done"


def cancel_stream(stream_id: str) -> bool:
    """Kill the process group of the active stream identified by ``stream_id``.

    Returns True when a live handle existed and got signalled; False when no
    such stream is active (already finished or unknown). Lives on the module
    registry so later ACP-side controls target the same handle.
    """
    with _active_lock:
        entry = _active.get(stream_id)
        if entry is None or entry.process.poll() is not None:
            return False
        entry.cancelled = True
        process = entry.process
    try:
        pgid = os.getpgid(process.pid)
        os.killpg(pgid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return True  # raced exit before kill — treated as success-ish
    except PermissionError:
        return False


def _register(entry: _ActiveStream) -> None:
    with _active_lock:
        _active[entry.stream_id] = entry


def _unregister(stream_id: str) -> None:
    with _active_lock:
        _active.pop(stream_id, None)


def _emit_line(on_chunk: ChunkCallback | None, stream_id: str, text: str, acc: list[str]) -> None:
    """Append to accumulator and forward chunk via callback (errors never block)."""
    acc.append(text)
    if on_chunk is not None:
        try:
            on_chunk(stream_id, text)
        except Exception:  # noqa: BLE001 — emitter outage must not kill the run
            pass


def _killpg(proc: subprocess.Popen[str]) -> None:
    """Kill proc's process group; ProcessLookupError tolerated."""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _pump_ready_line(
    proc: subprocess.Popen[str], on_chunk: ChunkCallback | None, stream_id: str, acc: list[str]
) -> bool:
    """Try one non-blocking pipe read; return True if EOF hit without exit."""
    ready = select.select([proc.stdout], [], [], 0.05)
    if not ready[0] or proc.stdout is None:
        return False
    line = proc.stdout.readline()
    if not line:
        return True  # EOF without exit yet — wait() picks up the zombie
    _emit_line(on_chunk, stream_id, line, acc)
    return False


def _reap_after_kill(proc: subprocess.Popen[str], stream_id: str) -> None:
    """Reap after the group kill — SIGKILL only signals; wait() is the reap
    (a killed child stays a zombie until reaped, never leave it for init)."""
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        logger.warning(
            "stream %s: child %s did not reap after SIGKILL — os will reap on parent exit",
            stream_id,
            proc.pid,
        )


def _drain_remaining(
    proc: subprocess.Popen[str], on_chunk: ChunkCallback | None, stream_id: str, acc: list[str]
) -> int:
    """Read all buffered output after exit/EOF/cancel; return lines drained."""
    if proc.stdout is None:
        return 0
    count = 0
    for trailing in proc.stdout:
        count += 1
        if isinstance(trailing, str):
            _emit_line(on_chunk, stream_id, trailing, acc)
    return count


def _spawn(command: str, cwd: str | os.PathLike[str] | None) -> subprocess.Popen[str]:
    """Launch the command in its own process group (os.setsid).

    Uses the same sanitised tool env as the blocking path (R1-5): before
    2026-10-05 the streamed path inherited the full environment, session
    tokens included. With the local tool sandbox on (ADR-101) the command
    runs under its wrapper, which leads the new group, so the group kill
    still reaches everything; :class:`SandboxError` means nothing started.
    """
    from .tools import _tool_spawn_env

    workdir = os.fspath(cwd) if cwd is not None else os.getcwd()
    spec = wrap_spawn(command, shell=True, cwd=workdir, env=_tool_spawn_env())
    return subprocess.Popen(  # noqa: S603 — argv/shell is tool behaviour
        spec.argv,
        shell=spec.shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=workdir,
        env=spec.env,
        preexec_fn=os.setsid,
        stdin=subprocess.DEVNULL,
    )


def run_shell_streaming(
    command: str,
    *,
    stream_id: str,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float = 300.0,
    on_chunk: ChunkCallback | None = None,
) -> StreamResult:
    """Run ``command`` streaming stdout/stderr chunks via ``on_chunk``.

    The whole command runs in its own process group (``os.setsid``) so a
    :func:`cancel_stream` targets the group, never strays.

    Returns a :class:`StreamResult` with the accumulated text, returncode,
    status, elapsed wall clock and the traversed line count for UI emittance.
    """
    started = time.monotonic()
    parts: list[str] = []
    eof_hit = False
    timed_out = False

    proc = _spawn(command, cwd)
    entry = _ActiveStream(stream_id=stream_id, process=proc, started=started, command=command)
    _register(entry)
    assert proc.stdout is not None
    try:
        while True:
            if time.monotonic() - started > timeout:
                timed_out = True
                _killpg(proc)
                break
            if proc.poll() is not None or eof_hit:
                break
            if _pump_ready_line(proc, on_chunk, stream_id, parts):
                eof_hit = True
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            _killpg(proc)
            _reap_after_kill(proc, stream_id)
        _drain_remaining(proc, on_chunk, stream_id, parts)
    finally:
        _unregister(stream_id)
        if proc.stdout is not None:
            proc.stdout.close()
    returncode = proc.poll()
    elapsed = time.monotonic() - started
    status = _interpret_exit(returncode, entry.cancelled, timed_out)
    line_count = len(parts)
    return StreamResult(
        status=status,
        stream_id=stream_id,
        command=command,
        output="".join(parts),
        returncode=returncode,
        cancelled=entry.cancelled,
        elapsed_sec=round(elapsed, 3),
        traversed_lines=line_count,
        extras={"timed_out": timed_out},
    )
