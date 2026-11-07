"""Static regression tests for Gateway NGINX configuration."""

from __future__ import annotations

from pathlib import Path


def _nginx_config_text() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / "gateway" / "nginx" / "nginx.conf").read_text()


def _nginx_server_config_text() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / "gateway" / "nginx" / "conf.d" / "server.conf").read_text()


def test_audit_log_uses_redacted_query_variable():
    """Audit JSON must not log raw presigned URL query strings."""
    config = _nginx_config_text()

    assert "map $args $audit_query" in config
    assert '"query":"$audit_query"' in config
    assert '"query":"$args"' not in config
    assert config.index("map $args $audit_query") < config.index("log_format audit_json")


def test_audit_query_redacts_signed_url_credentials():
    """The redaction map must cover common S3/GCS signed URL secrets."""
    config = _nginx_config_text()

    for param_name in (
        "X-Amz-Signature",
        "X-Goog-Signature",
        "Signature",
        "X-Amz-Security-Token",
        "sig",
    ):
        assert param_name in config
    assert '"<redacted>"' in config


def test_nginx_forwards_verified_auth_headers_to_umrs():
    """Strict-mode UMRS APIs depend on verified auth headers from the sidecar."""
    config = _nginx_server_config_text()

    for route in (
        "location /gw/llm/",
        "location /gw/rag/",
        "location /gw/mcp/",
        "location /gw/skills/",
        "location = /gw/skills",
        "location /gw/endpoints/",
        "location = /gw/endpoints",
        "location /gw/jobs/",
        "location = /gw/jobs",
        "location /gw/models/",
        "location = /gw/models",
        "location /gw/workflows/",
        "location = /gw/workflows",
        # /gw/rag-profiles intentionally absent: locations removed with the
        # UMRS rag-profiles CRUD (2026-10-18, H-10.1 rag-profiles wave);
        # test_nginx_has_no_rag_profiles_location pins the removal.
        "location /gw/components/",
        "location /gw/files/upload",
        "location /gw/catalog/",
        "location /gw/vision/",
        "location /gw/batch/",
    ):
        start = config.index(route)
        next_location = config.find("\n    location ", start + len(route))
        block = config[start:] if next_location == -1 else config[start:next_location]
        assert "auth_request_set $auth_verified_project_id" in block
        assert "proxy_set_header X-Verified-Project-ID $auth_verified_project_id" in block
        assert "proxy_set_header X-Verified-Is-Admin $auth_verified_is_admin" in block


def test_nginx_proxies_catalog_workflow_and_pipeline_routes_to_umrs():
    """The NGINX Gateway path must mirror Python Gateway UMRS adapters."""
    config = _nginx_server_config_text()

    for gateway_path, umrs_path in (
        ("/gw/workflows", "/umrs/workflows"),
        ("/gw/components", "/umrs/components"),
        ("/gw/skills", "/umrs/skills"),
        ("/gw/catalog", "/umrs/catalog"),
        ("/gw/vision", "/umrs/vision"),
    ):
        assert f"location {gateway_path}/" in config
        assert f"rewrite ^{gateway_path}/(.*)$ {umrs_path}/$1 break;" in config
        if gateway_path not in ("/gw/components", "/gw/vision", "/gw/catalog"):
            assert f"location = {gateway_path}" in config
            assert f"rewrite ^ {umrs_path} break;" in config


def test_nginx_has_no_pipelines_location():
    """2026-10-18 (hardening H-10.1 follow-up): UMRS deleted the
    /umrs/pipelines CRUD, so the edge no longer proxies /gw/pipelines."""
    config = _nginx_server_config_text()

    assert "location /gw/pipelines" not in config
    assert "location = /gw/pipelines" not in config
    assert "/umrs/pipelines" not in config


def test_nginx_has_no_rag_profiles_location():
    """2026-10-18 (hardening H-10.1 rag-profiles wave): UMRS deleted the
    /umrs/rag-profiles CRUD (ephemeral, never read at runtime, 410 since
    2026-08-22), so the edge no longer proxies /gw/rag-profiles. On the old
    config both location blocks existed."""
    config = _nginx_server_config_text()

    assert "location /gw/rag-profiles" not in config
    assert "location = /gw/rag-profiles" not in config
    assert "/umrs/rag-profiles" not in config


def test_nginx_bare_collection_routes_have_exact_matches():
    """UMRS collection routes without a trailing slash (list/create) need an
    exact-match block: a prefix-only ``location /gw/<fam>/`` answers the bare
    path with nginx's slash 301, whose absolute ``http://<host>:<port>``
    Location dropped the client's credentials behind the TLS edge (D-05,
    2026-09-25)."""
    config = _nginx_server_config_text()
    # ``groups`` became ``circles`` with ADR-086 (the route moved, this list did not).
    for fam in ("scientific-catalogs", "circles", "evidence", "projects"):
        assert f"location = /gw/{fam} {{" in config, fam
        assert f"rewrite ^ /umrs/{fam} break;" in config, fam
        start = config.index(f"location = /gw/{fam} {{")
        block = config[start : config.index("\n    }", start)]
        assert "auth_request /auth/validate;" in block, fam
        assert "proxy_set_header X-Verified-HMAC $auth_verified_hmac;" in block, fam


def test_nginx_health_uses_single_json_content_type():
    """Avoid duplicate Content-Type headers on the lightweight health route."""
    config = _nginx_server_config_text()
    start = config.index("location /health {")
    end = config.index("\n    location /health/ready", start)
    block = config[start:end]

    assert "default_type application/json" in block
    assert "add_header Content-Type" not in block


def _location_block(config: str, route: str) -> str:
    start = config.index(route)
    next_location = config.find("\n    location ", start + len(route))
    return config[start:] if next_location == -1 else config[start:next_location]


def test_nginx_llm_and_companion_routes_allow_two_hour_upstream_reads():
    """W5/U2 (2026-10-05): a non-streaming reasoning turn sends no byte until
    the model finishes (measured up to 93 min), so /gw/llm/ and /gw/companion/
    wait up to 7200 s — the UMRS proxies' total deadline — instead of the
    600 s that cut long turns. Streaming stays unbuffered."""
    config = _nginx_server_config_text()
    for route in ("location /gw/llm/ {", "location /gw/companion/ {"):
        block = _location_block(config, route)
        assert "proxy_read_timeout 7200s;" in block, route
        assert "proxy_send_timeout 7200s;" in block, route
        assert "proxy_read_timeout 600s;" not in block, route
        assert "proxy_buffering off;" in block, route
        assert "auth_request /auth/validate;" in block, route


def test_nginx_two_hour_reads_stay_scoped_to_llm_and_companion():
    """The raise is per route: the Assistant chat route keeps its 600 s limit."""
    block = _location_block(_nginx_server_config_text(), "location /gw/chat/ {")
    assert "proxy_read_timeout 600s;" in block
    assert "proxy_send_timeout 600s;" in block


# ── Error-page honesty (cycle-2 umrs-api lane, 2026-10-21) ─────────────────
# Live: a burst over general_limit answered 503 "Backend service temporarily
# unavailable" (nginx's default limit_req_status is 503, so @error429 never
# ran and clients saw an outage instead of a rate limit), and the JSON 401/
# 503 bodies went out as application/octet-stream.


def test_rate_limits_answer_429_not_503():
    config = _nginx_config_text()
    assert "limit_req_status 429;" in config
    assert "limit_conn_status 429;" in config


def _named_location(config: str, name: str) -> str:
    start = config.index(f"location {name} {{")
    return config[start : config.index("\n    }", start)]


def test_json_error_pages_send_one_json_content_type():
    config = _nginx_server_config_text()
    for name in ("@error401", "@error429", "@error5xx"):
        block = _named_location(config, name)
        assert "default_type application/json;" in block, name
        assert "add_header Content-Type" not in block, name


def test_rate_limited_answer_says_when_to_retry():
    block = _named_location(_nginx_server_config_text(), "@error429")
    assert "add_header Retry-After" in block


def test_sites_me_route_is_authenticated_and_reaches_umrs():
    """Multi-HPC plan S2 (2026-10-24): ``/gw/sites/me`` carries the caller's
    own account row, so it rides auth_request and signed identity like
    ``/gw/quota/``; the open ``/health`` stays static."""
    config = _nginx_server_config_text()
    start = config.index("location /gw/sites/ {")
    end = config.find("\n    location ", start + 1)
    block = config[start:end]
    assert "auth_request /auth/validate;" in block
    assert "rewrite ^/gw/sites/(.*)$ /umrs/sites/$1 break;" in block
    assert "proxy_set_header X-Verified-HMAC $auth_verified_hmac;" in block
    assert "proxy_set_header X-User-ID $auth_user_id;" in block


def test_health_backend_probes_umrs_and_fails_closed():
    """Issue #634 (2026-10-30): ``/health`` is the gateway's own liveness and
    answered 200 with every backend down, and ``/health/backend`` fell into
    that prefix too. The exact-match ``/health/backend`` must ask UMRS
    ``/health`` (auth_request, 2 s timeouts) and map every failed probe to a
    fixed 503, never relay the backend's body. Runtime proof:
    test_nginx_audit_runtime.py::test_health_backend_*."""
    config = _nginx_server_config_text()
    block = _named_location(config, "= /health/backend")
    assert "auth_request /_health/umrs;" in block
    assert "error_page 401 403 500 502 503 504 = @health_backend_down;" in block
    assert "@health_backend_up;" in block.split("try_files", 1)[1]
    assert "proxy_pass" not in block
    assert "return 200 " in _named_location(config, "@health_backend_up")
    assert "return 503 " in _named_location(config, "@health_backend_down")


def test_health_backend_is_rate_limited_on_its_own_zone():
    """Review 2026-10-30: ``/health/backend`` is anonymous and every hit costs
    UMRS a ``/health`` (sync DB round trip), so it is rate-limited on its own
    zone (behind Apache every client is 127.0.0.1: ``general_limit`` would be
    one bucket shared with real /gw traffic), and the location-level
    ``error_page`` list must keep 429 on the JSON ``@error429`` answer.
    Runtime proof: test_nginx_audit_runtime.py::test_health_backend_is_rate_limited_with_the_json_429."""
    block = _named_location(_nginx_server_config_text(), "= /health/backend")
    assert "limit_req zone=health_backend burst=10 nodelay;" in block
    assert "error_page 429 = @error429;" in block
    assert "limit_req_zone $server_port zone=health_backend:1m rate=5r/s;" in _nginx_config_text()


def test_health_backend_probe_asks_umrs_health_with_short_timeouts():
    """The auth_request target of ``/health/backend`` (issue #634)."""
    probe = _named_location(_nginx_server_config_text(), "= /_health/umrs")
    assert "internal;" in probe
    assert "proxy_pass http://umrs/health;" in probe
    assert "proxy_connect_timeout 2s;" in probe
    assert "proxy_read_timeout 2s;" in probe
    assert 'proxy_set_header Authorization "";' in probe


def test_gateway_liveness_stays_static():
    """``/health`` is the nginx container's liveness (HEALTHCHECK, k8s): it
    must not start depending on the backend, or a backend outage restarts
    a healthy gateway (issue #634 kept it static and added /health/backend)."""
    liveness = _named_location(_nginx_server_config_text(), "/health")
    assert "return 200 " in liveness
    assert "auth_request" not in liveness
