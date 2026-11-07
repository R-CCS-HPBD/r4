"""R4.md per-repo agent briefing file support.

Discovery, loading, and scaffolding of ``R4.md`` — a Markdown file placed
at the repository root that the R4 agent reads at the beginning of every
session. Inspired by per-repository agent briefing patterns.

The file is optional. When present it is prepended to the system prompt
as a ``<repo_briefing>`` block so the model is immediately aware of
project-specific constraints, naming conventions, test commands, etc.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_R4_MD_FILENAME = "R4.md"

_SCAFFOLD_TEMPLATE = """\
# R4 Agent Briefing

> This file is read by the R4 agent at the start of every session.
> Add project-specific context that the agent should always know.

## Project Overview

<!-- Describe your project in 1-2 sentences. -->

## Key Conventions

<!-- List naming conventions, import rules, preferred libraries, etc. -->

## Build & Test

```bash
# How to build
# How to run tests
# How to lint
```

## File Layout

<!-- Briefly describe the key directories and their purpose. -->

## Do Not

<!-- Things the agent should NEVER do in this repo. -->
"""


"""Per-repo briefing chain — every *instruction filename* the model expects.

Priority order (first hit per filename wins at the closest directory):
  R4.md → AGENTS.md → agent.md → AGENT.md → CLAUDE.md → OPENCODE.md

Why the chain exists (2026-09-22): opencode agents read whichever of these
exists; same contract, same priority. Nothing is rendered twice: exactly
one file per source class loads, at its nearest parent level.

Each loaded section is stamped with its own origin header so the model sees
WHICH file each guidance came from and stops guessing.
"""

_INSTRUCTION_FILES: tuple[str, ...] = (
    "R4.md",
    "AGENTS.md",
    "agent.md",
    "AGENT.md",
    "CLAUDE.md",
    "OPENCODE.md",
)

# Per-file and total budgets (T5 topic-21). A briefing-unload hit can push a
# single AGENTS.md past ~100KB — stuck-in-turn latency when it renders into
# system text EVERY round. Opencode-style discipline: system-hit gets the
# head chunk only; full file always reachable by name when the model asks.
_BRIEFING_FILE_CHAR_CAP: int = 4_000
_BRIEFING_TOTAL_CHAR_CAP: int = 8_000


def _truncate_briefing(text: str, *, cap: int) -> str:
    """Cap a file's content honestly — marker states the elision."""
    if len(text) <= cap:
        return text
    return (
        f"{text[:cap]}\n\n"
        f"[… briefing file truncated: {len(text) - cap} chars of {len(text)} bytes elided; "
        "ask for the file to view the rest …]"
    )


def find_instruction_files(
    start: Optional[Path] = None,
) -> list[tuple[str, Path]]:
    """Walk up from *start*; yield (filename, path) once per unique filename.

    Order is by instruction chain priority, not by directory. Files found at
    a closer parent level win over parents' (same filename in both levels).
    """
    current = (start or Path.cwd()).resolve()
    found: dict[str, Path] = {}
    for parent in [current, *current.parents]:
        for name in _INSTRUCTION_FILES:
            if name in found:
                continue
            candidate = parent / name
            if candidate.is_file():
                found[name] = candidate
        if len(found) == len(_INSTRUCTION_FILES):
            break
    return [(name, found[name]) for name in _INSTRUCTION_FILES if name in found]


def find_r4_md(start: Optional[Path] = None) -> Optional[Path]:
    """Nearest ``R4.md`` wrapper for the legacy callers.

    The full chain lives in :func:`find_instruction_files`; this wrapper keeps
    the pre-existing load_briefing(...) signature stable during the wave.
    """
    for name, path in find_instruction_files(start):
        if name == _R4_MD_FILENAME:
            return path
    return None


def load_briefing(start: Optional[Path] = None) -> Optional[str]:
    """Load every instruction file found on the walk-up chain, budget-capped.

    Per-file budget applied AND a chain total budget. The origin header always
    reads the full file name; only the body carries its own elision marker —
    the model knows the rest can always open the file on demand.
    """
    hits = find_instruction_files(start)
    if not hits:
        return None
    blocks: list[str] = []
    total = 0
    for name, path in hits:
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            continue
        body = _truncate_briefing(body, cap=_BRIEFING_FILE_CHAR_CAP)
        header = f"<!-- {name} @ {path} -->"
        block = f"{header}\n{body}"
        # Total budget protects the chain when a chain of files rides together
        # (one R4.md usually fits, one big AGENTS.md then uses the residual cap).
        if total and total + len(block) > _BRIEFING_TOTAL_CHAR_CAP:
            remaining = _BRIEFING_TOTAL_CHAR_CAP - total
            block = f"{header}\n{_truncate_briefing(body, cap=max(100, remaining))}"
        blocks.append(block)
        total += len(block)
        if total >= _BRIEFING_TOTAL_CHAR_CAP:
            break
    merged = "\n\n".join(blocks)
    return merged if merged else None


def scaffold_r4_md(target_dir: Optional[Path] = None) -> Path:
    """Create a starter ``R4.md`` file in *target_dir* (default: cwd).

    Returns the path to the created file.

    Raises:
        FileExistsError: If an ``R4.md`` already exists at the location.
    """
    dest = (target_dir or Path.cwd()).resolve() / _R4_MD_FILENAME
    if dest.exists():
        raise FileExistsError(f"{dest} already exists")
    dest.write_text(_SCAFFOLD_TEMPLATE, encoding="utf-8")
    logger.info("Created %s", dest)
    return dest
