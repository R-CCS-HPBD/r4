"""AuthenticationError carries a console sign-in remedy (stabilization 2026-10-21, slash-b#8).

Signed-out /usage, /quota, /evidence, /circles and /job list printed a bare
"Authentication failed": the only remedy the error carried was the CLI
recipe (``printf … | r4 auth login --api-key-stdin``). ``tui_hint`` names
the console's own path; the CLI ``hint`` is unchanged.
"""

from __future__ import annotations

from r4.errors import TUI_SIGN_IN_HINT, AuthenticationError


def test_a_401_names_the_console_sign_in():
    error = AuthenticationError(status_code=401)
    assert error.tui_hint == TUI_SIGN_IN_HINT
    assert "/connect" in error.tui_hint
    assert "r4 auth login" in error.hint  # the CLI recipe stays


def test_the_default_error_names_it_too():
    assert "/connect" in AuthenticationError().tui_hint


def test_a_403_has_no_sign_in_remedy():
    error = AuthenticationError("Access denied (403)", status_code=403)
    assert error.tui_hint is None
    assert "Access denied" in error.hint
