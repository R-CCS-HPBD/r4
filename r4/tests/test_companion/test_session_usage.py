"""Tests for companion usage helper functions."""

from __future__ import annotations

import pytest

from r4.companion.session_models import BudgetLimitExceededError, UsageSummary
from r4.companion.session_usage import (
    guard_llm_call,
    load_usage_state_from_metadata,
    record_usage,
    sync_metadata_usage_state,
    usage_report,
)


def test_record_usage_serializes_metadata_and_increments_counter():
    usage_records = []
    usage_summary = UsageSummary()

    current_turn_calls = record_usage(
        usage_records=usage_records,
        usage_summary=usage_summary,
        current_turn_llm_calls=0,
        call_type="turn",
        payload={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "estimated": True,
            "model": "qwen-test",
            "metadata": {"nested": (1, 2)},
        },
    )

    assert current_turn_calls == 1
    assert usage_summary.total_tokens == 15
    assert usage_records[0].metadata == {"metadata": {"nested": [1, 2]}}


def test_guard_llm_call_enforces_turn_and_session_limits():
    with pytest.raises(BudgetLimitExceededError, match="per-turn"):
        guard_llm_call(
            current_turn_llm_calls=2,
            max_llm_calls_per_turn=2,
            session_llm_calls=1,
            max_llm_calls_per_session=5,
        )

    with pytest.raises(BudgetLimitExceededError, match="per-session"):
        guard_llm_call(
            current_turn_llm_calls=1,
            max_llm_calls_per_turn=2,
            session_llm_calls=5,
            max_llm_calls_per_session=5,
        )


def test_sync_and_load_usage_state_round_trip():
    metadata: dict[str, object] = {}
    usage_summary = UsageSummary(llm_calls=2, total_tokens=15)
    usage_records = []
    record_usage(
        usage_records=usage_records,
        usage_summary=usage_summary,
        current_turn_llm_calls=0,
        call_type="turn",
        payload={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    )

    sync_metadata_usage_state(
        metadata=metadata,
        usage_summary=usage_summary,
        usage_records=usage_records,
        active_endpoints=[{"instance_id": "ep-1"}],
        active_endpoints_updated_at="2026-04-14T12:00:00Z",
    )

    restored_summary, restored_records, active_endpoints, updated_at = (
        load_usage_state_from_metadata(metadata)
    )

    assert restored_summary.llm_calls == usage_summary.llm_calls
    assert restored_records[0].total_tokens == 3
    assert active_endpoints == [{"instance_id": "ep-1"}]
    assert updated_at == "2026-04-14T12:00:00Z"


def test_usage_report_includes_endpoint_budget_summary():
    report = usage_report(
        usage_summary=UsageSummary(llm_calls=3, total_tokens=30),
        active_endpoints=[
            {"instance_id": "ep-1", "remaining_seconds": 600, "node_hours_estimate": 1.25},
            {"instance_id": "ep-2", "remaining_seconds": 300, "node_hours_estimate": 0.75},
        ],
        warning_threshold_sec=900,
        block_threshold_sec=300,
    )

    assert report["summary"]["llm_calls"] == 3
    assert report["budget"]["node_hours_estimated"] == 2.0
    assert report["budget"]["nearest_expiry_seconds"] == 300
