"""Pins for the large-response spill (W2, row H6; gap row D-26).

Results above 3x the per-result cap (40,000 -> 120,000 chars) are written
inside the workspace (``.r4/outputs/<tool>_<sha12>.txt``) and replaced by
head + tail + the path; an unwritable workspace falls back to the system temp
dir; results between the cap and 3x keep the head/tail truncation.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

from r4.agent import large_response
from r4.agent.large_response import (
    SPILL_HEAD_CHARS,
    SPILL_MULTIPLIER,
    SUMMARIZE_THRESHOLD,
    TRUNCATE_THRESHOLD,
    handle_large_response,
    workspace_root,
)

CAP = 40_000  # companion per-result cap (companion/agent.py COMPANION_TOOL_OUTPUT_TRUNCATE)


def _big(size: int) -> str:
    """Distinct head / middle / tail so slices can be told apart."""
    return "H" * 3_000 + "M" * (size - 4_000) + "T" * 1_000


def _spill(content: str, workspace: Path, **kwargs) -> str:
    return handle_large_response(
        content,
        tool_name=kwargs.pop("tool_name", "shell_exec"),
        truncate_at=CAP,
        summarize_at=CAP * SPILL_MULTIPLIER,
        workspace=workspace,
        **kwargs,
    )


def _sha12(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def test_thresholds_follow_the_per_result_cap():
    assert TRUNCATE_THRESHOLD == 40_000
    assert SPILL_MULTIPLIER == 3
    assert SUMMARIZE_THRESHOLD == 120_000


def test_companion_cap_matches_the_handler_default():
    from r4.companion import agent

    assert agent.COMPANION_TOOL_OUTPUT_TRUNCATE == TRUNCATE_THRESHOLD


def test_at_or_under_cap_is_returned_as_is(tmp_path):
    text = "x" * CAP
    assert _spill(text, tmp_path) == text
    assert not (tmp_path / ".r4").exists()


def test_between_cap_and_spill_threshold_is_head_tail_truncated(tmp_path):
    text = _big(CAP * SPILL_MULTIPLIER)  # exactly 120,000: not spilled
    out = _spill(text, tmp_path)
    assert out.startswith("H" * 3_000)
    assert out.endswith("T" * 1_000)
    assert "chars omitted from shell_exec" in out
    assert len(out) < CAP
    assert not (tmp_path / ".r4").exists()


def test_spill_writes_inside_the_workspace(tmp_path):
    text = _big(CAP * SPILL_MULTIPLIER + 1)
    out = _spill(text, tmp_path)
    expected = tmp_path.resolve() / ".r4" / "outputs" / f"shell_exec_{_sha12(text)}.txt"
    assert expected.read_text(encoding="utf-8") == text
    assert str(expected) in out
    assert "file_read" in out
    assert (expected.parent / ".gitignore").read_text().strip().endswith("*")


def test_spill_reference_carries_head_tail_and_path(tmp_path):
    text = _big(200_000)
    out = _spill(text, tmp_path)
    header, head, marker, tail = out.split("\n\n")
    assert "200,000 chars" in header
    assert head == "H" * SPILL_HEAD_CHARS
    assert tail == "T" * 1_000
    assert "197,000 chars omitted" in marker
    assert ".r4/outputs/shell_exec_" in marker
    assert len(out) < 4_000


def test_spill_uses_the_project_dot_r4_above_cwd(tmp_path):
    """A subdirectory must not grow its own .r4/ (it would shadow the project's config)."""
    (tmp_path / ".r4").mkdir()
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    assert workspace_root(sub) == tmp_path.resolve()
    out = _spill(_big(130_000), sub)
    assert not (sub / ".r4").exists()
    assert str(tmp_path.resolve() / ".r4" / "outputs") in out


def test_workspace_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    text = _big(130_000)
    out = handle_large_response(
        text, tool_name="python_exec", truncate_at=CAP, summarize_at=CAP * 3
    )
    expected = tmp_path.resolve() / ".r4" / "outputs" / f"python_exec_{_sha12(text)}.txt"
    assert expected.is_file()
    assert str(expected) in out


def test_tool_name_is_made_file_safe(tmp_path):
    out = _spill(_big(130_000), tmp_path, tool_name="mcp/server:tool name")
    outputs = tmp_path.resolve() / ".r4" / "outputs"
    spilled = [p for p in outputs.iterdir() if p.suffix == ".txt"]
    assert len(spilled) == 1
    assert spilled[0].name.startswith("mcp_server_tool_name_")
    assert str(spilled[0]) in out


def _fallback_path(text: str, tool: str = "shell_exec") -> Path:
    return Path(tempfile.gettempdir()) / f"r4_large_{tool}_{_sha12(text)}.txt"


def test_unwritable_workspace_falls_back_to_tmpdir(tmp_path, monkeypatch):
    """A file where .r4/ should be makes the workspace unwritable even for root."""
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmpdir))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".r4").write_text("not a directory")
    text = _big(130_000)
    out = _spill(text, workspace)
    fallback = _fallback_path(text)
    assert fallback.parent == tmpdir
    assert fallback.read_text(encoding="utf-8") == text
    assert str(fallback) in out
    assert out.split("\n\n")[1] == "H" * SPILL_HEAD_CHARS


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root ignores dir permissions"
)
def test_read_only_workspace_falls_back_to_tmpdir(tmp_path, monkeypatch):
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmpdir))
    workspace = tmp_path / "ro"
    workspace.mkdir()
    workspace.chmod(0o500)
    try:
        text = _big(130_000)
        out = _spill(text, workspace)
    finally:
        workspace.chmod(0o700)
    assert not (workspace / ".r4").exists()
    assert str(_fallback_path(text)) in out


def test_nothing_writable_still_returns_head_and_tail(tmp_path, monkeypatch):
    def refuse(content, directory, fname):
        raise PermissionError(f"refused {directory}")

    monkeypatch.setattr(large_response, "_write_spill", refuse)
    out = _spill(_big(130_000), tmp_path)
    assert "could not be saved" in out
    assert out.split("\n\n")[1] == "H" * SPILL_HEAD_CHARS
    assert out.endswith("T" * 1_000)


def test_explicit_scratch_dir_is_the_only_location(tmp_path):
    scratch = tmp_path / "scratch"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    text = _big(130_000)
    out = _spill(text, workspace, scratch_dir=scratch)
    expected = scratch / f"r4_large_shell_exec_{_sha12(text)}.txt"
    assert expected.read_text(encoding="utf-8") == text
    assert str(expected) in out
    assert not (workspace / ".r4").exists()
