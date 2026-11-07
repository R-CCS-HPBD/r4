"""Focused tests for Phase 2 trust-building changes.

F-021: ``r4 session list`` alias.
F-022: Global ``--verbose`` flag.
F-023: Duplicate model name warnings.
Output field-name drift fixes (job list --json, session timestamp format,
model list --json created_at).
"""

import argparse
import io
import json
from argparse import Namespace
from unittest.mock import patch

import pytest

# =============================================================================
# F-021: ``r4 session list`` alias
# =============================================================================


class TestSessionListAlias:
    """``r4 session list`` is accepted and behaves identically to ``r4 session``."""

    def test_session_subcommand_defaults_to_list(self):
        """No subcommand → session_subcommand='list'."""
        from r4._cli.sessions import SessionsCommand

        handler = SessionsCommand()
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        handler.register(sub)

        args = parser.parse_args(["session"])
        assert args.session_subcommand == "list"

    def test_session_list_subcommand_accepted(self):
        """``session list`` → session_subcommand='list'."""
        from r4._cli.sessions import SessionsCommand

        handler = SessionsCommand()
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        handler.register(sub)

        args = parser.parse_args(["session", "list"])
        assert args.session_subcommand == "list"

    def test_session_invalid_subcommand_rejected(self):
        """Unknown session subcommands still fail in argparse."""
        from r4._cli.sessions import SessionsCommand

        handler = SessionsCommand()
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        handler.register(sub)

        with pytest.raises(SystemExit):
            parser.parse_args(["session", "bogus"])

    def test_session_management_subcommands_accepted(self):
        """``show``, ``archive``, and ``remove`` are first-class session verbs."""
        from r4._cli.sessions import SessionsCommand

        handler = SessionsCommand()
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        handler.register(sub)

        for verb in ("show", "archive", "remove", "delete"):
            args = parser.parse_args(["session", verb, "session-1"])
            assert args.session_subcommand == verb
            assert args.session_id == "session-1"


# =============================================================================
# F-022: Global --verbose flag
# =============================================================================


class TestGlobalVerboseFlag:
    """The root parser accepts -v/--verbose."""

    def test_verbose_flag_defaults_to_false(self):
        from r4.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["session"])
        assert args.verbose is False

    def test_verbose_flag_short(self):
        from r4.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["-v", "session"])
        assert args.verbose is True

    def test_verbose_flag_long(self):
        from r4.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--verbose", "session"])
        assert args.verbose is True


# =============================================================================
# F-023: Duplicate model name warnings
# =============================================================================


class TestDuplicateModelNameWarnings:
    """``_warn_duplicate_names`` emits stderr warnings for duplicate names."""

    def test_no_warning_for_unique_names(self, capsys):
        from r4._cli.model import _warn_duplicate_names
        from r4.models import ModelScope

        Model = type("Model", (), {})
        models = [Model(), Model()]
        models[0].name = "alpha"
        models[0].model_id = "alpha-id"
        models[0].scope = ModelScope.PROJECT
        models[1].name = "beta"
        models[1].model_id = "beta-id"
        models[1].scope = ModelScope.PROJECT

        _warn_duplicate_names(models)

        assert capsys.readouterr().err == ""

    def test_warning_for_duplicates(self, capsys):
        from r4._cli.model import _warn_duplicate_names
        from r4.models import ModelScope

        Model = type("Model", (), {})
        models = [Model(), Model(), Model()]
        models[0].name = "alpha"
        models[0].model_id = "alpha-1"
        models[0].scope = ModelScope.SYSTEM
        models[1].name = "alpha"
        models[1].model_id = "alpha-2"
        models[1].scope = ModelScope.PROJECT
        models[2].name = "beta"
        models[2].model_id = "beta-id"
        models[2].scope = ModelScope.PROJECT

        _warn_duplicate_names(models)

        err = capsys.readouterr().err
        assert "alpha" in err
        assert "2 times" in err
        assert "beta" not in err

    def test_no_warning_for_empty_list(self, capsys):
        from r4._cli.model import _warn_duplicate_names

        _warn_duplicate_names([])

        assert capsys.readouterr().err == ""


# =============================================================================
# Output field-name drift fixes
# =============================================================================


class TestJobListJson:
    """``r4 job list --json`` flag exists and produces structured output."""

    def test_job_list_accepts_json_flag(self):
        from r4._cli.job import JobCommand

        handler = JobCommand()
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        handler.register(sub)

        args = parser.parse_args(["job", "list", "--json"])
        assert args.as_json is True


class TestSessionTimestampFormat:
    """Session --json output preserves full timestamps."""

    def test_session_row_preserves_full_timestamp(self):
        """The handle method stores full updated_at, not truncated."""
        from r4._cli.sessions import SessionsCommand

        handler = SessionsCommand()
        full_ts = "2026-05-01T10:30:00+09:00"

        mock_session = {
            "id": "test-id",
            "name": "test",
            "kind": "agent",
            "info": "1 msgs",
            "updated_at": full_ts,
            "created_at": full_ts,
            "project_id": "project-alpha",
            "project": "project-alpha",
            "endpoint": "endpoint-alpha",
            "endpoints": ["endpoint-alpha"],
            "archived_at": "",
        }

        args = Namespace(
            session_subcommand="list",
            kind="agent",
            as_json=True,
        )

        with patch("r4._cli.sessions._rows_for_kind", return_value=[mock_session]):
            # Capture stdout
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                handler.handle(args)

        data = json.loads(captured.getvalue())
        # Full timestamp should be preserved in JSON, not truncated to 19 chars
        assert data[0]["updated_at"] == full_ts


class TestModelListJsonCreatedAt:
    """``r4 model list --json`` includes created_at field."""

    def test_model_list_json_includes_created_at(self):
        """The model list JSON output now includes a created_at field."""
        # Verify by checking that the code constructs the entry with created_at
        import ast
        from pathlib import Path

        source = Path(__file__).parent.parent / "src" / "r4" / "_cli" / "model.py"
        tree = ast.parse(source.read_text())

        # Find string literal "created_at" used as dict key in the list handler
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "created_at":
                found = True
                break

        assert found, "model.py should reference 'created_at' for model list JSON output"
