"""Large-result spills leave the task directory alone under the terminal profile
(TB 4.0 harness-efficiency plan 2026-10-24, item H16).

By default a spill lands in ``<workspace>/.r4/outputs`` (W2), which a
benchmark verifier could see; with ``R4_AGENT_PROFILE=terminal`` it goes to
``$TMPDIR/r4-outputs``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from r4.agent.large_response import handle_large_response

CAP = 40_000
TERMINAL_PROFILE_SPILL_DIR = "r4-outputs"  # large_response.TERMINAL_PROFILE_SPILL_DIR


@pytest.fixture
def temp_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "systmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _spill(workspace: Path) -> str:
    return handle_large_response(
        "x" * (CAP * 4),
        tool_name="shell_exec",
        truncate_at=CAP,
        summarize_at=CAP * 3,
        workspace=workspace,
    )


def test_terminal_profile_spills_to_the_temp_dir(tmp_path, temp_root, monkeypatch):
    """Old code: the spill went to <workspace>/.r4/outputs."""
    monkeypatch.setenv("R4_AGENT_PROFILE", "terminal")
    workspace = tmp_path / "app"
    workspace.mkdir()
    out = _spill(workspace)
    spills = list((temp_root / TERMINAL_PROFILE_SPILL_DIR).glob("shell_exec_*.txt"))
    assert len(spills) == 1
    assert str(spills[0]) in out
    assert not (workspace / ".r4").exists()


def test_default_profile_keeps_the_workspace_spill(tmp_path, temp_root, monkeypatch):
    monkeypatch.delenv("R4_AGENT_PROFILE", raising=False)
    workspace = tmp_path / "app"
    workspace.mkdir()
    out = _spill(workspace)
    spills = list((workspace / ".r4" / "outputs").glob("shell_exec_*.txt"))
    assert len(spills) == 1
    assert str(spills[0]) in out
    assert not (temp_root / TERMINAL_PROFILE_SPILL_DIR).exists()


def test_other_profile_values_are_the_default(tmp_path, temp_root, monkeypatch):
    monkeypatch.setenv("R4_AGENT_PROFILE", "research")
    workspace = tmp_path / "app"
    workspace.mkdir()
    _spill(workspace)
    assert (workspace / ".r4" / "outputs").is_dir()
