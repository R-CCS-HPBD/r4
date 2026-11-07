"""Transport routing consolidation (Plan 4).

Centralizes the ZMQ-vs-HTTP decision that was previously duplicated in
``config.Config.should_use_zmq`` + inline ``try/except`` fallbacks across
``jobs.py``, ``workflows.py``, ``models.py``, ``data.py``.

Design invariants (see ``.github/instructions/development.instructions.md``):

- **UMRS-first boundary** — this module never decides *which* transport is
  authoritative; it only asks :class:`~r4.config.Config` and optionally
  falls back HTTP→ZMQ-down→HTTP-degraded.
- **No new env vars / public config surface** — pure internal refactor;
  ``Config.should_use_zmq`` stays as the data source.
- **Pluggable for tests** — ``execute(...)`` takes the two callables so
  tests don't need to stand up real transports.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Literal, Optional

from .config import Config
from .errors import R4ConnectionError, R4TimeoutError

Transport = Literal["zmq", "http"]
DEFAULT_ZMQ_UNAVAILABLE_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
    ImportError,
    R4ConnectionError,
    R4TimeoutError,
)

_log = logging.getLogger(__name__)


def transport_fallback_hint() -> str:
    """Return the operator hint shown when direct ZMQ falls back to HTTP."""
    return (
        "Set R4_TRANSPORT_MODE=gateway to use Gateway HTTP only, "
        "or fix UMRS_SERVER_HOST/UMRS_CONTROL_PORT for direct ZMQ."
    )


class TransportRouter:
    """Chooses transport per-operation and encapsulates the fallback chain."""

    def __init__(self, config: Config) -> None:
        self._config = config

    # --- decision -----------------------------------------------------------

    def route(self, operation: str) -> Transport:
        """Return the preferred transport for an operation."""
        return "zmq" if self._config.should_use_zmq(operation) else "http"

    # --- execution ----------------------------------------------------------

    def execute(
        self,
        operation: str,
        *,
        http_fn: Callable[[], Any],
        zmq_fn: Optional[Callable[[], Any]] = None,
        zmq_unavailable: Optional[tuple[type[BaseException], ...]] = None,
    ) -> Any:
        """Run an operation via the preferred transport with explicit fallback.

        Args:
            operation: Logical op name (e.g. ``"job_list"``).
            http_fn: Callable executing the HTTP (Gateway) path.
            zmq_fn: Callable executing the direct-ZMQ path (optional).
            zmq_unavailable: Exception types that signal "ZMQ is down, fall
                back to HTTP".  Defaults to a small set of transport-level
                connectivity and timeout errors. Other exceptions propagate.

        Returns:
            Whatever the chosen callable returns.
        """
        transport = self.route(operation)
        if transport == "zmq" and zmq_fn is not None:
            unavailable_errors = zmq_unavailable or DEFAULT_ZMQ_UNAVAILABLE_ERRORS
            try:
                return zmq_fn()
            except unavailable_errors as exc:
                _log.warning(
                    "transport fallback ZMQ→HTTP for %s: %s. %s",
                    operation,
                    exc,
                    transport_fallback_hint(),
                )
        return http_fn()


__all__ = [
    "TransportRouter",
    "Transport",
    "DEFAULT_ZMQ_UNAVAILABLE_ERRORS",
    "transport_fallback_hint",
]
