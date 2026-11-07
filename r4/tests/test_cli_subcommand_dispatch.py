"""Unknown-subcommand routing for the r4 CLI (regression, 2026-09-14).

`r4 project run` — a verb WP-07 retired, and one QUICKSTART kept advertising
for ~10 months — did not produce an argparse error. The unknown token was
handed to the *top-level* lazy-dispatch registry, matched the global `r4 run`
(agent exec), and that handler read options the bare fallback Namespace never
carried, so the user got an uncaught ``AttributeError`` traceback.

The rule these tests pin: only the ROOT parser may lazy-dispatch. An unknown
token under a subcommand belongs to that subcommand's namespace.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


def _run(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "r4", *argv],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("bad", ["run", "nonesuch"])
def test_unknown_project_subcommand_is_an_argparse_error(bad: str) -> None:
    """It must name the invalid choice, not crash, and not run agent-exec."""
    result = _run("project", bad, "some-project")
    combined = result.stdout + result.stderr

    assert result.returncode == 2, combined
    assert "invalid choice" in combined
    assert bad in combined
    # The defect signature: a traceback from the misrouted global handler.
    assert "Traceback" not in combined, combined
    assert "AttributeError" not in combined, combined
    assert "exec_command" not in combined, combined


def test_unknown_project_subcommand_lists_real_subcommands() -> None:
    """The error is only useful if it shows what IS valid."""
    result = _run("project", "run")
    combined = result.stdout + result.stderr

    for expected in ("list", "create", "loop"):
        assert expected in combined, f"{expected!r} missing from: {combined}"


def test_root_lazy_dispatch_still_works() -> None:
    """The root parser must keep resolving lazily-registered commands."""
    result = _run("run", "--help")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage: r4 run" in result.stdout


def test_unknown_root_command_suggests_a_neighbour() -> None:
    """Root-level typos keep their did-you-mean suggestion."""
    result = _run("projekt")
    combined = result.stdout + result.stderr

    assert result.returncode != 0
    assert "projekt" in combined
    assert "project" in combined


def test_project_help_does_not_advertise_the_retired_verb() -> None:
    """The epilog told users to run a verb that no longer exists."""
    result = _run("project", "--help")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "r4 project run" not in result.stdout
    assert "r4 project loop start" in result.stdout
