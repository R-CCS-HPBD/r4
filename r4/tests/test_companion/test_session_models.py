"""Tests for companion session data structures."""

from __future__ import annotations

from r4.companion.session_models import UsageRecord, UsageSummary


def test_usage_record_round_trip():
    record = UsageRecord(
        call_type="turn",
        prompt_tokens=12,
        completion_tokens=5,
        total_tokens=17,
        estimated=True,
        model="qwen-test",
        metadata={"source": "unit"},
    )

    restored = UsageRecord.from_dict(record.to_dict())

    assert restored == record


def test_usage_summary_records_compaction_and_subagent_calls():
    summary = UsageSummary()

    summary.record(
        UsageRecord(call_type="turn", prompt_tokens=10, completion_tokens=2, total_tokens=12)
    )
    summary.record(
        UsageRecord(call_type="compaction", prompt_tokens=4, completion_tokens=1, total_tokens=5)
    )
    summary.record(
        UsageRecord(call_type="subagent", prompt_tokens=8, completion_tokens=3, total_tokens=11)
    )

    assert summary.to_dict() == {
        "llm_calls": 3,
        "compaction_calls": 1,
        "subagent_calls": 1,
        "prompt_tokens": 22,
        "completion_tokens": 6,
        "total_tokens": 28,
        "estimated_calls": 0,
    }
