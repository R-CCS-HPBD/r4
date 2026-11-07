"""Serialization helpers for companion session metadata."""

from __future__ import annotations

from dataclasses import dataclass
from functools import singledispatch
from typing import Any

MAX_PERSISTED_USAGE_RECORDS = 500


@dataclass(frozen=True)
class RestoredSessionState:
    usage_summary_data: dict[str, Any]
    usage_record_data: list[dict[str, Any]]
    active_endpoints: list[dict[str, Any]]
    active_endpoints_updated_at: str | None


def dump_session_state(
    *,
    usage_summary_data: dict[str, Any],
    usage_record_data: list[dict[str, Any]],
    active_endpoints: list[dict[str, Any]],
    active_endpoints_updated_at: str | None,
) -> dict[str, Any]:
    return {
        "usage": {
            "summary": usage_summary_data,
            "records": usage_record_data[-MAX_PERSISTED_USAGE_RECORDS:],
        },
        "active_endpoints": [
            endpoint for endpoint in active_endpoints if isinstance(endpoint, dict)
        ],
        "active_endpoints_updated_at": active_endpoints_updated_at,
    }


def load_session_state(metadata: dict[str, Any]) -> RestoredSessionState:
    return RestoredSessionState(
        usage_summary_data=_load_usage_summary_data(metadata),
        usage_record_data=_load_usage_record_data(metadata),
        active_endpoints=_load_active_endpoints(metadata),
        active_endpoints_updated_at=_load_active_endpoints_updated_at(metadata),
    )


def _load_usage_summary_data(metadata: dict[str, Any]) -> dict[str, Any]:
    usage = metadata.get("usage")
    if not isinstance(usage, dict):
        return {}
    summary = usage.get("summary")
    if not isinstance(summary, dict):
        return {}
    return summary


def _load_usage_record_data(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    usage = metadata.get("usage")
    if not isinstance(usage, dict):
        return []
    records = usage.get("records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _load_active_endpoints(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    endpoints = metadata.get("active_endpoints")
    if not isinstance(endpoints, list):
        return []
    return [endpoint for endpoint in endpoints if isinstance(endpoint, dict)]


def _load_active_endpoints_updated_at(metadata: dict[str, Any]) -> str | None:
    updated_at = metadata.get("active_endpoints_updated_at")
    if not isinstance(updated_at, str):
        return None
    return updated_at


def json_safe(value: Any) -> Any:
    return _json_safe(value)


@singledispatch
def _json_safe(value: Any) -> Any:
    return str(value)


@_json_safe.register(type(None))
def _json_safe_none(value: None) -> None:
    return value


@_json_safe.register(str)
@_json_safe.register(int)
@_json_safe.register(float)
@_json_safe.register(bool)
def _json_safe_primitive(value: Any) -> Any:
    return value


@_json_safe.register(list)
def _json_safe_list(value: list[Any]) -> list[Any]:
    return [_json_safe(item) for item in value]


@_json_safe.register(tuple)
def _json_safe_tuple(value: tuple[Any, ...]) -> list[Any]:
    return [_json_safe(item) for item in value]


@_json_safe.register(dict)
def _json_safe_dict(value: dict[Any, Any]) -> dict[str, Any]:
    return {str(key): _json_safe(item) for key, item in value.items()}
