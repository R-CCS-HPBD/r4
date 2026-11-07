"""ADR-111: ``/memory`` and ``r4 memory`` verbs (list, add, forget,
lessons, promote, off/on)."""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.companion.harness.steering import SteeringLoop
from r4.companion.memory_commands import MemoryCommandContext, run_memory_command
from r4.companion.memory_store import MemoryStore


@pytest.fixture
def ctx(tmp_path) -> MemoryCommandContext:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return MemoryCommandContext(
        store=MemoryStore(tmp_path / "memory", "gw--demo"),
        steering=SteeringLoop(lessons_dir=tmp_path / "lessons"),
        session=SimpleNamespace(session_id="s-1", metadata={}),
        cwd=repo,
    )


def _run(ctx, line: str) -> list[str]:
    return run_memory_command(line.split(), ctx)


def test_add_list_show_forget(ctx):
    assert _run(ctx, "list") == ["No saved memory yet. Add a fact: /memory add <text>"]
    assert _run(ctx, "add --user I am a seismologist") == ["Saved user/i-am-a-seismologist."]
    assert _run(ctx, "add use ng-dgx-m3 for staged steps") == [
        "Saved project/use-ng-dgx-m3-for-staged-steps."
    ]
    listing = _run(ctx, "")
    assert listing[0] == "user:" and "project:" in listing
    shown = _run(ctx, "show use-ng-dgx-m3-for-staged-steps")
    assert shown[0].startswith("project/use-ng-dgx-m3-for-staged-steps [project]")
    assert _run(ctx, "forget i-am-a-seismologist") == ["Forgot user/i-am-a-seismologist."]
    assert _run(ctx, "forget nothing-here") == ["No saved fact named 'nothing-here'."]


def test_same_words_get_a_free_name(ctx):
    _run(ctx, "add note one")
    assert _run(ctx, "add note one") == ["Saved project/note-one-2."]


def test_errors_come_back_as_lines(ctx):
    assert _run(ctx, "add --expires tomorrow x") == ["memory: expires must be YYYY-MM-DD"]
    assert _run(ctx, "bogus")[0].startswith("usage:")


def test_lessons_list_and_forget(ctx):
    ctx.steering.add_lesson("c", "m", "no, use squeue --me", "use squeue --me")
    ctx.steering.add_lesson("c", "m", "no, keep jobs short", "keep jobs short")
    assert _run(ctx, "list")[-1] == "lessons from your corrections: 2 (/memory lessons)"
    assert _run(ctx, "lessons")[1:] == ["  1. use squeue --me", "  2. keep jobs short"]
    assert _run(ctx, "lessons forget 1") == ["Forgot lesson 1: use squeue --me"]
    assert [lesson.rule for lesson in SteeringLoop(ctx.steering._dir).list_lessons()] == [
        "keep jobs short"
    ]
    assert _run(ctx, "lessons forget 9") == ["No lesson 9."]
    assert _run(ctx, "lessons clear") == ["Cleared every lesson."]


def test_promote_to_briefing_appends_to_the_repository_r4md(ctx):
    _run(ctx, "add always cite the dataset DOI")
    (ctx.cwd / "R4.md").write_text("# Briefing\n\nExisting rule.")
    lines = _run(ctx, "promote always-cite-the-dataset-doi briefing")
    assert lines[0].startswith("Appended always-cite-the-dataset-doi")
    text = (ctx.cwd / "R4.md").read_text()
    assert text.startswith("# Briefing\n\nExisting rule.\n")
    assert "## From saved memory\n\n- always cite the dataset DOI\n" in text
    _run(ctx, "add second rule")
    _run(ctx, "promote second-rule briefing")
    assert (ctx.cwd / "R4.md").read_text().count("## From saved memory") == 1


def test_promote_to_knowledge_stages_a_proposal_only(ctx):
    _run(ctx, "add the 2024 run used 4 km grid spacing")
    ctx.client = MagicMock()
    ctx.client.knowledge.graph_propose.return_value = {"proposal_id": "prop-9"}
    lines = _run(ctx, "promote the-2024-run-used-4-km knowledge g-1")
    call = ctx.client.knowledge.graph_propose.call_args
    assert call.args == ("g-1",)
    assert "4 km grid spacing" in call.kwargs["missing_information"][0]
    assert lines[-1] == "A reviewer approves it with: r4 knowledge review approve prop-9"


def test_off_and_on_flip_the_session_switch(ctx):
    assert _run(ctx, "off")[0].startswith("Memory is off")
    assert ctx.session.metadata["memory_off"] is True
    assert _run(ctx, "list")[-1] == "memory is off for this session (/memory on)"
    _run(ctx, "on")
    assert ctx.session.metadata["memory_off"] is False


def test_cli_runs_the_same_verbs(tmp_path, monkeypatch, capsys):
    from r4._cli.memory import cmd_memory

    monkeypatch.setenv("R4_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("R4_COMPANION_MEMORY_DIR", str(tmp_path / "legacy"))
    monkeypatch.chdir(tmp_path)
    assert cmd_memory(Namespace(words=["add", "--user", "prefers", "short", "replies"])) == 0
    assert cmd_memory(Namespace(words=["list"])) == 0
    out = capsys.readouterr().out
    assert "Saved user/prefers-short-replies." in out
    assert "prefers-short-replies [user]" in out
    assert cmd_memory(Namespace(words=["off"])) == 1


def test_promote_from_a_linked_worktree_writes_that_worktrees_r4md(ctx, tmp_path):
    lane = ctx.cwd / ".r4" / "worktrees" / "lane"
    lane.mkdir(parents=True)
    (ctx.cwd / ".git" / "worktrees" / "lane").mkdir(parents=True)
    (lane / ".git").write_text(f"gitdir: {ctx.cwd / '.git' / 'worktrees' / 'lane'}\n")
    ctx.cwd = lane
    _run(ctx, "add keep logs under scratch")
    _run(ctx, "promote keep-logs-under-scratch briefing")
    assert (lane / "R4.md").is_file()
    assert not (tmp_path / "repo" / "R4.md").exists()
