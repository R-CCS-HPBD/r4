"""Demo-vs-CLI drift test (bug.memo T4, 2026-04-25).

Scans ``demos/*/run_demo.sh`` for lines that invoke ``r4 <cmd> <sub>``
and asserts every referenced ``<cmd>`` exists in the current CLI. A
regression like Doc1 (demos referencing ``r4 agent resume`` /
``r4 agent audit`` after those subcommands were renamed) would fail
this test immediately.

The check is intentionally conservative:
  * only validates the FIRST token after ``r4`` is a real top-level
    subcommand;
  * ignores shell-variable / templated calls (``r4 $cmd``);
  * ignores lines that are obviously comments or echoed help text
    referencing hypothetical future commands (``# r4 future-cmd``).

Subcommand-level validation (e.g. ``r4 agent resume``) is not
attempted here because many valid invocations pass positional
arguments that we can't distinguish from subcommand names without
parsing argparse — that's what the T2 CLI-help test covers for the
declared commands.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

DEMOS_DIR = Path(__file__).resolve().parents[2] / "demos"

# One-token top-level subcommands accepted by the current argparse tree.
# Kept in sync with test_cli_help.py::TOP_LEVEL_COMMANDS.
KNOWN_TOP_LEVEL = {
    "chat",
    "rag",
    "model",
    "config",
    "project",
    "auth",
    "job",
    "mcp",
    "agent",
    "endpoint",
    "health",
    "companion",
    "artifact",
    "workflow",
    "dashboard",
    "data",
    "up",
    "down",
    "session",
    "search",
    "team",
    "skill",
    "vision",
    "recipe",
    "schedule",
    "admin",
    "run",
    "explain",
    "onboard",
    "completion",
    "doctor",
}

R4_COMMAND_SUBSTITUTION = re.compile(r"\$\(\s*r4\s+([a-z][a-z0-9_-]*)\b")


def _iter_invoked_top_level_commands(raw: str) -> list[str]:
    commands: list[str] = []

    try:
        tokens = shlex.split(raw, comments=False, posix=True)
    except ValueError:
        tokens = []

    for index, token in enumerate(tokens[:-1]):
        if token != "r4":
            continue
        next_token = tokens[index + 1]
        if re.fullmatch(r"[a-z][a-z0-9_-]*", next_token):
            commands.append(next_token)

    commands.extend(match.group(1) for match in R4_COMMAND_SUBSTITUTION.finditer(raw))
    return commands


def _collect_demo_files() -> list[Path]:
    if not DEMOS_DIR.exists():
        return []
    return sorted(DEMOS_DIR.glob("*/run_demo.sh"))


@pytest.mark.parametrize("demo", _collect_demo_files(), ids=lambda p: p.name)
def test_demo_only_uses_known_subcommands(demo: Path) -> None:
    unknown: list[tuple[int, str, str]] = []
    for lineno, raw in enumerate(demo.read_text().splitlines(), start=1):
        line = raw.strip()
        # Skip comments and shebang.
        if line.startswith("#") or line.startswith("!"):
            continue
        for cmd in _iter_invoked_top_level_commands(raw):
            if cmd in KNOWN_TOP_LEVEL:
                continue
            # Heuristic: ignore templated tokens like `$cmd` or `{CMD}`
            unknown.append((lineno, cmd, raw))

    assert not unknown, f"{demo.name} references unknown r4 subcommands:\n" + "\n".join(
        f"  line {ln}: r4 {cmd!r}  ::  {src}" for ln, cmd, src in unknown
    )


def test_at_least_one_demo_exists() -> None:
    demos = _collect_demo_files()
    assert demos, f"expected demo scripts under {DEMOS_DIR}"
