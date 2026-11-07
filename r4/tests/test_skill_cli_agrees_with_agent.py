"""`r4 skill list` shows the skills the agent loads.

Defect 6 of defect-wave-2026-10-08: on a fresh HOME the CLI printed "No skills
found." while the agent loaded every packaged builtin. The CLI scanned
``~/.agents/skills`` (which the agent never loads) and ``~/.config/r4/skills``;
the agent loads builtins, its user dirs, then the project dirs, later ones
overriding by name.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from r4 import skills as skills_mod
from r4.companion import skills as agent_skills


def _skill(root: Path, name: str, description: str) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\nbody\n")
    return path


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Agent roots redirected into tmp: no real HOME or cwd is read."""
    user = tmp_path / "user"
    project = tmp_path / "project" / ".r4" / "skills"
    monkeypatch.setattr(agent_skills.SkillRegistry, "_USER_SKILL_DIRS", [user])
    monkeypatch.setattr(
        agent_skills.SkillRegistry, "_project_skill_dirs", staticmethod(lambda: [project])
    )
    return user, project


def _builtin_names() -> set[str]:
    return {
        p.name
        for p in agent_skills.BUILTIN_SKILLS_DIR.iterdir()
        if p.is_dir() and (p / "SKILL.md").exists()
    }


def test_builtins_are_listed_with_their_source(roots):
    loaded = {s.name: s for s in skills_mod.SkillLoader().scan_local()}
    assert _builtin_names() <= set(loaded)
    assert {loaded[name].source for name in _builtin_names()} == {"builtin"}


def test_cli_lists_exactly_what_the_agent_loads(roots):
    user, project = roots
    _skill(user, "my-fit", "user skill")
    _skill(project, "lab-notes", "project skill")
    registry = agent_skills.SkillRegistry()
    registry.load_user_skills()
    agent_view = {s.name: s.source for s in registry.list_all()}
    cli_view = {s.name: s.source for s in skills_mod.SkillLoader().scan_local()}
    assert cli_view == agent_view


def test_later_roots_override_earlier_by_name(roots):
    user, project = roots
    builtin = sorted(_builtin_names())[0]
    _skill(user, builtin, "my override")
    _skill(project, "shared", "from user")  # project wins over user below
    _skill(user, "shared", "from user")
    loader = skills_mod.SkillLoader()
    loader.scan_local()
    assert loader.get(builtin).source == "user"
    assert loader.get("shared").source == "project"


def test_list_command_is_not_empty_on_a_fresh_home(roots, capsys):
    from r4._cli.skill import SkillCommand

    assert SkillCommand()._list(Namespace(as_json=False)) == 0
    out = capsys.readouterr().out
    assert "No skills found" not in out
    assert "builtin" in out


@pytest.mark.parametrize("name", ["..", "../x", "a/b", ""])
def test_uninstall_refuses_names_that_escape_the_skill_dir(name, tmp_path, monkeypatch, capsys):
    from r4._cli import skill as skill_cli

    victim = tmp_path / "config-r4"
    (victim / "skills").mkdir(parents=True)
    (victim / "keep.txt").write_text("x")
    monkeypatch.setattr(skill_cli, "_SKILL_DIR", victim / "skills")
    assert skill_cli.SkillCommand()._uninstall(Namespace(name=name)) == 1
    assert (victim / "keep.txt").exists()


def test_description_comes_from_the_front_matter(roots):
    """The table showed '---' (the front-matter fence) as every builtin's description."""
    user, _ = roots
    _skill(user, "my-fit", "Fit a peak with a Voigt profile")
    loader = skills_mod.SkillLoader()
    loader.scan_local()
    assert loader.get("my-fit").description == "Fit a peak with a Voigt profile"
    assert all(s.description != "---" for s in loader.list_skills())
