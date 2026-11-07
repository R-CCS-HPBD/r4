"""Quiet-exit contract for Ctrl+C during TUI launch.

Regression: Ctrl+C inside child-process wait leaked a full subprocess
traceback through cli.main. Launchers must terminate the child quietly and
exit 130.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from r4._cli.console import _launch_binary, _launch_dev_source, cmd_console


def test_launch_binary_ctrl_c_quits_silently(tmp_path: Path):
    fake = MagicMock()
    fake.wait.side_effect = [KeyboardInterrupt, 0]  # interrupt, then child exits
    fake.poll.return_value = None
    with patch("r4._cli.console.subprocess.Popen", return_value=fake) as popen:
        code = _launch_binary(tmp_path / "r4-tui", demo=False, backend="cli", env={})
    assert code == 130
    popen.assert_called_once()
    fake.terminate.assert_called_once()
    assert fake.wait.call_count == 2  # interrupted wait + graceful final wait


def test_launch_binary_normal_exit(tmp_path: Path):
    fake = MagicMock()
    fake.wait.return_value = 0
    with patch("r4._cli.console.subprocess.Popen", return_value=fake):
        code = _launch_binary(tmp_path / "r4-tui", demo=False, backend="cli", env={})
    assert code == 0


def test_launch_dev_source_ctrl_c_quits_silently(tmp_path: Path):
    fake = MagicMock()
    fake.wait.side_effect = [KeyboardInterrupt, 0]  # interrupt, then child exits
    fake.poll.return_value = None
    with (
        patch("r4._cli.console._console_package_dir", return_value=tmp_path),
        patch("r4._cli.console._find_bun", return_value=str(tmp_path / "bun")),
        patch("r4._cli.console.subprocess.Popen", return_value=fake),
    ):
        code = _launch_dev_source(demo=False, backend="cli", env={"PATH": ""})
    assert code == 130
    fake.terminate.assert_called_once()


def test_cmd_console_pins_workspace_to_launch_cwd(tmp_path: Path, monkeypatch):
    """Dev-source launch chdirs into packages/r4-tui-v3, so cwd() is no
    longer the user's launch dir. The TUI resolves its workspace from
    R4_TUI_WORKSPACE first — it must be pinned to the launch pwd."""
    captured: dict = {}

    def fake_launch(*, demo, fresh, backend, env):
        captured.update(env)
        return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("R4_TUI_WORKSPACE", raising=False)
    monkeypatch.setattr("r4._cli.console.resolve_tui_binary", lambda: None)
    monkeypatch.setattr("r4._cli.console._launch_dev_source", fake_launch)
    code = cmd_console(SimpleNamespace(demo=False, offline=False, backend=None))
    assert code == 0
    assert captured["R4_TUI_WORKSPACE"] == str(tmp_path)


def test_cmd_console_respects_explicit_workspace_override(tmp_path: Path, monkeypatch):
    captured: dict = {}

    def fake_launch(*, demo, fresh, backend, env):
        captured.update(env)
        return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("R4_TUI_WORKSPACE", "/explicit/workspace")
    monkeypatch.setattr("r4._cli.console.resolve_tui_binary", lambda: None)
    monkeypatch.setattr("r4._cli.console._launch_dev_source", fake_launch)
    code = cmd_console(SimpleNamespace(demo=False, offline=False, backend=None))
    assert code == 0
    assert captured["R4_TUI_WORKSPACE"] == "/explicit/workspace"
