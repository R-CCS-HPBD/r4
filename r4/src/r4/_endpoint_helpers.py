"""Helpers for normalizing UMRS endpoint API responses.

Extracted from :mod:`r4.client` to keep that module under the project's
module-size lint budget. The helpers are pure functions — no network
access, no dependencies on :class:`r4.client.Client`.
"""

from __future__ import annotations

from typing import Any


def normalize_endpoint_record(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a UMRS endpoint payload into the CLI's expected shape."""
    endpoint_url = data.get("service_url") or data.get("litellm_url")
    return {
        "instance_id": data.get("job_id"),
        "job_id": data.get("job_id"),
        "project_id": data.get("project_id"),
        "model": data.get("model"),
        "served_model_name": data.get("served_model_name"),
        "status": data.get("status"),
        "endpoint_url": endpoint_url,
        "service_url": data.get("service_url"),
        "service_kind": data.get("service_kind"),
        "serving_backend": data.get("serving_backend"),
        "model_type": data.get("model_type"),
        "invoke_base_path": data.get("invoke_base_path"),
        "readiness_probe": data.get("readiness_probe"),
        "readiness_path": data.get("readiness_path"),
        "launch_profiles": data.get("launch_profiles") or [],
        "backend": data.get("backend"),
        "host": data.get("host"),
        "port": data.get("port"),
        # MM-4-MA / r4 map: surface security-relevant context.
        "owner_type": data.get("owner_type"),
        "owner_id": data.get("owner_id"),
        "tier": data.get("tier") or data.get("owner_type"),
        "egress_domains": data.get("egress_domains") or [],
        "mounts": data.get("mounts") or {},
        "mcp_servers": data.get("mcp_servers") or [],
        "created_at": data.get("created_at"),
        "ready_at": data.get("ready_at"),
        "last_used_at": data.get("last_used_at"),
        "expires_at": data.get("expires_at"),
        "error": data.get("error_message"),
        "message": data.get("message"),
        "detail": data.get("detail") or data.get("status_detail"),
        "job_status": data.get("job_status"),
        "active_attempt_status": data.get("active_attempt_status"),
        "active_attempt_status_detail": data.get("active_attempt_status_detail"),
        "active_attempt_backend": data.get("active_attempt_backend"),
        "active_attempt_no": data.get("active_attempt_no"),
        "variant": data.get("variant"),
    }


def normalize_endpoint_provision_response(
    requested_model: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Normalize a UMRS provision response into the CLI's expected shape."""
    endpoint = data.get("endpoint")
    normalized = {
        "instance_id": data.get("job_id"),
        "job_id": data.get("job_id"),
        "model": requested_model,
        "status": data.get("status", "unknown"),
        "retry_after": data.get("retry_after"),
        "error": data.get("error"),
        "endpoint_url": None,
    }
    if endpoint:
        normalized.update(normalize_endpoint_record(endpoint))
    return normalized


def build_endpoint_log_summary(job: dict[str, Any]) -> dict[str, Any]:
    """Build a human-readable endpoint log summary from UMRS job attempts."""
    lines = [
        f"job {job.get('job_id', 'unknown')}: status={job.get('status', 'unknown')}"
        f" type={job.get('job_type', 'unknown')}"
    ]
    for attempt in job.get("attempts", []):
        lines.append(
            f"attempt #{attempt.get('attempt_no', '?')}: "
            f"status={attempt.get('status', 'unknown')} "
            f"backend={attempt.get('backend', 'unknown')}"
        )
        for label in ("status_detail", "exit_reason", "log_uri"):
            if attempt.get(label):
                lines.append(f"  {label}: {attempt[label]}")

    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "logs": lines,
        "attempts": job.get("attempts", []),
    }


__all__ = [
    "build_endpoint_log_summary",
    "normalize_endpoint_provision_response",
    "normalize_endpoint_record",
]
