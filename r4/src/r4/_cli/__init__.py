"""CLI command registry + handler ABC (Plan 2).

Plan 2 of the r4 refactor is to decompose the ~2250-line ``r4.cli`` monolith
into one module per command group. This package provides the scaffolding:

- :class:`CommandHandler` — abstract base for a command group.
- :func:`command` — class decorator that registers a handler.
- :class:`CLIRegistry` — global discoverable registry.

Existing handlers in ``r4.cli`` continue to work unchanged; new or migrated
handlers should subclass :class:`CommandHandler`. The top-level parser in
``cli.main`` iterates the registry and delegates automatically.
"""

from __future__ import annotations

from .base import CommandHandler
from .registry import CLIRegistry, command, registry

__all__ = ["CLIRegistry", "CommandHandler", "command", "registry"]
