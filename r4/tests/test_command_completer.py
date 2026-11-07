"""Tests for _CommandCompleter slash-command completion.

Regression tests for: "typing / and selecting command via Enter returns nothing".
"""

from unittest.mock import MagicMock, patch

import pytest


class TestCommandCompleter:
    """Test slash-command completion via make_prompt_session."""

    @pytest.fixture
    def mock_command_specs(self):
        """Mock command_specs to return known commands."""
        from r4.companion.ui.command_bridge import CommandSpec

        specs = [
            CommandSpec("help", "Show commands and shortcuts", "help"),
            CommandSpec("quit", "Save and exit", "session"),
            CommandSpec("usage", "Show billing usage", "context"),
            CommandSpec("endpoint", "Endpoint operations", "endpoint"),
            CommandSpec("probe", "Re-scan environment", "harness"),
        ]
        with patch(
            "r4.companion.ui.command_bridge.command_specs",
            return_value=specs,
        ):
            with patch(
                "r4.companion.ui.command_bridge.get_slash_registry",
                return_value=MagicMock(
                    command_list=lambda: [],
                    command_aliases=lambda name: [],
                ),
            ):
                yield specs

    def test_session_has_completer(self, mock_command_specs):
        """make_prompt_session should return a session with a completer."""
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            session = self._make_session()
            assert session.completer is not None

    def test_completes_slash(self, mock_command_specs):
        """Completing '/hel' should yield '/help'."""
        from prompt_toolkit.document import Document

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            session = self._make_session()
            doc = Document(text="/hel", cursor_position=4)
            completions = list(session.completer.get_completions(doc, MagicMock()))

        texts = [c.text for c in completions]
        assert "/help" in texts

    def test_completes_partial_slash(self, mock_command_specs):
        """Completing '/' should yield all commands."""
        from prompt_toolkit.document import Document

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            session = self._make_session()
            doc = Document(text="/", cursor_position=1)
            completions = list(session.completer.get_completions(doc, MagicMock()))

        assert len(completions) >= 3  # help, quit, usage at minimum

    def test_no_completion_without_slash(self, mock_command_specs):
        """Regular text should not trigger slash completion."""
        from prompt_toolkit.document import Document

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            session = self._make_session()
            doc = Document(text="hello world", cursor_position=11)
            completions = list(session.completer.get_completions(doc, MagicMock()))

        assert completions == []

    def _make_session(self):
        from r4.companion.ui.command_bridge import make_prompt_session

        return make_prompt_session()

    def test_completion_replaces_from_slash(self, mock_command_specs):
        """Completion start_position should be 0 (from the slash)."""
        from prompt_toolkit.document import Document

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            session = self._make_session()
            doc = Document(text="/h", cursor_position=2)
            completions = list(session.completer.get_completions(doc, MagicMock()))

        for c in completions:
            assert c.start_position == 0, f"Expected start_position=0, got {c.start_position}"
            assert c.text.startswith("/")
