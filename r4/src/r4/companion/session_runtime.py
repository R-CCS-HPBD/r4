"""Runtime helpers for companion session orchestration."""

from __future__ import annotations

import logging
from typing import Any

from .endpoint_state import (
    collect_active_endpoints,
    list_active_endpoints_response,
    should_refresh_active_endpoints,
    supports_endpoint_refresh,
)


def get_harness(
    *,
    current_harness: Any,
    client: Any,
    context_manager: Any,
    skill_registry: Any,
    logger: logging.Logger,
) -> Any:
    if current_harness is not None:
        return current_harness
    try:
        from .harness import ResearchHarness

        return ResearchHarness(
            client=client,
            context_manager=context_manager,
            skill_registry=skill_registry,
        )
    except Exception as exc:
        logger.warning("Harness init failed — quality sensors and steering disabled: %s", exc)
        return None


def harness_post_turn(
    *,
    harness: Any,
    user_message: str,
    response: str,
    messages: list[Any],
    logger: logging.Logger,
) -> None:
    if harness is None:
        return
    try:
        harness.post_turn(
            user_message=user_message,
            response=response,
            messages=messages,
        )
    except Exception as exc:
        logger.warning("Harness post-turn error — quality feedback may be incomplete: %s", exc)


def refresh_active_endpoints(
    *,
    client: Any,
    active_endpoints: list[dict[str, Any]],
    last_refreshed_at: str | None,
    refresh_interval_sec: int,
    warning_threshold_sec: int,
    block_threshold_sec: int,
    force: bool,
    logger: logging.Logger,
) -> tuple[list[dict[str, Any]], str | None]:
    if not supports_endpoint_refresh(client):
        return active_endpoints, last_refreshed_at

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    if not should_refresh_active_endpoints(
        last_refreshed_at=last_refreshed_at,
        now=now,
        refresh_interval_sec=refresh_interval_sec,
        force=force,
    ):
        return active_endpoints, last_refreshed_at

    response = list_active_endpoints_response(client=client, logger=logger)
    if response is None:
        return active_endpoints, last_refreshed_at

    refreshed_endpoints = collect_active_endpoints(
        client=client,
        response=response,
        now=now,
        warning_threshold_sec=warning_threshold_sec,
        block_threshold_sec=block_threshold_sec,
        logger=logger,
    )
    return refreshed_endpoints, now.isoformat()
