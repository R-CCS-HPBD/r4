"""Bounded read timeout for read-only CLI views (T1, stabilization cycle 2).

The config's request timeout (``R4_TIMEOUT``, default 300 s) is sized for
long-running mutating calls (provisioning, uploads, ingest). A read-only
list or status view inherited it, so against a gateway that accepted the
connection but never answered, ``r4 usage``, ``r4 map``, ``r4 governance
audit`` and twenty more sat silent for five minutes (a CLI sweep on
2026-10-21 found 22 commands still running at 150 s). Those views now use
:data:`READ_ONLY_TIMEOUT_S` unless the user names a timeout with
``R4_TIMEOUT``; every other command keeps its timeouts.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import replace

READ_ONLY_TIMEOUT_S = 30

# Command paths (as typed; a prefix covers everything under it) that only
# read. Everything not listed keeps the config timeout.
READ_ONLY_PATHS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("usage",),
        ("map",),
        ("trace",),
        ("doctor",),
        ("health",),
        ("quota",),
        ("governance", "ownership"),
        ("governance", "audit"),
        ("governance", "shares"),
        ("governance", "quotas"),
        ("admin", "analytics"),
        ("admin", "trace"),
        ("agent", "audit"),
        ("agent", "sessions"),
        ("skill", "pending"),
        ("knowledge", "graphs"),
        ("knowledge", "inventory"),
        ("knowledge", "stats"),
        ("knowledge", "health"),
        ("knowledge", "docs", "list"),
        ("data", "intake"),
        ("data", "assets", "search"),
        ("data", "assets", "stats"),
        ("data", "catalog"),
        ("run", "list"),
        ("session", "list"),
        ("session", "show"),
        ("job", "list"),
        ("workflow", "list"),
        ("workflow", "definition", "list"),
        ("artifact", "list"),
        ("evidence", "list"),
        ("evidence", "memory", "list"),
        ("provenance", "list"),
        ("endpoint", "list"),
        ("catalog", "list"),
        ("bucket", "list"),
        ("rag", "stats"),
        ("rag", "docs", "list"),
        ("ops", "health"),
        ("ops", "topology"),
        ("ops", "timeline"),
        ("circle", "mine"),
        ("grant", "mine"),
    }
)


def _subparsers_of(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction[argparse.ArgumentParser] | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _positional_verb(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str | None:
    """A leaf's verb given as a positional with choices (``r4 session list``)."""
    for action in parser._actions:
        if action.option_strings or not action.choices:
            continue
        value = getattr(args, action.dest, None)
        return value if isinstance(value, str) else None
    return None


def command_path(parser: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[str, ...]:
    """The command words ``args`` was parsed from, e.g. ``("governance", "audit")``."""
    path: list[str] = []
    current: argparse.ArgumentParser | None = parser
    while current is not None:
        sub = _subparsers_of(current)
        name = getattr(args, sub.dest, None) if sub is not None else None
        if sub is None or not isinstance(name, str):
            break
        path.append(name)
        current = sub.choices.get(name)
    verb = _positional_verb(current, args) if current is not None else None
    return (*path, verb) if verb else tuple(path)


def is_read_only_view(path: tuple[str, ...]) -> bool:
    return any(path[: len(prefix)] == prefix for prefix in READ_ONLY_PATHS)


def apply_read_only_timeout(parser: argparse.ArgumentParser, args: argparse.Namespace) -> bool:
    """Bound the process config's read timeout for a read-only view.

    No-op when ``R4_TIMEOUT`` is set (the user's choice wins), for any other
    command, or when the config timeout is already within the bound.
    Returns whether the timeout was changed.
    """
    if os.environ.get("R4_TIMEOUT", "").strip():
        return False
    if not is_read_only_view(command_path(parser, args)):
        return False
    from ..config import get_config, set_config

    config = get_config()
    if config.timeout <= READ_ONLY_TIMEOUT_S:
        return False
    set_config(replace(config, timeout=READ_ONLY_TIMEOUT_S))
    return True
