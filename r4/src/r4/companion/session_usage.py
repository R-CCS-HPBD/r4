"""Usage accounting and session state helpers."""

from __future__ import annotations

from typing import Any

from .endpoint_state import summarize_endpoint_usage
from .session_models import BudgetLimitExceededError, UsageRecord, UsageSummary
from .session_state import dump_session_state, json_safe, load_session_state

USAGE_METADATA_EXCLUDED_KEYS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "estimated",
    "model",
}


def record_usage(
    *,
    usage_records: list[UsageRecord],
    usage_summary: UsageSummary,
    current_turn_llm_calls: int,
    call_type: str,
    payload: dict[str, Any],
) -> int:
    model = payload.get("model", "")
    if not isinstance(model, str):
        model = ""

    usage = UsageRecord(
        call_type=call_type,
        prompt_tokens=int(payload.get("prompt_tokens", 0)),
        completion_tokens=int(payload.get("completion_tokens", 0)),
        total_tokens=int(payload.get("total_tokens", 0)),
        estimated=bool(payload.get("estimated", False)),
        model=model,
        metadata={
            key: json_safe(value)
            for key, value in payload.items()
            if key not in USAGE_METADATA_EXCLUDED_KEYS
        },
    )
    usage_records.append(usage)
    usage_summary.record(usage)
    return current_turn_llm_calls + 1


def guard_llm_call(
    *,
    current_turn_llm_calls: int,
    max_llm_calls_per_turn: int | None,
    session_llm_calls: int,
    max_llm_calls_per_session: int | None,
) -> None:
    if max_llm_calls_per_turn is not None and current_turn_llm_calls >= max_llm_calls_per_turn:
        raise BudgetLimitExceededError(f"per-turn LLM call cap reached ({max_llm_calls_per_turn})")
    if max_llm_calls_per_session is not None and session_llm_calls >= max_llm_calls_per_session:
        raise BudgetLimitExceededError(
            f"per-session LLM call cap reached ({max_llm_calls_per_session})"
        )


def sync_metadata_usage_state(
    *,
    metadata: dict[str, Any],
    usage_summary: UsageSummary,
    usage_records: list[UsageRecord],
    active_endpoints: list[dict[str, Any]],
    active_endpoints_updated_at: str | None,
) -> None:
    metadata.update(
        dump_session_state(
            usage_summary_data=usage_summary.to_dict(),
            usage_record_data=[record.to_dict() for record in usage_records],
            active_endpoints=active_endpoints,
            active_endpoints_updated_at=active_endpoints_updated_at,
        )
    )


def load_usage_state_from_metadata(
    metadata: dict[str, Any],
) -> tuple[UsageSummary, list[UsageRecord], list[dict[str, Any]], str | None]:
    state = load_session_state(metadata)
    return (
        UsageSummary.from_dict(state.usage_summary_data),
        [UsageRecord.from_dict(record) for record in state.usage_record_data],
        state.active_endpoints,
        state.active_endpoints_updated_at,
    )


def usage_report(
    *,
    usage_summary: UsageSummary,
    active_endpoints: list[dict[str, Any]],
    warning_threshold_sec: int,
    block_threshold_sec: int,
) -> dict[str, Any]:
    return {
        "summary": usage_summary.to_dict(),
        "active_endpoints": active_endpoints,
        "budget": summarize_endpoint_usage(
            active_endpoints,
            warning_threshold_sec=warning_threshold_sec,
            block_threshold_sec=block_threshold_sec,
        ),
    }
