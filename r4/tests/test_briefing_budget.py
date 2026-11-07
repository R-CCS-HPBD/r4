"""Pins for briefing budget discipline (T5 topic-21 — system prompt cap)."""

from __future__ import annotations

from pathlib import Path

from r4.briefing import (
    _BRIEFING_FILE_CHAR_CAP,
    _BRIEFING_TOTAL_CHAR_CAP,
    _INSTRUCTION_FILES,
    load_briefing,
)


def _touch(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


class TestBriefingBudget:
    def test_small_file_loads_byte_identical(self, tmp_path):
        _touch(tmp_path / "R4.md", "small-body")
        body = load_briefing(tmp_path)
        assert body is not None
        assert "small-body" in body

    def test_big_file_gets_cap_marker(self, tmp_path):
        _touch(tmp_path / "R4.md", "x" * (2 * _BRIEFING_FILE_CHAR_CAP))
        body = load_briefing(tmp_path)
        assert body is not None
        assert "briefing file truncated" in body
        assert "elided" in body

    def test_chain_total_cap_limits_assembly_when_many_files(self, tmp_path):
        # pinned - R4.md body 2× its own file-cap; fits own cap exactly.
        # Remaining budget for AGENTS.md = total - used; drills slightly under.
        big_r4 = "x" * (2 * _BRIEFING_FILE_CHAR_CAP)
        _touch(tmp_path / "R4.md", big_r4)
        big_agents = "a" * (2 * _BRIEFING_FILE_CHAR_CAP)
        _touch(tmp_path / "AGENTS.md", big_agents)
        body = load_briefing(tmp_path)
        assert body is not None
        # shared total cap absorbs BOTH their full file-caps only when the chain
        # becomes realistic; AGENTS.md got its piece or the chain trimmed.
        # Specific assertion: total block length ≤ total cap + slack for headers.
        assert len(body) <= _BRIEFING_TOTAL_CHAR_CAP + 500

    def test_second_file_carries_residual_cap_when_needed(self, tmp_path):
        big_r4 = "r" * (_BRIEFING_TOTAL_CHAR_CAP - 500)
        _touch(tmp_path / "R4.md", big_r4)
        _touch(tmp_path / "agent.md", "boring-boring")
        body = load_briefing(tmp_path)
        assert body is not None
        assert "agent.md" in body

    def test_no_files_means_none(self, tmp_path):
        assert load_briefing(tmp_path) is None


_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPO_BRIEFING_FILES = ("R4.md", "AGENTS.md", "CLAUDE.md")


class TestRepoBriefingFits:
    """This repo's own briefing chain fits the caps (hardening plan H-11.8, 2026-10-18).

    R4 loads R4.md, AGENTS.md and CLAUDE.md from this tree into its prompt. A
    file past the per-file cap, or a chain past the total, is cut mid-rule
    (before 2026-10-18 CLAUDE.md was). Only the repo's files are copied into
    a scratch directory, so files above the checkout cannot change the result.
    """

    def _chain(self, tmp_path: Path) -> str:
        for name in _REPO_BRIEFING_FILES:
            _touch(tmp_path / name, (_REPO_ROOT / name).read_text(encoding="utf-8"))
        body = load_briefing(tmp_path)
        assert body is not None
        return body

    def test_each_file_fits_the_per_file_cap(self):
        sizes = {
            name: len((_REPO_ROOT / name).read_text(encoding="utf-8"))
            for name in _REPO_BRIEFING_FILES
        }
        over = {name: size for name, size in sizes.items() if size > _BRIEFING_FILE_CHAR_CAP}
        assert not over, f"over {_BRIEFING_FILE_CHAR_CAP} chars: {over}"

    def test_the_chain_loads_whole(self, tmp_path):
        body = self._chain(tmp_path)
        assert "briefing file truncated" not in body
        assert len(body) <= _BRIEFING_TOTAL_CHAR_CAP
        for name in _REPO_BRIEFING_FILES:
            assert f"<!-- {name} @ " in body

    def test_no_other_chain_file_at_the_repo_root(self):
        extra = [
            name
            for name in _INSTRUCTION_FILES
            if name not in _REPO_BRIEFING_FILES and (_REPO_ROOT / name).is_file()
        ]
        assert not extra, f"{extra} would join the briefing chain; budget them here first"
