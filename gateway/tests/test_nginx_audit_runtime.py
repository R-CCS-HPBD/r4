"""Runtime checks for the Gateway NGINX image: audit log, health probes."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")


REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_CONTEXT = REPO_ROOT / "gateway" / "nginx"


def _command_available(command: list[str]) -> bool:
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _container_runtime() -> str | None:
    requested = os.getenv("R4_CONTAINER_RUNTIME") or os.getenv("CONTAINER_RUNTIME")
    if requested:
        return requested if _command_available([requested, "info"]) else None

    for runtime in ("podman", "docker"):
        if _command_available([runtime, "info"]):
            return runtime
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def nginx_image():
    runtime = _container_runtime()
    if runtime is None:
        pytest.skip(
            "No healthy Podman or Docker runtime is available for Gateway NGINX runtime test"
        )

    image_tag = f"r4-gateway-nginx-audit-test:{os.getpid()}"
    subprocess.run(
        [runtime, "build", "-t", image_tag, str(NGINX_CONTEXT)],
        cwd=REPO_ROOT,
        check=True,
    )
    try:
        yield runtime, image_tag
    finally:
        subprocess.run([runtime, "image", "rm", "-f", image_tag], cwd=REPO_ROOT, check=False)


def _upstream_env(umrs_port: int) -> list[str]:
    """`-e` pairs for the upstreams; only UMRS_PORT varies between fixtures."""
    env = {
        "LITELLM_HOST": "127.0.0.1",
        "LITELLM_PORT": "11434",
        "UMRS_HOST": "127.0.0.1",
        "UMRS_PORT": str(umrs_port),
        "A2L_HOST": "127.0.0.1",
        "A2L_PORT": "8002",
        "SIDECAR_HOST": "127.0.0.1",
        "SIDECAR_PORT": "8081",
    }
    return [arg for key, value in env.items() for arg in ("-e", f"{key}={value}")]


def _gateway_container(image: tuple[str, str], suffix: str, umrs_port: int):
    """Run the gateway image; yield (runtime, container name, service URL)."""
    runtime, image_tag = image
    container_name = f"r4-nginx-{suffix}-test-{os.getpid()}"
    host_port = _free_port()
    subprocess.run(
        [runtime, "run", "-d", "--name", container_name, "-p", f"127.0.0.1:{host_port}:8080"]
        + _upstream_env(umrs_port)
        + [image_tag],
        cwd=REPO_ROOT,
        check=True,
    )
    try:
        service_url = f"http://127.0.0.1:{host_port}"
        _wait_for_health(service_url)
        yield runtime, container_name, service_url
    finally:
        subprocess.run([runtime, "rm", "-f", container_name], cwd=REPO_ROOT, check=False)


@pytest.fixture(scope="module")
def nginx_container(nginx_image):
    """Nothing listens on 127.0.0.1:8082 in the container: the UMRS backend is down."""
    yield from _gateway_container(nginx_image, "audit", 8082)


@pytest.fixture(scope="module")
def nginx_container_backend_up(nginx_image):
    """UMRS upstream = the gateway's own :8080, whose /health answers 200: backend up."""
    yield from _gateway_container(nginx_image, "backend-up", 8080)


def _wait_for_health(service_url: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{service_url}/health", timeout=2)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    pytest.fail("Gateway NGINX test container did not become healthy")


def _audit_log(runtime: str, container_name: str) -> str:
    result = subprocess.run(
        [runtime, "exec", container_name, "cat", "/var/log/nginx/audit.json.log"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.mark.integration
@pytest.mark.e2e
def test_presigned_signature_redacted_in_runtime_audit_log(nginx_container):
    runtime, container_name, service_url = nginx_container
    query = "&".join(
        [
            "X-Amz-Signature=secret-amz",
            "X-Goog-Signature=secret-goog",
            "Signature=secret-signature",
            "X-Amz-Security-Token=secret-token",
            "sig=secret-short",
            "safe=visible",
        ]
    )

    response = httpx.get(f"{service_url}/health?{query}", timeout=5)
    assert response.status_code == 200

    log_text = _audit_log(runtime, container_name)
    audit_rows = [json.loads(line) for line in log_text.splitlines() if line.strip()]

    assert any(row["path"] == "/health" and row["query"] == "<redacted>" for row in audit_rows)
    for secret_value in (
        "secret-amz",
        "secret-goog",
        "secret-signature",
        "secret-token",
        "secret-short",
    ):
        assert secret_value not in log_text


# --- backend-aware health (issue #634, 2026-10-30) ---------------------------
# /health is the gateway's own liveness and stays 200 with the backend down;
# /health/backend is 200 only when UMRS /health answers. Before the fix
# /health/backend fell into the `location /health` prefix and answered 200
# with no backend at all.


@pytest.mark.integration
@pytest.mark.e2e
def test_health_backend_fails_closed_when_umrs_is_down(nginx_container):
    _, _, service_url = nginx_container

    assert httpx.get(f"{service_url}/health", timeout=5).status_code == 200
    response = httpx.get(f"{service_url}/health/backend", timeout=10)

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["service"] == "backend"


@pytest.mark.integration
@pytest.mark.e2e
def test_health_backend_is_200_when_umrs_answers(nginx_container_backend_up):
    _, _, service_url = nginx_container_backend_up

    response = httpx.get(f"{service_url}/health/backend", timeout=10)

    assert response.status_code == 200
    body = response.json()
    # The "backend" here is the gateway's own /health ("service": "gateway"):
    # the fixed answer proves the backend's body is not relayed.
    assert (body["status"], body["service"]) == ("healthy", "backend")


@pytest.mark.integration
@pytest.mark.e2e
def test_health_backend_is_rate_limited_with_the_json_429(nginx_container):
    """Review 2026-10-30: the anonymous /health/backend costs UMRS a DB round
    trip per hit, so it has its own limit_req zone (5 r/s, burst 10). The
    location-level error_page replaces the server-level one, so the 429 must
    still be the JSON @error429 answer, not nginx's default HTML page.
    Kept last in this module: it drains the bucket of the shared container."""
    _, _, service_url = nginx_container

    with httpx.Client(timeout=10) as client:
        responses = [client.get(f"{service_url}/health/backend") for _ in range(30)]

    codes = [r.status_code for r in responses]
    assert set(codes) == {429, 503}, codes  # the backend is down: 503 until the burst is spent
    limited = responses[codes.index(429)]
    assert (limited.json()["error"], limited.headers["Retry-After"]) == ("Too Many Requests", "1")
