"""Cooperative cancellation for in-flight companion turns."""

from __future__ import annotations

import threading
from collections.abc import Callable

STOPPED_MESSAGE = "(Generation stopped.)"


class TurnCancelledError(Exception):
    """Raised when the user stops an in-flight companion turn."""


class TurnCancellation:
    """Thread-safe cancellation token for one companion turn."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._closers: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    def is_set(self) -> bool:
        return self._event.is_set()

    def request(self) -> None:
        self._event.set()
        with self._lock:
            closers = list(self._closers)
        for close in closers:
            try:
                close()
            except Exception:  # pragma: no cover - best-effort teardown
                pass

    def check(self) -> None:
        if self._event.is_set():
            raise TurnCancelledError()

    def register_close(self, close: Callable[[], None]) -> None:
        with self._lock:
            self._closers.append(close)
            if self._event.is_set():
                try:
                    close()
                except Exception:  # pragma: no cover - best-effort teardown
                    pass

    def clear(self) -> None:
        self._event.clear()
        with self._lock:
            self._closers.clear()


def cancel_check_from(token: TurnCancellation | None) -> Callable[[], None] | None:
    """Return an HTTP/agent cancel callback for *token*, if any."""
    if token is None:
        return None
    return token.check


def response_for_cancelled_turn(*, partial: str = "") -> str:
    """User-visible assistant text when a turn is stopped mid-flight."""
    text = (partial or "").strip()
    if text:
        return f"{text}\n\n{STOPPED_MESSAGE}"
    return STOPPED_MESSAGE


def is_stopped_reply(text: str) -> bool:
    """Whether ``text`` is a stopped turn's stored reply: the marker alone, or
    a partial reply ending in it (:func:`response_for_cancelled_turn`)."""
    return (text or "").rstrip().endswith(STOPPED_MESSAGE)
