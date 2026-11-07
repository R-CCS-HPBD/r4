"""Tests for companion runtime helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from r4.companion.session_runtime import (
    get_harness,
    harness_post_turn,
    refresh_active_endpoints,
)


def test_get_harness_reuses_existing_instance():
    harness = object()

    result = get_harness(
        current_harness=harness,
        client=MagicMock(),
        context_manager=MagicMock(),
        skill_registry=MagicMock(),
        logger=MagicMock(),
    )

    assert result is harness


def test_harness_post_turn_ignores_missing_harness():
    harness_post_turn(
        harness=None,
        user_message="hello",
        response="world",
        messages=[],
        logger=MagicMock(),
    )


def test_harness_post_turn_logs_failures():
    harness = MagicMock()
    harness.post_turn.side_effect = RuntimeError("boom")
    logger = MagicMock()

    harness_post_turn(
        harness=harness,
        user_message="hello",
        response="world",
        messages=[],
        logger=logger,
    )

    logger.warning.assert_called_once()


def test_refresh_active_endpoints_returns_existing_state_when_client_unsupported():
    active_endpoints, updated_at = refresh_active_endpoints(
        client=object(),
        active_endpoints=[{"instance_id": "ep-1"}],
        last_refreshed_at="2026-04-14T12:00:00Z",
        refresh_interval_sec=30,
        warning_threshold_sec=900,
        block_threshold_sec=300,
        force=False,
        logger=MagicMock(),
    )

    assert active_endpoints == [{"instance_id": "ep-1"}]
    assert updated_at == "2026-04-14T12:00:00Z"


def test_refresh_active_endpoints_collects_new_state():
    client = MagicMock()
    logger = MagicMock()
    client.list_endpoints.return_value = {
        "endpoints": [{"instance_id": "ep-1", "job_id": "ep-1"}],
    }
    client.get_endpoint_status.return_value = {
        "instance_id": "ep-1",
        "job_id": "ep-1",
        "status": "ready",
    }

    with patch("r4.companion.session_runtime.collect_active_endpoints") as collect_mock:
        collect_mock.return_value = [{"instance_id": "ep-1", "status": "ready"}]
        active_endpoints, updated_at = refresh_active_endpoints(
            client=client,
            active_endpoints=[],
            last_refreshed_at=None,
            refresh_interval_sec=30,
            warning_threshold_sec=900,
            block_threshold_sec=300,
            force=False,
            logger=logger,
        )

    assert active_endpoints == [{"instance_id": "ep-1", "status": "ready"}]
    assert isinstance(updated_at, str)
