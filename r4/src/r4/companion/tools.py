"""Built-in tool implementations for the companion.

Each tool is a dict with ``name``, ``description``, ``parameters``
(JSON Schema), and an ``execute`` callable. Tools wrap existing r4
SDK functions and basic OS operations.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Union

from .agent_workspace import WorkspaceEscape, contained, current_workspace
from .agent_workspace import resolve as _ws_resolve
from .agent_workspace import resolve_cwd as _ws_cwd
from .apply_patch import apply_patch as _apply_patch_impl
from .client_extra_tools import build_client_extra_tools
from .coding import (
    compile_code,
    detect_code_context_text,
    detect_language_servers_text,
    run_code,
    start_language_servers,
    test_code,
    unconfined_language_servers,
)
from .decision_tools import build_decision_tools
from .file_filtering import format_directory_listing, list_directory_entries
from .job_watch import build_job_watch_tools
from .knowledge_tools import build_knowledge_tools
from .mcp_first_class import build_first_class_mcp_tools
from .passage_tool import build_open_passage_tool
from .plan_commit_tools import build_plan_commit_tools
from .project_tools import build_project_tools
from .research_loop_tools import build_research_loop_tools
from .sandbox import SandboxError, wrap_spawn
from .shell_detach import detach_enabled as _shell_detach_enabled
from .shell_detach import pipe_buffer_note as _pipe_buffer_note
from .shell_detach import reap_finished as _reap_detached_shells
from .shell_detach import run_to_files as _run_to_files
from .shell_detach import unconfined_detached as _unconfined_detached
from .streaming_shell import cancel_stream as _shell_cancel_stream
from .streaming_shell import new_stream_id as _shell_new_stream_id
from .streaming_shell import run_shell_streaming as _shell_run_streaming
from .team_plan_tools import build_team_plan_tools
from .team_plan_tools import executor_of as team_plan_executor_of
from .test_fix_loop import render_report as _tfr_render_report
from .test_fix_loop import run_test_fix_loop as _tfr_run
from .tool_evidence import label_passages, passage_from_rag_source
from .tool_python import tool_python
from .workflow_tools import build_workflow_tools

if TYPE_CHECKING:
    from r4.client import Client

    from .shell_detach import DetachedShell as _DetachedShell

logger = logging.getLogger(__name__)

# A model name, or the session's accessor for its current one (ADR-092 D-6).
SessionModel = Union[str, Callable[[], Union[str, None]], None]


# ---------------------------------------------------------------------------
# Tool builder helper
# ---------------------------------------------------------------------------


def _tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    execute,
    *,
    concurrency_safe: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
        "execute": execute,
        "concurrency_safe": concurrency_safe,
    }


# ---------------------------------------------------------------------------
# Shell / Python execution
# ---------------------------------------------------------------------------

# Optional chunk sink injected by the ACP/webview layer: when set, _shell_exec
# routes through streaming_shell and emits per-line chunks keyed by stream_id
# so the UI can live-render. Default None => old atomic behaviour (not risk).
_shell_chunk_sink = None
_shell_status_sink = None


def set_shell_chunk_sink(sink) -> None:
    """Register ``sink(stream_id, chunk)`` called per output line; None disables."""
    global _shell_chunk_sink
    _shell_chunk_sink = sink


def get_shell_chunk_sink():
    """Current sink (or None) — session loops save/restore it around turns."""
    return _shell_chunk_sink


def set_shell_status_sink(sink) -> None:
    """Register ``sink(stream_id, status)`` called once at stream end."""
    global _shell_status_sink
    _shell_status_sink = sink


def _notify_shell_stream_end(stream_id: str, status: str) -> None:
    """Fire the terminal-status sink; never breaks the tool result path."""
    if _shell_status_sink is None:
        return
    with contextlib.suppress(Exception):
        _shell_status_sink(stream_id, status)


# ---------------------------------------------------------------------------
# Execution limits for shell_exec / python_exec (harness repair plan
# 2026-10-05, rows H6 + H7; gap row D-26). The tool schemas below quote these
# numbers, so the model reads the same limits the code enforces.
# ---------------------------------------------------------------------------

EXEC_DEFAULT_TIMEOUT_S = 180
EXEC_MAX_TIMEOUT_S = 1800
# Output over the cap keeps its head and tail; the middle is replaced by a
# "…[truncated N chars]…" marker.
EXEC_OUTPUT_CAP_CHARS = 32_000
EXEC_OUTPUT_HEAD_CHARS = 20_000
EXEC_OUTPUT_TAIL_CHARS = 8_000
# On timeout the result carries this many trailing chars of the output so far.
EXEC_TIMEOUT_TAIL_CHARS = 8_000
# Crawl cap for `find` (see effective_timeout_for_command).
FIND_TIMEOUT_CAP_S = 25
# After the group kill: how long to wait for the pipes to reach EOF. Only a
# grandchild that left the group (setsid / double fork) can hold them open.
_EXEC_KILL_DRAIN_S = 5.0

_SHELL_BACKGROUND_HINT = (
    "run long jobs in the background with output in a file: "
    "`nohup <cmd> > job.log 2>&1 &`, then poll `tail -n 50 job.log`"
)
_PYTHON_BACKGROUND_HINT = (
    "for long jobs write the code to a file and start it with shell_exec in the "
    "background: `nohup python job.py > job.log 2>&1 &`, then poll `tail -n 50 job.log`"
)

# Under the terminal-bench ablation profile (R4_AGENT_PROFILE=terminal, plan
# item H11) the cap is agent_profile.SHELL_OUTPUT_CAP, split 5:3 head:tail;
# this is the value used when that module or constant is absent.
TERMINAL_PROFILE_OUTPUT_CAP_CHARS = 16_000


_ON_TIMEOUT_KILL = "the whole process group is killed"
# H18 (detach on): the description says what the tool does (review 2026-10-24:
# it said "killed" while the command was left running).
_ON_TIMEOUT_DETACH = (
    "the command is detached, not killed: it keeps running with its output going "
    "to files that the result names, with its pid,"
)


def _exec_limits_text(cap: int, head: int, tail: int, *, detach: bool = False) -> str:
    on_timeout = _ON_TIMEOUT_DETACH if detach else _ON_TIMEOUT_KILL
    return (
        f"Default timeout {EXEC_DEFAULT_TIMEOUT_S} s; pass `timeout` (seconds) up to "
        f"{EXEC_MAX_TIMEOUT_S} s for longer runs (larger values are clamped). On timeout "
        f"{on_timeout} and the last {EXEC_TIMEOUT_TAIL_CHARS:,} chars "
        f"of output are returned. Output over {cap:,} chars is cut to the "
        f"first {head:,} + last {tail:,} chars."
    )


def _shell_exec_description(limits: str) -> str:
    return (
        "Execute a shell command and return its stdout/stderr. "
        + limits
        + " Jobs that may outlast the timeout (builds, training, long simulations): start "
        "them in the background with output redirected to a file, "
        "`nohup <cmd> > job.log 2>&1 &`, then poll `tail -n 50 job.log` in later calls. "
        f"`find` commands stop after {FIND_TIMEOUT_CAP_S} s; bound them with -maxdepth."
    )


def _python_exec_description(limits: str) -> str:
    return (
        "Execute Python code in a subprocess (python -c) and return its stdout/stderr. "
        + limits
        + " For jobs that may outlast the timeout, write the code to a file and start it "
        "with shell_exec in the background (`nohup python job.py > job.log 2>&1 &`), "
        "then poll the log."
    )


_EXEC_LIMITS_TEXT = _exec_limits_text(
    EXEC_OUTPUT_CAP_CHARS, EXEC_OUTPUT_HEAD_CHARS, EXEC_OUTPUT_TAIL_CHARS
)
SHELL_EXEC_DESCRIPTION = _shell_exec_description(_EXEC_LIMITS_TEXT)
PYTHON_EXEC_DESCRIPTION = _python_exec_description(_EXEC_LIMITS_TEXT)
EXEC_TIMEOUT_PARAM_DESCRIPTION = (
    f"Timeout in seconds (default {EXEC_DEFAULT_TIMEOUT_S}, max {EXEC_MAX_TIMEOUT_S})"
)


_APPLY_PATCH_TAIL = (
    "create/modify/delete/rename files "
    "(multi-hunk, atomic per file). PREFER this over file_write "
    "for multi-hunk edits of existing files — it only sends the "
    "changed lines plus context, not the whole file. Set dry_run=true "
    "to validate without writing."
)


def _apply_patch_texts() -> tuple[str, str]:
    """(tool description, ``patch_text`` description) for apply_patch.

    H10 names the Codex envelope the parser accepts. The text belongs to the
    prompt, so ``R4_COMPANION_PROMPT_HYGIENE=0`` restores the old wording
    (the parser still accepts the envelope)."""
    from .agent_profile import hygiene_enabled

    if not hygiene_enabled():
        return (
            "Apply a unified diff to " + _APPLY_PATCH_TAIL,
            "Unified diff text (git-apply compatible)",
        )
    return (
        "Apply a unified diff (or a Codex `*** Begin Patch` envelope) to " + _APPLY_PATCH_TAIL,
        "Unified diff text (git-apply compatible), or a Codex "
        "envelope: `*** Begin Patch`, `*** Update File: <path>` "
        "with `@@` context hunks, `*** Add File: <path>` with `+` "
        "lines, `*** Delete File: <path>`, `*** End Patch`",
    )


def _terminal_profile_active() -> bool:
    """``R4_AGENT_PROFILE=terminal`` (read from the env: agent_profile may be absent)."""
    return os.environ.get("R4_AGENT_PROFILE", "").strip().lower() == "terminal"


def _terminal_profile_cap() -> int:
    """``agent_profile.SHELL_OUTPUT_CAP`` when that module provides a positive int."""
    try:
        from . import agent_profile
    except ImportError:
        return TERMINAL_PROFILE_OUTPUT_CAP_CHARS
    cap = getattr(agent_profile, "SHELL_OUTPUT_CAP", TERMINAL_PROFILE_OUTPUT_CAP_CHARS)
    if isinstance(cap, int) and cap > 0:
        return cap
    return TERMINAL_PROFILE_OUTPUT_CAP_CHARS


def exec_output_limits() -> tuple[int, int, int]:
    """``(cap, head, tail)`` chars for exec output: the defaults, or the terminal profile's."""
    if not _terminal_profile_active():
        return EXEC_OUTPUT_CAP_CHARS, EXEC_OUTPUT_HEAD_CHARS, EXEC_OUTPUT_TAIL_CHARS
    cap = _terminal_profile_cap()
    head = cap * 5 // 8
    return cap, head, cap - head


def _cap_exec_output(output: str) -> str:
    """Cut *output* over the cap to head + tail, naming the omitted count."""
    cap, head, tail = exec_output_limits()
    if len(output) <= cap:
        return output
    omitted = len(output) - head - tail
    return output[:head] + f"\n…[truncated {omitted} chars]…\n" + output[-tail:]


def shell_exec_description() -> str:
    """The shell_exec description: the profile's cap under the terminal profile,
    and the detach behaviour when detach-on-timeout is on (H18)."""
    detach = _shell_detach_enabled()
    if not _terminal_profile_active() and not detach:
        return SHELL_EXEC_DESCRIPTION
    return _shell_exec_description(_exec_limits_text(*exec_output_limits(), detach=detach))


def python_exec_description() -> str:
    """The python_exec description; it quotes the terminal profile's cap under that profile."""
    if not _terminal_profile_active():
        return PYTHON_EXEC_DESCRIPTION
    return _python_exec_description(_exec_limits_text(*exec_output_limits()))


def resolve_exec_timeout(timeout: int | float | str | None) -> tuple[int, str]:
    """Normalise a model-supplied ``timeout`` for shell_exec / python_exec.

    Args:
        timeout: The raw tool argument; ``None`` means the default.

    Returns:
        ``(seconds, note)`` with seconds in ``1..EXEC_MAX_TIMEOUT_S``. *note*
        is a one-line result prefix saying how the value was clamped or
        replaced, or ``""`` when it was used as given.
    """
    if timeout is None:
        return EXEC_DEFAULT_TIMEOUT_S, ""
    try:
        seconds = int(float(timeout))
    except (TypeError, ValueError, OverflowError):
        return EXEC_DEFAULT_TIMEOUT_S, (
            f"[timeout {timeout!r} is not a number of seconds; "
            f"used the default {EXEC_DEFAULT_TIMEOUT_S} s]\n"
        )
    if seconds < 1:
        return EXEC_DEFAULT_TIMEOUT_S, (
            f"[timeout {seconds} s is below 1 s; used the default {EXEC_DEFAULT_TIMEOUT_S} s]\n"
        )
    if seconds > EXEC_MAX_TIMEOUT_S:
        return EXEC_MAX_TIMEOUT_S, (
            f"[timeout {seconds} s exceeds the {EXEC_MAX_TIMEOUT_S} s maximum; "
            f"clamped to {EXEC_MAX_TIMEOUT_S} s]\n"
        )
    return seconds, ""


@dataclass
class _ExecOutcome:
    """What one captured child run wrote (everything so far, on a timeout)."""

    stdout: str  # decoded stdout
    stderr: str  # decoded stderr
    returncode: int | None  # None when the child could not be reaped
    timed_out: bool  # True when the timeout fired and the group was killed


def _as_text(data: str | bytes | None) -> str:
    """Pipe data as text; ``communicate`` hands raw bytes on a timeout."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    """SIGKILL the child's whole process group (the child leads its own session)."""
    from ..procgroup import signal_group

    signal_group(proc)


def _drain_after_kill(proc: subprocess.Popen[str]) -> tuple[str, str]:
    """Collect what the killed group wrote; never block past the drain window."""
    out: str | bytes | None
    err: str | bytes | None
    try:
        out, err = proc.communicate(timeout=_EXEC_KILL_DRAIN_S)
    except subprocess.TimeoutExpired as exc:  # an escaped grandchild holds a pipe
        out, err = exc.output, exc.stderr
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=1.0)
    return _as_text(out), _as_text(err)


def _run_captured(
    args: str | list[str], *, shell: bool, cwd: str, timeout: int, env: dict[str, str]
) -> _ExecOutcome:
    """Run *args* in a new session, capture its output, kill the group on timeout.

    The child leads its own process group (``start_new_session``), so the
    timeout kill reaches every member: both sides of ``a && b``, pipelines and
    ``&`` children that still hold the output pipes. With the sandbox on
    (ADR-101) the wrapper leads the group instead, and the kill reaches it and
    everything inside; a wrapper that cannot be built raises
    :class:`SandboxError` before anything starts (never run unconfined).
    """
    spec = wrap_spawn(args, shell=shell, cwd=cwd, env=env)
    proc = subprocess.Popen(
        spec.argv,
        shell=spec.shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=spec.env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        out, err = _drain_after_kill(proc)
        return _ExecOutcome(out, err, proc.returncode, timed_out=True)
    except BaseException:
        _kill_process_group(proc)
        with contextlib.suppress(Exception):
            proc.wait(timeout=1.0)
        raise
    return _ExecOutcome(out or "", err or "", proc.returncode, timed_out=False)


def _combine_streams(stdout: str, stderr: str) -> str:
    """stdout, then stderr under a ``[stderr]`` marker (the tools' result shape)."""
    return stdout + ("\n[stderr]\n" + stderr if stderr else "")


def _completed_exec_text(outcome: _ExecOutcome) -> str:
    """Result text for a run that finished inside its timeout."""
    output = _combine_streams(outcome.stdout, outcome.stderr)
    if outcome.returncode != 0:
        output += f"\n[exit code: {outcome.returncode}]"
    return _cap_exec_output(output) or "(no output)"


def _timeout_exec_text(seconds: int, partial: str, hint: str) -> str:
    """Timeout result: the error line, a recovery hint, the tail of the output so far."""
    tail = partial[-EXEC_TIMEOUT_TAIL_CHARS:]
    return (
        f"Error: command timed out after {seconds} s\n"
        f"Hint: {hint}\n"
        "[partial output before timeout]\n" + (tail or "(no output)")
    )


def _raise_or_background_hint(seconds: int, background_hint: str) -> str:
    """Suggest a larger timeout while one is still allowed, else backgrounding."""
    if seconds < EXEC_MAX_TIMEOUT_S:
        return f"pass a larger `timeout` (max {EXEC_MAX_TIMEOUT_S} s), or {background_hint}"
    return background_hint


def _shell_timeout_hint(command: str, seconds: int) -> str:
    """`find` keeps its crawl-specific hint; everything else gets the limits hint.

    A command piped into an EOF filter (``| tail`` …) also gets a note saying
    why its timeout returned nothing (plan item H18).
    """
    if command.strip().startswith("find"):
        hint = _find_command_hint(command)
    else:
        hint = _raise_or_background_hint(seconds, _SHELL_BACKGROUND_HINT)
    note = _pipe_buffer_note(command)
    return f"{hint}\n{note}" if note else hint


def _detached_exec_text(seconds: int, partial: str, command: str, handle: "_DetachedShell") -> str:
    """Timeout result for a detached command: why, the handle, the output so far."""
    note = _pipe_buffer_note(command)
    return (
        f"Command timed out after {seconds} s and was detached, not killed: it keeps "
        "running in the background and its output keeps going to the files below.\n"
        + handle.describe()
        + "\n"
        + (note + "\n" if note else "")
        + "[partial output before timeout]\n"
        + (partial[-EXEC_TIMEOUT_TAIL_CHARS:] or "(no output)")
    )


def _shell_exec_detachable(command: str, cwd: str, seconds: int) -> str:
    """The captured path with output in files; a timeout detaches instead of killing."""
    run = _run_to_files(
        command, cwd=cwd, timeout=seconds, env=_tool_spawn_env(), tail_chars=EXEC_TIMEOUT_TAIL_CHARS
    )
    if run.detached is not None:
        partial = _combine_streams(run.stdout, run.stderr)
        return _detached_exec_text(seconds, partial, command, run.detached)
    return _completed_exec_text(_ExecOutcome(run.stdout, run.stderr, run.returncode, False))


def _shell_exec_captured(command: str, cwd: str, seconds: int) -> str:
    """The pipe path: capture, and kill the process group on timeout."""
    outcome = _run_captured(command, shell=True, cwd=cwd, timeout=seconds, env=_tool_spawn_env())
    if outcome.timed_out:
        partial = _combine_streams(outcome.stdout, outcome.stderr)
        return _timeout_exec_text(seconds, partial, _shell_timeout_hint(command, seconds))
    return _completed_exec_text(outcome)


def _shell_exec_streamed(command: str, cwd: str | None, timeout: int) -> str:
    """Streaming branch used when a chunk sink is registered."""
    sid = _shell_new_stream_id()
    try:
        res = _shell_run_streaming(
            command, stream_id=sid, cwd=cwd, timeout=float(timeout), on_chunk=_shell_chunk_sink
        )
    except SandboxError as e:  # refused before anything started (ADR-101 D-3)
        return f"Error: {e}"
    _notify_shell_stream_end(sid, res.status)
    output = res.output
    if res.status == "timed_out":
        hint = _shell_timeout_hint(command, timeout)
        return _timeout_exec_text(timeout, output, hint) + f"\n(stream {sid})"
    if res.status == "cancelled":
        return f"[cancelled stream {sid}]\n{_cap_exec_output(output)}"
    if res.returncode not in (0, None):
        output += f"\n[exit code: {res.returncode}]"
    return _cap_exec_output(output) or f"(no output, stream {sid})"


def _tool_spawn_env() -> dict[str, str]:
    """Venue-aware env for tool subprocesses (R1-5: no session token leaks).

    PYTHONUNBUFFERED=1 (unless set) so Python run through the shell flushes as
    it goes and a timeout still returns partial output (W2, 2026-10-05).
    ``R4_COMPANION_SESSION=1`` marks the subprocess as the agent's hands: an
    ``r4`` CLI started there sends its knowledge calls as ``agent`` and will
    not decide or establish (review of K1 part A, 2026-10-24; K-12).
    """
    from ..knowledge import COMPANION_SHELL_ENV

    try:
        from ..venue import sanitize_tool_spawn_env

        env = sanitize_tool_spawn_env()
    except Exception:  # noqa: BLE001 — local mode fallback: inherit
        env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env[COMPANION_SHELL_ENV] = "1"
    return env


def unconfined_running() -> list[str]:
    """Agent processes still running that were started with the sandbox off.

    Detached shells and language servers (the long-lived spawns). Turning the
    sandbox on does not touch them: they are listed, not killed (ADR-101
    residual risks), so ``session.sandbox_set`` can name them.
    """
    return [*_unconfined_detached(), *unconfined_language_servers()]


def _find_command_hint(command: str) -> str:
    """Timeout-class recovery hint for `find` commands (walked too much).

    It's honest to say the shell can't analyze what path it crawls. The hint
    tells the agent to reduce the search window *its own args* — not users'.
    """
    if "maxdepth" not in command:
        return (
            "Retried with `-maxdepth 3` is usually enough for code bases — "
            "try: same query with `-maxdepth N` (or `-type f -name <pattern>` "
            "to skip the deep mount walk)."
        )
    return "narrow the root or add filters (-type f / -name <pattern>) to keep the walk small."


def _is_find_command(command: str) -> bool:
    stripped = command.strip()
    return stripped.startswith("find ") or stripped.startswith("find\t") or stripped == "find"


def effective_timeout_for_command(command: str, caller_timeout: int) -> int:
    """Cap hard-crawlers at a shorter window (25s) — recovery guidance on
    timeout tells the agent to tighten the arg instead of hanging silently.
    """
    if _is_find_command(command):
        return min(caller_timeout, FIND_TIMEOUT_CAP_S)
    return caller_timeout


def _shell_runner(command: str) -> Callable[[str, str, int], str]:
    """The detaching runner when detach is on (H18), else the killing one.

    A capped ``find`` is always killed: the cap exists to stop the crawl, and
    its timeout hint (bound it with -maxdepth) only comes with the kill.
    """
    if _shell_detach_enabled() and not _is_find_command(command):
        return _shell_exec_detachable
    return _shell_exec_captured


def _shell_exec(
    command: str,
    cwd: str | None = None,
    timeout: int | float | str | None = EXEC_DEFAULT_TIMEOUT_S,
) -> str:
    """Execute a shell command and return combined output.

    Args:
        command: Shell command line (run with ``/bin/sh -c``).
        cwd: Working directory; defaults to the process cwd.
        timeout: Seconds before the process group is killed; clamped to
            ``EXEC_MAX_TIMEOUT_S`` (the clamp is stated in the result).

    Returns:
        stdout/stderr (capped at ``EXEC_OUTPUT_CAP_CHARS``), or an ``Error:``
        line; a timeout returns the tail of the output collected so far.
    """
    seconds, note = resolve_exec_timeout(timeout)
    note = _reap_detached_shells() + note
    try:
        cwd = _ws_cwd(cwd)  # a plan agent starts in its workspace (ADR-092 slice 5)
    except WorkspaceEscape as e:
        return f"{note}Error: {e}"
    if _shell_chunk_sink is not None:
        return note + _shell_exec_streamed(command, cwd, seconds)
    seconds = effective_timeout_for_command(command, seconds)
    runner = _shell_runner(command)
    try:
        return note + runner(command, cwd or os.getcwd(), seconds)
    except Exception as e:
        return f"{note}Error: {e}"


def _python_exec(code: str, timeout: int | float | str | None = EXEC_DEFAULT_TIMEOUT_S) -> str:
    """Execute Python code in a subprocess and return output.

    Args:
        code: Source passed to ``python -c``.
        timeout: Seconds before the process group is killed; clamped to
            ``EXEC_MAX_TIMEOUT_S`` (the clamp is stated in the result).

    Returns:
        stdout/stderr (capped at ``EXEC_OUTPUT_CAP_CHARS``), or an ``Error:``
        line; a timeout returns the tail of the output collected so far.
    """
    seconds, note = resolve_exec_timeout(timeout)
    env = _tool_spawn_env()
    # Unbuffered, so prints made before a timeout reach the pipe (partial output).
    env.setdefault("PYTHONUNBUFFERED", "1")
    # A plan agent's code starts in its workspace (ADR-092 slice 5).
    cwd = _ws_cwd(None) or os.getcwd()
    try:
        outcome = _run_captured(
            [tool_python(), "-c", code], shell=False, cwd=cwd, timeout=seconds, env=env
        )
    except Exception as e:
        return f"{note}Error: {e}"
    if outcome.timed_out:
        partial = _combine_streams(outcome.stdout, outcome.stderr)
        hint = _raise_or_background_hint(seconds, _PYTHON_BACKGROUND_HINT)
        return note + _timeout_exec_text(seconds, partial, hint)
    return note + _completed_exec_text(outcome)


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------


def _render_file_slice(text: str, offset: int, max_lines: int, limit: int | None) -> str:
    """Render a line-numbered slice of *text* for the file-read tool."""
    lines = text.splitlines()
    start = max(0, int(offset))
    cap = max(1, int(limit) if limit is not None else int(max_lines))
    if start >= len(lines):
        return f"Error: offset {start} past end of file ({len(lines)} lines)"
    selected = lines[start : start + cap]
    if start == 0 and len(lines) <= cap:
        return text
    body = "\n".join(f"{start + i + 1}|{line}" for i, line in enumerate(selected))
    remaining = len(lines) - start - len(selected)
    if remaining > 0:
        body += f"\n…[{remaining} more lines, {len(lines)} total]"
    return body


def _file_read(
    path: str,
    max_lines: int = 500,
    offset: int = 0,
    limit: int | None = None,
) -> str:
    """Read a file's content, optionally starting at a line offset."""
    try:
        p = _ws_resolve(path, write=False)
    except WorkspaceEscape as e:
        return f"Error: {e}"
    if not p.exists():
        return f"Error: file not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"
    try:
        return _render_file_slice(p.read_text(errors="replace"), offset, max_lines, limit)
    except Exception as e:
        return f"Error reading {path}: {e}"


def _format_diff(before: str, after: str, path: str, max_lines: int = 30) -> str:
    """Produce a short unified diff snippet for tool result display."""
    diff = list(
        unified_diff(
            before.splitlines(keepends=False),
            after.splitlines(keepends=False),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
            n=2,
        )
    )
    if not diff:
        return ""
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"…[diff truncated, {len(diff)} total lines]"]
    return "\n".join(diff)


# ---------------------------------------------------------------------------
# Session file-change tracking (OpenCode-parity session diffs)
#
# tools that mutate files record cumulative before→after state here so the
# session can show precise per-file diffs (and later revert) without git.
# ---------------------------------------------------------------------------

_CHANGE_CONTENT_CAP = 64 * 1024  # 64 KB per side; diffs beyond that stay useful via tool logs

_session_changes: dict[str, dict] = {}


def _cap_change_text(text: str) -> str:
    return text if len(text) <= _CHANGE_CONTENT_CAP else text[:_CHANGE_CONTENT_CAP]


def _count_new_in(needles: list[str], haystack: list[str]) -> int:
    return sum(1 for line in needles if line not in haystack)


_CHANGE_DIFF_LINE_CAP = 400


def _full_diff(before: str, after: str, path: str) -> str:
    """Full unified diff for the side-by-side renderer (line-capped)."""
    lines = list(
        unified_diff(
            before.splitlines(keepends=False),
            after.splitlines(keepends=False),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
            n=3,
        )
    )
    if len(lines) > _CHANGE_DIFF_LINE_CAP:
        lines = lines[:_CHANGE_DIFF_LINE_CAP]
        lines.append(f"…[diff truncated at {_CHANGE_DIFF_LINE_CAP} lines]")
    return "\n".join(lines)


def _before_is_exact(p: Path, before: str) -> bool:
    """True when ``before`` re-encodes to the file's bytes as UTF-8.

    The tools read text with universal newlines (and ``_file_write`` with
    ``errors="replace"``), so a CRLF or non-UTF-8 file's before-text is not
    its bytes; a revert must not write that text back (TUI audit #68).
    """
    try:
        return p.read_bytes() == before.encode("utf-8")
    except (OSError, ValueError):
        return False


def _record_file_change(
    path: str, before: str, after: str, *, existed: bool = True, exact: bool = True
) -> None:
    """Accumulate cumulative before→after per path (session-private VCS).

    ``existed`` is False when the tool created the file; ``exact`` is False
    when ``before`` is not the file's original bytes. The first record also
    notes whether its before-text was capped, so a revert can refuse rather
    than write a partial or altered file (TUI audit #68, 2026-10-17).

    A plan agent's write inside an isolated workspace (ADR-092 slice 5) is
    not a change to this session's tree: it reaches the project only through
    an approved merge-back, so it is not recorded here.
    """
    workspace = current_workspace()
    if workspace is not None and workspace.isolated:
        return
    try:
        rel = str(Path(path).expanduser().resolve().relative_to(Path.cwd().resolve()))
    except Exception:
        rel = str(path)
    entry = _session_changes.get(rel)
    if entry is None:
        entry = {
            "path": rel,
            "before": _cap_change_text(before),
            "before_existed": existed,
            "before_truncated": len(before) > _CHANGE_CONTENT_CAP,
            "before_lossy": not exact,
            "after": _cap_change_text(after),
            "inserted": _count_new_in(after.splitlines(), before.splitlines()),
            "deleted": _count_new_in(before.splitlines(), after.splitlines()),
            "edits": 1,
        }
        _session_changes[rel] = entry
    else:
        original = str(entry.get("before", ""))
        entry["after"] = _cap_change_text(after)
        entry["inserted"] = _count_new_in(after.splitlines(), original.splitlines())
        entry["deleted"] = _count_new_in(original.splitlines(), after.splitlines())
        entry["edits"] = int(entry.get("edits", 0)) + 1
    entry["diff_text"] = _full_diff(str(entry.get("before", "")), str(entry.get("after", "")), rel)


def get_session_changes() -> list[dict]:
    """Cumulative per-path before→after state recorded by write/edit tools."""
    return sorted(_session_changes.values(), key=lambda change: str(change.get("path", "")))


def _revert_refusal(entry: dict, path: str) -> str | None:
    """Why the recorded before-state cannot be written back exactly, if so."""
    if entry.get("before_truncated"):
        return f"before-state of {path} was truncated at {_CHANGE_CONTENT_CAP} characters"
    if entry.get("before_lossy"):
        return f"before-state of {path} was not plain UTF-8/LF text"
    return None


def revert_session_change(path: str) -> dict:
    """Restore a file to the session's recorded BEFORE state (OpenCode restore).

    The revert is itself recorded as a new edit on the same path (before :=
    current content, after := recorded before) so the change history stays
    honest and future reverts keep working. A file the session created is
    deleted; a before-text that was capped or decoded lossily at record time
    is refused, since writing it back would silently alter the file.
    """
    entry = _session_changes.get(str(path))
    if entry is None:
        raise ValueError(f"no recorded change for path: {path}")
    refusal = _revert_refusal(entry, str(path))
    if refusal is not None:
        raise ValueError(f"{refusal}; cannot revert it exactly")
    current = ""
    target = Path(path).expanduser()
    if target.is_file():
        current = target.read_text(errors="replace")
    saved_before = str(entry.get("before", ""))
    result = {"path": entry["path"], "reverted": True, "bytes": len(saved_before)}
    if entry.get("before_existed", True):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(saved_before.encode("utf-8"))
    else:
        target.unlink(missing_ok=True)
        result["deleted"] = True
    _record_file_change(path, current, saved_before)
    entry["reverted"] = True
    return result


def reset_session_changes() -> None:
    """Clear the registry (opened/reset sessions should start clean)."""
    _session_changes.clear()


def _file_write(path: str, content: str) -> str:
    """Write content to a file (creates parent dirs)."""
    try:
        p = _ws_resolve(path, write=True)
    except WorkspaceEscape as e:
        return f"Error: {e}"
    try:
        existed = p.is_file()
        before = p.read_text(errors="replace") if existed else ""
        exact = not existed or _before_is_exact(p, before)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        _record_file_change(str(p), before, content, existed=existed, exact=exact)
        diff = _format_diff(before, content, path)
        msg = f"Written {len(content)} bytes to {path}"
        if diff:
            msg += "\n" + diff
        return msg
    except Exception as e:
        return f"Error writing {path}: {e}"


def _file_edit(
    path: str,
    old_text: str,
    new_text: str,
    expected_count: int | None = None,
) -> str:
    """Replace ``old_text`` with ``new_text`` in a file.

    By default the match must be unique (single occurrence). Pass
    ``expected_count`` to allow replacing a known number of occurrences;
    the edit fails loudly if the actual count differs.
    """
    try:
        p = _ws_resolve(path, write=True)
    except WorkspaceEscape as e:
        return f"Error: {e}"
    if not p.exists():
        return f"Error: file not found: {path}"
    try:
        before = p.read_text()
        edited = _replaced_text(before, old_text, new_text, expected_count, path)
        if isinstance(edited, str):
            return edited
        content, replaced = edited
        exact = _before_is_exact(p, before)
        p.write_text(content)
        _record_file_change(str(p), before, content, exact=exact)
        diff = _format_diff(before, content, path)
        msg = f"Edited {path} (replaced {replaced} occurrence{'s' if replaced != 1 else ''})"
        if diff:
            msg += "\n" + diff
        return msg
    except Exception as e:
        return f"Error editing {path}: {e}"


def _replaced_text(
    content: str, old_text: str, new_text: str, expected_count: int | None, path: str
) -> tuple[str, int] | str:
    """``(new content, occurrences replaced)``, or the ``Error:`` text."""
    count = content.count(old_text)
    if count == 0:
        return f"Error: old_text not found in {path}"
    if expected_count is None:
        if count > 1:
            return (
                f"Error: old_text matches {count} locations in {path}; "
                "must be unique (or pass expected_count)"
            )
        return content.replace(old_text, new_text, 1), 1
    if count != expected_count:
        return (
            f"Error: old_text matches {count} locations in {path}, expected_count={expected_count}"
        )
    return content.replace(old_text, new_text), count


def _apply_patch(patch_text: str, dry_run: bool = False, workdir: str | None = None) -> str:
    """Apply a unified diff; thin wrapper returning display text.

    A plan agent's patch applies under its workspace (ADR-092 slice 5): the
    workdir resolves inside it, and apply_patch keeps every file under the
    workdir.
    """
    if current_workspace() is not None:
        try:
            workdir = str(_ws_resolve(workdir or ".", write=True))
        except WorkspaceEscape as e:
            return f"Error: {e}"
    result = _apply_patch_impl(patch_text, workdir=workdir, dry_run=dry_run)
    return result.to_display()


def _make_test_fix_loop_handler(client: Client):
    """Bind the session Client so the registered handler closure owns it."""

    def _test_fix_loop(
        command: str,
        files: list[str],
        max_iterations: int = 4,
        tail_bytes: int = 8000,
    ) -> str:
        """Drive edit→test→edit in one call; return machine-parseable summary."""
        try:
            report = _tfr_run(
                client,
                command,
                files,
                cwd=_ws_cwd(None),  # a plan agent tests in its workspace (ADR-092 slice 5)
                max_iterations=max_iterations,
                tail_bytes=tail_bytes,
            )
        except (SandboxError, WorkspaceEscape) as e:
            return f"Error: {e}"
        return _tfr_render_report(report)

    return _test_fix_loop


def _grep_search(
    pattern: str,
    path: str = ".",
    regex: bool = True,
    max_results: int = 200,
    case_insensitive: bool = False,
) -> str:
    """Search files for ``pattern``. Uses ripgrep when available, else Python ``re``.

    Returns matching lines in the form ``file:line:text``.
    """
    try:
        root = _ws_resolve(path, write=False)
    except WorkspaceEscape as e:
        return f"Error: {e}"
    if not root.exists():
        return f"Error: path not found: {path}"

    rg_output = _grep_with_rg(pattern, root, regex, max_results, case_insensitive)
    if rg_output is not None:
        return rg_output

    return _grep_with_python(pattern, root, regex, max_results, case_insensitive)


def _grep_with_rg(
    pattern: str,
    root: Path,
    regex: bool,
    max_results: int,
    case_insensitive: bool,
) -> str | None:
    rg = subprocess.run(
        ["which", "rg"], capture_output=True, text=True, stdin=subprocess.DEVNULL
    ).stdout.strip()
    if not rg:
        return None

    cmd = [rg, "--no-heading", "--line-number", "--color=never"]
    if not regex:
        cmd.append("--fixed-strings")
    if case_insensitive:
        cmd.append("-i")
    cmd.extend(["--max-count", str(max_results), pattern, str(root)])
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
        )
    except Exception as e:
        return f"Error: rg failed: {e}"
    return _format_rg_search_result(result, max_results)


def _format_rg_search_result(result, max_results: int) -> str:
    out = result.stdout.strip()
    if not out and result.returncode not in (0, 1):
        return f"Error: rg returned {result.returncode}: {result.stderr.strip()}"
    lines = out.splitlines()
    if len(lines) > max_results:
        lines = lines[:max_results] + [f"…[truncated, showing {max_results}]"]
    return "\n".join(lines) if lines else "(no matches)"


def _grep_with_python(
    pattern: str,
    root: Path,
    regex: bool,
    max_results: int,
    case_insensitive: bool,
) -> str:
    import re as _re

    flags = _re.IGNORECASE if case_insensitive else 0
    try:
        compiled = _re.compile(pattern if regex else _re.escape(pattern), flags)
    except _re.error as e:
        return f"Error: invalid regex: {e}"

    matches: list[str] = []
    for file_path in _grep_targets(root):
        try:
            if _append_python_grep_matches(matches, file_path, compiled, max_results):
                return "\n".join(matches)
        except OSError:
            continue
    return "\n".join(matches) if matches else "(no matches)"


def _grep_targets(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    # In a plan agent's workspace, a symlink that leads out is not followed
    # into a read (ADR-092 slice 5); without one, contained() is always True.
    return [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts and contained(p)]


def _append_python_grep_matches(
    matches: list[str],
    file_path: Path,
    compiled,
    max_results: int,
) -> bool:
    lines = file_path.read_text(errors="replace").splitlines()
    for line_number, line in enumerate(lines, 1):
        if compiled.search(line):
            matches.append(f"{file_path}:{line_number}:{line}")
            if len(matches) >= max_results:
                matches.append(f"…[truncated, showing {max_results}]")
                return True
    return False


def _glob_search(pattern: str, path: str = ".", max_results: int = 200) -> str:
    """List files matching a glob pattern (e.g. ``**/*.py``)."""
    root = _glob_root(pattern, path)
    if isinstance(root, str):
        return root
    matches = []
    for p in root.glob(pattern):
        if not contained(p):
            continue  # a symlink led out of the agent's workspace
        matches.append(str(p))
        if len(matches) >= max_results:
            matches.append(f"…[truncated, showing {max_results}]")
            break
    return "\n".join(matches) if matches else "(no matches)"


def _glob_root(pattern: str, path: str) -> Path | str:
    """The directory a glob runs in, or the ``Error:`` text. In a plan
    agent's workspace (ADR-092 slice 5) it resolves there, and a pattern
    that climbs out with ``..`` is refused."""
    try:
        root = _ws_resolve(path, write=False)
    except WorkspaceEscape as e:
        return f"Error: {e}"
    if current_workspace() is not None and ".." in Path(pattern).parts:
        return f"Error: refused: glob pattern {pattern!r} climbs out with '..' (ADR-092 slice 5)"
    if not root.exists():
        return f"Error: path not found: {path}"
    if not root.is_dir():
        return f"Error: not a directory: {path}"
    return root


def _list_dir(path: str = ".", ignore: list[str] | None = None) -> str:
    """List immediate children of a directory (non-recursive).

    Prefer glob_search or grep_search to find files by name or content.
    Respects .gitignore (when in a git repo) and skips common bulky directories.
    """
    try:
        p = _ws_resolve(path, write=False)
    except WorkspaceEscape as e:
        return f"Error: {e}"
    if not p.exists():
        return f"Error: directory not found: {path}"
    if not p.is_dir():
        return f"Error: not a directory: {path}"

    try:
        entries, notes = list_directory_entries(p.resolve(), ignore=ignore)
    except OSError as exc:
        return f"Error: {exc}"

    return format_directory_listing(p.resolve(), entries, notes=notes)


# ---------------------------------------------------------------------------
# Polyglot coding tools
# ---------------------------------------------------------------------------


def _detect_code_context(path: str = ".") -> str:
    """Detect local coding context for C/C++/Fortran/R/Python work."""
    try:
        return detect_code_context_text(path)
    except Exception as e:
        return f"Error detecting code context: {e}"


def _detect_language_servers(path: str = ".", language: str | None = None) -> str:
    """Detect available language servers for semantic coding support."""
    try:
        return detect_language_servers_text(path, language=language)
    except Exception as e:
        return f"Error detecting language servers: {e}"


def _start_language_servers(path: str = ".", language: str | None = None) -> str:
    """Start available language-server processes for the current project."""
    try:
        return start_language_servers(path, language=language)
    except SandboxError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error starting language servers: {e}"


def _compile_code(
    path: str = ".",
    language: str | None = None,
    source_path: str | None = None,
    target: str | None = None,
    output_path: str | None = None,
    timeout: int = 120,
) -> str:
    """Compile or syntax-check code in the current workspace."""
    try:
        return compile_code(
            path,
            language=language,
            source_path=source_path,
            target=target,
            output_path=output_path,
            timeout=timeout,
        )
    except SandboxError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error compiling code: {e}"


def _run_code(
    path: str = ".",
    language: str | None = None,
    source_path: str | None = None,
    executable: str | None = None,
    args: str | None = None,
    timeout: int = 60,
) -> str:
    """Run local code or a compiled executable."""
    try:
        return run_code(
            path,
            language=language,
            source_path=source_path,
            executable=executable,
            args=args,
            timeout=timeout,
        )
    except SandboxError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error running code: {e}"


def _test_code(
    path: str = ".",
    language: str | None = None,
    target: str | None = None,
    timeout: int = 180,
) -> str:
    """Run local tests using detected project conventions."""
    try:
        return test_code(path, language=language, target=target, timeout=timeout)
    except SandboxError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error testing code: {e}"


# ---------------------------------------------------------------------------
# HPC tools
# ---------------------------------------------------------------------------

# D-37 / V-01 (2026-09-22): the companion never talks to a scheduler from a
# workstation — these tools are CLUSTER-VENUE scoped (login node, where
# sbatch/squeue exist). Off-cluster they refuse with the durable path named.
_HPC_OFF_CLUSTER_REFUSAL = (
    "unavailable off-cluster (D-37): the companion does not talk to a "
    "scheduler directly from a workstation. Use the durable UMRS path "
    "(r4 workflow trigger / r4 endpoint up), or run on a "
    "cluster login node (cluster venue)."
)


def _off_cluster(tool_name: str) -> str | None:
    """Named refusal when the Slurm CLI is absent (workstation side)."""
    if shutil.which("sbatch") is not None:
        return None
    return f"Error: {tool_name} {_HPC_OFF_CLUSTER_REFUSAL}"


# ADR-075 G2 ("workflow on"): when armed, compute must ride the UMRS
# durable lane — the legacy local lane is refused by name, never silent.
_WORKFLOW_ARMED = False


def _identity_project_id(client: Any) -> str | None:
    """The caller's identity project ("" → None), tolerant of duck clients."""
    cfg = getattr(client, "config", None) or getattr(client, "_config", None)
    value = str(getattr(cfg, "project_id", "") or "")
    return value or None


def set_workflow_armed_flag(armed: bool) -> None:
    """Mirror of the session's workflow arm onto this process's tool guards
    (same facade pattern as the knowledge-write gate)."""
    global _WORKFLOW_ARMED
    _WORKFLOW_ARMED = bool(armed)


def _workflow_armed_refusal(tool: str) -> str | None:
    if not _WORKFLOW_ARMED:
        return None
    return (
        f"workflow arm is ON for this session: {tool} must go through the "
        "UMRS durable lane — local execution is refused while armed "
        "(restore UMRS reachability, or /workflow off)."
    )


def _hpc_submit(script_path: str, dry_run: bool = False) -> str:
    """Submit a Slurm job script (cluster venue only — see _HPC_OFF_CLUSTER_REFUSAL)."""
    refusal = _off_cluster("hpc_submit")
    if refusal is not None:
        return refusal
    p = Path(script_path).expanduser()
    if not p.exists():
        return f"Error: script not found: {script_path}"
    if dry_run:
        return f"[DRY RUN] Would submit: {script_path}\n" + p.read_text()[:2000]
    # ROADMAP 3.3 (D-37 scheduler leg): the durable UMRS path — the client
    # hands the script to the server; the server owns sbatch via SSH.
    durable = _hpc_submit_via_umrs(str(p))
    if durable is not None:
        return durable
    armed_refusal = _workflow_armed_refusal("hpc_submit")
    if armed_refusal is not None:
        return armed_refusal
    # Fallback: local sbatch (legacy in-venue lane; the durable path is the design's).
    try:
        result = subprocess.run(
            ["sbatch", str(p)],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
        return result.stdout + result.stderr
    except FileNotFoundError:
        return "Error: sbatch not found — are you on an HPC login node?"
    except Exception as e:
        return f"Error submitting job: {e}"


_HPC_ACCESS_CODE = re.compile(r"hpcaccess\.[a-z_]+")


def _hpc_access_refusal(error: Exception) -> str | None:
    """The tool result for a site-policy refusal (ADR-112 ``hpcaccess.*``),
    or None for any other failure.

    The server reached a decision, so this is neither "unreachable" nor
    retryable: the message and its typed code go back whole (plan §3 A.3.2),
    in armed sessions too, never cut to 120 characters or replaced by retry
    advice (review 2026-11-08).
    """
    text = str(error)
    code = getattr(error, "error_code", None) or ""
    match = _HPC_ACCESS_CODE.search(f"{code} {text}")
    if match is None:
        return None
    return (
        f"Error: the site refused this job ({match.group(0)}): {text}. Nothing was "
        "submitted; retrying will not help. Ask the site operator or an "
        "administrator about the project's access to this site."
    )


def _hpc_submit_via_umrs(script_path: str) -> str | None:
    """POST an hpc_script durable job; None when UMRS is unreachable (legacy lane)."""
    try:
        from ..client import Client

        client = Client()
        data = client.post(
            f"{getattr(client, '_api_prefix', '/gw')}/jobs",
            json={
                "job_type": "hpc_script",
                "spec": {"script_path": script_path},
                "enqueue_immediately": True,
            },
        )
    except Exception as e:
        refusal = _hpc_access_refusal(e)
        if refusal is not None:
            return refusal
        # UMRS unreachable: armed sessions get None (the caller refuses by
        # name). Unarmed sessions get the honest error — nothing submitted
        # anywhere (the earlier note claimed a local-sbatch fallback; the
        # caller returned the note as-is, so sbatch never ran and nothing
        # was submitted — C-2(vi) 2026-10-19, D-37).
        if _WORKFLOW_ARMED:
            return None
        return (
            f"Error: durable UMRS submit failed ({str(e)[:120]}); nothing was "
            "submitted. Retry once the gateway is reachable, or run from an "
            "HPC login node for the legacy sbatch lane."
        )
    job_id = str(data.get("job_id") or "")
    if not job_id:
        return f"Error: UMRS returned no job id for {script_path}"
    return (
        f"Submitted via UMRS durable job {job_id} (hpc_script). Track with hpc_job_status {job_id}."
    )


def _hpc_job_status(job_id: str | None = None) -> str:
    """Check Slurm job status (cluster venue only — see _HPC_OFF_CLUSTER_REFUSAL)."""
    refusal = _off_cluster("hpc_job_status")
    if refusal is not None:
        return refusal
    # Durable-first: a UMRS durable id (``job-<hex>``, the submit tool's
    # return) or a numeric id resolves via UMRS; other ids stay legacy.
    durable = _hpc_durable_status(job_id)
    if durable is not None:
        return durable
    cmd = ["squeue", "--me", "--format=%i %j %T %M %P %l"]
    if job_id:
        cmd = ["squeue", "-j", str(job_id), "--format=%i %j %T %M %P %l"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL
        )
        return result.stdout or "(no jobs found)"
    except FileNotFoundError:
        return "Error: squeue not found — are you on an HPC login node?"
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


# UMRS durable job ids are ``job-<12 hex>`` (umrs jobs.models.generate_job_id);
# bare digits stay accepted (numeric ids the gateway may also resolve).
_DURABLE_JOB_ID = re.compile(r"(?:job-[0-9a-f]+|\d+)")


def _hpc_durable_status(job_id: str | None) -> str | None:
    """GET the durable job's status; None when the id is not durable/reachable."""
    if not job_id or not _DURABLE_JOB_ID.fullmatch(job_id.strip()):
        return None
    try:
        from ..client import Client

        client = Client()
        data = client.get(f"{getattr(client, '_api_prefix', '/gw')}/jobs/{job_id.strip()}")
    except Exception:
        return None  # not a durable id (or UMRS down) → the legacy squeue lane
    status = str(data.get("status") or "")
    if not status:
        return None
    slurm = str((data.get("spec") or {}).get("script_path") or "")
    return (
        f"Durable job {job_id}: {status}"
        + (f" (script: {slurm})" if slurm else "")
        + " — status via UMRS (D-37); use `r4 job describe` for attempts."
    )


_MCP_SCHEMA_MAX_CHARS = 600


def _mcp_tool_line(tool: Any) -> str:
    """One ``mcp_list_tools`` row: name, description and the input schema
    (``MCPTool.parameters``) as compact JSON, cut at a bounded length."""
    line = f"- {tool.name}: {tool.description}"
    schema = getattr(tool, "parameters", None)
    if not schema:
        return line
    text = json.dumps(schema, separators=(",", ":"), sort_keys=True, default=str)
    if len(text) > _MCP_SCHEMA_MAX_CHARS:
        text = text[:_MCP_SCHEMA_MAX_CHARS] + "...(truncated)"
    return f"{line}\n  input schema: {text}"


def _client_for_site(
    client: Client | None,
    site: str | None,
    handle: Any = "",
    *,
    act: bool = False,
    lenient: bool = False,
) -> Any:
    """The ``SiteTarget`` for a tool call (``site=`` and/or ``id@site``).

    Fails closed on an unknown site, never running at home instead (X-a;
    before 2026-10-24 ``_resolve_source_client`` returned the active
    deployment's client for a typo or a public source). ``act`` (a call
    that starts or changes work) also refuses a non-home site that is not
    eligible; reads only resolve the name. ``lenient`` keeps an ``@`` that
    names no site inside the id (file keys). See ``site_routing.route``.
    """
    from .site_routing import route

    return route(client, site, handle, act=act, lenient=lenient)


_SITE_PARAM: dict[str, str] = {
    "type": "string",
    "description": "HPC site (see hpc_sites); default: this session's",
}
_SOURCE_ALIAS_PARAM: dict[str, str] = {
    "type": "string",
    "description": "Alias of site (a registered platform source name)",
}


def _site_json(payload: Any, target: Any) -> str:
    """A JSON tool result tagged with its site (and ``id@site`` handle)."""
    from .site_routing import tag

    body = dict(payload) if isinstance(payload, dict) else {"result": payload}
    return json.dumps(tag(body, target), indent=2, default=str)


def _endpoint_line(ep: dict[str, Any], site: str) -> str:
    return (
        f"- {ep.get('instance_id', '?')}@{site}  "
        f"model={ep.get('model', '?')}  "
        f"tier={ep.get('tier', '?')}  "
        f"status={ep.get('status', '?')}"
    )


def _job_line(job: Any, site: str) -> str:
    """One job row; ``site`` is the site that runs it (from its venue), ""
    when that is none of the user's sites (then no handle is offered)."""
    handle = f"{job.job_id}@{site}" if site else str(job.job_id)
    where = "" if site else f"  venue={getattr(job, 'venue', '')} (not one of your sites)"
    return f"- {handle}  type={job.job_type}  status={job.status}  created={job.created_at}{where}"


def _job_payload(job: Any, site: str) -> dict[str, Any]:
    """``job_status``'s JSON, labelled with the site that runs the job."""
    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "created_at": str(job.created_at),
        "venue": getattr(job, "venue", None),
        "attempts": job.attempts,
        "site": site or None,
    }
    if site:
        payload["handle"] = f"{job.job_id}@{site}"
    return payload


def _one_site_session(client: Any) -> bool:
    from ..sites import site_sources

    try:
        return client is not None and len(site_sources(getattr(client, "config", None))) < 2
    except Exception:  # noqa: BLE001 — unsure: keep the full schemas
        return False


def _fit_site_params(tools: list[dict[str, Any]], client: Any) -> list[dict[str, Any]]:
    """One-site sessions: drop the ``site=`` parameter from the schemas.

    Review P5 (2026-10-24): the multi-site parameters cost every user about
    900 tokens a turn though only a session with two or more sites can use
    them (the ``<hpc_sites>`` block has the same gate). ``hpc_sites`` stays:
    the HPC skill names it, and it shows a one-site user's mode and account.
    """
    if not _one_site_session(client):
        return tools
    from .workflow_tools import _SITE_PARAM as _WF_SITE_PARAM

    for tool in tools:
        props = (tool.get("parameters") or {}).get("properties") or {}
        if props.get("site") is _SITE_PARAM or props.get("site") is _WF_SITE_PARAM:
            del props["site"]
    return tools


def _hpc_sites_json(client: Any, *, refresh: bool = False) -> str:
    """``hpc_sites``: the inventory rows as JSON (multi-HPC plan S2)."""
    from ..sites import inventory

    try:
        rows = inventory(getattr(client, "config", None), refresh=refresh)
    except Exception as e:  # noqa: BLE001 — the tool reports, never raises
        return f"Error listing HPC sites: {e}"
    return json.dumps({"sites": [r.to_dict() for r in rows]}, indent=2, default=str)


def _project_scaffold(
    name: str,
    path: str = ".",
    template: str = "python",
) -> str:
    """Scaffold a new research project with standard directory layout."""
    root = Path(path).expanduser() / name
    if root.exists():
        return f"Error: directory already exists: {root}"

    try:
        if template == "python":
            _scaffold_python(root, name)
        elif template == "research":
            _scaffold_research(root, name)
        elif template == "hpc":
            _scaffold_hpc(root, name)
        else:
            return f"Error: unknown template '{template}'. Known: python, research, hpc"

        tree = _scaffold_tree(root)
        return f"Created {template} project '{name}' at {root}\n\n{tree}"
    except Exception as exc:
        return f"Error scaffolding project: {exc}"


def _scaffold_python(root: Path, name: str) -> None:
    dirs = [
        root / "src" / name,
        root / "tests",
        root / "docs",
        root / "data",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    (root / "README.md").write_text(
        f"# {name}\n\nProject description here.\n\n"
        f"## Installation\n\n```bash\npip install -e .\n```\n\n"
        f"## Testing\n\n```bash\npytest tests/\n```\n"
    )
    (root / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n*.egg-info/\n.pytest_cache/\n.env\n.venv/\nbuild/\ndist/\n"
    )
    (root / "pyproject.toml").write_text(
        f'[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n\n'
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
        f'description = ""\nreadme = "README.md"\nrequires-python = ">=3.10"\n'
        f'dependencies = []\n\n[project.optional-dependencies]\ndev = ["pytest"]\n\n'
        f'[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    )
    (root / "Makefile").write_text(
        ".PHONY: test lint format\n\n"
        "test:\n\tpytest tests/ -v\n\n"
        "lint:\n\truff check src/ tests/\n\n"
        "format:\n\truff format src/ tests/\n"
    )
    (root / "src" / name / "__init__.py").write_text("")
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / "test_placeholder.py").write_text("def test_example():\n    assert True\n")


def _scaffold_research(root: Path, name: str) -> None:
    dirs = [
        root / "data",
        root / "notebooks",
        root / "scripts",
        root / "results",
        root / "literature",
        root / "drafts",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    (root / "README.md").write_text(
        f"# {name}\n\nResearch project workspace.\n\n"
        f"## Structure\n\n"
        f"- `data/` — raw and processed data\n"
        f"- `notebooks/` — Jupyter notebooks for exploration\n"
        f"- `scripts/` — analysis and processing scripts\n"
        f"- `results/` — figures, tables, and outputs\n"
        f"- `literature/` — papers and references\n"
        f"- `drafts/` — paper drafts and notes\n"
    )
    (root / ".gitignore").write_text(
        "data/*.csv\ndata/*.nc\ndata/*.h5\n"
        "results/*.png\nresults/*.pdf\n"
        ".ipynb_checkpoints/\n__pycache__/\n"
        ".env\n"
    )


def _scaffold_hpc(root: Path, name: str) -> None:
    dirs = [
        root / "src",
        root / "jobs",
        root / "containers",
        root / "scripts",
        root / "results",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    (root / "README.md").write_text(
        f"# {name}\n\nHPC project workspace.\n\n"
        f"## Structure\n\n"
        f"- `src/` — source code\n"
        f"- `jobs/` — Slurm batch scripts\n"
        f"- `containers/` — Singularity/Apptainer definitions\n"
        f"- `scripts/` — build and utility scripts\n"
        f"- `results/` — job outputs and artefacts\n"
    )
    (root / ".gitignore").write_text(
        "results/*.out\nresults/*.err\n*.sif\n*.simg\n__pycache__/\n.env\n"
    )
    (root / "Makefile").write_text(
        ".PHONY: build submit clean\n\n"
        "build:\n\t# Add build steps here\n\n"
        "submit:\n\tsbatch jobs/template.sh\n\n"
        "clean:\n\trm -f results/*.out results/*.err\n"
    )
    (root / "jobs" / "template.sh").write_text(
        "#!/bin/bash\n"
        "#SBATCH --job-name={name}\n"
        "#SBATCH --partition=gpu\n"
        "#SBATCH --gpus=1\n"
        "#SBATCH --time=01:00:00\n"
        "#SBATCH --output=results/%j.out\n"
        "#SBATCH --error=results/%j.err\n\n"
        "module load singularity\n"
        "singularity exec container.sif python src/main.py\n"
    )
    (root / "containers" / "Dockerfile").write_text(
        "FROM ubuntu:22.04\n\n"
        "RUN apt-get update && apt-get install -y build-essential\n"
        "WORKDIR /app\n"
        "COPY . /app\n"
        'CMD ["bash"]\n'
    )
    (root / "containers" / "singularity.def").write_text(
        "Bootstrap: docker\nFrom: ubuntu:22.04\n\n"
        "%post\n"
        "  apt-get update && apt-get install -y build-essential\n\n"
        "%runscript\n"
        "  python /app/src/main.py\n\n"
    )


def _scaffold_tree(root: Path, prefix: str = "") -> str:
    lines = []
    entries = sorted(root.iterdir(), key=lambda e: (e.is_file(), e.name))
    for idx, entry in enumerate(entries):
        is_last = idx == len(entries) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")
        if entry.is_dir():
            extension = "    " if is_last else "│   "
            lines.append(_scaffold_tree(entry, prefix=prefix + extension))
    return "\n".join(lines)


def _build_contract_start_tool(
    goal: str,
    features: list[dict[str, Any]],
    smoke_command: str | None = None,
    force: bool = False,
) -> str:
    from .build_contract import start_build_contract

    result = start_build_contract(
        os.getcwd(), goal, features, smoke_command=smoke_command, force=force
    )
    return json.dumps(result, indent=2)


def _build_contract_status_tool() -> str:
    from .build_contract import contract_status

    return json.dumps(contract_status(os.getcwd()), indent=2)


def _build_contract_pass_tool(feature_id: str, evidence: str) -> str:
    from .build_contract import mark_feature_passed

    return json.dumps(mark_feature_passed(os.getcwd(), feature_id, evidence), indent=2)


def _core_tools() -> list[dict[str, Any]]:
    """Return the core tool definitions that do not require a client."""
    from r4_contracts.agent_tool_playbook import (
        PLATFORM_GUIDE_TOOL_DESCRIPTION,
        PLATFORM_GUIDE_TOPICS,
        platform_guide,
    )

    def _platform_guide(topic: str = "all") -> str:
        try:
            return platform_guide(topic)
        except ValueError as exc:
            return f"Error: {exc}"

    return [
        _tool(
            "platform_guide",
            PLATFORM_GUIDE_TOOL_DESCRIPTION,
            {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "enum": list(PLATFORM_GUIDE_TOPICS),
                        "default": "all",
                        "description": "Playbook section to return",
                    },
                },
            },
            _platform_guide,
        ),
        _tool(
            "shell_exec",
            shell_exec_description(),
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "cwd": {"type": "string", "description": "Working directory (optional)"},
                    "timeout": {
                        "type": "integer",
                        "description": EXEC_TIMEOUT_PARAM_DESCRIPTION,
                        "default": EXEC_DEFAULT_TIMEOUT_S,
                    },
                },
                "required": ["command"],
            },
            _shell_exec,
        ),
        _tool(
            "python_exec",
            python_exec_description(),
            {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                    "timeout": {
                        "type": "integer",
                        "description": EXEC_TIMEOUT_PARAM_DESCRIPTION,
                        "default": EXEC_DEFAULT_TIMEOUT_S,
                    },
                },
                "required": ["code"],
            },
            _python_exec,
        ),
        _tool(
            "file_read",
            "Read the contents of a file",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                    "max_lines": {"type": "integer", "description": "Max lines", "default": 500},
                    "offset": {
                        "type": "integer",
                        "description": "0-based line number to start reading from",
                        "default": 0,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines to read (overrides max_lines when set)",
                    },
                },
                "required": ["path"],
            },
            _file_read,
        ),
        _tool(
            "file_write",
            "Write content to a file (creates parent directories). "
            "Relative paths resolve against the session workspace root — "
            "choose a workspace-appropriate location (e.g. src/, docs/, "
            "reports/, data-outputs/) rather than /tmp; never invent paths "
            "outside the workspace unless the user explicitly asked",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
            _file_write,
        ),
        _tool(
            "file_edit",
            "Replace a text occurrence in a file "
            "(unique by default; pass expected_count to allow N occurrences)",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_text": {"type": "string", "description": "Text to find"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                    "expected_count": {
                        "type": "integer",
                        "description": (
                            "If set, fail unless exactly this many occurrences are found"
                        ),
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
            _file_edit,
        ),
        _tool(
            "apply_patch",
            _apply_patch_texts()[0],
            {
                "type": "object",
                "properties": {
                    "patch_text": {
                        "type": "string",
                        "description": _apply_patch_texts()[1],
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Validate only; do not write",
                        "default": False,
                    },
                    "workdir": {
                        "type": "string",
                        "description": "Directory the patch applies to (default: cwd)",
                    },
                },
                "required": ["patch_text"],
            },
            _apply_patch,
        ),
        _tool(
            "list_dir",
            (
                "List immediate children of one directory (non-recursive). "
                "Respects .gitignore in git repos. Prefer glob_search or grep_search "
                "to find files by name or content."
            ),
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path", "default": "."},
                    "ignore": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional glob patterns to exclude (e.g. '*.log', 'temp_*')",
                    },
                },
            },
            _list_dir,
        ),
        _tool(
            "grep_search",
            "Search files for a pattern (regex by default). Uses ripgrep when available.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Pattern to search"},
                    "path": {"type": "string", "description": "Root path", "default": "."},
                    "regex": {
                        "type": "boolean",
                        "description": "Treat pattern as regex",
                        "default": True,
                    },
                    "case_insensitive": {"type": "boolean", "default": False},
                    "max_results": {"type": "integer", "default": 200},
                },
                "required": ["pattern"],
            },
            _grep_search,
        ),
        _tool(
            "glob_search",
            "Find files by glob pattern (e.g. **/*.py)",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern"},
                    "path": {"type": "string", "default": "."},
                    "max_results": {"type": "integer", "default": 200},
                },
                "required": ["pattern"],
            },
            _glob_search,
        ),
        _tool(
            "detect_code_context",
            "Detect local coding languages, build systems, toolchains, and suggested commands",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File or directory path",
                        "default": ".",
                    },
                },
            },
            _detect_code_context,
        ),
        _tool(
            "detect_language_servers",
            "Detect available and running language servers for semantic coding support",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File or directory path",
                        "default": ".",
                    },
                    "language": {"type": "string", "description": "Optional language override"},
                },
            },
            _detect_language_servers,
        ),
        _tool(
            "start_language_servers",
            "Start available language-server processes for supported local languages",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project root or source path",
                        "default": ".",
                    },
                    "language": {"type": "string", "description": "Optional language override"},
                },
            },
            _start_language_servers,
        ),
        _tool(
            "compile_code",
            "Compile or syntax-check C, C++, Fortran, R, or Python code "
            "using local project conventions",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project root or source file",
                        "default": ".",
                    },
                    "language": {"type": "string", "description": "Optional language override"},
                    "source_path": {"type": "string", "description": "Optional source file path"},
                    "target": {"type": "string", "description": "Optional make/cmake target"},
                    "output_path": {
                        "type": "string",
                        "description": "Optional compiled executable path",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds",
                        "default": 120,
                    },
                },
            },
            _compile_code,
        ),
        _tool(
            "run_code",
            "Run local code or a compiled executable for C, C++, Fortran, R, or Python",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project root, source file, or executable",
                        "default": ".",
                    },
                    "language": {"type": "string", "description": "Optional language override"},
                    "source_path": {"type": "string", "description": "Optional source file path"},
                    "executable": {"type": "string", "description": "Optional executable path"},
                    "args": {"type": "string", "description": "Optional command-line arguments"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds",
                        "default": 60,
                    },
                },
            },
            _run_code,
        ),
        _tool(
            "test_code",
            "Run detected project tests for C, C++, Fortran, R, or Python code",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project root or source path",
                        "default": ".",
                    },
                    "language": {"type": "string", "description": "Optional language override"},
                    "target": {"type": "string", "description": "Optional make test target"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds",
                        "default": 180,
                    },
                },
            },
            _test_code,
        ),
        _tool(
            "hpc_submit",
            "Submit a Slurm batch job script (cluster venue; refuses off-cluster, D-37)",
            {
                "type": "object",
                "properties": {
                    "script_path": {"type": "string", "description": "Path to the .sh script"},
                    "dry_run": {"type": "boolean", "description": "Preview only", "default": False},
                },
                "required": ["script_path"],
            },
            _hpc_submit,
        ),
        _tool(
            "hpc_job_status",
            "Check Slurm job queue status (cluster venue; refuses off-cluster, D-37)",
            {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Job ID (optional, default: all my jobs)",
                    },
                },
            },
            _hpc_job_status,
        ),
        _tool(
            "project_scaffold",
            "Scaffold a new research project (python, research, or hpc template)",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name"},
                    "path": {"type": "string", "description": "Target directory", "default": "."},
                    "template": {
                        "type": "string",
                        "description": "Template type",
                        "enum": ["python", "research", "hpc"],
                        "default": "python",
                    },
                },
                "required": ["name"],
            },
            _project_scaffold,
        ),
        _tool(
            "build_contract_start",
            "Start a durable multi-session build contract: records the goal and a feature list "
            "(JSON; every feature starts failing) under .r4/build/, so later sessions resume "
            "from the contract instead of lost context. Use for any build spanning many turns "
            "or sessions. Features cannot be edited or deleted afterwards by design.",
            {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "What the finished build is"},
                    "features": {
                        "type": "array",
                        "description": "Feature list; each has description and optional steps[]",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "steps": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["description"],
                        },
                    },
                    "smoke_command": {
                        "type": "string",
                        "description": "Optional basic end-to-end check (sh), run before new work",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Replace an active contract (default false — refuses)",
                    },
                },
                "required": ["goal", "features"],
            },
            _build_contract_start_tool,
        ),
        _tool(
            "build_contract_status",
            "Show the active build contract: passing/total, the next failing feature, and the "
            "recorded smoke command. Read this before choosing work in a multi-session build.",
            {"type": "object", "properties": {}},
            _build_contract_status_tool,
        ),
        _tool(
            "build_contract_pass",
            "Mark ONE build-contract feature passing, with evidence (the test/run/diff that "
            "proves it). One-way: already-passing features reject. The ONLY allowed change to "
            "a contract's feature list — never edit features.json directly.",
            {
                "type": "object",
                "properties": {
                    "feature_id": {"type": "string", "description": "e.g. F-03"},
                    "evidence": {
                        "type": "string",
                        "description": "Name of the check proving the feature works",
                    },
                },
                "required": ["feature_id", "evidence"],
            },
            _build_contract_pass_tool,
        ),
    ]


def _client_lab_tools(client: Client, *, session_model: str | None = None) -> list[dict[str, Any]]:  # noqa: C901
    """Return lab catalog and agentic-search tool definitions.

    Extracted from ``_client_tools`` to keep builder functions under the
    complexity/length budget enforced in CI.
    """
    tools: list[dict[str, Any]] = []

    # -------------------------------------------------------------------
    # Data catalog direct tools. Thin, read-only wrappers over the gateway
    # catalog routes so the companion can do a one-shot metadata grep or a
    # byte-bounded ground-truth read without spinning up the full agentic
    # search planner. See ADR-011.
    # -------------------------------------------------------------------
    def _lab_catalog_search(
        query: str,
        limit: int = 20,
        modality: str | None = None,
        state: str | None = None,
        tag: str | None = None,
        prefix: str | None = None,
    ) -> str:
        try:
            from r4.search.catalog_format import format_catalog_search_response

            params: dict[str, object] = {"q": query, "limit": limit}
            if modality:
                params["modality"] = modality
            if state:
                params["state"] = state
            if tag:
                params["tag"] = tag
            if prefix:
                params["prefix"] = prefix

            data = client.get(
                f"{getattr(client, '_api_prefix', '/gw')}/catalog/search", params=params
            )
            results = data.get("results", []) if isinstance(data, dict) else []
            payload = format_catalog_search_response(query, results)
            return json.dumps(payload, indent=2, default=str)
        except Exception as e:
            return f"Error querying catalog: {e}"

    tools.append(
        _tool(
            "lab_catalog_search",
            (
                "Search uploaded lab data catalog metadata to discover s3_keys. "
                "Returns summaries, tags, modality, suggested_read — metadata only. "
                "Pick a hit, then call lab_file_read with its s3_key."
            ),
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Metadata search query (name, tag, modality)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results",
                        "default": 20,
                    },
                    "modality": {
                        "type": "string",
                        "description": "Filter by detected_modality",
                    },
                    "state": {
                        "type": "string",
                        "description": "Filter by catalog state",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Filter rows containing this tag",
                    },
                    "prefix": {
                        "type": "string",
                        "description": "Filter s3_key prefix within the project",
                    },
                },
                "required": ["query"],
            },
            _lab_catalog_search,
        )
    )

    def _lab_file_read(
        s3_key: str,
        offset: int = 0,
        length: int = 1_048_576,
        **kwargs: Any,
    ) -> str:
        try:
            from r4.search.catalog_format import (
                format_lab_file_read_response,
                map_read_as_to_source,
            )

            gateway_source = map_read_as_to_source(kwargs.get("as", "content"))
            data = client.get(
                f"{getattr(client, '_api_prefix', '/gw')}/catalog/read",
                params={
                    "s3_key": s3_key,
                    "offset": offset,
                    "length": length,
                    "source": gateway_source,
                },
            )
            if not isinstance(data, dict):
                return "Unexpected catalog read response."
            payload = format_lab_file_read_response(data)
            return json.dumps(payload, indent=2, default=str)
        except ValueError as e:
            return f"Error reading lab file: {e}"
        except Exception as e:
            return f"Error reading lab file: {e}"

    tools.append(
        _tool(
            "lab_file_read",
            (
                "Read an uploaded lab file by s3_key from lab_catalog_search. "
                "Use as=content (default); as=extracted_text or as=raw_bytes when needed."
            ),
            {
                "type": "object",
                "properties": {
                    "s3_key": {
                        "type": "string",
                        "description": "S3 object key (from lab_catalog_search)",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Byte offset to start reading",
                        "default": 0,
                    },
                    "length": {
                        "type": "integer",
                        "description": "Max bytes to read",
                        "default": 1_048_576,
                    },
                    "as": {
                        "type": "string",
                        "description": "content (default), extracted_text, or raw_bytes",
                        "default": "content",
                    },
                },
                "required": ["s3_key"],
            },
            _lab_file_read,
        )
    )

    # -------------------------------------------------------------------
    # Agentic search (bug.memo C1, 2026-04-25). Lets the companion reach
    # beyond RAG-indexed content into the full search pipeline (web +
    # code + RAG) without shelling out to `r4 search`.
    # -------------------------------------------------------------------
    def _search_agentic(
        query: str,
        depth: str = "shallow",
        scope: str = "all",
        project_id: str | None = None,
    ) -> str:
        try:
            import asyncio

            from ..search import AgenticSearch, SearchConfig
            from ..search.core import SearchDepth, SearchScope

            resolved_project = project_id or getattr(client._config, "project_id", None)
            cfg = SearchConfig(
                project_id=resolved_project,
                depth=SearchDepth(depth),
                scope=SearchScope(scope),
                # Synthesis/planner legs ride the SESSION's effective model —
                # never the profile default (2026-09-29 rivault/kimi-k3 class).
                model=session_model,
            )
            agent = AgenticSearch(client, cfg)

            try:
                result = asyncio.run(agent.search(query))
            except RuntimeError:
                # Already inside an event loop (TUI): create a nested one.
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(agent.search(query))
                finally:
                    loop.close()

            payload = {
                "answer": getattr(result, "answer", str(result)),
                "confidence": getattr(result, "confidence", None),
                "warnings": getattr(result, "warnings", []),
                "citations": getattr(result, "citations", []),
                "sources": [s.to_detail_dict() for s in getattr(result, "sources", [])[:10]],
            }
            return json.dumps(payload, indent=2, default=str)
        except Exception as e:
            return f"Error running agentic search: {e}"

    tools.append(
        _tool(
            "search_agentic",
            (
                "Multi-hop agentic search over lab catalog, RAG, web, and code. "
                "Prefer this over search_docs when the question may involve "
                "uploaded lab files or sources outside the RAG index."
            ),
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language query",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID (uses current project if omitted)",
                    },
                    "depth": {
                        "type": "string",
                        "enum": ["shallow", "moderate", "deep"],
                        "description": "How many search hops to attempt",
                        "default": "shallow",
                    },
                    "scope": {
                        "type": "string",
                        "enum": [
                            "rag_only",
                            "web_only",
                            "code_only",
                            "lab_only",
                            "all",
                        ],
                        "description": (
                            "Source scope: all includes lab catalog when project is set; "
                            "lab_only restricts to catalog + RAG"
                        ),
                        "default": "all",
                    },
                },
                "required": ["query"],
            },
            _search_agentic,
        )
    )

    return tools


def _rag_tool_error_message(action: str, exc: Exception) -> str:
    """Typed failure text for RAG tools (finding F.2, 2026-09-05).

    A bare 500 from ``/gw/rag/upload`` told the agent nothing actionable.
    When the failure is a 5xx, the embedding backend is unavailable: say so
    in a parseable, agent-readable form and state the queue outcome honestly
    (the upload failed — nothing was queued).
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 500:
        return (
            f"[embedding_backend_unavailable] RAG {action} failed with gateway status {status}. "
            "The embedding/indexing backend is down; the document was NOT queued. "
            "Wait for recovery (check `job_list` / `platform_health`), then retry — "
            "or answer from already-indexed knowledge via search_docs."
        )
    return f"Error {action} for RAG: {exc}"


# search_docs / rag_query output (ADR-093 D-10): the server ranks, cuts and
# judges sufficiency; these only render what it returned.
_SEARCH_DOCS_PASSAGE_CHARS = 1200
_RAG_ANSWER_META_KEYS = ("citations", "abstained", "uncited")


def _format_search_docs(result: Any, evidence: Any = None) -> str:
    """'[S<n>] <source_name> › <section> (score) id=<source_id>' plus the text,
    or the server's insufficiency line. The labels are the session ledger's
    (K1.4); a tool built without a session numbers its hits '[n]'."""
    status = result.sufficiency.status
    if status == "insufficient" or not result.passages:
        return _insufficiency_line(result)
    passages = list(result.passages)
    tags = label_passages(evidence, passages) or [str(n) for n in range(1, len(passages) + 1)]
    entries = [_search_docs_entry(tag, p) for tag, p in zip(tags, passages)]
    if status == "partial":
        entries.insert(0, "Partial match: these passages cover the query only in part.")
    return "\n\n".join(entries)


def _search_docs_entry(tag: Any, passage: Any) -> str:
    from r4_contracts.context_pack import sanitize_name, sanitize_text

    from ..rag import passage_score

    head = sanitize_name(passage.source_name or passage.source_id)
    if passage.section:
        head = f"{head} › {sanitize_name(passage.section)}"
    text = sanitize_text(passage.text).strip()
    if len(text) > _SEARCH_DOCS_PASSAGE_CHARS:
        text = text[:_SEARCH_DOCS_PASSAGE_CHARS] + " …"
    score = passage_score(passage)
    return f"[{tag}] {head} ({score:.3f}) id={passage.source_id}\n{text}"


def _insufficiency_line(result: Any) -> str:
    sufficiency = result.sufficiency
    measure = sufficiency.basis
    if sufficiency.value is not None and sufficiency.threshold is not None:
        measure = f"{sufficiency.basis} {sufficiency.value:.2f} < {sufficiency.threshold:.2f}"
    facts = [sufficiency.status, measure, sufficiency.detail, f"searched {result.searched}"]
    reason = "; ".join(fact for fact in facts if fact)
    return f"No passage in this project answers this query ({reason})."


def _rag_query_payload(result: Any, evidence: Any = None, project_id: str = "") -> dict[str, Any]:
    """The answer, its sources by name and section, and the server's citation
    map. With a session ledger each source carries its label (``sid``, K1.4),
    recorded under the project queried (an explicit ``project_id`` included:
    the label used to say '' and opening it fetched in the current project)."""
    sources = list(getattr(result, "sources", []) or [])[:10]
    entries = [_rag_source_entry(s) for s in sources]
    passages = [passage_from_rag_source(n, s) for n, s in enumerate(sources, start=1)]
    labels = label_passages(evidence, passages, project_id=project_id)
    for entry, sid in zip(entries, labels):
        entry["sid"] = sid
    payload: dict[str, Any] = {"answer": getattr(result, "answer", str(result)), "sources": entries}
    payload.update(_rag_answer_metadata(result))
    return payload


def _rag_source_entry(source: Any) -> dict[str, Any]:
    meta = getattr(source, "metadata", None) or {}
    document_id = getattr(source, "document_id", None)
    name = getattr(source, "source_name", None) or meta.get("source_name")
    section = getattr(source, "section", None) or meta.get("section")
    return {
        "document_id": document_id,
        "source_name": name or document_id,
        "section": section or "",
        "score": getattr(source, "score", None),
        "content": str(getattr(source, "content", source))[:400],
    }


def _rag_answer_metadata(result: Any) -> dict[str, Any]:
    """``citations``/``abstained``/``uncited`` as the server sent them: response
    metadata first, then the response itself, then the first source's metadata."""
    found: dict[str, Any] = {}
    for container in _rag_answer_containers(result):
        for key in _RAG_ANSWER_META_KEYS:
            if key in container:
                found.setdefault(key, container[key])
    return found


def _rag_answer_containers(result: Any) -> list[dict[str, Any]]:
    raw = getattr(result, "raw_response", None)
    raw = raw if isinstance(raw, dict) else {}
    first = list(getattr(result, "sources", None) or [])[:1]
    candidates = [raw.get("metadata"), raw, *(getattr(s, "metadata", None) for s in first)]
    return [c for c in candidates if isinstance(c, dict)]


# Tool families registered after the core tools (order = discovery order).
_TOOL_FAMILY_BUILDERS = (
    build_workflow_tools,
    build_job_watch_tools,
    build_project_tools,
    build_research_loop_tools,
    build_knowledge_tools,
    build_first_class_mcp_tools,
    build_decision_tools,
)


def _retrieve_reader(session_model: SessionModel, client: Any = None) -> dict[str, str | None]:
    """``profile`` and ``reader_model`` for search_docs' retrieve (ADR-093 D-14).

    The session model reads what the search returns, so UMRS sizes its cut
    for it: rikyu's kimi-k3 or glm-5.3 gets the large cut (6 passages, the
    medium size since ADR-093 MD-14, 2026-10-13; 10 before), the
    stand-alone llama3.2:3b the small one, exactly as before. The profile is
    the model's DETECTED scale on ``client``'s route (``r4.model_scale``,
    memoised, so the router's detection serves it), as for the router's pack.
    Read per call, so a ``/model`` switch sizes the next search. No session
    model sends nothing, and the site's default applies.
    """
    try:
        model = _session_model_now(session_model)
    except Exception:  # noqa: BLE001 - an accessor failure only loses the sizing
        return {}
    if not model:
        return {}
    from .retrieval_router import reader_fields

    return reader_fields(model, client=client)


def _family_tools(
    build_family: Callable[..., list[dict[str, Any]]],
    client: Client,
    reader: SessionModel,
    web_access: Any = None,
    evidence: Any = None,
) -> list[dict[str, Any]]:
    """One tool family's tools. The knowledge family gets the session's
    reader: ``knowledge_query`` sizes its K3 limit by it per call, as
    ``search_docs`` sizes its cut (ADR-093 D-14), and the session's evidence
    ledger (K1.4); the research loop family gets the session's web-access
    accessor (a server plan loop's runs use the user's choice, ADR-099 D-2);
    the others take the client."""
    if build_family is build_knowledge_tools:
        return build_family(client, reader=reader, evidence=evidence)
    if build_family is build_research_loop_tools:
        return build_family(client, web_access=web_access, reader=reader)
    return build_family(client)


def _client_tools(  # noqa: C901
    client: Client,
    *,
    session_model: str | None = None,
    reader: SessionModel = None,
    web_access: Any = None,
    evidence: Any = None,
) -> list[dict[str, Any]]:
    """Return tool definitions that require a connected r4 client.

    ``reader`` is the session's model accessor (else ``session_model``):
    search_docs reads it per call to size the server's cut (ADR-093 D-14).
    ``web_access`` is the session's web-access accessor (research loop tools).
    ``evidence`` is the session's :class:`~r4.companion.tool_evidence.ToolEvidence`:
    the knowledge tools record what they return in its ledger (K1.4).
    """
    tools: list[dict[str, Any]] = []
    reading = reader if reader is not None else session_model

    def _search_docs(query: str, top_k: int | None = None) -> str:
        # ADR-093 D-10: the retrieve verb (no hidden answer generation); the
        # server ranks, cuts and judges sufficiency, this only renders it.
        try:
            from ..graph_scope import retrieve_graph_args

            # ADR-108 D-8: the user's graph constraints, as knowledge_query.
            scope = retrieve_graph_args(client)
            result = client.rag.retrieve(
                query,
                k_max=top_k,
                **_retrieve_reader(reading, client),
                graphs=scope.get("graphs"),
                exclude_graphs=scope.get("exclude_graphs"),
            )
        except Exception as e:  # noqa: BLE001 - reported to the agent
            return f"Error searching docs: {e}"
        return _format_search_docs(result, evidence)

    tools.append(
        _tool(
            "search_docs",
            (
                "Search project documents. Returns the relevant passages best first as "
                "'[label] <file> › <section> (score) id=<passage id>' with their text "
                "(cite them by label), or says that no passage answers the query."
            ),
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "top_k": {
                        "type": "integer",
                        # No number here (r4-scale-router-13): omitted, the
                        # server sizes the bound by the reading model's D-14
                        # profile (4 small, 6 medium or unknown, 6 large
                        # since ADR-093 MD-14, 2026-10-13; 10 before), and
                        # the session's model can change per call.
                        "description": (
                            "Upper bound on passages (1-12). The server keeps only "
                            "the relevant ones. Omit it to get the server's bound "
                            "for the model reading the results (larger for a larger "
                            "model); set it only to get fewer."
                        ),
                    },
                },
                "required": ["query"],
            },
            _search_docs,
        )
    )
    tools.append(build_open_passage_tool(client, evidence))

    def _list_models() -> str:
        try:
            from ..model_routing import count_registered_sources, list_models_across_sources

            if count_registered_sources() > 1:
                source_models = list_models_across_sources()
                if not source_models:
                    return "No models available."
                lines = [f"- {sm.qualified}  (via {sm.source_name})" for sm in source_models[:40]]
                return "\n".join(lines)
            models = client.list_models()
            if not models:
                return "No models available."
            return "\n".join(f"- {m}" for m in models[:30])
        except Exception as e:
            return f"Error listing models: {e}"

    tools.append(
        _tool(
            "list_models",
            "List available AI models across all registered sources "
            "(use model@source to target a specific source)",
            {"type": "object", "properties": {}},
            _list_models,
        )
    )

    # -------------------------------------------------------------------
    # Platform tools (endpoint, job, workflow, MCP)
    # -------------------------------------------------------------------

    def _endpoint_list(
        tier: str | None = None, source: str | None = None, site: str | None = None
    ) -> str:
        try:
            target = _client_for_site(client, site or source)
            result = target.client.list_endpoints(tier=tier)
            eps = result if isinstance(result, list) else result.get("endpoints", [])
            if not eps:
                return f"No active endpoints on {target.site}."
            return "\n".join(_endpoint_line(ep, target.site) for ep in eps)
        except Exception as e:
            return f"Error listing endpoints: {e}"

    tools.append(
        _tool(
            "endpoint_list",
            "List active inference endpoints on the platform",
            {
                "type": "object",
                "properties": {
                    "tier": {
                        "type": "string",
                        "description": "Filter by tier (system/project/user)",
                    },
                    "site": _SITE_PARAM,
                    "source": _SOURCE_ALIAS_PARAM,
                },
            },
            _endpoint_list,
        )
    )

    def _endpoint_status(
        instance_id: str, site: str | None = None, source: str | None = None
    ) -> str:
        try:
            target = _client_for_site(client, site or source, instance_id)
            result = target.client.get_endpoint_status(target.ident)
            return _site_json(result, target)
        except Exception as e:
            return f"Error getting endpoint status: {e}"

    tools.append(
        _tool(
            "endpoint_status",
            "Get status of a specific inference endpoint",
            {
                "type": "object",
                "properties": {
                    "instance_id": {
                        "type": "string",
                        "description": "Endpoint instance ID (or id@site)",
                    },
                    "site": _SITE_PARAM,
                    "source": _SOURCE_ALIAS_PARAM,
                },
                "required": ["instance_id"],
            },
            _endpoint_status,
        )
    )

    def _parse_mounts_json(raw: str | None) -> dict | None:
        if not raw:
            return None
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("mounts must be a JSON object per UMRS EndpointMounts")
        return parsed

    def _parse_egress_domains(raw: str | None) -> list[str] | None:
        if not raw:
            return None
        values = [part.strip() for part in raw.split(",") if part.strip()]
        return values or None

    def _endpoint_provision(
        model: str,
        tier: str = "user",
        source: str | None = None,
        mounts: str | None = None,
        egress_domains: str | None = None,
        max_model_len: int | None = None,
        gpu_count: int | None = None,
        node_count: int | None = None,
        partition: str | None = None,
        ttl_sec: int | None = None,
        wait: bool = True,
        site: str | None = None,
    ) -> str:
        try:
            target = _client_for_site(client, site or source, act=True)
            result = target.client.provision_endpoint(
                model=model,
                tier=tier,
                mounts=_parse_mounts_json(mounts),
                egress_domains=_parse_egress_domains(egress_domains),
                max_model_len=max_model_len,
                gpu_count=gpu_count,
                node_count=node_count,
                partition=partition,
                ttl_sec=ttl_sec,
                wait=wait,
            )
            return _site_json(result, target) + f"\nProvisioned on site '{target.site}'"
        except Exception as e:
            return f"Error provisioning endpoint: {e}"

    tools.append(
        _tool(
            "endpoint_provision",
            "Provision a new inference endpoint (mini-RiVault instance)."
            " Customized envs: mounts + egress_domains attach MCP/RAG/data and allow APIs;"
            " ask_user first when tier or mounts are ambiguous",
            {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "Model name/path to serve"},
                    "tier": {
                        "type": "string",
                        "description": "Deployment tier (user/project/system)",
                        "default": "user",
                    },
                    "site": _SITE_PARAM,
                    "source": _SOURCE_ALIAS_PARAM,
                    "mounts": {
                        "type": "string",
                        "description": (
                            'JSON object per UMRS EndpointMounts: {"mcp_servers":'
                            ' [{"source": "/host/path", "target": "/mcp-servers/x"}],'
                            ' "project_data": {...}, "rag_index": {...}} (read-only)'
                        ),
                    },
                    "egress_domains": {
                        "type": "string",
                        "description": "Comma-separated allowlisted external API domains",
                    },
                    "max_model_len": {"type": "integer"},
                    "gpu_count": {"type": "integer"},
                    "node_count": {"type": "integer"},
                    "partition": {"type": "string", "description": "Slurm partition"},
                    "ttl_sec": {"type": "integer", "description": "Lifetime, 0 = no expiry"},
                    "wait": {"type": "boolean", "default": True},
                },
                "required": ["model"],
            },
            _endpoint_provision,
        )
    )

    def _endpoint_stop(
        instance_id: str, force: bool = False, site: str | None = None, source: str | None = None
    ) -> str:
        try:
            target = _client_for_site(client, site or source, instance_id, act=True)
            result = target.client.stop_endpoint(target.ident, force=force)
            return _site_json(result, target)
        except Exception as e:
            return f"Error stopping endpoint: {e}"

    tools.append(
        _tool(
            "endpoint_stop",
            "Stop an inference endpoint",
            {
                "type": "object",
                "properties": {
                    "instance_id": {
                        "type": "string",
                        "description": "Endpoint instance ID (or id@site)",
                    },
                    "force": {"type": "boolean", "description": "Force stop", "default": False},
                    "site": _SITE_PARAM,
                    "source": _SOURCE_ALIAS_PARAM,
                },
                "required": ["instance_id"],
            },
            _endpoint_stop,
        )
    )

    def _job_list(status: str | None = None, limit: int = 20, site: str | None = None) -> str:
        try:
            from .site_routing import job_site_labeller

            target = _client_for_site(client, site)
            jobs = target.client.jobs.list(status=status, limit=limit)
            if not jobs:
                return f"No jobs found on {target.site}."
            label = job_site_labeller(client, target)
            return "\n".join(_job_line(j, label(j)) for j in jobs)
        except Exception as e:
            return f"Error listing jobs: {e}"

    tools.append(
        _tool(
            "job_list",
            "List platform jobs (model downloads, workflows, etc.)",
            {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status (queued/running/completed/failed)",
                    },
                    "limit": {"type": "integer", "description": "Max results", "default": 20},
                    "site": _SITE_PARAM,
                },
            },
            _job_list,
        )
    )

    def _job_status(job_id: str, site: str | None = None) -> str:
        try:
            from .site_routing import job_site_labeller

            target = _client_for_site(client, site, job_id)
            job = target.client.jobs.get(target.ident)
            payload = _job_payload(job, job_site_labeller(client, target)(job))
            return json.dumps(payload, indent=2, default=str)
        except Exception as e:
            return f"Error getting job status: {e}"

    tools.append(
        _tool(
            "job_status",
            "Get detailed status of a platform job",
            {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID (or id@site)"},
                    "site": _SITE_PARAM,
                },
                "required": ["job_id"],
            },
            _job_status,
        )
    )

    def _job_cancel(job_id: str, site: str | None = None) -> str:
        try:
            from .site_routing import job_action_target

            target = job_action_target(client, site, job_id)
            job = target.client.jobs.cancel(target.ident)
            return f"Job {job.job_id}@{target.site} cancelled (status={job.status})"
        except Exception as e:
            return f"Error cancelling job: {e}"

    tools.append(
        _tool(
            "job_cancel",
            "Cancel a running or queued platform job",
            {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID to cancel (or id@site)"},
                    "site": _SITE_PARAM,
                },
                "required": ["job_id"],
            },
            _job_cancel,
        )
    )

    for build_family in _TOOL_FAMILY_BUILDERS:
        tools.extend(_family_tools(build_family, client, reading, web_access, evidence))
    tools.append(
        _tool(
            "shell_stream_cancel",
            "Cancel a shell command running under the streaming executor "
            "(identified by stream_id reported in earlier tool output)",
            {
                "type": "object",
                "properties": {
                    "stream_id": {
                        "type": "string",
                        "description": "id reported by a shell_exec under streaming",
                    },
                },
                "required": ["stream_id"],
            },
            lambda stream_id: "cancelled" if _shell_cancel_stream(stream_id) else "not-found",
        )
    )
    tools.append(
        _tool(
            "test_fix_loop",
            "Run a test command and on failure keep editing-and-retesting "
            "INSIDE this one call (edit → test → edit) until it passes or "
            "limits hit — collapses per-cycle turns into one. Returns a "
            "structured per-iteration report.",
            {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Test command to run (e.g. 'pytest -x')",
                    },
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Files (relative paths) available for the loop's LLM"
                            " edit proposals — only these are supplied as context"
                        ),
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "Stop after N cycles (default 4)",
                        "default": 4,
                    },
                    "tail_bytes": {
                        "type": "integer",
                        "description": "Cap captured command output length (default 8000)",
                        "default": 8000,
                    },
                },
                "required": ["command", "files"],
            },
            _make_test_fix_loop_handler(client),
        )
    )

    def _mcp_list_servers() -> str:
        try:
            servers = client.mcp.list_servers()
            if not servers:
                return "No MCP servers registered."
            lines = []
            for s in servers:
                name = s.get("name", "?")
                url = s.get("url", "?")
                lines.append(f"- {name}: {url}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing MCP servers: {e}"

    tools.append(
        _tool(
            "mcp_list_servers",
            "List registered MCP tool servers",
            {"type": "object", "properties": {}},
            _mcp_list_servers,
        )
    )

    def _mcp_list_tools(server: str | None = None) -> str:
        try:
            tools_list = client.mcp.list_tools(server=server)
            if not tools_list:
                return "No MCP tools available."
            return "\n".join(_mcp_tool_line(t) for t in tools_list[:50])
        except Exception as e:
            return f"Error listing MCP tools: {e}"

    tools.append(
        _tool(
            "mcp_list_tools",
            "List available MCP tools (optionally filter by server)",
            {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "Filter by server name (optional)",
                    },
                },
            },
            _mcp_list_tools,
        )
    )

    def _mcp_invoke(tool_name: str, arguments: str | None = None) -> str:
        try:
            kwargs = json.loads(arguments) if arguments else {}
            from .mcp_first_class import mcp_result_text

            return mcp_result_text(client.mcp.invoke(tool_name, **kwargs))
        except json.JSONDecodeError:
            return "Error: arguments must be valid JSON"
        except Exception as e:
            return f"Error invoking MCP tool: {e}"

    tools.append(
        _tool(
            "mcp_invoke",
            "Invoke an MCP tool by name with JSON arguments",
            {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Tool name"},
                    "arguments": {
                        "type": "string",
                        "description": "JSON string of tool arguments",
                    },
                },
                "required": ["tool_name"],
            },
            _mcp_invoke,
        )
    )

    # -------------------------------------------------------------------
    # Model registry tools (UMRS model management)
    # -------------------------------------------------------------------

    def _model_list_registry(
        scope: str | None = None,
        model_type: str | None = None,
        limit: int = 30,
    ) -> str:
        try:
            from ..models import ModelClient

            mc = ModelClient(config=client.config)
            models = mc.list_models(
                scope=scope,
                model_type=model_type,
                limit=limit,
            )
            if not models:
                return "No models in registry."
            lines = []
            for m in models:
                lines.append(
                    f"- {m.name}  scope={m.scope.value}  "
                    f"type={m.model_type.value}  status={m.status.value}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing model registry: {e}"

    tools.append(
        _tool(
            "model_list_registry",
            "List models from the UMRS model registry with scope/type filters",
            {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["system", "project", "user"],
                        "description": "Filter by scope",
                    },
                    "model_type": {
                        "type": "string",
                        "enum": ["llm", "embedding", "vision", "reranker", "ocr", "vlm"],
                        "description": "Filter by model type",
                    },
                    "limit": {"type": "integer", "default": 30},
                },
            },
            _model_list_registry,
        )
    )

    def _model_details(model_id: str) -> str:
        try:
            from ..models import ModelClient

            mc = ModelClient(config=client.config)
            m = mc.get_model(model_id)
            return json.dumps(
                {
                    "model_id": m.model_id,
                    "name": m.name,
                    "scope": m.scope.value,
                    "model_type": m.model_type.value,
                    "status": m.status.value,
                    "description": m.description,
                    "huggingface_id": m.huggingface_id,
                    "storage_path": m.storage_path,
                    "created_at": str(m.created_at),
                },
                indent=2,
                default=str,
            )
        except Exception as e:
            return f"Error getting model details: {e}"

    tools.append(
        _tool(
            "model_details",
            "Get detailed information about a model from the UMRS registry",
            {
                "type": "object",
                "properties": {
                    "model_id": {"type": "string", "description": "Model ID"},
                },
                "required": ["model_id"],
            },
            _model_details,
        )
    )

    def _model_stage(repo_id: str, scope: str = "user") -> str:
        try:
            from ..models import ModelClient, ModelScope, StageRequest

            mc = ModelClient(config=client.config)
            req = StageRequest(
                source_id=repo_id,
                scope=ModelScope(scope),
            )
            job_id = mc.stage_model(req)
            return (
                f"Model staging job submitted.\n"
                f"  repo_id: {repo_id}\n"
                f"  scope:   {scope}\n"
                f"  job_id:  {job_id}\n\n"
                f"Monitor progress with job_status(job_id='{job_id}')."
            )
        except Exception as e:
            return f"Error staging model: {e}"

    tools.append(
        _tool(
            "model_stage",
            (
                "Request a model download from HuggingFace via a UMRS "
                "durable job. The download runs server-side; monitor "
                "progress with job_status."
            ),
            {
                "type": "object",
                "properties": {
                    "repo_id": {
                        "type": "string",
                        "description": "HuggingFace repo ID (e.g. Qwen/Qwen3-8B)",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["system", "project", "user"],
                        "description": "Target scope",
                        "default": "user",
                    },
                },
                "required": ["repo_id"],
            },
            _model_stage,
        )
    )

    # -------------------------------------------------------------------
    # File / data tools
    # -------------------------------------------------------------------

    def _file_upload_platform(
        file_path: str,
        project_id: str | None = None,
        site: str | None = None,
    ) -> str:
        try:
            target = _client_for_site(client, site, act=True)
            result = target.client.data.upload(
                file_path,
                project_id=project_id,
            )
            return (
                f"Uploaded to {target.site}: {result.filename}\n"
                f"  file_id: {result.file_id}\n"
                f"  s3_key:  {result.s3_key or '(assigned)'}\n"
                f"  size:    {result.size_bytes} bytes\n"
                f"  status:  {result.status}"
            )
        except Exception as e:
            return f"Error uploading file: {e}"

    tools.append(
        _tool(
            "file_upload_platform",
            "Upload a local file to UMRS storage (S3/MinIO)",
            {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Local file path"},
                    "project_id": {
                        "type": "string",
                        "description": "Project ID (uses current project if omitted)",
                    },
                    "site": _SITE_PARAM,
                },
                "required": ["file_path"],
            },
            _file_upload_platform,
        )
    )

    def _file_list_platform(
        project_id: str | None = None,
        prefix: str | None = None,
        limit: int = 50,
        site: str | None = None,
    ) -> str:
        try:
            target = _client_for_site(client, site)
            files = target.client.data.list_files(
                project_id=project_id,
                prefix=prefix,
                limit=limit,
            )
            if not files:
                return f"No files found on {target.site}."
            return "\n".join(
                f"- {f.filename}  id={f.file_id}@{target.site}  size={f.size_bytes} bytes"
                for f in files
            )
        except Exception as e:
            msg = str(e)
            if "no server route" in msg or "retired" in msg:
                return (
                    "Project file listing via gateway data API is retired "
                    "(no server route). Use the lab catalog tools for "
                    "project file lookup, or ` client.data.upload` (ZMQ) "
                    "for uploads."
                )
            return f"Error listing files: {e}"

    tools.append(
        _tool(
            "file_list_platform",
            "List files stored in UMRS storage for a project",
            {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Project ID (uses current if omitted)",
                    },
                    "prefix": {
                        "type": "string",
                        "description": "Filter by key prefix",
                    },
                    "limit": {"type": "integer", "default": 50},
                    "site": _SITE_PARAM,
                },
            },
            _file_list_platform,
        )
    )

    def _file_download_platform(
        file_id: str,
        local_path: str,
        project_id: str | None = None,
        site: str | None = None,
    ) -> str:
        try:
            target = _client_for_site(client, site, file_id, lenient=True)
            result = target.client.data.download(
                target.ident,
                local_path,
                project_id=project_id,
            )
            return (
                f"Downloaded from {target.site}: {result.filename}\n"
                f"  local_path: {result.local_path}\n"
                f"  size:       {result.size_bytes} bytes\n"
                f"  status:     {result.status}"
            )
        except Exception as e:
            return f"Error downloading file: {e}"

    tools.append(
        _tool(
            "file_download_platform",
            "Download a file from UMRS storage to a local path",
            {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": "File ID or S3 key (or id@site)",
                    },
                    "local_path": {"type": "string", "description": "Local destination path"},
                    "project_id": {
                        "type": "string",
                        "description": "Project ID (uses current if omitted)",
                    },
                    "site": _SITE_PARAM,
                },
                "required": ["file_id", "local_path"],
            },
            _file_download_platform,
        )
    )

    # -------------------------------------------------------------------
    # RAG tools
    # -------------------------------------------------------------------

    def _rag_query(
        query: str,
        top_k: int = 5,
        threshold: float | None = None,
        project_id: str | None = None,
    ) -> str:
        # ADR-093 D-10: no 0.7 default; None is omitted and the server decides.
        try:
            result = client.rag.query(
                query,
                top_k=top_k,
                threshold=threshold,
                project_id=project_id,
            )
            payload = _rag_query_payload(result, evidence, project_id or "")
            return json.dumps(payload, indent=2, default=str)
        except Exception as e:
            return _rag_tool_error_message("querying RAG", e)

    tools.append(
        _tool(
            "rag_query",
            (
                "Semantic search + answer generation over RAG-indexed "
                "documents. Returns an answer with source citations."
            ),
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language query"},
                    "top_k": {"type": "integer", "default": 5},
                    "threshold": {
                        "type": "number",
                        "description": (
                            "Legacy absolute similarity cut (0-1). Omit it: the "
                            "server decides which passages are relevant."
                        ),
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID (uses current if omitted)",
                    },
                },
                "required": ["query"],
            },
            _rag_query,
        )
    )

    def _rag_upload(
        file_path: str, project_id: str | None = None, proposal_id: str | None = None
    ) -> str:
        from .knowledge_graph_tools import agent_mutation

        try:
            # The agent's upload, never the person's (review of K1 part A):
            # UMRS needs an approved proposal and lands the rows proposed.
            with agent_mutation(client):
                result = client.rag.upload(
                    file_path,
                    project_id=project_id,
                    proposal_id=proposal_id,
                )
            return (
                f"Document uploaded for RAG indexing.\n"
                f"  document_id: {result.document_id}\n"
                f"  filename:    {result.filename}\n"
                f"  status:      {result.status}"
            )
        except Exception as e:
            return _rag_tool_error_message("uploading document", e)

    tools.append(
        _tool(
            "rag_upload",
            "Upload a document for RAG indexing (embedding + retrieval)",
            {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Local file path to upload",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Project ID (uses current if omitted)",
                    },
                    "proposal_id": {
                        "type": "string",
                        "description": "Approved knowledge_graph_propose id (UMRS requires one)",
                    },
                },
                "required": ["file_path"],
            },
            _rag_upload,
        )
    )

    def _rag_list_documents(project_id: str | None = None) -> str:
        try:
            docs = client.rag.list_documents(project_id=project_id)
            if not docs:
                return "No documents indexed."
            lines = []
            for d in docs:
                doc_id = d.get("document_id", "?")
                fname = d.get("filename", "?")
                chunks = d.get("chunks", 0)
                lines.append(f"- {fname}  id={doc_id}  chunks={chunks}")
            return "\n".join(lines)
        except Exception as e:
            return _rag_tool_error_message("listing RAG documents", e)

    tools.append(
        _tool(
            "rag_list_documents",
            "List documents indexed in the RAG system",
            {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Project ID (uses current if omitted)",
                    },
                },
            },
            _rag_list_documents,
        )
    )

    # -------------------------------------------------------------------
    # Vision tools
    # -------------------------------------------------------------------

    def _vision_submit(
        image: str,
        model: str | None = None,
        adapter: str | None = None,
    ) -> str:
        try:
            result = client.vision.submit(
                image,
                model=model,
                adapter=adapter,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return f"Error submitting vision task: {e}"

    tools.append(
        _tool(
            "vision_submit",
            (
                "Submit a vision inference task. Accepts a local file "
                "path or S3 URI. Auto-uploads local files."
            ),
            {
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Image path (local file or s3:// URI)",
                    },
                    "model": {
                        "type": "string",
                        "description": "Vision model/endpoint to use",
                    },
                    "adapter": {
                        "type": "string",
                        "description": "BentoML vision adapter name",
                    },
                },
                "required": ["image"],
            },
            _vision_submit,
        )
    )

    def _vision_status(task_id: str, model: str | None = None) -> str:
        try:
            result = client.vision.status(task_id, model=model)
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return f"Error getting vision status: {e}"

    tools.append(
        _tool(
            "vision_status",
            "Get the status of a vision inference task",
            {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Vision task ID"},
                    "model": {"type": "string", "description": "Model name (optional)"},
                },
                "required": ["task_id"],
            },
            _vision_status,
        )
    )

    # -------------------------------------------------------------------
    # Job retry
    # -------------------------------------------------------------------

    def _job_retry(job_id: str, site: str | None = None) -> str:
        try:
            from .site_routing import job_action_target

            target = job_action_target(client, site, job_id)
            job = target.client.jobs.retry(target.ident)
            return f"Job {job.job_id}@{target.site} retried (status={job.status})"
        except Exception as e:
            return f"Error retrying job: {e}"

    tools.append(
        _tool(
            "job_retry",
            "Retry a failed platform job",
            {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID to retry (or id@site)"},
                    "site": _SITE_PARAM,
                },
                "required": ["job_id"],
            },
            _job_retry,
        )
    )

    def _platform_health(site: str | None = None) -> str:
        try:
            target = _client_for_site(client, site)
            healthy = target.client.health()
            readiness = target.client.readiness()
            return _site_json({"healthy": healthy, "readiness": readiness}, target)
        except Exception as e:
            return f"Error checking platform health: {e}"

    tools.append(
        _tool(
            "platform_health",
            "Check overall platform health and readiness",
            {"type": "object", "properties": {"site": _SITE_PARAM}},
            _platform_health,
        )
    )

    tools.append(
        _tool(
            "hpc_sites",
            "My HPC sites: health, access mode, my account, scheduler, staged "
            "lane, GPU, selectable",
            {
                "type": "object",
                "properties": {
                    "refresh": {
                        "type": "boolean",
                        "description": "Probe again (skip the cache)",
                        "default": False,
                    }
                },
            },
            lambda refresh=False: _hpc_sites_json(client, refresh=refresh),
        )
    )

    tools.extend(_client_lab_tools(client, session_model=session_model))

    return tools


def _project_tools(
    client: Client | None,
) -> list[dict[str, Any]]:
    """Return the project-management tools (client config sync-only).

    Split out of `_project_and_teamup_tools` (CCN debt, exemption-age
    review 2026-09-13) — behavior identical.
    """
    tools: list[dict[str, Any]] = []

    # Project management (bug.memo C3, 2026-04-25). These do not need the
    # client — the project manager writes to ~/.config/r4/projects.json.
    def _project_list() -> str:
        try:
            from ..project import get_project_manager

            pm = get_project_manager()
            projects = pm.list()
            current = pm.get_current()
            if not projects:
                return "No projects configured. Create one with: r4 project create <id>"
            lines = []
            for p in projects:
                marker = "*" if p.project_id == current else " "
                name = f" ({p.name})" if p.name else ""
                lines.append(f"{marker} {p.project_id}{name}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing projects: {e}"

    tools.append(
        _tool(
            "project_list",
            "List r4 projects and show which one is current",
            {"type": "object", "properties": {}},
            _project_list,
        )
    )

    def _project_use(project_id: str) -> str:
        try:
            from ..project import get_project_manager

            pm = get_project_manager()
            if not pm.get(project_id):
                return (
                    f"Project not found: {project_id}. "
                    f"Available: {[p.project_id for p in pm.list()]}"
                )
            pm.set_current(project_id)
            # Sync the cached client config so knowledge/research calls
            # resolve the freshly switched project (config is snapshot at
            # construction and would otherwise stay stale mid-session).
            config = getattr(client, "config", None)
            if config is not None:
                with contextlib.suppress(Exception):
                    config.project_id = project_id
            return f"Current project set to: {project_id}"
        except Exception as e:
            return f"Error switching project: {e}"

    tools.append(
        _tool(
            "project_use",
            (
                "Switch the current r4 project. Subsequent RAG / job / "
                "endpoint calls default to this project unless overridden."
            ),
            {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Project ID to make current",
                    },
                },
                "required": ["project_id"],
            },
            _project_use,
        )
    )

    return tools


def _teamup_tools(
    client: Client | None,
    session_model: str | None = None,
    permission_manager: Any = None,
    usage_hooks: Any = None,
    plan_executor: Any = None,
    web_access: Any = None,
) -> list[dict[str, Any]]:
    """Return the teamup tools (list/status + gated create).

    Split out of `_project_and_teamup_tools` (CCN debt, exemption-age
    review 2026-09-13) — behavior identical. ``usage_hooks`` is the chat
    session's ``(record_usage, guard_llm_call)``: a team started or continued
    here charges its role rounds to it (ADR-092 MD-7). ``plan_executor`` is
    this session's team plan executor: every crew but ``hpc`` is a plan
    template and runs through it (ADR-092 slice 4). ``web_access`` is the
    chat session's web-access accessor: the roles' web tools follow the
    lead's choice (ADR-099 D-9, RS-1 fix W3).
    """
    tools: list[dict[str, Any]] = []

    # Teamup read-only tools (bug.memo C2, 2026-04-25). Companion can list
    # and inspect multi-agent sessions but cannot start one — `teamup_start`
    # is a long-running orchestration that needs explicit human go-ahead.
    def _teamup_list() -> str:
        try:
            from ..teamup import TeamupSession

            sessions = TeamupSession.list_sessions()
            if not sessions:
                return "No teamup sessions found."
            lines = [
                f"{s['session_id'][:8]}  {s['name']:<24}  "
                f"{s['status']:<10}  iter {s['iteration']}  "
                f"updated={s['updated_at']}"
                for s in sessions
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing teamup sessions: {e}"

    tools.append(
        _tool(
            "teamup_list",
            "List local teamup (multi-agent HPC optimization) sessions",
            {"type": "object", "properties": {}},
            _teamup_list,
        )
    )

    def _teamup_status(session_id: str) -> str:
        try:
            from ..teamup import TeamupSession

            session = TeamupSession.load(session_id, client)
            return json.dumps(session.get_status(), indent=2, default=str)
        except Exception as e:
            return f"Error loading teamup session {session_id}: {e}"

    tools.append(
        _tool(
            "teamup_status",
            "Show status of a teamup session (iteration, agents, SOTA, budget)",
            {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Teamup session ID",
                    },
                },
                "required": ["session_id"],
            },
            _teamup_status,
        )
    )

    def _teamup_conclusions(session_id: str) -> str:
        """What the team CONCLUDED: per-role final answers, from the team's
        own ChangeLog (the authoritative store — RAG/search index documents,
        not team iteration outcomes; no RAG needed for this question)."""
        try:
            from ..teamup import TeamupSession

            session = TeamupSession.load(session_id, client)
            status = session.get_status()
            conclusions = []
            for role_value, agent_status in (status.get("agents") or {}).items():
                responses = [
                    entry
                    for entry in session.comms.get_agent_entries(role_value.upper())
                    if entry.action == "RESPONSE"
                ]
                conclusions.append(
                    {
                        "role": role_value,
                        "status": agent_status.get("status"),
                        "answers": [
                            {"answer": entry.description, "at": entry.timestamp.isoformat()}
                            for entry in responses[-2:]  # latest bounded tail
                        ],
                        "errors": agent_status.get("errors", []),
                    }
                )
            return json.dumps(
                {
                    "session_id": session.session_id,
                    "name": session.config.name,
                    "crew": session.config.crew_pack,
                    "status": session.status.value,
                    "iteration": session.iteration,
                    "conclusions": conclusions,
                    "note": (
                        "Authoritative record of what the team concluded — "
                        "the team's ChangeLog. RAG/search index documents, "
                        "not team iteration outcomes; no RAG needed here."
                    ),
                },
                indent=2,
                default=str,
            )
        except Exception as e:
            return f"Error loading conclusions for team {session_id}: {e}"

    tools.append(
        _tool(
            "teamup_conclusions",
            (
                "Read what a teamup session CONCLUDED: every role's persisted "
                "final answers (ChangeLog RESPONSE entries), statuses and errors. "
                "AUTHORITATIVE for team iteration outcomes — prefer this over "
                "rag_query/search_agentic, which index documents, not what a "
                "team produced."
            ),
            {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Teamup session ID",
                    },
                },
                "required": ["session_id"],
            },
            _teamup_conclusions,
        )
    )

    # Agent-initiated team start (B-family, 2026-09-27; confirmed intent: the
    # agent starts a team when the task class deserves one, gated by
    # RISKY_TOOLS). One iteration starts async; further rounds continue via
    # teamup_run below or the Work pane. Root-agent prompt tailor lands once
    # before the first turn (B3), from each crew's own baselines.
    def _start_iteration_async(session, countdown: int = 1) -> None:
        """Daemon iteration runner with a per-session guard (never two at once)."""
        import asyncio

        def _run(active_session) -> None:
            try:
                for _ in range(max(1, countdown)):
                    if not asyncio.run(active_session.run_iteration()):
                        break
            except Exception:
                logger.exception("team async iteration failed: %s", active_session.session_id)

        session.run_in_background(_run)

    def _teamup_start(
        goal: str,
        crew: str = "research",
        name: str | None = None,
        max_iterations: int = 6,
        models: dict[str, str] | None = None,
        run: bool = True,
        hardware_target: str = "generic",
        code_path: str | None = None,
        parallel_roles: int = 1,
    ) -> str:
        from ..teams.crews import DEFAULT_CREW, crew_name, plan_crew_of

        # ADR-092 slice 4: the same reading the permission gate makes. A plan
        # crew was let through the gate WITHOUT a teamup_start card: its one
        # approval is the plan's own never-auto card, asked by the executor
        # (no card, no run). Anything else is hpc, under teamup_start's rule.
        plan_crew = plan_crew_of(crew)
        if plan_crew is not None:
            return _crew_plan_start(plan_crew, goal, models, run)
        try:
            crew_name(crew or DEFAULT_CREW)  # hpc, or a named refusal
        except KeyError as exc:
            return json.dumps({"ok": False, "error": f"team_start refuses {exc.args[0]}"})
        return _hpc_team_start(
            goal,
            name=name,
            max_iterations=max_iterations,
            models=models,
            run=run,
            hardware_target=hardware_target,
            code_path=code_path,
            parallel_roles=parallel_roles,
        )

    def _crew_plan_start(crew_key: str, goal: str, models: Any, run: bool) -> str:
        """A template crew runs its plan through this session's executor
        (ADR-092 slice 4): validated, then the plan's own card (MD-5)."""
        if plan_executor is None:
            return json.dumps(
                {"ok": False, "crew": crew_key, "error": "team plans need a connected client"}
            )
        try:
            reply = plan_executor.start_crew(crew_key, goal, models=models, run=run)
        except Exception as exc:  # noqa: BLE001 — honest error payload.
            logger.exception("crew plan start failed")
            reply = {"ok": False, "crew": crew_key, "error": str(exc)[:300]}
        return json.dumps(reply, default=str)

    def _hpc_team_start(
        goal: str,
        *,
        name: str | None,
        max_iterations: int,
        models: dict[str, str] | None,
        run: bool,
        hardware_target: str,
        code_path: str | None,
        parallel_roles: int,
    ) -> str:
        """The HPC preset: a teamup session, tailored, first iteration async."""
        from ..teams.service import TeamService
        from ..teamup.bootstrap import bootstrap_team

        crew_key = "hpc"
        try:
            # ADR-092 slice 3 (ADR-072 one seam): the team is opened by
            # TeamService, which hands it this chat's model, manager and
            # usage hooks (MD-7: the rounds charge this chat).
            service = TeamService(
                client,
                permission_manager=permission_manager,
                session_model=session_model,
                usage_hooks=usage_hooks,
                web_access=web_access,
            )
            session, tailor = bootstrap_team(
                client,
                model=session_model,
                goal=goal,
                crew=crew_key,
                name=name,
                max_iterations=max_iterations,
                models=models,
                hardware_target=hardware_target,
                code_path=code_path,
                parallel_roles=parallel_roles,
                # 2026-10-01: stamp the caller's identity project for
                # work-pane scoping; "" → None stays honest. Tolerates both
                # the real Client (config property or private _config) and
                # duck-typed fixture clients (no config attrs at all).
                project_id=_identity_project_id(client),
                service=service,
            )
            started = False
            if run:
                _start_iteration_async(session)
                started = True
            return json.dumps(
                {
                    "ok": True,
                    "session_id": session.session_id,
                    "name": session.config.name,
                    "crew": crew_key,
                    "roles": [r.value for r in session.config.roles],
                    "goal": goal,
                    "tailor": {
                        "root": tailor.get("root"),
                        "tailored": tailor.get("tailored", []),
                        "kept_default": tailor.get("kept_default", []),
                        **({"degraded": tailor["degraded"]} if tailor.get("degraded") else {}),
                    },
                    "iterations_started": 1 if started else 0,
                    "watch": "Work pane → teams row (Enter roster/prompts) or /teamup status",
                }
            )
        except Exception as exc:  # noqa: BLE001 — honest error payload.
            logger.exception("team_start failed")
            return json.dumps({"ok": False, "error": str(exc)[:300]})

    tools.append(
        _tool(
            "teamup_start",
            (
                "Start a multi-agent team on a goal that deserves several agents. Pick "
                "crew by task class: research (planner/experimenter/analyst/reviewer) for "
                "science, code for development, paper-writing, kakenhi-report or "
                "peer-review for research writing, hpc (pm/se/pg/cd) for HPC code "
                "optimization. Every crew but hpc is a team plan template (the same as "
                "team_plan_run(template=...)): the user approves the plan's card, then it "
                "runs in the background; read it with team_plan_result(run_id). The hpc "
                "crew opens a teamup session, tailors each role's prompt from THIS "
                "project and starts its first iteration async (Work pane)."
            ),
            {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "Research question or optimization goal",
                    },
                    "crew": {
                        "type": "string",
                        "description": (
                            "research (science) | code (development) | paper-writing | "
                            "kakenhi-report | peer-review | hpc (HPC code optimization)"
                        ),
                        "enum": [
                            "research",
                            "code",
                            "paper-writing",
                            "kakenhi-report",
                            "peer-review",
                            "hpc",
                        ],
                        "default": "research",
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "Iteration budget (hpc crew, 1-20)",
                        "default": 6,
                    },
                    "name": {"type": "string", "description": "Optional session name (hpc crew)"},
                    "models": {
                        "type": "object",
                        "description": (
                            "Optional per-role model {role: model}; for a plan crew a role "
                            "its template names a model for (e.g. reviewer)"
                        ),
                    },
                    "run": {
                        "type": "boolean",
                        "description": (
                            "Start now (default true); false only validates a plan crew, or "
                            "opens an hpc session without its first iteration"
                        ),
                        "default": True,
                    },
                    "hardware_target": {
                        "type": "string",
                        "description": "Hardware target tag (hpc crew)",
                        "default": "generic",
                    },
                    "code_path": {
                        "type": "string",
                        "description": "Optional source path (hpc crew)",
                    },
                    "parallel_roles": {
                        "type": "integer",
                        "description": (
                            "Roles running concurrently per iteration (default 1 = "
                            "sequential pipeline order; parallel roles do not see each "
                            "other's same-iteration changelog context)"
                        ),
                        "default": 1,
                    },
                },
                "required": ["goal"],
            },
            _teamup_start,
        )
    )

    def _teamup_run(session_id: str, iterations: int = 1) -> str:
        from ..teamup import TeamupSession

        try:
            iterations = max(1, min(3, int(iterations)))
            session = TeamupSession.load(session_id, client)
            session._usage_hooks = usage_hooks  # ADR-092 MD-7: the rounds charge this chat
            session._web_access = web_access  # ADR-099 D-9: the lead's web choice
            _start_iteration_async(session, iterations)
            return json.dumps(
                {
                    "ok": True,
                    "session_id": session.session_id,
                    "iterations_started": iterations,
                    "watch": "Work pane → teams row",
                }
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)[:300]})

    tools.append(
        _tool(
            "teamup_run",
            "Continue an existing teamup session (more iterations, async, 1-3)",
            {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Teamup session ID"},
                    "iterations": {
                        "type": "integer",
                        "description": "Iterations to run (1-3)",
                        "default": 1,
                    },
                },
                "required": ["session_id"],
            },
            _teamup_run,
        )
    )

    return tools


def _project_and_teamup_tools(
    client: Client | None = None,
    session_model: SessionModel = None,
    permission_manager: Any = None,
    usage_hooks: Any = None,
    turn_cancel: Any = None,
    web_access: Any = None,
) -> list[dict[str, Any]]:
    """Return project, teamup and team plan tool definitions (mostly client-free).

    ``usage_hooks`` is the chat session's ``(usage_callback, pre_llm_call)``
    pair: a team plan's agents (ADR-092 D-7) and a team's role turns (MD-7)
    charge every model round to it.
    The plan tools get ``session_model`` as given (an accessor is read per
    call); the teamup tools bind its current value. ``turn_cancel`` is the
    session's accessor for the running turn's cancellation token: a plan a
    turn starts is cancelled with that turn (ADR-092 slice 3). ``web_access``
    is the session's web-access accessor, handed to every team agent.
    """
    plan_tools = build_team_plan_tools(
        client,
        session_model=session_model,
        permission_manager=permission_manager,
        usage_hooks=usage_hooks,
        turn_cancel=turn_cancel,
        web_access=web_access,
    )
    return [
        *_project_tools(client),
        *_teamup_tools(
            client,
            session_model=_session_model_now(session_model),
            permission_manager=permission_manager,
            usage_hooks=usage_hooks,
            # ADR-092 slice 4: crews are plan templates, run by this executor.
            plan_executor=team_plan_executor_of(plan_tools),
            web_access=web_access,
        ),
        *plan_tools,
    ]


# Read-only tools that may run concurrently inside one tool-call round.
# Criteria: no filesystem mutation, no process spawn, no interactive prompt,
# and remote access goes through stateless per-request HTTP clients.
_CONCURRENCY_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "platform_guide",
        "file_read",
        "list_dir",
        "grep_search",
        "glob_search",
        "detect_code_context",
        "detect_language_servers",
        "lab_catalog_search",
        "lab_file_read",
        "search_docs",
        "open_passage",
        "list_models",
        "endpoint_list",
        "endpoint_status",
        "job_list",
        "job_status",
        "mcp_list_servers",
        "mcp_list_tools",
        "model_list_registry",
        "model_details",
        "file_list_platform",
        "rag_list_documents",
        "platform_health",
        "hpc_sites",
        "project_list",
        "teamup_list",
        "teamup_status",
        # ADR-092 D-9: listing templates and reading a plan's result.json.
        "team_plan_templates",
        "team_plan_result",
        "hpc_job_status",
    }
)


def _session_model_now(session_model: SessionModel) -> str | None:
    """The session's model now, for tools that bind a model at build time.

    ``session_model`` may be the session's model accessor (ADR-092 D-6, D-9):
    the team plan tools read it at each call, so a ``/model`` switch reaches
    the next plan's agents, preflight and approval card.
    """
    return session_model() if callable(session_model) else session_model


def get_builtin_tools(
    client: Client | None = None,
    *,
    session_model: SessionModel = None,
    permission_manager: Any = None,
    usage_hooks: Any = None,
    turn_cancel: Any = None,
    web_access: Any = None,
    evidence: Any = None,
) -> list[dict[str, Any]]:
    """Return all built-in tool definitions.

    ``usage_hooks`` (``(usage_callback, pre_llm_call)``, or None) is threaded
    to the team plan tools so plan agents charge the chat session's usage
    records and LLM-call caps (ADR-092 D-7). ``session_model`` is a model
    name or the session's accessor for it (``_session_model_now``).
    ``turn_cancel`` (the session's accessor for the running turn's token)
    lets a team plan started in a turn be cancelled with it (slice 3).
    ``web_access`` (the session's accessor for its web-access choice) puts
    ``web_fetch`` and ``web_search`` behind the user's choice (ADR-099 D-9).
    ``evidence`` (the session's ToolEvidence) has the knowledge tools record
    their passages in the session's evidence ledger (K1.4).
    """
    tools = _core_tools()
    # ADR-108 W1c: the session's worktree (local; no platform needed).
    from .worktree_tools import build_worktree_tools

    tools.extend(build_worktree_tools())
    if client is not None:
        tools.extend(
            _client_tools(
                client,
                session_model=_session_model_now(session_model),
                reader=session_model,
                web_access=web_access,
                evidence=evidence,
            )
        )
    tools.extend(
        _project_and_teamup_tools(
            client,
            session_model=session_model,
            permission_manager=permission_manager,
            usage_hooks=usage_hooks,
            turn_cancel=turn_cancel,
            web_access=web_access,
        )
    )
    tools.extend(build_client_extra_tools(client))
    if client is not None:  # ADR-105 D-4: the commit tool reads who approved it
        tools.extend(build_plan_commit_tools(client, permission_manager))

    # Merge agent built-in tools (TodoWrite, ToolSearch, AskUserQuestion,
    # WebFetch, FinalOutput) — these are first-class tools the agent always
    # has access to, regardless of MCP server configuration.
    tools.extend(_agent_builtin_tools(web_access))
    tools = _fit_site_params(tools, client)

    for tool in tools:
        if tool.get("name") in _CONCURRENCY_SAFE_TOOLS:
            tool["concurrency_safe"] = True

    return tools


def _make_agent_tool_executor(tool_name: str, web_access: Any = None):
    """Return an executor that accepts keyword args like other companion tools.

    ``web_fetch`` and ``web_search`` check the user's choice at each call
    first (ADR-099 D-9): a ``/web`` change reaches the next call. Without
    the session's accessor (a team agent, a recipe) they follow
    ``R4_WEB_ACCESS``, else refuse: never unguarded (RS-1 fix W3).
    """
    from .web_access import WEB_GUARDS, ambient_web_access

    guard = WEB_GUARDS.get(tool_name)
    current = web_access if web_access is not None else ambient_web_access

    def execute(**kwargs: Any) -> str:
        if guard is not None:
            return guard(current(), kwargs, _execute_agent_tool)
        return _execute_agent_tool(tool_name, kwargs)

    return execute


def _agent_builtin_tools(web_access: Any = None) -> list[dict[str, Any]]:
    """Return agent built-in tools from ``r4.agent.tools`` in companion format.

    These tools (TodoWrite, ToolSearch, AskUserQuestion, WebFetch,
    FinalOutput) are implemented in the agent module and are always
    available to the companion agent. ``web_access`` is the session's
    accessor for its web-access choice (see :func:`_make_agent_tool_executor`).
    """
    from r4.agent.tools import get_builtin_registry

    registry = get_builtin_registry()
    result: list[dict[str, Any]] = []
    for defn in registry.definitions():
        result.append(
            {
                "name": defn.name,
                "description": defn.description,
                "parameters": defn.parameters,
                "is_final": defn.is_final,
                "execute": _make_agent_tool_executor(defn.name, web_access),
            }
        )
    return result


def _execute_agent_tool(name: str, args: dict[str, Any]) -> str:
    """Execute a built-in agent tool by name and return output text."""
    from r4.agent.tools import get_builtin_registry

    registry = get_builtin_registry()
    result = registry.handle(name, args)
    if result.error:
        return f"Error: {result.error}"
    if isinstance(result.output, str):
        return result.output
    return str(result.output)
