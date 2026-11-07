"""Demo 07 names the workflow its research loop runs (2026-10-23).

Without ``--workflow`` the server refuses a loop start whenever several
workflows are registered, and Demo 06 registers its DAG right before Demo 07
runs: every nightly ai4s loop start was a 400 (Ops Watch #532 triage).
"""

from __future__ import annotations

import re
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1] / "demos" / "07_ask" / "run_demo.sh"


def test_demo07_loop_start_names_demo06s_workflow() -> None:
    text = DEMO.read_text()
    starts = [
        line
        for line in text.splitlines()
        if "r4 project loop start" in line and "demo_try_cmd" in line
    ]
    assert starts, "demo 07 no longer starts a loop"
    for line in starts:
        assert re.search(r"--workflow\s+\S+", line), line
    assert "R4_DEMO_COMPLEX_WORKFLOW_NAME:-r4-complex-dag-demo" in text
