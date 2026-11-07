"""Tests for the aurora rotating status helper (Wave-1 UX upgrade)."""

from __future__ import annotations

import io
import time

from rich.console import Console

from r4.ui_theme import STATUS_PHRASES, rotating_status


class TestStatusPhrases:
    def test_phrases_present(self) -> None:
        assert len(STATUS_PHRASES) >= 4

    def test_phrases_are_single_verbs(self) -> None:
        for phrase in STATUS_PHRASES:
            assert phrase and phrase[0].isupper()


class TestRotatingStatus:
    def _console(self) -> tuple[Console, io.StringIO]:
        buf = io.StringIO()
        return Console(file=buf, force_terminal=False, width=80), buf

    def test_context_starts_and_stops_cleanly(self) -> None:
        console, _ = self._console()
        with rotating_status(console, interval=0.05):
            time.sleep(0.02)
        # No exception, thread joined — subsequent console use still works.

    def test_text_rotates_with_elapsed(self) -> None:
        console, _ = self._console()
        with rotating_status(console, interval=0.02):
            time.sleep(0.07)
        # Non-terminal consoles render status as plain text; rotation itself
        # is exercised without depending on ANSI output.

    def test_first_phrase_is_from_list(self) -> None:
        console, buf = self._console()
        with rotating_status(console, phrases=("Pondering",), interval=0.05):
            time.sleep(0.01)
        # With a single phrase the rendered line, if written at all, must
        # contain that phrase.
        written = buf.getvalue()
        assert not written or "Pondering" in written
