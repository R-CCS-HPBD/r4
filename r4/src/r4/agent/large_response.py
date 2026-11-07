"""Large response handler for the R4 agent.

When tool output exceeds a configurable threshold it is cut to head + tail,
or — when very large — saved to a file and replaced with head + tail plus the
file's path, so the model can ``file_read`` (or grep) the rest instead of
losing it. This keeps one tool result from overflowing the context window.

Spill location (harness repair plan 2026-10-05, row H6; gap row D-26): inside
the workspace, ``<workspace>/.r4/outputs/<tool>_<sha12>.txt``. The workspace
is the nearest directory at or above cwd that already holds a ``.r4/``
directory (the home directory excluded), else cwd itself — so a subdirectory
of a project never grows a second ``.r4/`` that would shadow the project's
``.r4/config.toml`` (``r4.config`` resolves the nearest one). When the
workspace is not writable the file goes to the system temp dir as
``r4_large_<tool>_<sha12>.txt`` (the location used before W2).

Under the terminal-bench ablation profile (``R4_AGENT_PROFILE=terminal``; TB
4.0 harness-efficiency plan 2026-10-24, item H16) spills go to
``$TMPDIR/r4-outputs/<tool>_<sha12>.txt`` instead, so a benchmark verifier
never sees an R4 artefact in the task's directory.

Inspired by goose's ``large_response_handler``.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default thresholds (characters). The companion passes its per-result cap as
# ``truncate_at`` and ``SPILL_MULTIPLIER`` times that as ``summarize_at``
# (``companion/agent.py``); the defaults follow the same derivation.
TRUNCATE_THRESHOLD = 40_000
SPILL_MULTIPLIER = 3
SUMMARIZE_THRESHOLD = TRUNCATE_THRESHOLD * SPILL_MULTIPLIER

# Inline text kept next to the path of a spilled response.
SPILL_HEAD_CHARS = 2_000
SPILL_TAIL_CHARS = 1_000

# Workspace-relative spill directory; a ``*`` .gitignore keeps spills out of git.
WORKSPACE_SPILL_DIR = Path(".r4") / "outputs"
# Spill directory name under the system temp dir for the terminal profile.
TERMINAL_PROFILE_SPILL_DIR = "r4-outputs"
_SPILL_GITIGNORE = "# R4 tool-output spills (regenerable)\n*\n"


def handle_large_response(
    content: str,
    *,
    tool_name: str = "",
    truncate_at: int = TRUNCATE_THRESHOLD,
    summarize_at: int = SUMMARIZE_THRESHOLD,
    scratch_dir: Optional[Path] = None,
    workspace: Optional[Path] = None,
) -> str:
    """Process a potentially large tool response.

    - Under ``truncate_at``: returned as-is.
    - Between ``truncate_at`` and ``summarize_at``: head + tail with a
      truncation notice.
    - Over ``summarize_at``: written to a file; the first
      ``SPILL_HEAD_CHARS`` and last ``SPILL_TAIL_CHARS`` chars are returned
      with the file's path.

    Args:
        content: The raw tool output.
        tool_name: Name of the tool (for logging/messages and the file name).
        truncate_at: Character count to start truncating.
        summarize_at: Character count to switch to file storage.
        scratch_dir: Explicit directory for the spill file; when given, it is
            the only location tried.
        workspace: Directory to resolve the workspace from (default: cwd).

    Returns:
        The (possibly truncated/replaced) content.
    """
    length = len(content)
    if length <= truncate_at:
        return content

    if length <= summarize_at:
        head_size = truncate_at // 2
        tail_size = truncate_at // 4
        return (
            content[:head_size]
            + (
                f"\n\n... [{length - head_size - tail_size:,} chars omitted "
                f"from {tool_name or 'output'}] ...\n\n"
            )
            + content[-tail_size:]
        )

    path = _spill_to_file(content, tool_name, scratch_dir=scratch_dir, workspace=workspace)
    if path is not None:
        logger.info("Large response from %s (%d chars) saved to %s", tool_name, length, path)
    return _spill_reference(content, tool_name or "tool", path)


def _tool_slug(tool_name: str) -> str:
    """File-name-safe form of a tool name (MCP names may carry ``/`` or ``:``)."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_name).strip("._")
    return slug[:64] or "output"


def _home_dir() -> Optional[Path]:
    """The resolved home directory, or None when it cannot be determined."""
    try:
        return Path.home().resolve()
    except (RuntimeError, OSError):
        return None


def workspace_root(start: Path) -> Path:
    """Nearest directory at/above *start* holding ``.r4/`` (home excluded), else *start*.

    Args:
        start: The directory the tool ran in (normally cwd).

    Returns:
        The directory whose ``.r4/outputs`` receives spills.
    """
    start = start.resolve()
    home = _home_dir()
    for parent in (start, *start.parents):
        if parent == home:
            break
        if (parent / ".r4").is_dir():
            return parent
    return start


def _write_spill(content: str, directory: Path, fname: str) -> Path:
    """Write *content* to ``directory / fname`` (directories created)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / fname
    path.write_text(content, encoding="utf-8")
    return path


def _ensure_spill_gitignore(directory: Path) -> None:
    """Drop a ``*`` .gitignore into the workspace spill dir (best effort)."""
    ignore = directory / ".gitignore"
    if not ignore.exists():
        with contextlib.suppress(OSError):
            ignore.write_text(_SPILL_GITIGNORE, encoding="utf-8")


def _terminal_profile_active() -> bool:
    """``R4_AGENT_PROFILE=terminal``, read from the env (no companion import here)."""
    return os.environ.get("R4_AGENT_PROFILE", "").strip().lower() == "terminal"


def _spill_candidates(
    slug: str, digest: str, scratch_dir: Optional[Path], workspace: Optional[Path]
) -> list[tuple[Path, str, bool]]:
    """``(directory, file name, is_workspace)`` in the order they are tried."""
    tmp_name = f"r4_large_{slug}_{digest}.txt"
    if scratch_dir is not None:
        return [(scratch_dir, tmp_name, False)]
    if _terminal_profile_active():
        profile_dir = Path(tempfile.gettempdir()) / TERMINAL_PROFILE_SPILL_DIR
        return [(profile_dir, f"{slug}_{digest}.txt", False)]
    candidates: list[tuple[Path, str, bool]] = []
    try:
        root = workspace_root(workspace or Path.cwd())
        candidates.append((root / WORKSPACE_SPILL_DIR, f"{slug}_{digest}.txt", True))
    except OSError as exc:  # cwd deleted under us
        logger.warning("No workspace for large %s response: %s", slug, exc)
    candidates.append((Path(tempfile.gettempdir()), tmp_name, False))
    return candidates


def _spill_to_file(
    content: str,
    tool_name: str,
    *,
    scratch_dir: Optional[Path],
    workspace: Optional[Path],
) -> Optional[Path]:
    """Save *content* where the model can read it back; None when nothing is writable."""
    slug = _tool_slug(tool_name)
    digest = hashlib.sha256(content.encode()).hexdigest()[:12]
    for directory, fname, is_workspace in _spill_candidates(slug, digest, scratch_dir, workspace):
        try:
            path = _write_spill(content, directory, fname)
        except OSError as exc:
            logger.warning("Could not save large %s response in %s: %s", slug, directory, exc)
            continue
        if is_workspace:
            _ensure_spill_gitignore(directory)
        return path
    return None


def _spill_reference(content: str, label: str, path: Optional[Path]) -> str:
    """Inline text for a spilled response: header, head, omission marker, tail."""
    length = len(content)
    head = content[:SPILL_HEAD_CHARS]
    tail = content[max(len(head), length - SPILL_TAIL_CHARS) :]
    omitted = length - len(head) - len(tail)
    if path is None:
        header = (
            f"[Response from {label} was {length:,} chars and could not be saved to a "
            f"file. First {len(head):,} and last {len(tail):,} chars below]"
        )
        marker = f"... [{omitted:,} chars omitted] ..."
    else:
        header = (
            f"[Response from {label} was {length:,} chars — full text saved to {path}. "
            f"Read the rest with file_read (offset/limit) or grep that file. "
            f"First {len(head):,} and last {len(tail):,} chars below]"
        )
        marker = f"... [{omitted:,} chars omitted — full text in {path}] ..."
    return f"{header}\n\n{head}\n\n{marker}\n\n{tail}"
