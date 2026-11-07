from __future__ import annotations

import threading

import pytest

from r4.companion.cancellation import (
    STOPPED_MESSAGE,
    TurnCancellation,
    TurnCancelledError,
    response_for_cancelled_turn,
)


def test_turn_cancellation_raises_on_check() -> None:
    token = TurnCancellation()
    token.request()
    with pytest.raises(TurnCancelledError):
        token.check()


def test_turn_cancellation_invokes_registered_close() -> None:
    token = TurnCancellation()
    closed = threading.Event()

    token.register_close(closed.set)
    token.request()

    assert closed.is_set()


def test_response_for_cancelled_turn_without_partial() -> None:
    assert response_for_cancelled_turn() == STOPPED_MESSAGE


def test_response_for_cancelled_turn_with_partial() -> None:
    text = response_for_cancelled_turn(partial="partial answer")
    assert "partial answer" in text
    assert STOPPED_MESSAGE in text
