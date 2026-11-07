"""CLI smoke tests for `r4 companion` subcommand."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest


@pytest.fixture(autouse=True)
def _require_r4_on_path():
    """Skip CLI tests if the r4 entry point is not installed."""
    if shutil.which("r4") is None:
        pytest.skip("r4 CLI not on PATH")


@pytest.fixture()
def clean_home(tmp_path):
    """Subprocess env with an isolated HOME/XDG_CONFIG_HOME.

    Without it, tests read the developer's real companion store
    (~/.config/r4) and fail whenever the user has saved sessions.
    """
    home = tmp_path / "home"
    config = tmp_path / "config"
    home.mkdir()
    config.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(config)
    return env


def test_companion_help():
    """r4 companion --help exits 0 and shows expected flags."""
    result = subprocess.run(
        ["r4", "companion", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "--resume" in result.stdout
    assert "--skill" in result.stdout
    assert "--model" in result.stdout
    assert "sessions" in result.stdout
    assert "export" in result.stdout


def test_companion_sessions_empty(clean_home):
    """r4 companion sessions works when no sessions exist."""
    result = subprocess.run(
        ["r4", "companion", "sessions"],
        capture_output=True,
        text=True,
        timeout=10,
        env=clean_home,
    )
    assert result.returncode == 0
    # Isolated HOME → the store is empty → "No saved sessions."
    assert "no saved sessions" in result.stdout.lower()
