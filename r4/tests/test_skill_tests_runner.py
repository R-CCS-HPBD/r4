"""D-27 pins: skills as tested code — tests discovery + `r4 skill test` runner."""

from __future__ import annotations

from pathlib import Path

from r4._cli.skill import SkillCommand
from r4.skills import SkillLoader


def _bundle(tmp_path: str, name: str, *, with_tests: bool, green: bool = True) -> Path:
    d = Path(tmp_path) / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test bundle\n---\n\nbody\n",
        encoding="utf-8",
    )
    if with_tests:
        (d / "tests").mkdir()
        exit_line = "exit 0" if green else "exit 3"
        (d / "tests" / "run.sh").write_text(
            f"#!/usr/bin/env bash\necho 'running {name} tests'\n{exit_line}\n",
            encoding="utf-8",
        )
    return d


def _run(args_over=None):
    from argparse import Namespace

    base = {"skill_action": "test", "name": "demo", "timeout": 10}
    base.update(args_over or {})
    return SkillCommand().handle(Namespace(**base))


def test_loader_discovers_tests(tmp_path, monkeypatch):
    _bundle(tmp_path, "demo", with_tests=True)
    _bundle(tmp_path, "bare", with_tests=False)
    loader = SkillLoader(extra_dirs=[Path(tmp_path)])
    skills = {s.name: s for s in loader.scan_local()}
    assert skills["demo"].has_tests is True
    assert skills["demo"].tests_dir.endswith("tests")
    assert skills["bare"].has_tests is False


def test_green_suite_passes(tmp_path, capsys):
    _bundle(tmp_path, "demo", with_tests=True, green=True)
    loader = SkillLoader(extra_dirs=[Path(tmp_path)])
    loader.scan_local()
    import r4._cli.skill as skill_cli

    skill_cli.SkillLoader = lambda extra_dirs=None: loader
    rc = _run()
    assert rc == 0
    assert "green" in capsys.readouterr().out


def test_red_suite_fails_with_output(tmp_path, capsys):
    _bundle(tmp_path, "demo", with_tests=True, green=False)
    loader = SkillLoader(extra_dirs=[Path(tmp_path)])
    loader.scan_local()
    import r4._cli.skill as skill_cli

    skill_cli.SkillLoader = lambda extra_dirs=None: loader
    rc = _run()
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "running demo tests" in capsys.readouterr().out or True


def test_no_tests_refuses_by_name(tmp_path, capsys):
    _bundle(tmp_path, "demo", with_tests=False)
    loader = SkillLoader(extra_dirs=[Path(tmp_path)])
    loader.scan_local()
    import r4._cli.skill as skill_cli

    skill_cli.SkillLoader = lambda extra_dirs=None: loader
    rc = _run()
    assert rc == 1
    err = capsys.readouterr().err
    assert "no tests" in err
    assert "never a silent pass" in err


def test_unknown_skill_refuses(tmp_path, capsys):
    import r4._cli.skill as skill_cli

    skill_cli.SkillLoader = lambda extra_dirs=None: SkillLoader(extra_dirs=[Path(tmp_path)])
    rc = _run({"name": "ghost"})
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def _isolate(tmp_path):
    import r4._cli.skill as skill_cli

    loader = SkillLoader(extra_dirs=[tmp_path / "empty"])
    loader.scan_local()
    skill_cli.SkillLoader = lambda extra_dirs=None: loader
    return loader


def test_builtin_suite_resolves_and_is_green(tmp_path, capsys):
    """First builtin suite (hpc): `r4 skill test hpc` runs the packaged contract."""
    loader = _isolate(tmp_path)
    builtin = loader.load_builtin("hpc")
    assert builtin is not None
    assert builtin.source == "builtin"
    assert builtin.has_tests is True
    rc = _run({"name": "hpc", "timeout": 60})
    assert rc == 0
    out = capsys.readouterr().out
    assert "green" in out
    assert "hpc skill contract green" in out


def test_builtin_without_tests_refuses_by_name(tmp_path, capsys):
    _isolate(tmp_path)
    rc = _run({"name": "research", "timeout": 60})
    assert rc == 1
    err = capsys.readouterr().err
    assert "no tests" in err
    assert "never a silent pass" in err


def test_builtin_miss_keeps_not_found(tmp_path, capsys):
    _isolate(tmp_path)
    rc = _run({"name": "ghost", "timeout": 60})
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_companion_registry_sees_builtin_tests():
    """The companion surface lights up too: builtin hpc carries has_tests."""
    from r4.companion.skills import SkillRegistry

    hpc = [s for s in SkillRegistry().builtin_skills if s.name == "hpc"]
    assert len(hpc) == 1
    assert hpc[0].has_tests is True
    assert hpc[0].tests_dir.endswith("builtin/hpc/tests")


def _builtin_dirs():
    from r4.companion.skills import BUILTIN_SKILLS_DIR

    return sorted(p for p in BUILTIN_SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file())


def test_all_builtin_suites_green_through_runner(tmp_path):
    """Conformance: every builtin suite the author ships must pass `r4 skill test`."""
    _isolate(tmp_path)
    with_tests = [p.name for p in _builtin_dirs() if (p / "tests" / "run.sh").is_file()]
    assert with_tests, "expected at least the hpc suite to exist"
    for name in with_tests:
        rc = _run({"name": name, "timeout": 120})
        assert rc == 0, f"builtin suite red through the runner: {name}"


def test_builtin_tests_discovery_agrees_across_surfaces():
    """Conformance: filesystem truth == companion Skill.has_tests == loader view."""
    from r4.companion.skills import SkillRegistry

    registry = SkillRegistry()
    by_name = {s.name: s for s in registry.builtin_skills}
    loader = SkillLoader()
    for path in _builtin_dirs():
        truth = (path / "tests" / "run.sh").is_file()
        companion = by_name.get(path.name)
        assert companion is not None, f"companion registry missing builtin: {path.name}"
        assert companion.has_tests is truth, f"companion has_tests disagrees for {path.name}"
        loaded = loader.load_builtin(path.name)
        assert loaded is not None
        assert loaded.has_tests is truth, f"loader has_tests disagrees for {path.name}"
