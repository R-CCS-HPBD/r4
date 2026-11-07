"""JSON helpers for urllib-based provider HTTP calls."""

from __future__ import annotations

import json
from typing import Any

from ..errors import APIError


def read_json_response(resp: Any, *, label: str = "LLM endpoint") -> dict[str, Any]:
    """Parse a urllib (or similar) response body as JSON.

    Raises :class:`APIError` with actionable text when the body is empty or
    not valid JSON — common when a gateway tunnel is stale or upstream vLLM
    is down but the HTTP status is still 200.
    """
    raw = resp.read()
    status = getattr(resp, "status", None) or getattr(resp, "status_code", None)
    status_suffix = f" (HTTP {status})" if status else ""

    if not raw:
        raise APIError(
            f"Empty response from {label}{status_suffix} — "
            "the gateway tunnel or upstream endpoint may be unavailable",
        )

    text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = text[:300].strip()
        raise APIError(
            f"Invalid JSON from {label}{status_suffix}: {exc}",
            response_body=snippet or text[:300],
        ) from exc

    if not isinstance(parsed, dict):
        raise APIError(
            f"Unexpected JSON type from {label}{status_suffix} "
            f"(expected object, got {type(parsed).__name__})",
            response_body=text[:300],
        )
    return parsed
