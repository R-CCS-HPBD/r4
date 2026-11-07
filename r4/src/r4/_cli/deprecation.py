"""Formal deprecation framework for R4 CLI commands.

Provides reusable infrastructure for deprecating commands with:
- Structured warnings with replacement hints
- Phase tracking (soft / warn / hidden / remove)
- Configurable suppression via R4_SUPPRESS_DEPRECATIONS=1
- Hidden-from-help and hidden-from-completion support
- Sunset version metadata
"""

from __future__ import annotations

import os
import sys
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class DeprecationPhase(Enum):
    """Lifecycle phase of a deprecated command."""

    SOFT = "soft"  # Warning on stderr, visible in --help
    WARN = "warn"  # Louder warning, hidden from --help
    HIDDEN = "hidden"  # Works but hidden from --help and completion
    REMOVED = "removed"  # Raises error with migration instructions


def _suppressed() -> bool:
    """Check if deprecation warnings are suppressed."""
    return os.environ.get("R4_SUPPRESS_DEPRECATIONS", "").strip() in (
        "1",
        "true",
        "yes",
    )


def emit_deprecation_warning(
    old_command: str,
    new_command: str,
    *,
    sunset_version: str = "v1.0",
    phase: DeprecationPhase = DeprecationPhase.SOFT,
) -> None:
    """Print a deprecation warning to stderr unless suppressed."""
    if _suppressed():
        return
    if phase == DeprecationPhase.HIDDEN:
        print(
            f"[deprecated] '{old_command}' \u2192 use '{new_command}' instead.",
            file=sys.stderr,
        )
        return
    print(
        f"[Deprecated] '{old_command}' is deprecated and will be removed "
        f"after {sunset_version}.\n"
        f"  Use: {new_command}\n"
        f"  Suppress: R4_SUPPRESS_DEPRECATIONS=1",
        file=sys.stderr,
    )
