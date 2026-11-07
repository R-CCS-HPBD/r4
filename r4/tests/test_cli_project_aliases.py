"""Tests for r4 project import/init aliases and deprecated ingest/understand names."""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import patch

from r4._cli.project import ProjectCommand, _project_handlers, cmd_project


def test_handlers_include_canonical_and_legacy_names():
    handlers = _project_handlers()
    # Legacy names are deprecation wrappers that delegate to the canonical
    # handlers (TASK-006) — they are distinct callables, not identical refs.
    assert "import" in handlers and "ingest" in handlers
    assert "init" in handlers and "understand" in handlers
    assert handlers["ingest"] is not handlers["import"]
    assert handlers["understand"] is not handlers["init"]


def test_project_parser_registers_import_and_init():
    import argparse

    root = argparse.ArgumentParser()
    top = root.add_subparsers(dest="command")
    project_parser = ProjectCommand().register(top)
    proj_sub = next(a for a in project_parser._actions if a.dest == "project_command")
    assert {"import", "init", "ingest", "understand"}.issubset(set(proj_sub.choices))


def test_ingest_emits_deprecation_warning(capsys):
    called = []

    def fake_ingest(args, pm, *rest):
        called.append(args.project_id)

    from r4._cli.project import _deprecated_handler

    with (
        patch("r4.project.get_project_manager"),
        patch(
            "r4.config.get_config",
            return_value=type("Cfg", (), {"profile": "default"})(),
        ),
        patch("r4.config.reload_config"),
        patch(
            "r4._cli.project._project_handlers",
            return_value={
                **_project_handlers(),
                "ingest": _deprecated_handler("ingest", "import", fake_ingest),
            },
        ),
    ):
        cmd_project(Namespace(project_command="ingest", project_id="demo"))

    assert called == ["demo"]
    err = capsys.readouterr().err
    assert "deprecated" in err.lower()
    assert "r4 project import" in err


def test_import_does_not_emit_deprecation_warning(capsys):
    called = []

    def fake_ingest(args, pm, *rest):
        called.append(True)

    with (
        patch("r4.project.get_project_manager"),
        patch(
            "r4.config.get_config",
            return_value=type("Cfg", (), {"profile": "default"})(),
        ),
        patch("r4.config.reload_config"),
        patch(
            "r4._cli.project._project_handlers",
            return_value={**_project_handlers(), "import": fake_ingest},
        ),
    ):
        cmd_project(Namespace(project_command="import", project_id="demo"))

    assert called
    assert "deprecated" not in capsys.readouterr().err.lower()


def test_understand_emits_deprecation_warning(capsys):
    from r4._cli.project import _deprecated_handler

    with (
        patch("r4.project.get_project_manager"),
        patch(
            "r4.config.get_config",
            return_value=type("Cfg", (), {"profile": "default"})(),
        ),
        patch("r4.config.reload_config"),
        patch(
            "r4._cli.project._project_handlers",
            return_value={
                **_project_handlers(),
                "understand": _deprecated_handler("understand", "init", lambda *a, **k: None),
            },
        ),
    ):
        cmd_project(Namespace(project_command="understand", project_id="demo"))

    err = capsys.readouterr().err
    assert "deprecated" in err.lower()
    assert "r4 project init" in err
