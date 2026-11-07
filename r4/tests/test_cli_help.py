"""CLI help smoke test (bug.memo T2, 2026-04-25; updated Phase 2l 2026-04-27).

Ensures `r4 <cmd> --help` exits 0 for every declared top-level
subcommand. A regression like B1/B2 (bad re-exports in ``teamup`` /
``search``) would make the corresponding ``--help`` invocation crash
on module import, and this test catches it before CI merges.

Phase 2l reorganized the top level (hard renames, no aliases):
``r4 init`` -> ``r4 config init``
``r4 login`` / ``r4 logout`` -> ``r4 auth login`` / ``r4 auth logout``
``r4 companion`` / ``r4 agent`` / ``r4 teamup`` / ``r4 search`` -> ``r4 ask <verb>``
``r4 pipeline`` -> ``r4 rag ingest``
``r4 profile`` -> ``r4 rag retriever``
``r4 sessions`` -> ``r4 session``
"""

from __future__ import annotations

import subprocess
import sys

import pytest

# Each entry is the path to invoke (space-separated). Together they cover
# every leaf surfaced by the new CLI tree.
TOP_LEVEL_COMMANDS = [
    "chat",
    "rag",
    "rag docs",
    "model",
    "config",
    "project",
    "auth",
    "auth login",
    "auth logout",
    "auth status",
    "job",
    "mcp",
    "mcp hatch",
    "companion",
    "endpoint",
    "health",
    "guide",
    "artifact",
    "workflow",
    "rag ingest",
    "rag retriever",
    "session",
    "agent",
    "search",
    "team",
    "skill",
    "vision",
    "recipe",
    "schedule",
    "admin",
    "admin analytics",
    "admin trace",
    "admin analytics",
    "admin trace",
    "pool",
    "source",
    "map",
    "governance",
    "run",
    "up",
    "down",
    "explain",
    "onboard",
    "completion",
]


PARENT_COMMANDS = [
    ("auth", "login"),
    ("model", "list"),
    ("config", "show"),
    ("project", "list"),
    ("job", "list"),
    ("mcp", "list"),
    ("mcp hatch", "create"),
    ("team", "create"),
    ("endpoint", "up"),
    ("artifact", "list"),
    ("workflow", "list"),
    ("rag", "upload"),
    ("rag docs", "list"),
    # "rag ingest" is intentionally absent: retired 2026-10-18 (H-10.1
    # follow-up), it prints the retirement notice instead of subcommand help
    # (pinned by test_rag_cli_handlers.py::test_rag_ingest_is_retired_without_a_request).
    ("rag retriever", "list"),
]


@pytest.mark.parametrize("cmd", TOP_LEVEL_COMMANDS)
def test_cli_help_exits_zero(cmd: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "r4.cli", *cmd.split(), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"`r4 {cmd} --help` exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # argparse writes help to stdout; make sure we actually got help text,
    # not just a silent zero exit.
    assert "usage:" in result.stdout.lower()


def test_cli_root_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "r4.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    # Every top-level command (first token of each path) should be
    # advertised in the root help.
    top_level_unique = {c.split()[0] for c in TOP_LEVEL_COMMANDS}
    for cmd in top_level_unique:
        assert cmd in result.stdout, f"missing subcommand `{cmd}` in root help"
    assert "Use umrs --help only for UMRS diagnostics" in result.stdout or "umrs" in result.stdout
    assert (
        "Use umrs-server admin --help for offline operator" in result.stdout
        or "umrs-server" in result.stdout
    )


def test_cli_completion_includes_nested_commands() -> None:
    from r4._cli.completion import completion_candidates
    from r4.cli import _build_parser

    parser = _build_parser()

    assert "rag" in completion_candidates(parser, [""], 0)
    assert "docs" in completion_candidates(parser, ["rag", ""], 1)
    assert "login" in completion_candidates(parser, ["auth", ""], 1)


def test_cli_completion_command_prints_setup() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "r4.cli", "completion", "bash"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "_R4_COMPLETE" in result.stdout
    assert "complete " in result.stdout


@pytest.mark.parametrize(("cmd", "child"), PARENT_COMMANDS)
def test_cli_parent_command_without_subcommand_shows_local_help(
    cmd: str,
    child: str,
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "r4.cli", *cmd.split()],
        capture_output=True,
        text=True,
        timeout=30,
    )

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 2, output
    assert f"usage: r4 {cmd}" in output, output
    assert child in output, output
    assert "hint: choose one of the subcommands shown above." in output, output
