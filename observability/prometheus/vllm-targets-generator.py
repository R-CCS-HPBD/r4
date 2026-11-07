#!/usr/bin/env python3
"""Generate Prometheus file_sd targets for ready vLLM endpoints.

Polls one or more UMRS HTTP servers for endpoints with status=ready or
degraded, then writes a Prometheus file service-discovery file.  Each target
uses the UMRS endpoint-metrics proxy path so Prometheus can scrape vLLM
metrics even when the endpoint is bound to loopback on the UMRS host.

Intended to run as a sidecar / cron job inside the observability stack.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from urllib.parse import urljoin

import urllib3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Environment-driven configuration
UMRS_URLS = os.environ.get(
    "VLLM_TARGETS_UMRS_URLS", "http://umrs:8000"
).split(",")
OUTPUT_FILE = os.environ.get(
    "VLLM_TARGETS_OUTPUT", "/etc/prometheus/vllm-targets.json"
)
HTTP_TIMEOUT = float(os.environ.get("VLLM_TARGETS_TIMEOUT", "10"))


def _http() -> urllib3.PoolManager:
    return urllib3.PoolManager(timeout=urllib3.Timeout(total=HTTP_TIMEOUT))


def fetch_ready_endpoints(umrs_url: str) -> list[dict[str, object]]:
    """Return ready/degraded endpoint dicts from a UMRS server."""
    base = umrs_url.rstrip("/") + "/"
    url = urljoin(base, "umrs/endpoints?status_filter=ready,degraded")
    try:
        resp = _http().request("GET", url)
    except urllib3.exceptions.HTTPError as exc:
        logger.warning("Could not reach %s: %s", umrs_url, exc)
        return []

    if resp.status != 200:
        logger.warning(
            "UMRS %s returned %s: %s", umrs_url, resp.status, resp.data[:200]
        )
        return []

    try:
        payload = json.loads(resp.data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Invalid JSON from %s: %s", umrs_url, exc)
        return []

    endpoints = payload.get("endpoints", []) if isinstance(payload, dict) else []
    return [ep for ep in endpoints if isinstance(ep, dict)]


def build_target_group(
    endpoint: dict[str, object],
    umrs_url: str,
    umrs_cluster: str,
) -> dict[str, object] | None:
    """Build one Prometheus file_sd target entry for an endpoint.

    Uses the UMRS proxy route ``/umrs/endpoints/{job_id}/metrics`` as the
    scrape path so Prometheus does not need direct network access to the
    loopback-bound endpoint port.
    """
    job_id = endpoint.get("job_id")
    if not job_id:
        return None

    model = endpoint.get("model") or "unknown"
    # Determine the scrape target address.  If the UMRS URL is loopback we
    # cannot use it from a container, so substitute the host-gateway alias.
    parsed = urllib3.util.parse_url(umrs_url)
    raw_host = parsed.host or "umrs"
    port = parsed.port or 8000
    scheme = parsed.scheme or "http"
    default_host = os.environ.get("VLLM_TARGETS_HOST", "host.containers.internal")
    host = default_host if raw_host in {"localhost", "127.0.0.1", "::1"} else raw_host

    target = f"{host}:{port}"
    metrics_path = f"/umrs/endpoints/{job_id}/metrics"

    labels: dict[str, str] = {
        "umrs_cluster": umrs_cluster,
        "endpoint_job_id": str(job_id),
        "model": str(model),
        "__scheme__": scheme,
        "__metrics_path__": metrics_path,
    }

    variant = endpoint.get("variant")
    if variant:
        labels["variant"] = str(variant)

    serving_backend = endpoint.get("serving_backend")
    if serving_backend:
        labels["serving_backend"] = str(serving_backend)

    return {"targets": [target], "labels": labels}


def generate_targets() -> list[dict[str, object]]:
    """Collect target groups from all configured UMRS servers."""
    targets: list[dict[str, object]] = []

    for idx, umrs_url in enumerate(UMRS_URLS):
        umrs_url = umrs_url.strip()
        if not umrs_url:
            continue
        cluster_label = os.environ.get(
            f"VLLM_TARGETS_UMRS_CLUSTER_{idx}",
            f"umrs-{idx}",
        )
        endpoints = fetch_ready_endpoints(umrs_url)
        logger.info(
            "Found %d ready/degraded endpoint(s) on %s (%s)",
            len(endpoints),
            umrs_url,
            cluster_label,
        )
        for endpoint in endpoints:
            group = build_target_group(endpoint, umrs_url, cluster_label)
            if group:
                targets.append(group)

    return targets


def write_targets(targets: list[dict[str, object]]) -> None:
    """Write targets atomically to OUTPUT_FILE."""
    output_dir = os.path.dirname(OUTPUT_FILE)
    if output_dir and not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    temp_file = OUTPUT_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(targets, f, indent=2)
        f.write("\n")
    os.replace(temp_file, OUTPUT_FILE)
    logger.info("Wrote %d target group(s) to %s", len(targets), OUTPUT_FILE)


def main() -> int:
    targets = generate_targets()
    write_targets(targets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
