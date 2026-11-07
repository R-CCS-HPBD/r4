"""/overview: 'help' was taken as a project id, and every snapshot line
became its own notice — ~60 for one command (stabilization 2026-10-21,
slash-c#4)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from r4.tui.commands import _cmd_overview


def _overview_app() -> SimpleNamespace:
    return SimpleNamespace(renderer=MagicMock(), client=SimpleNamespace())


def test_overview_help_prints_usage_and_builds_nothing() -> None:
    app = _overview_app()
    with patch("r4.companion.status_report.build_project_status") as build:
        _cmd_overview(app, ["help"])
        _cmd_overview(app, ["a", "b"])
    build.assert_not_called()
    infos = [call.args[0] for call in app.renderer.info.call_args_list]
    assert infos[0].startswith("usage: /overview [project-id]")
    assert "unexpected 'a b'" in infos[1]


def test_overview_renders_one_block_naming_its_scope() -> None:
    app = _overview_app()
    snapshot = "## Health\nok\n\n## Skills\n- a\n- b"
    with patch("r4.companion.status_report.build_project_status", return_value=snapshot):
        _cmd_overview(app, ["proj-x"])
    assert app.renderer.system.call_count == 0
    assert app.renderer.info.call_count == 1
    text = app.renderer.info.call_args.args[0]
    assert text.startswith("overview (project proj-x)\nHealth\nok")
