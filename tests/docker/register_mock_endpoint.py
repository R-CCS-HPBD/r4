#!/usr/bin/env python3
"""
Register Mock Endpoint for E2E Testing

This script registers the mock LiteLLM endpoint with UMRS so that
Gateway can discover and route requests to it.

Run this after UMRS is healthy in E2E environment.
"""

import os
import sys
import time

import httpx

# Configuration
UMRS_URL = os.getenv("UMRS_URL", "http://localhost:8001")
LITELLM_HOST = os.getenv("LITELLM_HOST", "compute-mock")
LITELLM_PORT = int(os.getenv("LITELLM_PORT", "11434"))
MODEL_NAME = os.getenv("MODEL_NAME", "mock-model")
PROJECT_ID = os.getenv("PROJECT_ID", "test-project")
JOB_ID = os.getenv("JOB_ID", "mock-job-001")
RETRY_COUNT = int(os.getenv("RETRY_COUNT", "10"))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "2"))


def wait_for_umrs(url: str, timeout: int = 60) -> bool:
    """Wait for UMRS to be healthy."""
    print(f"Waiting for UMRS at {url}...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = httpx.get(f"{url}/health", timeout=5)
            if response.status_code == 200:
                print("  ✓ UMRS is healthy")
                return True
        except Exception:
            pass
        time.sleep(2)
    print("  ✗ UMRS not healthy after timeout")
    return False


def register_endpoint(
    umrs_url: str,
    project_id: str,
    model: str,
    job_id: str,
    host: str,
    port: int,
) -> bool:
    """Register an endpoint with UMRS."""
    endpoint_url = f"{umrs_url}/umrs/endpoints"

    payload = {
        "project_id": project_id,
        "model": model,
        "job_id": job_id,
        "host": host,
        "port": port,
        "backend": "mock",
        "ttl_sec": 86400,  # 24 hours for testing
    }

    print(f"Registering endpoint: {model} -> {host}:{port}")

    try:
        response = httpx.post(endpoint_url, json=payload, timeout=10)
        if response.status_code == 201:
            print("  ✓ Endpoint registered successfully")
            return True
        elif response.status_code == 409:
            print("  ⚠ Endpoint already registered (conflict)")
            return True
        else:
            print(f"  ✗ Registration failed: {response.status_code}")
            print(f"    Response: {response.text}")
            return False
    except Exception as e:
        print(f"  ✗ Error registering endpoint: {e}")
        return False


def mark_endpoint_ready(umrs_url: str, job_id: str) -> bool:
    """Mark endpoint as ready."""
    status_url = f"{umrs_url}/umrs/endpoints/{job_id}/status"

    payload = {"status": "ready"}

    print(f"Marking endpoint {job_id} as ready...")

    try:
        response = httpx.put(status_url, json=payload, timeout=10)
        if response.status_code == 200:
            print("  ✓ Endpoint marked as ready")
            return True
        else:
            print(f"  ✗ Status update failed: {response.status_code}")
            print(f"    Response: {response.text}")
            return False
    except Exception as e:
        print(f"  ✗ Error updating status: {e}")
        return False


def main():
    print("=" * 60)
    print("Mock Endpoint Registration for E2E Testing")
    print("=" * 60)
    print()
    print("Configuration:")
    print(f"  UMRS URL:      {UMRS_URL}")
    print(f"  LiteLLM Host:  {LITELLM_HOST}")
    print(f"  LiteLLM Port:  {LITELLM_PORT}")
    print(f"  Model:         {MODEL_NAME}")
    print(f"  Project ID:    {PROJECT_ID}")
    print(f"  Job ID:        {JOB_ID}")
    print()

    # Wait for UMRS
    if not wait_for_umrs(UMRS_URL):
        print("Failed to connect to UMRS")
        sys.exit(1)

    # Register endpoint
    for attempt in range(RETRY_COUNT):
        if register_endpoint(UMRS_URL, PROJECT_ID, MODEL_NAME, JOB_ID, LITELLM_HOST, LITELLM_PORT):
            break
        print(f"Retry {attempt + 1}/{RETRY_COUNT}...")
        time.sleep(RETRY_DELAY)
    else:
        print("Failed to register endpoint after retries")
        sys.exit(1)

    # Mark as ready
    if not mark_endpoint_ready(UMRS_URL, JOB_ID):
        print("Warning: Failed to mark endpoint as ready (may already be ready)")

    print()
    print("=" * 60)
    print("✓ Mock endpoint registration complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
