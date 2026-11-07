"""ADR-101 final-review fixes (2026-10-30): host-side git, legacy agent steps,
project hooks, probe caching, the detached-shell view, plan-step cwd.

Each test fails on f7d36f927. No bwrap runs: the sandbox is a duck-typed
context (``FakeSandbox``) or off; git runs for real in ``tmp_path``.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from r4.companion import shell_detach
from r4.companion.sandbox import (
    UNAVAILABLE,
    SandboxError,
    SandboxSetting,
    SpawnSpec,
    set_current_sandbox,
)
from r4.companion.sandbox import backends as backends_mod
from r4.companion.sandbox import selftest as selftest_mod

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


class FakeSandbox:
    """Duck-typed on context: prefixes ``env`` and marks the child's env."""

    setting = SandboxSetting("on", "", "slash")

    def __init__(self, error: SandboxError | None = None) -> None:
        self.error = error
        self.calls: list[Any] = []

    def wrap(self, args: Any, *, shell: bool, cwd: Any, env: dict[str, str]) -> SpawnSpec:
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        inner = ["/bin/sh", "-c", args] if shell else list(args)
        child_env = {**env, "FAKE_SBX_ENV": "wrapped"}
        return SpawnSpec(["env", *inner], False, child_env, True, "fake", wrapped=True)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in ("R4_SANDBOX", "R4_SANDBOXED", "R4_LAUNCH_SANDBOX"):
        monkeypatch.delenv(name, raising=False)
    set_current_sandbox(None)
    yield
    set_current_sandbox(None)


def _executable(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# ── ESC-1: r4's own git calls never run a planted fsmonitor ─────────────────


def _planted_repo(tmp_path: Path) -> tuple[Path, Path]:
    """What the sandboxed shell could do: a nested repo whose fsmonitor is a script."""
    repo = tmp_path / "sub"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "notes.txt").write_text("x\n")
    subprocess.run(["git", "add", "notes.txt"], cwd=repo, check=True)
    marker = tmp_path / "pwned"
    hook = _executable(repo / "hook.sh", f"id -un > {marker}\nexit 1")
    subprocess.run(["git", "config", "core.fsmonitor", str(hook)], cwd=repo, check=True)
    return repo, marker


@needs_git
def test_list_dir_never_runs_a_planted_fsmonitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from r4.companion import file_filtering

    repo, marker = _planted_repo(tmp_path)
    # The plant works: plain git (the old call) runs the hook on the host.
    plain = ["git", "check-ignore", "-z", "--stdin"]
    subprocess.run(plain, cwd=repo, input=b"notes.txt\0", capture_output=True, check=False)
    assert marker.exists(), "precondition: this git honours core.fsmonitor"
    marker.unlink()
    monkeypatch.setattr(file_filtering, "respect_gitignore", lambda: True)
    entries, _notes = file_filtering.list_directory_entries(repo)
    assert "notes.txt" in {entry.name for entry in entries}
    assert not marker.exists()


@needs_git
def test_the_session_probe_runs_git_hardened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The finding did not reproduce an escape through rev-parse/branch/log;
    # the probe still must not hand an agent-made repository's config a run.
    from r4.companion.harness import probe as probe_mod
    from r4.safe_git import GIT_SAFE_CONFIG

    repo, marker = _planted_repo(tmp_path)
    calls: list[Any] = []
    real_run = subprocess.run

    def spy(argv: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append((argv, kwargs.get("env") or {}))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(probe_mod.subprocess, "run", spy)
    snap = probe_mod.EnvironmentSnapshot(cwd=str(repo))
    probe_mod.EnvironmentProbe.__new__(probe_mod.EnvironmentProbe)._probe_git(snap)
    assert snap.is_git_repo is True and not marker.exists()
    assert len(calls) == 3
    for argv, env in calls:
        assert argv[: 1 + len(GIT_SAFE_CONFIG)] == ["git", *GIT_SAFE_CONFIG]
        assert env.get("GIT_CONFIG_NOSYSTEM") == "1"


def test_the_tui_diff_switches_off_external_diff_and_textconv() -> None:
    from r4.safe_git import GIT_SAFE_CONFIG
    from r4.tui.commands import _git_diff_args

    args = _git_diff_args(".", False, ["--stat"])
    assert args[: 1 + len(GIT_SAFE_CONFIG)] == ["git", *GIT_SAFE_CONFIG]
    assert "core.fsmonitor=false" in args and "--no-ext-diff" in args and "--no-textconv" in args


# ── ESC-5: the legacy r4 agent's shell and python steps ─────────────────────


def _executor() -> Any:
    from r4.agent.core import EgressDecision, WorkflowExecutor

    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.egress_guard = SimpleNamespace(check=lambda **_kw: EgressDecision.APPROVED)
    return executor


def _step(**config: Any) -> Any:
    return SimpleNamespace(id="s1", tool_name=None, config=config)


def test_a_legacy_shell_step_runs_through_the_sandbox() -> None:
    from r4.agent.core import StepStatus

    set_current_sandbox(FakeSandbox())  # type: ignore[arg-type]
    result = _executor()._execute_shell(_step(command="sh -c 'echo $FAKE_SBX_ENV'"), {})
    assert result.status == StepStatus.COMPLETED
    assert result.output["stdout"].strip() == "wrapped"


def test_a_refused_legacy_shell_step_fails_and_runs_nothing(tmp_path: Path) -> None:
    from r4.agent.core import StepStatus

    target = tmp_path / "legacy"
    set_current_sandbox(FakeSandbox(SandboxError(UNAVAILABLE, "no backend")))  # type: ignore[arg-type]
    result = _executor()._execute_shell(_step(command=f"touch {target}"), {})
    assert result.status == StepStatus.FAILED and "sandbox.unavailable" in result.error
    assert not target.exists()


def test_a_legacy_python_step_is_refused_while_the_sandbox_is_on() -> None:
    from r4.agent.core import StepStatus

    code = "result = 41 + 1"
    assert _executor()._execute_python(_step(code=code), {}).output == 42  # off: unchanged
    set_current_sandbox(FakeSandbox())  # type: ignore[arg-type]
    result = _executor()._execute_python(_step(code=code), {})
    assert result.status == StepStatus.FAILED and "sandbox.refused" in result.error


# ── follow-up 1: project hooks under the sandbox, hooks.json protected ──────


def test_project_hooks_run_under_the_sandbox_and_user_hooks_do_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from r4.companion import user_hooks

    script = _executable(tmp_path / "hook.sh", 'echo "{\\"inject\\": \\"$FAKE_SBX_ENV\\"}"')
    sandbox = FakeSandbox()
    set_current_sandbox(sandbox)  # type: ignore[arg-type]
    project = user_hooks._HookCommand([str(script)], 5, project=True)
    assert user_hooks._run_hook_command(project, "UserPromptSubmit", {}).inject == "wrapped"
    user = user_hooks._HookCommand([str(script)], 5)
    assert user_hooks._run_hook_command(user, "UserPromptSubmit", {}).inject == ""
    assert sandbox.calls == [[str(script)]]


def test_a_project_hook_file_loads_as_project_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from r4.companion.user_hooks import load_user_hooks

    (tmp_path / ".r4").mkdir()
    (tmp_path / ".r4" / "hooks.json").write_text(json.dumps({"PreToolUse": ["deny.sh"]}))
    monkeypatch.setenv("R4_COMPANION_PROJECT_HOOKS", "1")
    hooks = load_user_hooks(str(tmp_path))
    assert [command.project for command in hooks.commands["PreToolUse"]] == [True]


def test_a_refused_project_hook_runs_nothing(tmp_path: Path) -> None:
    from r4.companion import user_hooks

    marker = tmp_path / "ran"
    script = _executable(tmp_path / "hook.sh", f"touch {marker}")
    set_current_sandbox(FakeSandbox(SandboxError(UNAVAILABLE, "no backend")))  # type: ignore[arg-type]
    command = user_hooks._HookCommand([str(script)], 5, project=True)
    assert user_hooks._run_hook_command(command, "PreToolUse", {}).block is False
    assert not marker.exists()


def test_an_existing_project_hook_file_is_write_protected(tmp_path: Path) -> None:
    from r4.companion.sandbox.policy import write_protection

    (tmp_path / ".r4").mkdir()
    hooks = tmp_path / ".r4" / "hooks.json"
    hooks.write_text("{}")
    existing, _missing = write_protection([str(tmp_path)])
    assert os.path.realpath(hooks) in existing


# ── LC-4: a failed probe or self-test is not remembered ─────────────────────


def test_a_failed_bwrap_probe_is_probed_again(tmp_path: Path) -> None:
    fake = _executable(
        tmp_path / "bwrap", "echo 'setting up uid map: Permission denied' >&2; exit 1"
    )
    backends_mod._PROBE_CACHE.pop(str(fake), None)
    assert backends_mod.BwrapBackend(str(fake)).probe()[0] is False
    _executable(fake, "exit 0")  # the user applied the fix the notice named
    assert backends_mod.BwrapBackend(str(fake)).probe() == (True, "")
    backends_mod._PROBE_CACHE.pop(str(fake), None)


def test_a_failed_self_test_is_run_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from r4.companion.sandbox.policy import SandboxPolicy

    selftest_mod.clear_cache()
    answers = [(False, "the self-test probe did not run: timed out after 30s"), (True, "")]
    monkeypatch.setattr(selftest_mod, "_run_probe", lambda backend, policy: answers.pop(0))
    backend, policy = SimpleNamespace(name="bwrap"), SandboxPolicy(write_roots=("/x",))
    assert selftest_mod.self_test_backend(backend, policy)[0] is False  # type: ignore[arg-type]
    assert selftest_mod.self_test_backend(backend, policy) == (True, "")  # type: ignore[arg-type]
    assert selftest_mod.self_test_backend(backend, policy) == (True, "")  # type: ignore[arg-type]
    selftest_mod.clear_cache()


def test_a_timeout_reason_is_one_short_line() -> None:
    exc = subprocess.TimeoutExpired(["/usr/bin/bwrap", "--ro-bind", "/", "/", "x" * 3000], 30)
    assert backends_mod.exc_reason(exc) == "timed out after 30s"
    assert len(backends_mod.exc_reason(OSError("a\nb" + "c" * 500))) <= 200


def test_a_timed_out_help_probe_is_not_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = str(tmp_path / "bwrap")
    calls: list[Any] = []

    def slow(*_a: Any, **_kw: Any) -> Any:
        calls.append(1)
        raise subprocess.TimeoutExpired("bwrap", 10)

    monkeypatch.setattr(backends_mod.subprocess, "run", slow)
    backend = backends_mod.BwrapBackend(fake)
    assert backend.supports_disable_userns() is False
    assert backend.supports_disable_userns() is False
    assert len(calls) == 2 and fake not in backends_mod._USERNS_FLAG_CACHE


# ── Q-10: only a wrapper r4 built has a private view ────────────────────────


def test_an_inherited_spec_is_never_a_private_view(tmp_path: Path) -> None:
    proc = subprocess.Popen(["sleep", "5"], start_new_session=True)
    try:
        inherited = SpawnSpec(["sleep", "5"], False, {}, sandboxed=True, backend="bwrap")
        out, err = tmp_path / "o", tmp_path / "e"
        out.write_text("")
        err.write_text("")
        run = shell_detach._detach(proc, out, err, 100, spec=inherited)
        assert run.detached is not None and run.detached.sandboxed is True
        assert run.detached.private_view is False
    finally:
        proc.kill()
        proc.wait()
        shell_detach._DETACHED.pop(proc.pid, None)


# ── OFF-1: a build marker above the agent workspace ─────────────────────────


def test_a_marker_above_the_workspace_does_not_move_the_step_out_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from r4.companion import tools
    from r4.companion.agent_workspace import AgentWorkspace, agent_workspace

    mono = tmp_path / "mono"
    (mono / ".git").mkdir(parents=True)
    sub = mono / "sub"
    sub.mkdir()
    (sub / "x.py").write_text("print('hello from x')\n")
    monkeypatch.chdir(sub)
    with agent_workspace(AgentWorkspace.of(sub)):
        out = tools._run_code("x.py")
    assert "hello from x" in out and "refused" not in out


# ── LC-3: the session directory outlives a dropped context ──────────────────


def test_a_dropped_context_keeps_its_dir_for_running_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gc
    import tempfile

    from r4.companion.sandbox import SandboxContext
    from r4.companion.web_access import make_web_access

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    web = make_web_access("none", source="flag")
    ctx = SandboxContext(
        setting=lambda: SandboxSetting("off", "", "default_off"), web_access=lambda: web
    )
    tmp = ctx.session_dirs().tmp
    (Path(tmp) / "job.out").write_text("checkpoint")
    finalizer = ctx._finalizer
    del ctx
    gc.collect()
    assert (Path(tmp) / "job.out").read_text() == "checkpoint"
    assert finalizer is not None
    finalizer()
    assert not os.path.exists(tmp)
