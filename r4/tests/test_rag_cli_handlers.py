"""Focused regressions for ``r4 rag`` command handlers."""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest


def _rag_parser():
    import argparse

    from r4._cli.rag import RagCommand

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    RagCommand().register(sub)
    return parser


@pytest.mark.parametrize(
    "argv",
    [
        ["rag", "ingest", "list", "--project", "demo"],
        ["rag", "ingest", "show", "pipe-1", "--json"],
        ["rag", "ingest", "create", "--file", "pipeline.json"],
        ["rag", "ingest", "update", "pipe-1", "--set", "name=x"],
        ["rag", "ingest", "delete", "pipe-1", "--yes"],
        ["rag", "ingest"],
    ],
)
def test_rag_ingest_is_retired_without_a_request(argv, capsys):
    """H-10.1 follow-up (2026-10-18): UMRS retired pipeline definitions and
    the /gw/pipelines edge location; `r4 rag ingest` names the replacement
    instead of calling the gateway."""
    from r4._cli.rag import RagCommand

    args = _rag_parser().parse_args(argv)
    client_cls = MagicMock(side_effect=AssertionError("no gateway call"))
    preflight = MagicMock(side_effect=AssertionError("no preflight"))
    with (
        patch("r4.client.Client", client_cls),
        patch("r4._cli._helpers._preflight_check", preflight),
    ):
        rc = RagCommand().handle(args)

    assert rc == 2
    err = capsys.readouterr().err
    assert "retired" in err
    assert "UMRS_RAG_CHUNK_MAX_TOKENS" in err
    assert "r4 rag retriever components" in err
    client_cls.assert_not_called()


def test_rag_help_no_longer_advertises_ingest_crud(capsys):
    from r4._cli import rag

    assert "ingest list" not in (rag.__doc__ or "")
    with pytest.raises(SystemExit):
        _rag_parser().parse_args(["rag", "--help"])
    help_text = capsys.readouterr().out
    assert "r4 rag ingest list" not in help_text
    assert "ingestion pipelines" not in help_text


@pytest.mark.parametrize(
    "argv",
    [
        ["rag", "retriever", "list", "--project", "demo"],
        ["rag", "retriever", "show", "prof-1", "--json"],
        ["rag", "retriever", "create", "--file", "profile.json"],
        ["rag", "retriever", "create"],
        ["rag", "retriever", "update", "prof-1", "--set", "name=x"],
        ["rag", "retriever", "delete", "prof-1", "--yes"],
    ],
)
def test_rag_retriever_crud_is_retired_without_a_request(argv, capsys):
    """H-10.1 rag-profiles wave (2026-10-18): the rag-profiles routes and the
    /gw/rag-profiles edge are deleted; the CRUD prints the reason and calls
    nothing (the old code called /gw/rag-profiles first, then folded the 410).
    `components` stays live."""
    from r4._cli.rag import RagCommand

    args = _rag_parser().parse_args(argv)
    client_cls = MagicMock(side_effect=AssertionError("no gateway call"))
    preflight = MagicMock(side_effect=AssertionError("no preflight"))
    with (
        patch("r4.client.Client", client_cls),
        patch("r4._cli._helpers._preflight_check", preflight),
    ):
        rc = RagCommand().handle(args)

    assert rc == 2
    err = capsys.readouterr().err
    assert "retired" in err
    assert "r4 rag retriever components" in err
    client_cls.assert_not_called()


def test_rag_help_no_longer_advertises_retriever_crud(capsys):
    from r4._cli import rag

    assert "list / show / create" not in (rag.__doc__ or "")
    with pytest.raises(SystemExit):
        _rag_parser().parse_args(["rag", "retriever", "--help"])
    help_text = capsys.readouterr().out
    assert "retired" in help_text.lower()
    assert "r4 rag retriever list --project demo" not in help_text


def test_handle_retriever_components_renders_text(capsys):
    from r4._cli.rag import _handle_retriever

    client = MagicMock()
    client.get.return_value = {
        "items": [
            {
                "name": "e5-base",
                "description": "default embedder",
            }
        ]
    }

    with patch("r4.client.Client", return_value=client):
        rc = _handle_retriever(
            Namespace(
                retriever_command="components",
                kind="embedders",
                as_json=False,
            )
        )

    assert rc == 0
    # The path now rides the client's mode probe (H-10.2); a MagicMock's
    # _api_prefix is a Mock, so pin the shape, not the prefix value.
    (called_path,), _ = client.get.call_args
    assert called_path.endswith("/components/embedders")
    output = capsys.readouterr().out
    assert "e5-base" in output
    assert "default embedder" in output


def test_rag_query_sends_no_threshold_unless_asked():
    """ADR-093 D-10: the 0.7 absolute cut is gone; the server's adaptive cut
    decides. A user can still force the legacy cut with --threshold."""
    import argparse

    from r4._cli.rag import RagCommand

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    RagCommand().register(sub)
    default = parser.parse_args(["rag", "query", "q", "-p", "p"])
    forced = parser.parse_args(["rag", "query", "q", "-p", "p", "--threshold", "0.7"])
    assert default.threshold is None and forced.threshold == 0.7
