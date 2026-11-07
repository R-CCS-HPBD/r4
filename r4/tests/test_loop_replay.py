"""D-52 pins: ``r4 project loop replay`` renders one trace id through the
loop record, iterations and gate inbox (read-only)."""

from __future__ import annotations

import json

from r4._cli.project_knowledge import _loop_replay


class _Loop:
    def to_dict(self):
        return {
            "loop_id": "loop-9",
            "research_goal": "characterise defects",
            "status": "stopped",
            "metadata": {
                "trace_id": "loop-9",
                "driver": "engine",
                "venue": "frontend",
                "durable_job_id": "job-1",
            },
        }


class _Iter:
    def __init__(self, n, hypo="h", summary=None):
        self._d = {
            "iteration_id": f"it-{n}",
            "loop_id": "loop-9",
            "iteration_number": n,
            "phase": "experiment_execution",
            "status": "completed",
            "hypothesis": hypo,
            "summary": summary or {},
        }

    def to_dict(self):
        return dict(self._d)


class _Runtime:
    def get_loop(self, loop_id):
        return _Loop()

    def list_iterations(self, loop_id, limit=50):
        return [_Iter(1, "vacancy clusters"), _Iter(2, "grain boundaries", {"loss": 0.1})]

    def list_approvals(self, loop_id=None):
        return [
            {
                "id": "ap-1",
                "status": "approved",
                "action": {"kind": "workflow_trigger", "iteration": 2},
            }
        ]


def test_replay_text_covers_trace_iterations_and_gates(capsys):
    _loop_replay(_Runtime(), "loop-9", False)
    out = capsys.readouterr().out
    assert "Replay trace loop-9" in out
    assert "driver=engine" in out
    assert "hosting job: job-1" in out
    assert "vacancy clusters" in out
    assert "grain boundaries" in out
    assert "results: loss" in out
    assert "[gate] ap-1 approved" in out
    assert "kind=workflow_trigger" in out


def test_replay_json_carries_all_sections(capsys):
    _loop_replay(_Runtime(), "loop-9", True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["trace_id"] == "loop-9"
    assert len(payload["iterations"]) == 2
    assert payload["approvals"][0]["id"] == "ap-1"


def test_replay_empty_iterations_is_honest(capsys):
    class _Empty(_Runtime):
        def list_iterations(self, loop_id, limit=50):
            return []

        def list_approvals(self, loop_id=None):
            return []

    _loop_replay(_Empty(), "loop-9", False)
    assert "(no iterations)" in capsys.readouterr().out
