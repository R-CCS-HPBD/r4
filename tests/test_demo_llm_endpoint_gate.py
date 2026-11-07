"""Demos 06 and 07 run their all-LLM workflow only where an LLM endpoint is ready.

Every step of ``r4-complex-dag-demo`` needs ``model_type='llm'``. The e2e demo
suite runs on ai4s, which has no LLM endpoint: each nightly run's Demo 06
trigger and Demo 07 loop failed "routing failed … no matching active
endpoint", were retried until exhausted, and fed JobFailureRateHigh
(Ops Watch #646–#650, 2026-10-30). ``demo_has_ready_llm_endpoint`` gates both.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

DEMOS = Path(__file__).resolve().parents[1] / "demos"
LIB = DEMOS / "lib" / "r4_demo.sh"


def _gate(tmp_path: Path, endpoints: object) -> int:
    """Exit status of demo_has_ready_llm_endpoint against a fake ``r4``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    payload = tmp_path / "endpoints.json"
    payload.write_text(json.dumps(endpoints))
    fake = bin_dir / "r4"
    fake.write_text(f'#!/usr/bin/env bash\ncat "{payload}"\n')
    fake.chmod(0o755)
    script = f'source "{LIB}"; demo_has_ready_llm_endpoint'
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "R4_DEMO_STATE_DIR": str(tmp_path / "state"),
    }
    return subprocess.run(["bash", "-c", script], env=env, check=False).returncode


@pytest.mark.parametrize(
    ("endpoints", "expected"),
    [
        ({"endpoints": []}, 1),
        ({"endpoints": [{"status": "pending", "model_type": "llm"}]}, 1),
        ({"endpoints": [{"status": "ready", "model_type": "vision"}]}, 1),
        ({"endpoints": [{"status": "ready", "model_type": "llm"}]}, 0),
        ({"endpoints": [{"status": "ACTIVE"}]}, 0),
        ([{"status": "running", "model_type": "llm"}], 0),
    ],
)
def test_the_gate_wants_a_ready_llm_endpoint(tmp_path, endpoints, expected) -> None:
    assert _gate(tmp_path, endpoints) == expected


def test_a_broken_listing_counts_as_no_endpoint(tmp_path) -> None:
    assert _gate(tmp_path, "not json at all") == 1


@pytest.mark.parametrize(
    ("demo", "guarded"),
    [
        ("06_complex_workflow/run_demo.sh", "trigger_complex_workflow"),
        ("07_ask/run_demo.sh", "r4 project loop start"),
    ],
)
def test_both_llm_runs_sit_behind_the_gate(demo: str, guarded: str) -> None:
    lines = (DEMOS / demo).read_text().splitlines()
    calls = [i for i, line in enumerate(lines) if guarded in line and "()" not in line]
    assert calls, (demo, guarded)
    for i in calls:
        assert "if demo_has_ready_llm_endpoint" in lines[i - 1], (demo, lines[i - 1])
