"""A signed-out bridge request answers with the console's sign-in remedy
(slash-b#8): ``_bridge_error_response`` read only ``exc.hint``, the shell's
``r4 auth login --api-key-stdin`` pipe, so /evidence and /circles told a
console user to run a shell command instead of /connect."""

from __future__ import annotations

from r4.companion.ui.bridge_server import _bridge_error_response
from r4.errors import TUI_SIGN_IN_HINT, AuthenticationError, ConfigurationError


def test_a_401_carries_the_console_sign_in_hint() -> None:
    error = _bridge_error_response(7, AuthenticationError(status_code=401))["error"]
    assert error["type"] == "AuthenticationError"
    assert error["hint"] == TUI_SIGN_IN_HINT


def test_a_403_keeps_its_own_hint() -> None:
    exc = AuthenticationError("Access denied (403)", status_code=403)
    error = _bridge_error_response(7, exc)["error"]
    assert error["hint"] == exc.hint != TUI_SIGN_IN_HINT


def test_an_error_without_a_tui_hint_is_unchanged() -> None:
    assert "hint" not in _bridge_error_response(7, ConfigurationError("bad"))["error"]
