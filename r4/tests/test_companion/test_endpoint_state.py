"""Tests for companion endpoint state helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from r4.companion.endpoint_state import (
    format_active_endpoints_prompt,
    normalize_endpoint_status,
    summarize_endpoint_usage,
)


def _format_duration(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m{secs:02d}s"


def test_normalize_endpoint_status_adds_runtime_and_budget_fields():
    now = datetime.now(timezone.utc)
    normalized = normalize_endpoint_status(
        status={
            "instance_id": "ep-1",
            "backend": "slurm",
            "created_at": (now - timedelta(hours=2)).isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
            "node_count": 2,
        },
        now=now,
        warning_threshold_sec=900,
        block_threshold_sec=300,
    )

    assert normalized["runtime_hours"] == 2.0
    assert normalized["node_hours_estimate"] == 4.0
    assert normalized["expiry_state"] == "warning"
    assert normalized["remaining_seconds"] is not None


def test_format_active_endpoints_prompt_includes_usage_fields():
    prompt = format_active_endpoints_prompt(
        [
            {
                "instance_id": "ep-1",
                "model": "qwen-test",
                "status": "ready",
                "backend": "slurm",
                "remaining_seconds": 600,
                "runtime_hours": 1.5,
                "node_hours_estimate": 3.0,
                "expiry_state": "warning",
            }
        ],
        format_duration=_format_duration,
    )

    assert "[Active endpoints]" in prompt
    assert "remaining=10m00s" in prompt
    assert "runtime=1.50h" in prompt
    assert "node_hours=3.00" in prompt
    assert "expiry=warning" in prompt


def test_summarize_endpoint_usage_aggregates_budget():
    summary = summarize_endpoint_usage(
        [
            {"instance_id": "ep-1", "remaining_seconds": 600, "node_hours_estimate": 1.25},
            {"instance_id": "ep-2", "remaining_seconds": 300, "node_hours_estimate": 0.75},
        ],
        warning_threshold_sec=900,
        block_threshold_sec=300,
    )

    assert summary == {
        "node_hours_estimated": 2.0,
        "active_endpoint_count": 2,
        "nearest_expiry_seconds": 300,
        "warning_threshold_seconds": 900,
        "block_threshold_seconds": 300,
    }
