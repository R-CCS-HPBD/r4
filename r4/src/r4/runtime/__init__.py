"""Research Runtime API and interchangeable runtime implementations.

Public surface:

- Contract: :class:`ResearchRuntime`, :class:`LoopSpec`, :class:`LoopState`,
  :class:`IterationState`, :class:`RuntimeCapabilities`.
- Implementations: :class:`UmrsRuntime` (authoritative substrate),
  :class:`LocalRuntime` (SQLite dev/CI runtime).
- Selection: :func:`get_runtime` / :func:`resolve_mode`.
"""

from .api import (
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_PAUSED,
    STATUS_RUNNING,
    STATUS_STOPPED,
    TERMINAL_STATUSES,
    IterationContext,
    IterationExecutor,
    IterationState,
    LoopHalted,
    LoopSpec,
    LoopState,
    ResearchRuntime,
    RuntimeCapabilities,
)
from .hybrid import HybridRuntime
from .local import LocalRuntime
from .resolver import RuntimeResolutionError, get_runtime, resolve_mode
from .umrs import UmrsRuntime

__all__ = [
    "STATUS_COMPLETED",
    "STATUS_ERROR",
    "STATUS_PAUSED",
    "STATUS_RUNNING",
    "STATUS_STOPPED",
    "TERMINAL_STATUSES",
    "HybridRuntime",
    "IterationContext",
    "IterationExecutor",
    "IterationState",
    "LocalRuntime",
    "LoopHalted",
    "LoopSpec",
    "LoopState",
    "ResearchRuntime",
    "RuntimeCapabilities",
    "RuntimeResolutionError",
    "UmrsRuntime",
    "get_runtime",
    "resolve_mode",
]
