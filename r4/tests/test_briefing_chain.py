"""Pins for the multi-file briefing chain (T5 topic-19 — instruction files)."""

from __future__ import annotations

from pathlib import Path

from r4.briefing import find_instruction_files, find_r4_md, load_briefing


def _touch(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class TestInstructionChain:
    def test_walk_picks_up_hits_at_current_dir_level_first(self, tmp_path, monkeypatch):
        agent_dir = tmp_path / "workspace" / "proj"
        agent_dir.mkdir(parents=True)
        _touch(tmp_path / "AGENTS.md", "root-AGENTS-body")
        _touch(agent_dir / "AGENTS.md", "inner-AGENTS-body")
        monkeypatch.chdir(agent_dir)
        hits = dict(find_instruction_files(agent_dir))
        assert hits.get("AGENTS.md") == agent_dir / "AGENTS.md"

    def test_agents_md_loads_via_briefing_chain(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _touch(tmp_path / "AGENTS.md", "my-project-rules")
        body = load_briefing(tmp_path)
        assert body is not None
        assert "my-project-rules" in body
        assert "AGENTS.md" in body  # origin-stamped per instruction family

    def test_agent_md_loads_when_agents_missing(self, tmp_path):
        _touch(tmp_path / "agent.md", "lowercase-agent-md")
        body = load_briefing(tmp_path)
        assert "lowercase-agent-md" in body

    def test_claude_and_opencode_md_also_count(self, tmp_path):
        _touch(tmp_path / "CLAUDE.md", "claude-rule-body")
        _touch(tmp_path / "OPENCODE.md", "opencode-rule-body")
        body = load_briefing(tmp_path)
        assert "claude-rule-body" in body
        assert "opencode-rule-body" in body

    def test_priority_order_r4_first_then_agents_then_agent_then_claude_then_opencode(
        self, tmp_path
    ):
        _touch(tmp_path / "R4.md", "r4-body")
        _touch(tmp_path / "AGENTS.md", "agents-body")
        _touch(tmp_path / "agent.md", "agent-body")
        _touch(tmp_path / "CLAUDE.md", "claude-body")
        _touch(tmp_path / "OPENCODE.md", "opencode-body")
        hits = [name for name, _ in find_instruction_files(tmp_path)]
        assert hits == ["R4.md", "AGENTS.md", "agent.md", "CLAUDE.md", "OPENCODE.md"]

    def test_no_files_means_none_returned_honestly(self, tmp_path):
        assert load_briefing(tmp_path) is None

    def test_legacy_wrapper_still_resolves_r4_md_only(self, tmp_path):
        _touch(tmp_path / "R4.md", "r4-content")
        _touch(tmp_path / "AGENTS.md", "agents-content")
        assert find_r4_md(tmp_path) == tmp_path / "R4.md"
