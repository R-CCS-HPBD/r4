"""Tests for companion session metadata serialization helpers."""

from __future__ import annotations

from r4.companion.session_state import dump_session_state, json_safe, load_session_state


def test_dump_and_load_session_state_round_trip():
    payload = dump_session_state(
        usage_summary_data={"llm_calls": 2},
        usage_record_data=[{"call_type": "turn"}, {"call_type": "compaction"}],
        active_endpoints=[{"instance_id": "ep-1"}, "skip-me"],
        active_endpoints_updated_at="2026-04-13T12:00:00Z",
    )

    restored = load_session_state(payload)

    assert restored.usage_summary_data == {"llm_calls": 2}
    assert restored.usage_record_data == [{"call_type": "turn"}, {"call_type": "compaction"}]
    assert restored.active_endpoints == [{"instance_id": "ep-1"}]
    assert restored.active_endpoints_updated_at == "2026-04-13T12:00:00Z"


def test_json_safe_normalizes_nested_values():
    class CustomValue:
        def __str__(self) -> str:
            return "custom-value"

    normalized = json_safe(
        {
            "tuple": (1, True),
            "list": [CustomValue()],
            3: {"nested": CustomValue()},
        }
    )

    assert normalized == {
        "tuple": [1, True],
        "list": ["custom-value"],
        "3": {"nested": "custom-value"},
    }
