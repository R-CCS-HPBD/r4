"""V-06 / D-27 pins: client skills honour the ``version`` front-matter field."""

from __future__ import annotations

from r4.companion.skills import BUILTIN_SKILLS_DIR, SkillRegistry


def test_builtin_skill_version_is_parsed() -> None:
    registry = SkillRegistry()
    skill = registry.get("ara-compiler")
    assert skill is not None, "ara-compiler builtin must load"
    assert skill.version == "1.0.0"


def test_skills_without_version_default_to_empty() -> None:
    skill = SkillRegistry._parse_skill_md(BUILTIN_SKILLS_DIR / "hpc" / "SKILL.md")
    assert skill is not None
    assert skill.version == ""


def test_at_least_one_builtin_declares_a_version() -> None:
    registry = SkillRegistry()
    versioned = [s.name for s in registry.builtin_skills if s.version]
    assert versioned, "expected at least one builtin SKILL.md to declare version:"
