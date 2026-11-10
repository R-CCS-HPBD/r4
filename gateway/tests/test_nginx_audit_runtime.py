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


def _gateway_container(
    image: tuple[str, str], suffix: str, umrs_port: int, app_conf: Path | None = None
):
    """Run the gateway image; yield (runtime, container name, service URL).

    ``app_conf`` is copied to the server-scoped ``/etc/nginx/app.d/`` before
    nginx starts (the include the deployment roles use), e.g. a stub UMRS
    route served by the gateway itself."""
    runtime, image_tag = image
    container_name = f"r4-nginx-{suffix}-test-{os.getpid()}"
    host_port = _free_port()
    subprocess.run(
        [runtime, "create", "--name", container_name, "-p", f"127.0.0.1:{host_port}:8080"]
        + _upstream_env(umrs_port)
        + [image_tag],
        cwd=REPO_ROOT,
        check=True,
    )
    if app_conf is not None:
        target = f"{container_name}:/etc/nginx/app.d/{app_conf.name}"
        subprocess.run([runtime, "cp", str(app_conf), target], cwd=REPO_ROOT, check=True)
    subprocess.run([runtime, "start", container_name], cwd=REPO_ROOT, check=True)
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


@pytest.fixture
def nginx_container_fresh(nginx_image):
    """A fresh backend-down gateway per test: its rate-limit buckets are full
    whatever ran before (no order dependence on the module containers)."""
    yield from _gateway_container(nginx_image, "fresh", 8082)


@pytest.fixture(scope="module")
def nginx_container_backend_up(nginx_image):
    """UMRS upstream = the gateway's own :8080, whose /health answers 200: backend up."""
    yield from _gateway_container(nginx_image, "backend-up", 8080)


# A stub of UMRS GET /umrs/status/service-badge, served by the gateway itself
# (UMRS upstream = its own :8080). Its body echoes what reached "UMRS": the
# stub's own $request_id (one per upstream hit, so a cached answer repeats
# it), the client's credentials, query string and method, and the gateway's
# X-Request-ID. Its Cache-Control and Set-Cookie must not reach the client.
_BADGE_STUB_CONF = """
location = /umrs/status/service-badge {
    default_type application/json;
    add_header Cache-Control "max-age=999" always;
    add_header Set-Cookie "stub=1" always;
    return 200 '{"schemaVersion":1,"label":"models","message":"6/6 up","color":"brightgreen",\
"cacheSeconds":300,"hit":"$request_id","rid":"$http_x_request_id",\
"seen":"$http_authorization|$http_cookie|$http_x_user_id|$http_x_verified_hmac|$args|$request_method"}';
}
"""


@pytest.fixture(scope="module")
def nginx_container_badge_stub(nginx_image, tmp_path_factory):
    """UMRS upstream = the gateway's own :8080, with the badge route stubbed."""
    conf = tmp_path_factory.mktemp("badge-stub") / "badge-stub.conf"
    conf.write_text(_BADGE_STUB_CONF)
    yield from _gateway_container(nginx_image, "badge-stub", 8080, conf)


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
def test_edge_flood_does_not_spend_the_direct_probe_bucket(nginx_container):
    """Review 2026-11-07, item 1: behind Apache every public request carries
    X-Forwarded-For and shares one bucket per gateway. The converge's
    "Platform serving" probe calls the gateway directly (no X-Forwarded-For)
    and must keep its own bucket. Before the fix the zone key was
    $server_port alone: after this flood the direct probe answered 429 and
    the converge verdict went red with the backend healthy."""
    _, _, service_url = nginx_container
    edge = {"X-Forwarded-For": "203.0.113.7"}

    with httpx.Client(timeout=10) as client:
        flood = [client.get(f"{service_url}/health/backend", headers=edge) for _ in range(30)]
        direct = client.get(f"{service_url}/health/backend")

    assert 429 in [r.status_code for r in flood]
    assert direct.status_code == 503  # the backend is down; not rate-limited


# --- public service-model badge (ADR-106 amendment 2026-11-07) -------------


def _fixed_badge(message: str, color: str) -> dict[str, object]:
    """The gateway's own badge answers (no cacheSeconds: never cached)."""
    return {"schemaVersion": 1, "label": "models", "message": message, "color": color}


@pytest.mark.integration
@pytest.mark.e2e
def test_status_badge_relays_umrs_answer_without_client_input(nginx_container_badge_stub):
    _, _, service_url = nginx_container_badge_stub
    client_headers = {
        "Authorization": "Bearer client-token",
        "Cookie": "r4_jwt=client-cookie",
        "X-User-ID": "mallory",
        "X-Verified-HMAC": "forged",
        "X-Request-ID": "client-chosen-id",
    }

    response = httpx.get(
        f"{service_url}/status/models.json?bust=1", headers=client_headers, timeout=10
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["message"], body["color"], body["cacheSeconds"]) == ("6/6 up", "brightgreen", 300)
    assert body["seen"] == "|||||GET"  # no credential, header or query string reached UMRS
    assert body["rid"] and body["rid"] != "client-chosen-id"
    assert response.headers["Cache-Control"] == "max-age=300"
    assert "set-cookie" not in response.headers


@pytest.mark.integration
@pytest.mark.e2e
def test_status_badge_is_micro_cached_under_one_key(nginx_container_badge_stub):
    """Two requests within 30 s cost UMRS one hit, whatever the query string."""
    _, _, service_url = nginx_container_badge_stub

    with httpx.Client(timeout=10) as client:
        first = client.get(f"{service_url}/status/models.json?a=1").json()
        second = client.get(f"{service_url}/status/models.json?b=2").json()

    assert first["hit"] == second["hit"]


@pytest.mark.integration
@pytest.mark.e2e
def test_status_badge_serves_get_and_head_only(nginx_container_badge_stub):
    _, _, service_url = nginx_container_badge_stub

    assert httpx.head(f"{service_url}/status/models.json", timeout=10).status_code == 200
    response = httpx.post(f"{service_url}/status/models.json", content=b"x", timeout=10)

    assert response.status_code == 403
    assert "schemaVersion" not in response.text


@pytest.mark.integration
@pytest.mark.e2e
def test_status_badge_rejects_large_get_bodies_before_proxying(nginx_container_badge_stub):
    """A large anonymous GET must not inherit the server's unlimited body size."""
    _, _, service_url = nginx_container_badge_stub
    response = httpx.request(
        "GET", f"{service_url}/status/models.json", content=b"x" * (256 * 1024), timeout=10
    )
    assert response.status_code == 200
    assert response.json() == _fixed_badge("unknown", "lightgrey")
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.integration
@pytest.mark.e2e
def test_status_badge_without_the_umrs_route_is_unknown(nginx_container_backend_up):
    """An upstream 4xx (here: a UMRS without the route, 404) is replaced by a
    fixed grey "unknown"; the upstream body is never relayed."""
    _, _, service_url = nginx_container_backend_up

    response = httpx.get(f"{service_url}/status/models.json", timeout=10)

    assert response.status_code == 200
    assert response.json() == _fixed_badge("unknown", "lightgrey")
    assert response.headers["Cache-Control"] == "no-store"


# A UMRS error answer whose body carries a per-request id (``leak-…``).
_BADGE_ERROR_STUB_CONF = """
location = /umrs/status/service-badge {
    default_type application/json;
    return STATUS '{"detail":"x","request_id":"leak-$request_id"}';
}
"""


@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.parametrize(
    ("status", "message", "color"),
    [(415, "unknown", "lightgrey"), (505, "down", "red")],
)
def test_status_badge_never_relays_an_unlisted_upstream_error(
    nginx_image, tmp_path, status, message, color
):
    """Review 2026-11-07, item 3: before every code was mapped, a UMRS 415 or
    505 (no error_page) passed its body, request id included, straight to
    the anonymous caller."""
    conf = tmp_path / "badge-error.conf"
    conf.write_text(_BADGE_ERROR_STUB_CONF.replace("STATUS", str(status)))
    gateway = _gateway_container(nginx_image, f"badge-{status}", 8080, conf)
    _, _, service_url = next(gateway)
    try:
        response = httpx.get(f"{service_url}/status/models.json", timeout=10)
    finally:
        gateway.close()

    assert response.status_code == 200
    assert response.json() == _fixed_badge(message, color)
    assert "leak-" not in response.text


@pytest.mark.integration
@pytest.mark.e2e
def test_status_badge_is_red_when_umrs_is_down(nginx_container):
    _, _, service_url = nginx_container

    response = httpx.get(f"{service_url}/status/models.json", timeout=10)

    assert response.status_code == 200
    assert response.json() == _fixed_badge("down", "red")
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.integration
@pytest.mark.e2e
def test_status_badge_rate_limit_is_grey_not_red(nginx_container_fresh):
    """Review 2026-11-07, item 1: a flood must not paint the badge red. Its
    own zone (5 r/s, burst 20) answers grey "rate limited"; the health
    probe buckets are untouched. A fresh container, so the /health/backend
    floods of other tests cannot have spent the direct bucket first."""
    _, _, service_url = nginx_container_fresh
    url = f"{service_url}/status/models.json"

    with httpx.Client(timeout=10) as client:
        answers = [client.get(url).json() for _ in range(40)]
        health = client.get(f"{service_url}/health/backend")

    assert {a["message"] for a in answers} == {"down", "rate limited"}
    limited = next(a for a in answers if a["message"] == "rate limited")
    assert limited["color"] == "lightgrey"
    assert health.status_code == 503  # its own bucket: the backend is down, not 429


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
