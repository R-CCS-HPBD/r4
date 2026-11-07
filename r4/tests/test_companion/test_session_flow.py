"""Tests for companion send-flow helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from r4.companion.session_flow import (
    evaluate_turn_endpoint_guard,
    finish_turn,
    run_agent_turn,
    send_turn,
    start_turn,
)
from r4.companion.session_models import BudgetLimitExceededError


def test_start_turn_updates_messages_and_selects_tools():
    messages = []
    skills = MagicMock()
    skills.auto_activate.return_value = ["hpc"]
    skills.active_tool_names.return_value = {"hpc_submit"}
    logger = MagicMock()

    updated_at, current_turn_llm_calls, active_tools = start_turn(
        messages=messages,
        user_message="run analysis",
        skills=skills,
        tools=[{"name": "vision_submit"}, {"name": "hpc_submit"}],
        updated_at_factory=lambda: "2026-04-14T12:00:00",
        logger=logger,
    )

    assert updated_at == "2026-04-14T12:00:00"
    assert current_turn_llm_calls == 0
    assert messages[0].content == "run analysis"
    assert active_tools == [{"name": "hpc_submit"}]
    logger.info.assert_called_once()


def test_evaluate_turn_endpoint_guard_refreshes_before_policy_check():
    refresh = MagicMock()

    decision = evaluate_turn_endpoint_guard(
        user_message="run a full workflow across the dataset",
        active_tools=[{"name": "shell_exec"}],
        refresh_active_endpoints=refresh,
        get_active_endpoints=lambda: [
            {"instance_id": "ep-1", "backend": "slurm", "remaining_seconds": 120}
        ],
        warning_threshold_sec=900,
        block_threshold_sec=300,
        format_duration=lambda seconds: f"{seconds}s",
    )

    refresh.assert_called_once_with()
    assert decision["state"] == "blocked"


def test_run_agent_turn_formats_budget_limit_errors():
    agent = MagicMock()
    agent.send.side_effect = BudgetLimitExceededError("per-session LLM call cap reached (0)")

    result = run_agent_turn(
        agent=agent,
        user_message="hello",
        history=[],
        extra_system="",
        active_tools=[],
    )

    assert result == "Budget limit reached: per-session LLM call cap reached (0)"


def test_finish_turn_appends_response_and_runs_callbacks():
    messages = []
    post_turn = MagicMock()
    sync_metadata = MagicMock()

    result = finish_turn(
        messages=messages,
        user_message="hello",
        response="world",
        post_turn=post_turn,
        sync_metadata=sync_metadata,
    )

    assert result == "world"
    assert messages[0].content == "world"
    post_turn.assert_called_once_with("hello", "world")
    sync_metadata.assert_called_once_with()


def test_send_turn_runs_success_path():
    start = MagicMock(return_value=[{"name": "python_exec"}])
    evaluate = MagicMock(return_value={"state": "clear"})
    prepare = MagicMock(
        return_value=(
            ("system", [{"role": "user", "content": "x"}], [{"name": "python_exec"}]),
            None,
        )
    )
    run = MagicMock(return_value="ok")
    finish = MagicMock(return_value="ok")

    result = send_turn(
        user_message="hello",
        start_turn=start,
        evaluate_turn_endpoint_guard=evaluate,
        prepare_turn_payload=prepare,
        run_agent_turn=run,
        finish_turn=finish,
    )

    assert result == "ok"
    finish.assert_called_once_with("hello", "ok")
