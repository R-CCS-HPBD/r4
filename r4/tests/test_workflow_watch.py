"""Tests for r4 workflow watch helpers (U2)."""

from __future__ import annotations

from r4._cli.workflow import (
    _step_status_map,
    _watch_exit_code,
    _watch_frame,
)


class _FakeWf:
    def __init__(self, desc=None, raise_desc=False):
        self._desc = desc or {}
        self._raise = raise_desc

    def describe(self, name, version=None):
        if self._raise:
            raise RuntimeError("no such workflow")
        return self._desc


_STEPS = [
    {"id": "a", "name": "ingest", "step_type": "umrs_job"},
    {"id": "b", "name": "verify", "step_type": "manual_gate", "depends_on": ["a"]},
]


def test_watch_frame_renders_dag_with_statuses():
    detail = {
        "status": "running",
        "workflow_name": "demo",
        "results": {"steps": {"a": {"status": "completed"}, "b": {"status": "running"}}},
    }
    frame = _watch_frame(_FakeWf({"steps": _STEPS}), detail, "exec-1")
    assert "watch exec-1" in frame
    assert "status: running" in frame
    assert "● [JOB] ingest" in frame
    assert "◐ [GATE] verify ← a" in frame


def test_watch_frame_progress_fallback_without_describe():
    detail = {"status": "running", "workflow_name": "", "progress": 0.42}
    frame = _watch_frame(_FakeWf(raise_desc=True), detail, "e2")
    assert "progress: 42.0%" in frame


def test_step_status_map_ignores_bad_rows():
    detail = {"results": {"steps": {"a": {"status": "failed"}, "b": "junk"}}}
    assert _step_status_map(detail) == {"a": "failed"}


def test_watch_exit_code_terminal_states():
    assert _watch_exit_code({"status": "completed"}) == 0
    assert _watch_exit_code({"status": "succeeded"}) == 0
    assert _watch_exit_code({"status": "failed"}) == 1
    assert _watch_exit_code({"status": "cancelled"}) == 1
