"""`pytest tests/` must not run the heavyweight E2E files by default (plan H-3.7).

These files block on a cluster, a compose stack or a live service when they
run. Each one is opt-in today: a module-level guard (``R4_E2E_K8S``,
``R4_E2E_COMPOSE``), a ``skipif`` on its service URL, or the ``e2e`` tier
marker that ``tests/conftest.py`` skips unless ``R4_RUN_E2E=1``. This test
runs them with every opt-in variable removed and fails if any test executes,
so a guard cannot be dropped silently and turn the root suite back into a hang
(2026-09-13: the root suite hung on these files when run whole).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

HEAVYWEIGHT_E2E = (
    "tests/test_k8s_e2e.py",
    "tests/test_custom_mcp_e2e.py",
    "tests/test_assistant_e2e.py",
    "tests/test_mock_e2e.py",
    "tests/test_research_journey_e2e.py",
)

_OPT_IN_PREFIXES = ("R4_RUN_E2E", "R4_E2E_", "ASSISTANT_E2E_", "E2E_TEST_MODE")


def _env_without_opt_ins() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(_OPT_IN_PREFIXES)}
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(REPO / "umrs" / "src"), env.get("PYTHONPATH", "")) if p
    )
    return env


def _outcomes(summary: str) -> dict[str, int]:
    return {word: int(n) for n, word in re.findall(r"(\d+) (\w+)", summary)}


def test_heavyweight_e2e_files_only_skip_by_default() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *HEAVYWEIGHT_E2E, "-q", "-p", "no:cacheprovider"]
        + ["-o", "addopts=", "-rN"],
        cwd=REPO,
        env=_env_without_opt_ins(),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    outcomes = _outcomes(last)

    assert proc.returncode in (0, 5), proc.stdout[-2000:] + proc.stderr[-2000:]
    assert outcomes.get("passed", 0) == 0, last
    assert outcomes.get("failed", 0) == 0, last
    assert outcomes.get("error", 0) + outcomes.get("errors", 0) == 0, last
