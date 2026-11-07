"""Tests for r4 dashboard (U1 aggregate board)."""

from __future__ import annotations

from r4._cli.dashboard import _render_board


def _overview():
    return {
        "total_jobs": 12,
        "running_jobs": 2,
        "queued_jobs": 1,
        "completed_jobs": 7,
        "failed_jobs": 2,
        "total_models": 5,
        "available_models": 4,
        "staging_models": 1,
        "deprecated_models": 0,
        "system_health": "healthy",
        "workflows": {
            "total": 9,
            "running": 1,
            "completed": 6,
            "failed": 1,
            "cancelled": 1,
        },
        "connectors": {
            "available_types": ["github", "gdrive"],
            "unavailable": ["federated"],
            "sync": {
                "github": {
                    "status": "ok",
                    "last_sync_at": "2026-08-01T10:00:00+00:00",
                    "docs_synced": 57,
                    "dlq": 1,
                }
            },
        },
    }


def test_board_renders_all_sections(capsys):
    _render_board(_overview())
    out = capsys.readouterr().out
    assert "system ✓ healthy" in out
    assert "total 12" in out
    assert "total 5" in out
    assert "total 9" in out
    assert "2 available, 1 import-failed" in out
    assert "card github" in out
    assert "docs 57" in out
    assert "dlq 1" in out


def test_board_section_filter(capsys):
    _render_board(_overview(), only="workflows")
    out = capsys.readouterr().out
    assert "workflows" in out and "total 9" in out
    assert "total 12" not in out  # jobs section suppressed


def test_board_missing_workflows_nullsafe(capsys):
    ov = _overview()
    ov["workflows"] = None
    _render_board(ov)
    assert "workflows     : n/a (registry absent)" in capsys.readouterr().out


def test_board_unhealthy_glyph(capsys):
    ov = _overview()
    ov["system_health"] = "unhealthy"
    _render_board(ov)
    assert "system ✗ unhealthy" in capsys.readouterr().out
