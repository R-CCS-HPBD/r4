"""R4 Aurora CLI theme — rich Console factory.

Loads the shared Aurora theme (r4/resources/theme.aurora.json, generated from
packages/r4-design/tokens.json) so CLI/companion output shares the platform
design identity. Falls back to a plain Console when the resource is missing.
"""

from __future__ import annotations

import itertools
import json
import random
import threading
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from rich.console import Console
from rich.theme import Theme

_RESOURCE = Path(__file__).resolve().parents[2] / "resources" / "theme.aurora.json"

# Cognition verbs for waiting states — matches the companion TUI identity.
STATUS_PHRASES: tuple[str, ...] = (
    "Pondering",
    "Reasoning",
    "Exploring context",
    "Formulating",
    "Synthesizing",
    "Deliberating",
)


@lru_cache(maxsize=1)
def r4_theme() -> Theme:
    """Load the Aurora rich theme from package resources."""
    try:
        styles = json.loads(_RESOURCE.read_text(encoding="utf-8"))["styles"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return Theme({})
    if not isinstance(styles, dict):
        return Theme({})
    return Theme({str(k): str(v) for k, v in styles.items()})


def make_console(**kwargs: object) -> Console:
    """Create a rich Console themed with the R4 Aurora palette."""
    return Console(theme=r4_theme(), **kwargs)  # type: ignore[arg-type]


@contextmanager
def rotating_status(
    console: Console,
    *,
    phrases: tuple[str, ...] = STATUS_PHRASES,
    interval: float = 1.6,
) -> Iterator[None]:
    """Animated status line with rotating verb + elapsed seconds.

    Safe over SSH: a single status region, ``dots`` spinner, and rich
    degrades to plain text when output is not a TTY.
    """
    stop = threading.Event()
    started = time.monotonic()
    order = list(phrases)
    random.shuffle(order)
    status = console.status(f"[r4.thinking]{order[0]}\u2026[/]", spinner="dots")
    status.start()

    def _tick() -> None:
        for phrase in itertools.cycle(order):
            if stop.wait(interval):
                return
            elapsed = int(time.monotonic() - started)
            status.update(f"[r4.thinking]{phrase}\u2026[/] [dim]\u00b7 {elapsed}s[/]")

    worker = threading.Thread(target=_tick, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=interval)
        status.stop()
