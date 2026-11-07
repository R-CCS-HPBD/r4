"""Regression tests: ``r4 project switch`` must align runtime default project.

Before this change, ``project switch`` only flipped the local project store's
``current`` pointer, but the runtime profile config kept its own
``project_id`` default — so users had to ``export R4_PROJECT_ID=<id>`` for
``r4 project current`` to show matching runtime defaults.

Also keeps the ``config context --project`` behaviour symmetric: it already
syncs the project store in the other direction.
"""

from __future__ import annotations

from argparse import Namespace

import pytest

pytest.importorskip("umrs")

from r4._cli.project import cmd_project  # noqa: E402
from r4.config import ProfileConfigStore, get_config, reload_config  # noqa: E402
from r4.project import ProjectConfig, ProjectManager  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Isolate project manager + profile store + runtime config."""
    pm = ProjectManager(config_dir=tmp_path / "r4-config")
    monkeypatch.setattr("r4.project._project_manager", pm)

    store_dir = tmp_path / "profiles"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(store_dir))
    monkeypatch.setenv("R4_CONFIG_DIR", str(store_dir / "r4"))

    # Environment must not mask persisted context in these tests.
    monkeypatch.delenv("R4_PROJECT_ID", raising=False)
    monkeypatch.delenv("R4_CONFIG_PROFILE", raising=False)

    # Fresh global config against a non-existent profile dir.
    reload_config(profile="default")
    return pm


def _make_project(pm, project_id: str, name: str = "Test") -> None:
    pm.create(ProjectConfig(project_id=project_id, name=name))


def test_project_switch_persists_profile_project_id(isolated):
    """switch writes project_id into the active profile's saved config."""
    _make_project(isolated, "alpha")
    _make_project(isolated, "beta")

    cmd_project(Namespace(project_command="switch", project_id="alpha"))
    saved = ProfileConfigStore().load("default")
    assert saved.get("project_id") == "alpha"

    cmd_project(Namespace(project_command="switch", project_id="beta"))
    saved = ProfileConfigStore().load("default")
    assert saved.get("project_id") == "beta"
    assert isolated.get_current() == "beta"


def test_project_current_runtime_defaults_match(isolated, capsys):
    """After switch, runtime default project == current project (no env needed)."""
    _make_project(isolated, "example-project-a", name="Example Project A")

    cmd_project(Namespace(project_command="switch", project_id="example-project-a"))
    capsys.readouterr()

    cmd_project(Namespace(project_command="current"))
    out = capsys.readouterr().out
    assert "Current project: example-project-a" in out
    assert "  Project: example-project-a" in out
    # And get_config() reflects it without env vars.
    assert get_config().project_id == "example-project-a"


def test_project_switch_warns_when_env_overrides(isolated, monkeypatch, capsys):
    """Env var masks persisted default → visible note on stderr."""
    _make_project(isolated, "alpha")
    monkeypatch.setenv("R4_PROJECT_ID", "OTHERPROJ")

    cmd_project(Namespace(project_command="switch", project_id="alpha"))
    captured = capsys.readouterr()
    assert "R4_PROJECT_ID=OTHERPROJ" in captured.err
    assert get_config().project_id == "OTHERPROJ"


def test_project_switch_unknown_project_exits(isolated, capsys):
    """Unknown project still fails before touching the profile store."""
    with pytest.raises(SystemExit):
        cmd_project(Namespace(project_command="switch", project_id="nope"))
    err = capsys.readouterr().err
    assert "Project not found" in err
    assert ProfileConfigStore().load("default").get("project_id") in (None, "")
