"""`r4 workflow results <id>` — per-step outputs and the download hint (ADR-079)."""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

from r4._cli.workflow import _cmd_results


class _WF:
    def get_results(self, execution_id):
        return SimpleNamespace(
            execution_id=execution_id,
            status="succeeded",
            error=None,
            outputs={
                "steps": {
                    "detect": {
                        "output_keys": ["runs/wf-1/detect/a.csv"],
                        "extra": {"gpus": 1, "slurm_job": {"workdir": "/x"}},
                    },
                }
            },
            artifacts=["alice.demo-output/runs/wf-1/detect/a.csv"],
        )


def test_results_prints_steps_and_a_download_command(capsys):
    assert _cmd_results(_WF(), Namespace(execution_id="wf-1", as_json=False)) == 0
    out = capsys.readouterr().out
    assert "[detect] 1 output key(s)" in out and "runs/wf-1/detect/a.csv" in out
    assert "slurm_job" not in out  # cluster paths stay out of the summary
    assert "r4 files download runs/wf-1/ --bucket alice.demo-output --recursive" in out


def test_results_json_carries_steps_and_artifacts(capsys):
    assert _cmd_results(_WF(), Namespace(execution_id="wf-1", as_json=True)) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "succeeded" and "detect" in data["steps"]
    assert data["artifacts"] == ["alice.demo-output/runs/wf-1/detect/a.csv"]
