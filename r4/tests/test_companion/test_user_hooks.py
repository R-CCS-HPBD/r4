"""G13 (2026-10-29): user/project-configured shell hooks.

Shell commands configured in ``~/.config/r4/hooks.json`` (always) and
``.r4/hooks.json`` (opt-in via R4_COMPANION_PROJECT_HOOKS) fire at
PreToolUse / PostToolUse / UserPromptSubmit / PreCompact; exit 2 blocks,
stdout JSON allows ``block``/``reason``/``inject``; failures log and allow.
"""

from __future__ import annotations

import json
import stat

import pytest

from r4.companion import user_hooks
from r4.companion.user_hooks import (
    UserHooks,
    load_user_hooks,
    register_dispatchers,
    set_current_user_hooks,
)


def _script(tmp_path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def _write_hooks(directory, data: dict) -> None:
    hooks_dir = directory / ".r4"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "hooks.json").write_text(json.dumps(data))


@pytest.fixture(autouse=True)
def _clean_dispatch():
    previous = user_hooks._current_user_hooks
    set_current_user_hooks(None)
    yield
    set_current_user_hooks(previous)


def test_project_hooks_are_opt_in(monkeypatch, tmp_path) -> None:
    _write_hooks(tmp_path, {"PreToolUse": [{"command": "deny.sh"}]})
    monkeypatch.delenv("R4_COMPANION_PROJECT_HOOKS", raising=False)
    assert not load_user_hooks(str(tmp_path)).any("PreToolUse")
    monkeypatch.setenv("R4_COMPANION_PROJECT_HOOKS", "1")
    assert load_user_hooks(str(tmp_path)).any("PreToolUse")


def test_malformed_config_is_skipped(monkeypatch, tmp_path) -> None:
    (tmp_path / ".r4").mkdir()
    (tmp_path / ".r4/hooks.json").write_text("{not json")
    monkeypatch.setenv("R4_COMPANION_PROJECT_HOOKS", "1")
    hooks = load_user_hooks(str(tmp_path))
    assert not hooks.any("PreToolUse")  # warns, never crashes the session


def test_block_exit_code_and_reason(monkeypatch, tmp_path) -> None:
    blocker = _script(tmp_path, "block.sh", "echo 'writes need review' >&2\nexit 2")
    monkeypatch.chdir(tmp_path)
    hooks = UserHooks()
    hooks.commands["PreToolUse"].append(user_hooks._HookCommand([blocker], 5))
    set_current_user_hooks(hooks)
    register_dispatchers()
    from r4.agent.hooks import HookAction, _default_hook_registry

    result = _default_hook_registry().run_pre_hooks("file_write", {"path": "x"})
    assert result.action == HookAction.BLOCK
    assert "writes need review" in result.reason


def test_stdout_json_inject(monkeypatch, tmp_path) -> None:
    injector = _script(
        tmp_path,
        "inject.sh",
        f"cat >/dev/null; echo '{json.dumps({'inject': 'repo uses pytest -q'})}'",
    )
    monkeypatch.chdir(tmp_path)
    hooks = UserHooks()
    hooks.commands["UserPromptSubmit"].append(user_hooks._HookCommand([injector], 5))
    decision = hooks.run("UserPromptSubmit", {"user_message": "fix the test"})
    assert not decision.block
    assert decision.inject == "repo uses pytest -q"


def test_timeout_allows_and_never_raises(tmp_path) -> None:
    sleeper = _script(tmp_path, "sleep.sh", "sleep 30")
    hooks = UserHooks()
    hooks.commands["PreToolUse"].append(user_hooks._HookCommand([sleeper], 1))
    decision = hooks.run("PreToolUse", {"tool_name": "shell_exec", "arguments": {}})
    assert not decision.block


def test_prompt_inject_reaches_the_turn_context(monkeypatch, tmp_path) -> None:
    injector = _script(
        tmp_path,
        "ctx.sh",
        f"cat >/dev/null; echo '{json.dumps({'inject': 'injected context line'})}'",
    )
    _write_hooks(tmp_path, {"UserPromptSubmit": [{"command": injector}]})
    monkeypatch.setenv("R4_COMPANION_PROJECT_HOOKS", "1")
    import r4.companion.model_resolution as model_resolution
    from r4.companion.permissions import PermissionMode
    from r4.companion.session import CompanionSession

    from .offline_session import OfflineClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("R4_HARNESS_ABLATE_MEMORY", "1")
    monkeypatch.setattr(model_resolution, "ensure_model_ready", lambda *_a, **_k: None)
    session = CompanionSession(
        session_id="hooks",
        name="hooks",
        client=OfflineClient(),
        model="test-model",
        permission_mode=PermissionMode.TRUST,
        endpoint_refresh_interval_sec=86_400,
    )
    session._start_turn("hello")
    assert session._user_hook_inject == "injected context line"
    _, turn_parts = session._turn_parts()
    assert "injected context line" in turn_parts


def test_post_tool_use_inject_appends(monkeypatch, tmp_path) -> None:
    note = _script(
        tmp_path,
        "note.sh",
        f"cat >/dev/null; echo '{json.dumps({'inject': '[audited]'})}'",
    )
    monkeypatch.chdir(tmp_path)
    hooks = UserHooks()
    hooks.commands["PostToolUse"].append(user_hooks._HookCommand([note], 5))
    set_current_user_hooks(hooks)
    register_dispatchers()
    from r4.agent.hooks import _default_hook_registry

    result = _default_hook_registry().run_post_hooks("file_read", {"path": "x"}, "contents")
    assert result.modified_output == "contents\n[audited]"
