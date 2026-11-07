#!/usr/bin/env python3
"""Canonical HPC E2E validation through R4 -> Gateway -> UMRS.

This harness validates the public platform boundary on real HPC infrastructure:

1. Gateway health and auth from the client side
2. Endpoint provisioning through `Client.provision_endpoint()`
3. Endpoint readiness through `Client.get_endpoint_status()`
4. LLM access through Gateway `/gw/llm/*`
5. Durable job attempt inspection for stable Slurm authority

Unlike `tests/test_slurm_e2e.py`, this file avoids treating raw `sbatch`
submission as the primary platform contract.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from r4.client import Client
from r4.errors import R4Error

# Infrastructure guard (2026-09-14). Every test here talks to a live Gateway
# (defaults http://localhost:8000 / :8001) and an API key; with nothing
# listening they hang on connect/retry rather than failing fast, which is one
# of the three files that kept the repo-root belt out of CI. An API key is the
# honest signal that an operator prepared this environment: without one the
# suite cannot authenticate anyway.
if not (os.environ.get("R4_E2E_RCCS_API_KEY") or os.environ.get("R4_E2E_AI4S_API_KEY")):
    pytest.skip(
        "requires a reachable R4 Gateway plus credentials — set "
        "R4_E2E_RCCS_API_KEY (and/or R4_E2E_AI4S_API_KEY), with "
        "R4_E2E_<PROFILE>_GATEWAY_URL if not on the localhost defaults",
        allow_module_level=True,
    )


@dataclass
class GatewayHPCTestConfig:
    """Configuration for Gateway-backed HPC tests."""

    gateway_url: str
    api_key: str
    project_id: str
    model_name: str
    provision_timeout: int = 600
    poll_interval_sec: float = 5.0
    cluster: str = ""  # "rccs" | "ai4s" | "" (auto-detected from URL)


# Pre-defined cluster profiles.  Each profile maps a short name to a
# Gateway URL prefix + default model.  Users override via env vars when
# the built-in defaults don't match their deployment.
_CLUSTER_PROFILES: dict[str, dict[str, str]] = {
    "rccs": {
        "gateway_url": os.environ.get("R4_E2E_RCCS_GATEWAY_URL", "http://localhost:8000"),
        "api_key": os.environ.get("R4_E2E_RCCS_API_KEY", ""),
        "project_id": os.environ.get("R4_E2E_RCCS_PROJECT_ID", "rivault"),
        "model_name": os.environ.get(
            "R4_E2E_RCCS_MODEL", os.environ.get("SLURM_TEST_MODEL_NAME", "")
        ),
    },
    "ai4s": {
        "gateway_url": os.environ.get("R4_E2E_AI4S_GATEWAY_URL", "http://localhost:8001"),
        "api_key": os.environ.get("R4_E2E_AI4S_API_KEY", ""),
        "project_id": os.environ.get("R4_E2E_AI4S_PROJECT_ID", "rivault"),
        "model_name": os.environ.get(
            "R4_E2E_AI4S_MODEL", os.environ.get("SLURM_TEST_MODEL_NAME", "")
        ),
    },
}


def _get_required_env(name: str, *aliases: str) -> str | None:
    for candidate in (name, *aliases):
        value = os.environ.get(candidate)
        if value:
            return value
    return None


def _gateway_headers(config: GatewayHPCTestConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.api_key}",
        "X-Project-ID": config.project_id,
    }


def _build_client(config: GatewayHPCTestConfig) -> Client:
    return Client(
        gateway_url=config.gateway_url,
        api_key=config.api_key,
        project_id=config.project_id,
    )


def _list_models_via_gateway(config: GatewayHPCTestConfig) -> list[dict[str, Any]]:
    response = httpx.get(
        f"{config.gateway_url}/gw/llm/v1/models",
        headers=_gateway_headers(config),
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def _wait_for_endpoint_ready(
    client: Client,
    instance_id: str,
    *,
    timeout_sec: int,
    poll_interval_sec: float,
) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    latest_status: dict[str, Any] | None = None

    while time.time() < deadline:
        latest_status = client.get_endpoint_status(instance_id)
        status = latest_status.get("status")
        if status == "ready":
            return latest_status
        if status in {"failed", "cancelled", "deprovisioned"}:
            raise AssertionError(f"Endpoint {instance_id} failed before readiness: {latest_status}")
        time.sleep(poll_interval_sec)

    raise TimeoutError(
        f"Endpoint {instance_id} did not become ready within {timeout_sec}s; "
        f"last status={latest_status}"
    )


def _provision_endpoint(
    client: Client,
    config: GatewayHPCTestConfig,
) -> tuple[str, bool]:
    existing_endpoint_ids = {
        endpoint.get("instance_id")
        for endpoint in client.list_endpoints().get("endpoints", [])
        if endpoint.get("model") == config.model_name and endpoint.get("instance_id")
    }
    response = client.provision_endpoint(
        config.model_name,
        wait=False,
        timeout=min(config.provision_timeout, 300),
    )
    instance_id = response.get("instance_id") or response.get("job_id")
    assert instance_id, f"Provision response did not include an instance_id: {response}"
    reused_existing_endpoint = instance_id in existing_endpoint_ids
    return instance_id, reused_existing_endpoint


def _cleanup_endpoint(client: Client, instance_id: str, reused_existing_endpoint: bool) -> None:
    if reused_existing_endpoint:
        return

    response = client.stop_endpoint(instance_id)
    assert response.get("status") in {
        "stopped",
        "deprovisioned",
        "deprovisioning",
        "cancelled",
    }, response


def _wait_for_endpoint_stopped(
    client: Client,
    instance_id: str,
    *,
    timeout_sec: int,
    poll_interval_sec: float,
) -> None:
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        endpoints = client.list_endpoints(include_stopped=True).get("endpoints", [])
        endpoint = next(
            (item for item in endpoints if item.get("instance_id") == instance_id),
            None,
        )
        if endpoint is None:
            return
        if endpoint.get("status") in {"deprovisioned", "stopped", "cancelled", "failed"}:
            return
        time.sleep(poll_interval_sec)

    raise TimeoutError(
        f"Endpoint {instance_id} did not reach a terminal state within {timeout_sec}s"
    )


def _wait_for_endpoint_starting(
    client: Client,
    instance_id: str,
    *,
    timeout_sec: int,
    poll_interval_sec: float,
) -> None:
    """Poll until an endpoint is at least STARTING, skipping if it never gets there."""
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        status_resp = client.get_endpoint_status(instance_id)
        status = status_resp.get("status", "").lower()
        if status in {"starting", "running", "ready"}:
            return
        if status in {"failed", "cancelled", "deprovisioned"}:
            pytest.skip(f"Endpoint reached {status} before STARTING; cannot test kill-mid-startup")
        time.sleep(poll_interval_sec)

    pytest.skip("Endpoint did not reach STARTING within timeout; cannot test kill-mid-startup")


def _profiles_to_run(profile: str) -> list[str]:
    """Return the cluster profiles selected by ``R4_E2E_PROFILE``."""
    if profile == "all":
        return list(_CLUSTER_PROFILES.keys())
    if profile in _CLUSTER_PROFILES:
        return [profile]
    pytest.skip(f"Unknown R4_E2E_PROFILE={profile!r}; use rccs, ai4s, or all")


def _resolve_cluster_values(cfg: dict[str, str]) -> dict[str, str | None]:
    """Resolve a cluster profile's settings, falling back to the legacy env vars."""
    gateway_url = cfg["gateway_url"] or _get_required_env("R4_GATEWAY_URL", "GATEWAY_URL")
    api_key = cfg["api_key"] or _get_required_env("R4_API_KEY", "GATEWAY_BEARER_TOKEN")
    project_id = cfg["project_id"] or _get_required_env("R4_PROJECT_ID", "GATEWAY_PROJECT_ID")
    model_name = cfg["model_name"] or _get_required_env("SLURM_TEST_MODEL_NAME", "R4_MODEL")

    # Legacy fallback: if per-cluster model is empty but legacy env is set
    if not model_name:
        model_name = _get_required_env("SLURM_TEST_MODEL_NAME", "R4_MODEL")

    return {
        "gateway_url": gateway_url,
        "api_key": api_key,
        "project_id": project_id,
        "model_name": model_name,
    }


def _missing_cluster_values(cluster_name: str, values: dict[str, str | None]) -> list[str]:
    """List the ``cluster.field`` labels of every unset value in a resolved profile."""
    return [f"{cluster_name}.{field}" for field, value in values.items() if not value]


@pytest.fixture(params=["rccs", "ai4s"])
def gateway_hpc_config(request) -> GatewayHPCTestConfig:
    """Parameterized fixture: runs each test against RCCS and AI4S clusters.

    Selects config from environment variables:

    - ``R4_E2E_PROFILE=rccs|ai4s`` — single-cluster mode (only that profile runs)
    - ``R4_E2E_PROFILE=all`` — run both (default)
    - Per-cluster env: ``R4_E2E_<PROFILE>_GATEWAY_URL``, ``R4_E2E_<PROFILE>_API_KEY``,
      ``R4_E2E_<PROFILE>_PROJECT_ID``, ``R4_E2E_<PROFILE>_MODEL``

    Falls back to legacy ``R4_GATEWAY_URL`` / ``SLURM_TEST_MODEL_NAME`` env
    vars when per-cluster vars are absent.  A cluster whose gateway URL is
    unreachable is skipped *per-test* (not skipped at fixture level) so that
    partial runs still report on the available cluster.
    """
    profile = os.environ.get("R4_E2E_PROFILE", "all").lower()
    profiles_to_run = _profiles_to_run(profile)

    cluster_name = request.param  # "rccs" or "ai4s"

    if cluster_name not in profiles_to_run:
        pytest.skip(f"Profile {cluster_name!r} excluded by R4_E2E_PROFILE={profile!r}")

    values = _resolve_cluster_values(_CLUSTER_PROFILES[cluster_name])

    missing = _missing_cluster_values(cluster_name, values)
    if missing:
        pytest.skip(
            f"Cluster {cluster_name!r} missing config: {', '.join(missing)}. "
            "Set R4_E2E_<PROFILE>_* env vars or "
            "R4_GATEWAY_URL/R4_API_KEY/R4_PROJECT_ID/SLURM_TEST_MODEL_NAME"
        )

    timeout = int(os.environ.get("HPC_TEST_PROVISION_TIMEOUT", "600"))
    poll = float(os.environ.get("HPC_TEST_POLL_INTERVAL_SEC", "5"))

    return GatewayHPCTestConfig(
        gateway_url=values["gateway_url"],
        api_key=values["api_key"],
        project_id=values["project_id"],
        model_name=values["model_name"],
        provision_timeout=timeout,
        poll_interval_sec=poll,
        cluster=cluster_name,
    )


def _is_cluster_reachable(gateway_url: str, timeout: int = 5) -> bool:
    """Return True if the gateway URL responds to an HTTP check."""
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(gateway_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 80
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


@pytest.mark.hpc
class TestGatewayHPCE2E:
    """Gateway-backed HPC integration tests."""

    @pytest.mark.slow
    def test_gateway_provision_chat_and_teardown(self, gateway_hpc_config: GatewayHPCTestConfig):
        client = _build_client(gateway_hpc_config)
        assert client.health(), "Gateway is not ready for HPC testing"

        instance_id: str | None = None
        reused_existing_endpoint = False
        try:
            instance_id, reused_existing_endpoint = _provision_endpoint(client, gateway_hpc_config)
            endpoint = _wait_for_endpoint_ready(
                client,
                instance_id,
                timeout_sec=gateway_hpc_config.provision_timeout,
                poll_interval_sec=gateway_hpc_config.poll_interval_sec,
            )

            models = _list_models_via_gateway(gateway_hpc_config)
            assert models, "Gateway /gw/llm/v1/models returned no models"
            assert endpoint.get("status") == "ready"

            response = client.chat(
                "Reply with exactly three words: gateway hpc ready.",
                model=gateway_hpc_config.model_name,
                temperature=0.0,
            )
            assert response.content.strip()
        finally:
            if instance_id:
                _cleanup_endpoint(client, instance_id, reused_existing_endpoint)

    @pytest.mark.slow
    def test_endpoint_attempts_preserve_slurm_backend(
        self, gateway_hpc_config: GatewayHPCTestConfig
    ):
        """Verify that the provisioned endpoint reports a Slurm-family backend.

        The REST provision path uses the direct orchestration flow (not the
        durable-job scheduler), so backend verification is done via the
        endpoint registry's ``backend`` field rather than durable job
        attempts.
        """
        client = _build_client(gateway_hpc_config)
        assert client.health(), "Gateway is not ready for HPC testing"

        instance_id: str | None = None
        reused_existing_endpoint = False
        try:
            instance_id, reused_existing_endpoint = _provision_endpoint(client, gateway_hpc_config)
            endpoint = _wait_for_endpoint_ready(
                client,
                instance_id,
                timeout_sec=gateway_hpc_config.provision_timeout,
                poll_interval_sec=gateway_hpc_config.poll_interval_sec,
            )

            backend = (endpoint.get("backend") or "").lower()
            assert backend, "Endpoint response missing 'backend' field"
            assert backend in {"slurm", "slurm-podman"}, f"Expected slurm backend, got '{backend}'"
        except R4Error as exc:
            pytest.fail(f"Failed to inspect endpoint backend metadata: {exc}")
        finally:
            if instance_id:
                _cleanup_endpoint(client, instance_id, reused_existing_endpoint)

    @pytest.mark.slow
    def test_reprovision_after_teardown_remains_slurm_backed(
        self,
        gateway_hpc_config: GatewayHPCTestConfig,
    ):
        client = _build_client(gateway_hpc_config)
        assert client.health(), "Gateway is not ready for HPC testing"

        first_instance_id: str | None = None
        second_instance_id: str | None = None
        reused_existing_endpoint = False
        reused_second_endpoint = False
        try:
            first_instance_id, reused_existing_endpoint = _provision_endpoint(
                client,
                gateway_hpc_config,
            )
            _wait_for_endpoint_ready(
                client,
                first_instance_id,
                timeout_sec=gateway_hpc_config.provision_timeout,
                poll_interval_sec=gateway_hpc_config.poll_interval_sec,
            )

            if reused_existing_endpoint:
                pytest.skip(
                    "Initial provision reused an existing endpoint; "
                    "skipping teardown/reprovision cycle"
                )

            _cleanup_endpoint(client, first_instance_id, reused_existing_endpoint)
            _wait_for_endpoint_stopped(
                client,
                first_instance_id,
                timeout_sec=gateway_hpc_config.provision_timeout,
                poll_interval_sec=gateway_hpc_config.poll_interval_sec,
            )
            first_instance_id = None

            second_instance_id, reused_second_endpoint = _provision_endpoint(
                client,
                gateway_hpc_config,
            )
            _wait_for_endpoint_ready(
                client,
                second_instance_id,
                timeout_sec=gateway_hpc_config.provision_timeout,
                poll_interval_sec=gateway_hpc_config.poll_interval_sec,
            )

            second_endpoint = client.get_endpoint_status(second_instance_id)
            backend = (second_endpoint.get("backend") or "").lower()
            assert backend, "Reprovisioned endpoint response missing 'backend' field"
            assert backend in {"slurm", "slurm-podman"}, (
                f"Expected slurm backend on reprovisioned endpoint, got '{backend}'"
            )
        finally:
            if first_instance_id:
                _cleanup_endpoint(client, first_instance_id, reused_existing_endpoint)
            if second_instance_id:
                _cleanup_endpoint(client, second_instance_id, reused_second_endpoint)

    @pytest.mark.slow
    def test_kill_mid_startup_no_port_leak(
        self,
        gateway_hpc_config: GatewayHPCTestConfig,
    ):
        """R-18 (Phase 2k): kill an endpoint mid-startup and verify the retry
        path does not double-allocate the port or leave a ghost compose stack.

        Steps:
            1. Provision an endpoint (model X).
            2. Poll until status is STARTING (pod running, not yet READY).
            3. Stop/cancel the endpoint immediately.
            4. Wait for the endpoint to reach a terminal state.
            5. Re-provision the same model X.
            6. Wait for the new endpoint to reach READY.
            7. Verify chat works and no port collision occurred.
        """
        client = _build_client(gateway_hpc_config)
        assert client.health(), "Gateway is not ready for HPC testing"

        first_id: str | None = None
        second_id: str | None = None
        reused_first = False
        reused_second = False
        try:
            # Step 1: provision
            first_id, reused_first = _provision_endpoint(client, gateway_hpc_config)

            if reused_first:
                pytest.skip("Provision reused existing endpoint; can't test kill-mid-startup")

            # Step 2: wait for STARTING (not READY)
            _wait_for_endpoint_starting(
                client,
                first_id,
                timeout_sec=gateway_hpc_config.provision_timeout,
                poll_interval_sec=gateway_hpc_config.poll_interval_sec,
            )

            # Step 3: kill immediately
            _cleanup_endpoint(client, first_id, reused_first)

            # Step 4: wait for terminal state
            _wait_for_endpoint_stopped(
                client,
                first_id,
                timeout_sec=gateway_hpc_config.provision_timeout,
                poll_interval_sec=gateway_hpc_config.poll_interval_sec,
            )
            first_id = None  # cleaned up

            # Step 5: re-provision
            second_id, reused_second = _provision_endpoint(client, gateway_hpc_config)

            # Step 6: wait for READY
            endpoint = _wait_for_endpoint_ready(
                client,
                second_id,
                timeout_sec=gateway_hpc_config.provision_timeout,
                poll_interval_sec=gateway_hpc_config.poll_interval_sec,
            )
            assert endpoint.get("status") == "ready"

            # Step 7: verify chat works (proves no port collision)
            response = client.chat(
                "Reply with exactly: retry ok",
                model=gateway_hpc_config.model_name,
                temperature=0.0,
            )
            assert response.content.strip(), "Chat after retry returned empty content"

        finally:
            if first_id:
                _cleanup_endpoint(client, first_id, reused_first)
            if second_id:
                _cleanup_endpoint(client, second_id, reused_second)
