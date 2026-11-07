"""`r4 project data-class get|set` — ADR-062 amendment D-5 (2026-10-15).

The owner reads and changes the server project's class from r4. Narrowing
(non_sensitive → sensitive) needs no confirmation; widening needs a typed
confirmation that names the project and the class the change allows —
never a bare --yes, and non-interactive runs must pass --confirm.
"""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _args(**over):
    base = {
        "data_class_action": "get",
        "project_id": "proj-a",
        "new_class": None,
        "confirm": None,
        "json": False,
    }
    base.update(over)
    return Namespace(**base)


def _run(args, current, set_reply=None, *, dies=False):
    """Run the verb against a mock client and return the mock — also when
    the verb dies (``dies=True`` expects SystemExit), so a test can still
    assert that nothing was written (review TV-4, 2026-10-16)."""
    from r4._cli import project_data_class as pdc

    client = MagicMock()
    client.get_platform_project.return_value = current
    client.set_project_data_class.return_value = set_reply if set_reply is not None else current
    with (
        patch("r4.client.Client", return_value=client),
        patch.object(pdc, "_project_id", lambda a, pm: "proj-a"),
    ):
        if dies:
            with pytest.raises(SystemExit):
                pdc.project_data_class(args, MagicMock())
        else:
            pdc.project_data_class(args, MagicMock())
    return client


REPLY_SENSITIVE = {"status": "ok", "project": {"data_class": "sensitive"}}
REPLY_NON_SENSITIVE = {"status": "ok", "project": {"data_class": "non_sensitive"}}


class TestGet:
    def test_get_prints_the_class(self, capsys):
        client = _run(_args(), REPLY_SENSITIVE)
        client.get_platform_project.assert_called_once_with("proj-a")
        assert "sensitive" in capsys.readouterr().out

    def test_get_json(self, capsys):
        _run(_args(json=True), REPLY_NON_SENSITIVE)
        out = capsys.readouterr().out
        assert json.loads(out) == {"project_id": "proj-a", "data_class": "non_sensitive"}


class TestSet:
    def test_narrowing_needs_no_confirmation(self, capsys):
        args = _args(data_class_action="set", new_class="sensitive")
        client = _run(args, REPLY_NON_SENSITIVE)
        client.set_project_data_class.assert_called_once_with("proj-a", "sensitive")

    def test_widening_without_confirm_dies_before_any_write(self, capsys):
        args = _args(data_class_action="set", new_class="non_sensitive")
        client = _run(args, REPLY_SENSITIVE, dies=True)  # stdin is not a TTY here
        client.set_project_data_class.assert_not_called()
        captured = capsys.readouterr()
        # the exact confirmation string is shown so scripts can echo it back
        assert "proj-a non_sensitive" in captured.out + captured.err

    def test_widening_with_wrong_confirm_dies(self):
        args = _args(
            data_class_action="set",
            new_class="non_sensitive",
            confirm="proj-a sensitive",
        )
        client_mock = MagicMock()
        client_mock.get_platform_project.return_value = REPLY_SENSITIVE
        from r4._cli import project_data_class as pdc

        with (
            patch("r4.client.Client", return_value=client_mock),
            patch.object(pdc, "_project_id", lambda a, pm: "proj-a"),
            pytest.raises(SystemExit),
        ):
            pdc.project_data_class(args, MagicMock())
        client_mock.set_project_data_class.assert_not_called()

    def test_widening_with_typed_confirm_writes(self, capsys):
        args = _args(
            data_class_action="set",
            new_class="non_sensitive",
            confirm="proj-a non_sensitive",
        )
        client = _run(args, REPLY_SENSITIVE, set_reply=REPLY_NON_SENSITIVE)
        client.set_project_data_class.assert_called_once_with("proj-a", "non_sensitive")
        assert "non_sensitive" in capsys.readouterr().out

    def test_widening_interactive_typed_confirm_writes(self, monkeypatch):
        """TTY path: the prompt asks for the exact '<project> <class>' line."""
        monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
        monkeypatch.setattr("builtins.input", lambda prompt="": "proj-a non_sensitive")
        args = _args(data_class_action="set", new_class="non_sensitive")
        client = _run(args, REPLY_SENSITIVE, set_reply=REPLY_NON_SENSITIVE)
        client.set_project_data_class.assert_called_once_with("proj-a", "non_sensitive")

    def test_interactive_wrong_answer_aborts(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
        monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
        args = _args(data_class_action="set", new_class="non_sensitive")
        client = _run(args, REPLY_SENSITIVE, dies=True)
        client.set_project_data_class.assert_not_called()

    def test_noop_set_says_so_and_writes_nothing(self, capsys):
        args = _args(data_class_action="set", new_class="sensitive")
        client = _run(args, REPLY_SENSITIVE)
        client.set_project_data_class.assert_not_called()
        assert "already" in capsys.readouterr().out

    def test_invalid_class_dies(self):
        args = _args(data_class_action="set", new_class="public")
        client = _run(args, REPLY_SENSITIVE, dies=True)
        client.set_project_data_class.assert_not_called()

    def test_project_omitted_class_still_parsed(self):
        """`r4 project data-class set non_sensitive` (no project positional)
        widens the CURRENT project — the class value is recognised."""
        args = _args(
            data_class_action="set",
            project_id="non_sensitive",  # argparse fills the first positional
            new_class=None,
            confirm="proj-a non_sensitive",
        )
        client = _run(args, REPLY_SENSITIVE, set_reply=REPLY_NON_SENSITIVE)
        client.set_project_data_class.assert_called_once_with("proj-a", "non_sensitive")


class TestJsonOutput:
    """``--json`` prints exactly one JSON document on stdout; the widening
    warning and the prompt go to stderr (review R4-3, 2026-10-16)."""

    def test_widening_with_confirm_prints_only_the_reply(self, capsys):
        args = _args(
            data_class_action="set",
            new_class="non_sensitive",
            confirm="proj-a non_sensitive",
            json=True,
        )
        _run(args, REPLY_SENSITIVE, set_reply=REPLY_NON_SENSITIVE)
        captured = capsys.readouterr()
        assert json.loads(captured.out) == REPLY_NON_SENSITIVE
        assert "Widening project 'proj-a'" in captured.err

    def test_interactive_widening_prompts_on_stderr(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
        monkeypatch.setattr("builtins.input", lambda prompt="": "proj-a non_sensitive")
        args = _args(data_class_action="set", new_class="non_sensitive", json=True)
        _run(args, REPLY_SENSITIVE, set_reply=REPLY_NON_SENSITIVE)
        captured = capsys.readouterr()
        assert json.loads(captured.out) == REPLY_NON_SENSITIVE
        assert "Type 'proj-a non_sensitive' to confirm" in captured.err

    def test_noop_prints_one_document(self, capsys):
        args = _args(data_class_action="set", new_class="sensitive", json=True)
        client = _run(args, REPLY_SENSITIVE)
        client.set_project_data_class.assert_not_called()
        assert json.loads(capsys.readouterr().out) == {
            "project_id": "proj-a",
            "data_class": "sensitive",
            "changed": False,
        }

    def test_narrowing_prints_only_the_reply(self, capsys):
        args = _args(data_class_action="set", new_class="sensitive", json=True)
        _run(args, REPLY_NON_SENSITIVE, set_reply=REPLY_SENSITIVE)
        assert json.loads(capsys.readouterr().out) == REPLY_SENSITIVE


class TestClientBody:
    def test_set_data_class_body_shape(self):
        from r4.client import Client

        c = Client.__new__(Client)
        c._is_umrs_direct = False  # body-shape pin; skip the mode probe
        c.patch = MagicMock(return_value={})
        c.set_project_data_class("p", "non_sensitive")
        assert c.patch.call_args.args[0] == "/gw/projects/p"
        assert c.patch.call_args.kwargs["json"] == {
            "action": "set_data_class",
            "data_class": "non_sensitive",
        }


def test_subcommand_dispatch():
    from r4._cli.project import _project_handlers
    from r4._cli.project_data_class import project_data_class

    assert _project_handlers()["data-class"] is project_data_class
    assert _project_handlers()["data_class"] is project_data_class


def _subparser(parser, name):
    """The subcommand parser ``name`` registered under ``parser``."""
    verbs = next(a for a in parser._actions if name in (getattr(a, "choices", None) or ()))
    return verbs.choices[name]


def _project_parser():
    from r4.cli import _build_parser

    return _subparser(_build_parser(), "project")


def test_project_help_lists_data_class_once():
    """``data_class`` is an alias of ``data-class`` (as ``ask-policy`` is of
    ``ask_policy``), not a second listed subcommand."""
    lines = [line.strip() for line in _project_parser().format_help().splitlines()]
    assert sum(line.startswith("data-class") for line in lines) == 1
    assert not any(line.startswith("data_class") for line in lines)


@pytest.mark.parametrize("spelling", ["data-class", "data_class"])
def test_both_spellings_parse(spelling):
    from r4.cli import _build_parser

    args = _build_parser().parse_args(["project", spelling, "set", "p1", "sensitive"])
    assert (args.data_class_action, args.project_id, args.new_class) == ("set", "p1", "sensitive")


def test_data_class_help_keeps_its_layout():
    """The class table and the examples keep their own lines; no jargon."""
    text = _subparser(_project_parser(), "data-class").format_help()
    assert "\n  sensitive      platform models" in text
    assert "\n  r4 project data-class get\n" in text
    assert "ADR-" not in text
