"""Detach-on-timeout for ``shell_exec`` (TB 4.0 harness-efficiency plan, item H18).

The captured shell path kills the whole process group when a command runs
past its timeout. A 30-minute build or training run that overshoots by a
minute is then lost, and when the command pipes into ``tail`` the result is
empty, because ``tail`` prints only at EOF (retry4 layout ``p24bDM4``: two
1,800 s timeouts that returned nothing).

With detach on, the command writes to two spill files from the start instead
of pipes. When it finishes in time, the files are read and removed, and the
result is the one the pipe path gives. When the timeout fires, the process is
left running: the result names its pid (it leads its own process group) and
the files, so a later ``shell_exec`` can ``tail`` them or ``kill`` it.
Detached processes are reaped on the next ``shell_exec`` call (the result
then says how they ended) and sent SIGTERM when the R4 process exits; the
spill directory is removed then unless one of them outlived the SIGTERM. A
host that can be stopped by a signal (the TUI bridge) calls
:func:`install_signal_cleanup`, because ``atexit`` does not run on SIGTERM.
The streamed shell path (ACP clients) never detaches.

With the local tool sandbox on (ADR-101) the command runs under its wrapper,
which leads the process group, so the handle's pid and ``kill -- -pid`` keep
working; each handle records whether it was started sandboxed, and
:func:`unconfined_detached` names the ones still running unconfined.

Switch: ``R4_SHELL_DETACH_ON_TIMEOUT`` (``1``/``0``). Unset, detach is on only
for unattended runs (``R4_AUTONOMOUS=1``); interactive sessions keep the kill.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..procgroup import signal_group
from .env_switch import autonomous, env_switch
from .sandbox import SpawnSpec, wrap_spawn

DETACH_ENV = "R4_SHELL_DETACH_ON_TIMEOUT"
# Commands whose output reaches the pipe only at EOF.
_EOF_FILTER_RE = re.compile(r"\|\s*(tail|head|sort|uniq|wc)\b")


def detach_enabled() -> bool:
    """``R4_SHELL_DETACH_ON_TIMEOUT`` when set, else whether ``R4_AUTONOMOUS`` is on."""
    return env_switch(DETACH_ENV, autonomous())


def pipe_buffer_note(command: str) -> str:
    """Why a timed-out ``… | tail`` returned nothing, or ``""`` for other commands."""
    match = _EOF_FILTER_RE.search(command)
    if match is None:
        return ""
    return (
        f"Note: output piped to `{match.group(1)}` appears only when the command ends, "
        "so a timeout returns none of it. Redirect to a file instead "
        "(`<cmd> > job.log 2>&1`) and poll `tail -n 50 job.log`."
    )


_SANDBOXED_NOTE = (
    "\nSandboxed (ADR-101): a sandboxed shell_exec sees neither this pid nor these "
    "files (its /tmp is private); read them with file_read, and the next shell_exec "
    "reports when the command ends."
)


@dataclass
class DetachedShell:
    """A timed-out command left running; its output keeps going to the files."""

    proc: subprocess.Popen[bytes]
    stdout_path: Path
    stderr_path: Path
    sandboxed: bool = False  # started under the local tool sandbox (ADR-101)
    # bwrap wrapped it: own pid namespace and private /tmp, so a sandboxed
    # shell sees neither the pid nor the spill files (not so for an inherited
    # sandbox, which wraps nothing, nor for Seatbelt).
    private_view: bool = False

    @property
    def pid(self) -> int:
        return self.proc.pid

    def describe(self) -> str:
        out, err = shlex.quote(str(self.stdout_path)), shlex.quote(str(self.stderr_path))
        pid = self.pid
        text = (
            f"Handle: pid {pid} (leads its own process group); "
            f"stdout -> {self.stdout_path}; stderr -> {self.stderr_path}\n"
            f"Poll: `tail -n 50 {out} {err}`; running? `kill -0 {pid} && echo running`; "
            f"stop it: `kill -- -{pid}`"
        )
        return text + (_SANDBOXED_NOTE if self.private_view else "")


@dataclass
class FileRun:
    """One command run with its output in files."""

    stdout: str
    stderr: str
    returncode: int | None
    detached: DetachedShell | None  # set when the timeout fired


_LOCK = threading.Lock()
_DETACHED: dict[int, DetachedShell] = {}
_SPILL_DIR: Path | None = None
_ATEXIT_REGISTERED = False


def _register_exit_cleanup() -> None:
    """Register :func:`cleanup_at_exit` once (caller holds ``_LOCK``)."""
    global _ATEXIT_REGISTERED
    if not _ATEXIT_REGISTERED:
        atexit.register(cleanup_at_exit)
        _ATEXIT_REGISTERED = True


def spill_dir() -> Path:
    """This process's private (0700) spill directory under the system temp dir."""
    global _SPILL_DIR
    with _LOCK:
        if _SPILL_DIR is None or not _SPILL_DIR.is_dir():
            _SPILL_DIR = Path(tempfile.mkdtemp(prefix="r4-shell-"))
            _register_exit_cleanup()
        return _SPILL_DIR


def _new_spill_paths() -> tuple[Path, Path]:
    stem = f"shell-{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}"
    directory = spill_dir()
    return directory / f"{stem}.out", directory / f"{stem}.err"


def _read_text(path: Path, tail_chars: int | None = None) -> str:
    """The file's text (universal newlines, like the pipe path); its tail when asked."""
    try:
        with path.open("rb") as fh:
            if tail_chars is not None:
                fh.seek(max(0, path.stat().st_size - tail_chars * 4))
            data = fh.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return text if tail_chars is None else text[-tail_chars:]


def _remove(*paths: Path) -> None:
    for path in paths:
        with contextlib.suppress(OSError):
            path.unlink()


def _kill_group(proc: subprocess.Popen[bytes], sig: int) -> None:
    signal_group(proc, sig)


def _register(handle: DetachedShell) -> None:
    with _LOCK:
        _DETACHED[handle.pid] = handle
        _register_exit_cleanup()


def run_to_files(
    command: str, *, cwd: str, timeout: int, env: dict[str, str], tail_chars: int
) -> FileRun:
    """Run *command* with its output in spill files; detach it on timeout.

    Args:
        command: Shell command line (``/bin/sh -c``).
        cwd: Working directory.
        timeout: Seconds to wait before detaching.
        env: Child environment.
        tail_chars: Chars of each stream returned on a timeout.

    Returns:
        The finished run's output (files removed), or, on timeout, the tail of
        the output so far and the :class:`DetachedShell` handle.
    """
    spec = wrap_spawn(command, shell=True, cwd=cwd, env=env)  # SandboxError: nothing started
    out_path, err_path = _new_spill_paths()
    with out_path.open("wb") as out_fh, err_path.open("wb") as err_fh:
        proc = subprocess.Popen(
            spec.argv,
            shell=spec.shell,
            stdout=out_fh,
            stderr=err_fh,
            cwd=cwd,
            env=spec.env,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
        )
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return _detach(proc, out_path, err_path, tail_chars, spec=spec)
    except BaseException:
        _kill_group(proc, signal.SIGKILL)
        with contextlib.suppress(Exception):
            proc.wait(timeout=1.0)
        _remove(out_path, err_path)
        raise
    run = FileRun(_read_text(out_path), _read_text(err_path), proc.returncode, None)
    _remove(out_path, err_path)
    return run


def _detach(
    proc: subprocess.Popen[bytes],
    out_path: Path,
    err_path: Path,
    tail_chars: int,
    *,
    spec: SpawnSpec | None = None,
) -> FileRun:
    sandboxed = spec is not None and spec.sandboxed
    # Only a wrapper this process built has a private /tmp and pid namespace;
    # an inherited sandbox passes the command through (review Q-10).
    private = spec is not None and spec.wrapped and spec.backend == "bwrap"
    handle = DetachedShell(proc, out_path, err_path, sandboxed=sandboxed, private_view=private)
    _register(handle)
    stdout = _read_text(out_path, tail_chars)
    stderr = _read_text(err_path, tail_chars)
    return FileRun(stdout, stderr, None, handle)


def reap_finished() -> str:
    """Reap detached commands that ended; one line per command, ``""`` when none."""
    with _LOCK:
        done = [h for h in _DETACHED.values() if h.proc.poll() is not None]
        for handle in done:
            _DETACHED.pop(handle.pid, None)
    return "".join(
        f"[detached pid {h.pid} finished with exit code {h.proc.returncode}; "
        f"its output is in {h.stdout_path} and {h.stderr_path}]\n"
        for h in done
    )


def detached_pids() -> list[int]:
    """Pids of detached commands not yet reaped (for tests and the session)."""
    with _LOCK:
        return sorted(_DETACHED)


def unconfined_detached() -> list[str]:
    """Detached commands still running that were started with the sandbox off."""
    with _LOCK:
        handles = [h for h in _DETACHED.values() if not h.sandboxed and h.proc.poll() is None]
    return [
        f"detached shell pid {h.pid} (output in {h.stdout_path})"
        for h in sorted(handles, key=lambda h: h.pid)
    ]


def _still_running(proc: subprocess.Popen[bytes], grace: float) -> bool:
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        return True
    return False


def terminate_detached(grace: float = 1.0) -> list[int]:
    """SIGTERM every detached command still running.

    Returns:
        Pids still running *grace* seconds after their SIGTERM.
    """
    with _LOCK:
        handles = list(_DETACHED.values())
        _DETACHED.clear()
    running = [handle for handle in handles if handle.proc.poll() is None]
    for handle in running:
        _kill_group(handle.proc, signal.SIGTERM)
    return [handle.pid for handle in running if _still_running(handle.proc, grace)]


def cleanup_at_exit() -> None:
    """Stop detached commands; remove the spill directory unless one survived.

    Registered with ``atexit`` when the directory is created or a command is
    detached (review 2026-10-24: each unattended process left one
    ``r4-shell-*`` directory behind)."""
    survivors = terminate_detached()
    directory = _SPILL_DIR
    if directory is not None and not survivors:
        shutil.rmtree(directory, ignore_errors=True)


def _cleanup_and_reraise(signum: int, _frame: object) -> None:
    cleanup_at_exit()
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def install_signal_cleanup() -> None:
    """Run :func:`cleanup_at_exit` on SIGTERM / SIGHUP, then the default action.

    Python's default SIGTERM handling skips ``atexit``, and detached commands
    run in their own sessions, so a host stopped by a signal left them
    running. Main thread only; a handler someone else installed is kept.
    """
    if threading.current_thread() is not threading.main_thread():
        return
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None and signal.getsignal(sig) is signal.SIG_DFL:
            signal.signal(sig, _cleanup_and_reraise)
