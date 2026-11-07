"""Endpoint refresh, normalization, and reporting helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable


def supports_endpoint_refresh(client: Any) -> bool:
    return hasattr(client, "list_endpoints") and hasattr(client, "get_endpoint_status")


def should_refresh_active_endpoints(
    *,
    last_refreshed_at: str | None,
    now: datetime,
    refresh_interval_sec: int,
    force: bool,
) -> bool:
    if force or not last_refreshed_at:
        return True
    last = _parse_iso(last_refreshed_at)
    if last is None:
        return True
    elapsed = (now - last).total_seconds()
    return elapsed >= refresh_interval_sec


def list_active_endpoints_response(
    *,
    client: Any,
    logger: logging.Logger,
) -> dict[str, Any] | None:
    try:
        return client.list_endpoints(include_stopped=False)
    except Exception as exc:
        logger.debug("Endpoint refresh failed during list: %s", exc)
        return None


def collect_active_endpoints(
    *,
    client: Any,
    response: dict[str, Any],
    now: datetime,
    warning_threshold_sec: int,
    block_threshold_sec: int,
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    for endpoint in response.get("endpoints", [])[:20]:
        normalized = refresh_endpoint_snapshot(
            client=client,
            endpoint=endpoint,
            now=now,
            warning_threshold_sec=warning_threshold_sec,
            block_threshold_sec=block_threshold_sec,
            logger=logger,
        )
        if normalized is not None:
            endpoints.append(normalized)
    return endpoints


def refresh_endpoint_snapshot(
    *,
    client: Any,
    endpoint: dict[str, Any],
    now: datetime,
    warning_threshold_sec: int,
    block_threshold_sec: int,
    logger: logging.Logger,
) -> dict[str, Any] | None:
    instance_id = endpoint.get("instance_id") or endpoint.get("job_id")
    if not instance_id:
        return None
    status = fetch_endpoint_status(
        client=client,
        instance_id=instance_id,
        endpoint=endpoint,
        logger=logger,
    )
    return normalize_endpoint_status(
        status=status,
        now=now,
        warning_threshold_sec=warning_threshold_sec,
        block_threshold_sec=block_threshold_sec,
    )


def fetch_endpoint_status(
    *,
    client: Any,
    instance_id: str,
    endpoint: dict[str, Any],
    logger: logging.Logger,
) -> dict[str, Any]:
    try:
        return client.get_endpoint_status(instance_id)
    except Exception as exc:
        logger.debug("Endpoint refresh failed for %s: %s", instance_id, exc)
        return endpoint


def normalize_endpoint_status(
    *,
    status: dict[str, Any],
    now: datetime,
    warning_threshold_sec: int,
    block_threshold_sec: int,
) -> dict[str, Any]:
    normalized = dict(status)
    normalized["remaining_seconds"] = seconds_until(normalized.get("expires_at"), now=now)
    normalized["runtime_seconds"] = runtime_seconds(normalized, now=now)
    runtime_value = normalized.get("runtime_seconds")
    if isinstance(runtime_value, int):
        runtime_hours = runtime_value / 3600
        normalized["runtime_hours"] = round(runtime_hours, 4)
        normalized["node_count"] = endpoint_node_count(normalized)
        normalized["node_hours_estimate"] = round(
            runtime_hours * normalized["node_count"],
            4,
        )
    normalized["expiry_state"] = classify_expiry_state(
        remaining_seconds=normalized.get("remaining_seconds"),
        warning_threshold_sec=warning_threshold_sec,
        block_threshold_sec=block_threshold_sec,
    )
    normalized["last_refreshed_at"] = now.isoformat()
    return normalized


def format_active_endpoints_prompt(
    endpoints: list[dict[str, Any]],
    *,
    format_duration: Callable[[int], str],
) -> str:
    if not endpoints:
        return ""

    lines = ["[Active endpoints]"]
    for endpoint in endpoints[:10]:
        line = (
            f"- {endpoint.get('instance_id', 'unknown')}: "
            f"model={endpoint.get('model', 'unknown')} "
            f"status={endpoint.get('status', 'unknown')} "
            f"backend={endpoint.get('backend', 'unknown')}"
        )
        remaining_seconds = endpoint.get("remaining_seconds")
        if isinstance(remaining_seconds, int):
            line += f" remaining={format_duration(remaining_seconds)}"
        runtime_hours = endpoint.get("runtime_hours")
        if isinstance(runtime_hours, (int, float)):
            line += f" runtime={runtime_hours:.2f}h"
        node_hours_estimate = endpoint.get("node_hours_estimate")
        if isinstance(node_hours_estimate, (int, float)):
            line += f" node_hours={node_hours_estimate:.2f}"
        expiry_state = endpoint.get("expiry_state")
        if isinstance(expiry_state, str) and expiry_state != "unknown":
            line += f" expiry={expiry_state}"
        lines.append(line)
    return "\n".join(lines)


def summarize_endpoint_usage(
    active_endpoints: list[dict[str, Any]],
    *,
    warning_threshold_sec: int,
    block_threshold_sec: int,
) -> dict[str, Any]:
    node_hours_estimated = round(
        sum(
            endpoint.get("node_hours_estimate", 0.0)
            for endpoint in active_endpoints
            if isinstance(endpoint.get("node_hours_estimate"), (int, float))
        ),
        4,
    )
    remaining_values = [
        endpoint.get("remaining_seconds")
        for endpoint in active_endpoints
        if isinstance(endpoint.get("remaining_seconds"), int)
    ]
    return {
        "node_hours_estimated": node_hours_estimated,
        "active_endpoint_count": len(active_endpoints),
        "nearest_expiry_seconds": min(remaining_values) if remaining_values else None,
        "warning_threshold_seconds": warning_threshold_sec,
        "block_threshold_seconds": block_threshold_sec,
    }


def runtime_seconds(endpoint: dict[str, Any], *, now: datetime) -> int | None:
    for key in ("ready_at", "started_at", "created_at", "last_used_at"):
        timestamp = _parse_iso(endpoint.get(key))
        if timestamp is not None:
            return max(0, int((now - timestamp).total_seconds()))
    return None


def endpoint_node_count(endpoint: dict[str, Any]) -> int:
    for key in ("node_count", "nodes", "num_nodes", "replicas"):
        value = endpoint.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 1


def classify_expiry_state(
    *,
    remaining_seconds: int | None,
    warning_threshold_sec: int,
    block_threshold_sec: int,
) -> str:
    if remaining_seconds is None:
        return "unknown"
    if remaining_seconds <= block_threshold_sec:
        return "critical"
    if remaining_seconds <= warning_threshold_sec:
        return "warning"
    return "healthy"


def seconds_until(value: str | None, *, now: datetime | None = None) -> int | None:
    expires_at = _parse_iso(value)
    if expires_at is None:
        return None
    current_time = now or datetime.now(timezone.utc)
    return max(0, int((expires_at - current_time).total_seconds()))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp
    except ValueError:
        return None
