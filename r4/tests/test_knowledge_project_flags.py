"""`r4 knowledge` takes the project the same way everywhere.

graph-* commands took ``--project-id``; upload/bulk-upload/query took a
*required* ``--project`` even when a current project was set (stand-alone
stack, 2026-10-09). Both spellings now work, and the corpus commands default to
the current project like `stats` and `search` already did.
"""

from __future__ import annotations

import argparse

import pytest

from r4._cli import knowledge as kcli
from r4.cli import _build_parser


@pytest.mark.parametrize("flag", ["--project", "--project-id", "-p"])
def test_graph_ingest_accepts_both_spellings(flag):
    argv = ["knowledge", "graph-ingest", "g", "--file", "a.md", flag, "p1"]
    args = _build_parser().parse_args(argv)
    assert args.project_id == "p1"


@pytest.mark.parametrize("flag", ["--project", "--project-id", "-p"])
def test_upload_accepts_both_spellings(flag):
    args = _build_parser().parse_args(["knowledge", "upload", "a.md", flag, "p1"])
    assert args.project == "p1"


@pytest.mark.parametrize("argv", [["knowledge", "upload", "a.md"], ["knowledge", "query", "q"]])
def test_corpus_commands_no_longer_require_the_flag(argv):
    args = _build_parser().parse_args(argv)
    assert args.project is None


def test_missing_project_resolves_to_the_current_one(monkeypatch):
    monkeypatch.setattr(kcli, "_current_project_id", lambda: "default")
    args = argparse.Namespace(project=None)
    assert kcli._resolve_project_arg(args) is True
    assert args.project == "default"


def test_no_project_anywhere_is_said(monkeypatch, capsys):
    monkeypatch.setattr(kcli, "_current_project_id", lambda: None)
    assert kcli._resolve_project_arg(argparse.Namespace(project=None)) is False
    assert "No project specified" in capsys.readouterr().err
