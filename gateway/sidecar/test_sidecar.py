"""Tests for the auth sidecar's local JWT validation."""

from __future__ import annotations

# Patch env before importing app
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from jose import jwt

TEST_SIDE_SECRET = "test-sidecar-secret"

os.environ["JWT_SECRET"] = TEST_SIDE_SECRET
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["DATABASE_URL"] = ""  # Disable DB in tests

# noqa-stamped below: the env bootstrap above must precede app imports.
import main as sidecar_main  # noqa: E402
from main import (  # noqa: E402
    DEFAULT_DEV_JWT_SECRET,
    app,
    validate_auth_config,
    validate_download_key,
)
from main import (  # noqa: E402
    JWT_SECRET as SIDE_JWT_SECRET,
)

client = TestClient(app)


def _make_token(claims: dict, secret: str | None = None) -> str:
    payload = {"exp": int(time.time()) + 3600, **claims}
    return jwt.encode(payload, secret or SIDE_JWT_SECRET, algorithm="HS256")


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "auth-sidecar"


def test_validate_missing_auth():
    resp = client.get("/validate")
    assert resp.status_code == 401


def test_validate_invalid_token():
    resp = client.get(
        "/validate",
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert resp.status_code == 401


def test_validate_wrong_secret():
    token = _make_token(
        {"sub": "user-1", "project_id": "proj-1"},
        secret="wrong-secret",
    )
    resp = client.get(
        "/validate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_validate_missing_project_id():
    token = _make_token({"sub": "user-1"})
    resp = client.get(
        "/validate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_validate_success():
    token = _make_token({"sub": "user-1", "project_id": "proj-1", "email": "u@x.com"})
    resp = client.get(
        "/validate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.headers["x-user-id"] == "user-1"
    assert resp.headers["x-project-id"] == "proj-1"
    assert resp.headers["x-user-email"] == "u@x.com"


def test_validate_alternative_claim_names():
    """project can also be specified as 'tenant_id'."""
    token = _make_token({"sub": "user-2", "tenant_id": "tenant-abc"})
    resp = client.get(
        "/validate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.headers["x-project-id"] == "tenant-abc"


def test_validate_expired_token():
    payload = {"sub": "user-1", "project_id": "proj-1", "exp": int(time.time()) - 100}
    token = _make_token(payload)
    resp = client.get(
        "/validate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# r4_jwt cookie sessions (browser access to /app/)
# ---------------------------------------------------------------------------


def test_validate_accepts_r4_jwt_cookie():
    token = _make_token({"sub": "user-1", "project_id": "proj-1"})
    with TestClient(app) as c:
        c.cookies.set("r4_jwt", token)
        resp = c.get("/validate")
    assert resp.status_code == 200
    assert resp.headers["x-user-id"] == "user-1"


def test_validate_rejects_invalid_cookie():
    with TestClient(app) as c:
        c.cookies.set("r4_jwt", "garbage")
        resp = c.get("/validate")
    assert resp.status_code == 401


def test_validate_header_wins_over_cookie():
    header_token = _make_token({"sub": "header-user", "project_id": "proj-1"})
    cookie_token = _make_token({"sub": "cookie-user", "project_id": "proj-2"})
    with TestClient(app) as c:
        c.cookies.set("r4_jwt", cookie_token)
        resp = c.get("/validate", headers={"Authorization": f"Bearer {header_token}"})
    assert resp.status_code == 200
    assert resp.headers["x-user-id"] == "header-user"


def test_validate_optional_accepts_cookie():
    token = _make_token({"sub": "user-9", "project_id": "proj-9"})
    with TestClient(app) as c:
        c.cookies.set("r4_jwt", token)
        resp = c.get("/validate-optional")
    assert resp.status_code == 200
    assert resp.headers["x-user-id"] == "user-9"


# ---------------------------------------------------------------------------
# /auth/web-login (browser token form)
# ---------------------------------------------------------------------------


def test_web_login_page_serves_form():
    with TestClient(app) as c:
        resp = c.get("/auth/web-login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'action="/auth/web-login"' in resp.text


def test_web_login_sets_cookie_and_redirects():
    token = _make_token({"sub": "user-1", "project_id": "proj-1"})
    with TestClient(app) as c:
        resp = c.post(
            "/auth/web-login",
            data={"token": token, "redirect_to": "/app/"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/app/"
    assert "r4_jwt=" in resp.headers.get("set-cookie", "")
    assert "HttpOnly" in resp.headers.get("set-cookie", "")


def test_web_login_rejects_invalid_token():
    with TestClient(app) as c:
        resp = c.post(
            "/auth/web-login",
            data={"token": "garbage", "redirect_to": "/app/"},
            follow_redirects=False,
        )
    assert resp.status_code == 401


def test_web_login_sanitizes_redirect():
    token = _make_token({"sub": "user-1", "project_id": "proj-1"})
    for evil in ("https://evil.example.com/", "//evil.example.com/", "javascript:alert(1)"):
        with TestClient(app) as c:
            resp = c.post(
                "/auth/web-login",
                data={"token": token, "redirect_to": evil},
                follow_redirects=False,
            )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/app/"


def test_web_login_page_escapes_and_rejects_markup_in_redirect():
    """2026-09-25 review: the target was echoed unescaped into the form."""
    for evil in ('/"><script>alert(1)</script>', "/x?a=<b>", "/\\evil.example.com/", "/`x`"):
        with TestClient(app) as c:
            resp = c.get("/auth/web-login", params={"redirect_to": evil})
        assert resp.status_code == 200
        assert "<script>" not in resp.text
        assert 'name="redirect_to" value="/app/"' in resp.text


def test_web_login_page_keeps_a_plain_query_target():
    with TestClient(app) as c:
        resp = c.get("/auth/web-login", params={"redirect_to": "/app/jobs?status=running&x=1"})
    # & is escaped for the attribute; the browser decodes it back on submit.
    assert 'value="/app/jobs?status=running&amp;x=1"' in resp.text


def test_web_login_page_security_headers():
    with TestClient(app) as c:
        resp = c.get("/auth/web-login")
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assert "form-action 'self'" in resp.headers["content-security-policy"]
    assert resp.headers["x-frame-options"] == "DENY"


def test_safe_redirect_target_rejects_trailing_newline():
    assert sidecar_main._safe_redirect_target("/app\n") == "/app/"
    assert sidecar_main._safe_redirect_target("/app/jobs?x=1") == "/app/jobs?x=1"


def test_web_login_post_rejects_backslash_redirect():
    token = _make_token({"sub": "user-1", "project_id": "proj-1"})
    with TestClient(app) as c:
        resp = c.post(
            "/auth/web-login",
            data={"token": token, "redirect_to": "/\\evil.example.com/"},
            follow_redirects=False,
        )
    assert resp.headers["location"] == "/app/"


# ---------------------------------------------------------------------------
# validate-optional (always 200, identity headers only when token valid)
# ---------------------------------------------------------------------------


def test_validate_optional_no_auth():
    """No token → 200 with no X-User-ID."""
    resp = client.get("/validate-optional")
    assert resp.status_code == 200
    assert "x-user-id" not in resp.headers


def test_validate_optional_invalid_token():
    """Bad token → 200 with no identity headers (not 401)."""
    resp = client.get(
        "/validate-optional",
        headers={"Authorization": "Bearer bad-token"},
    )
    assert resp.status_code == 200
    assert "x-user-id" not in resp.headers


def test_validate_optional_valid_token():
    """Good token → 200 with X-User-ID set."""
    token = _make_token({"sub": "user-1", "project_id": "proj-1", "email": "u@x.com"})
    resp = client.get(
        "/validate-optional",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.headers["x-user-id"] == "user-1"
    assert resp.headers["x-user-email"] == "u@x.com"


def test_validate_optional_expired_token():
    """Expired token → 200 but no identity (graceful fallback)."""
    payload = {"sub": "user-1", "project_id": "proj-1", "exp": int(time.time()) - 100}
    token = _make_token(payload)
    resp = client.get(
        "/validate-optional",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "x-user-id" not in resp.headers


def test_sidecar_rejects_dev_secret_on_public_bind():
    with pytest.raises(RuntimeError, match="loopback SIDECAR_BIND_HOST"):
        validate_auth_config(
            jwt_secret=DEFAULT_DEV_JWT_SECRET,
            gateway_environment="development",
            bind_host="0.0.0.0",
            allow_insecure_dev_auth=False,
        )


def test_sidecar_allows_real_secret_on_public_bind():
    validate_auth_config(
        jwt_secret="replace-me-with-a-real-secret",
        gateway_environment="production",
        bind_host="0.0.0.0",
        allow_insecure_dev_auth=False,
    )


def test_sidecar_allows_explicit_local_dev_secret_on_loopback():
    validate_auth_config(
        jwt_secret=DEFAULT_DEV_JWT_SECRET,
        gateway_environment="staging",
        bind_host="127.0.0.1",
        allow_insecure_dev_auth=True,
    )


def test_validate_download_key_allows_project_scoped_key():
    assert validate_download_key("proj-1/reports/a.pdf", "proj-1") == "proj-1/reports/a.pdf"


def test_validate_download_key_rejects_cross_project_key():
    with pytest.raises(Exception) as exc_info:
        validate_download_key("proj-2/reports/a.pdf", "proj-1")

    assert exc_info.value.status_code == 403


def test_validate_download_key_rejects_traversal():
    with pytest.raises(Exception) as exc_info:
        validate_download_key("proj-1/../secret.txt", "proj-1")

    assert exc_info.value.status_code == 400


def test_validate_download_success(monkeypatch):
    calls = []

    async def fake_authorize_download_with_umrs(key: str, claims: dict) -> str:
        calls.append({"key": key, "claims": dict(claims)})
        return "http://minio/r4-data/proj-1/reports/a.pdf?signature=ok"

    monkeypatch.setattr(
        sidecar_main,
        "authorize_download_with_umrs",
        fake_authorize_download_with_umrs,
    )
    token = _make_token({"sub": "user-1", "project_id": "proj-1"})
    resp = client.get(
        "/validate-download/proj-1/reports/a.pdf",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.headers["x-accel-redirect"].startswith("/internal/s3/?url=")
    assert resp.headers["content-disposition"] == 'attachment; filename="a.pdf"'
    assert len(calls) == 1
    captured = calls[0]
    assert captured["key"] == "proj-1/reports/a.pdf"
    exp = captured["claims"].pop("exp")
    assert isinstance(exp, int) and exp > time.time()
    assert captured["claims"] == {
        "user_id": "user-1",
        "project_id": "proj-1",
        "email": "",
        "roles": [],
        "project_memberships": [],
    }


def test_validate_download_denies_umrs_acl_failure(monkeypatch):
    async def fake_authorize_download_with_umrs(key: str, claims: dict) -> str:
        raise sidecar_main.HTTPException(status_code=403, detail="denied by UMRS")

    monkeypatch.setattr(
        sidecar_main,
        "authorize_download_with_umrs",
        fake_authorize_download_with_umrs,
    )
    token = _make_token({"sub": "user-1", "project_id": "proj-1"})
    resp = client.get(
        "/validate-download/proj-1/reports/a.pdf",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "denied by UMRS"


def test_authorize_download_with_umrs_forwards_verified_context(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, timeout: float):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url: str, json: dict, headers: dict):
            calls.append({"url": url, "json": dict(json), "headers": dict(headers)})
            return httpx.Response(
                200,
                json={
                    "key": "proj-1/reports/a.pdf",
                    "url": "http://minio/r4-data/proj-1/reports/a.pdf?signature=ok",
                },
            )

    monkeypatch.setattr(sidecar_main.httpx, "AsyncClient", FakeAsyncClient)

    import asyncio

    presigned_url = asyncio.run(
        sidecar_main.authorize_download_with_umrs(
            "proj-1/reports/a.pdf",
            {"project_id": "proj-1"},
        )
    )

    assert presigned_url == "http://minio/r4-data/proj-1/reports/a.pdf?signature=ok"
    assert calls[0]["url"].endswith("/umrs/files/presign")
    assert calls[0]["json"]["operation"] == "download"
    assert calls[0]["json"]["key"] == "proj-1/reports/a.pdf"
    assert calls[0]["headers"]["X-Verified-Project-ID"] == "proj-1"


def test_validate_download_rejects_cross_project_path():
    token = _make_token({"sub": "user-1", "project_id": "proj-1"})
    resp = client.get(
        "/validate-download/proj-2/reports/a.pdf",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403


# ── H-1 memberships lane (2026-10-18): the memberships list is signed ───────
# X-Verified-Project-Memberships had no HMAC, so UMRS could not tell a
# gateway-issued list from one a direct caller wrote. The sidecar now adds
# X-Verified-Memberships-HMAC over the canonical (sorted, deduplicated) list
# bound to the signed identity tuple. Expected values are computed here with
# the stdlib (the gateway CI does not install r4-contracts); contract parity
# is pinned in umrs/tests/test_h1r_memberships_sign_parity.py.

_MEMBERSHIP_SECRET = "sidecar-memberships-secret"


def _expected_memberships_hmac(user: str, project: str, admin: bool, members: list[str]) -> str:
    import hashlib
    import hmac

    canonical = ",".join(sorted({m.strip() for m in members if m.strip()}))
    body = f"memberships-v1|{user}\n{project}\n{1 if admin else 0}\n{canonical}".encode()
    return hmac.new(_MEMBERSHIP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_validate_signs_the_memberships_list(monkeypatch):
    monkeypatch.setenv("UMRS_HANDSHAKE_HMAC_SECRET", _MEMBERSHIP_SECRET)
    token = _make_token(
        {"sub": "user-1", "project_id": "proj-1", "project_memberships": ["proj-2", "proj-1"]}
    )
    resp = client.get("/validate", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.headers["x-verified-project-memberships"] == "proj-2,proj-1"
    expected = _expected_memberships_hmac("user-1", "proj-1", False, ["proj-1", "proj-2"])
    assert resp.headers["x-verified-memberships-hmac"] == expected


def test_validate_optional_signs_the_memberships_list(monkeypatch):
    monkeypatch.setenv("UMRS_HANDSHAKE_HMAC_SECRET", _MEMBERSHIP_SECRET)
    token = _make_token(
        {"sub": "root", "project_id": "ops", "roles": ["admin"], "project_memberships": ["ops"]}
    )
    resp = client.get("/validate-optional", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    expected = _expected_memberships_hmac("root", "ops", True, ["ops"])
    assert resp.headers["x-verified-memberships-hmac"] == expected


def test_validate_omits_memberships_hmac_without_a_list_or_secret(monkeypatch):
    monkeypatch.setenv("UMRS_HANDSHAKE_HMAC_SECRET", _MEMBERSHIP_SECRET)
    token = _make_token({"sub": "user-1", "project_id": "proj-1"})
    resp = client.get("/validate", headers={"Authorization": f"Bearer {token}"})
    assert "x-verified-memberships-hmac" not in resp.headers

    monkeypatch.delenv("UMRS_HANDSHAKE_HMAC_SECRET")
    token = _make_token({"sub": "user-1", "project_id": "proj-1", "project_memberships": ["p"]})
    resp = client.get("/validate", headers={"Authorization": f"Bearer {token}"})
    assert resp.headers["x-verified-project-memberships"] == "p"
    assert "x-verified-memberships-hmac" not in resp.headers


# ── /ready-aggregate honesty (cycle-2 umrs-api lane, 2026-10-21) ───────────
# Live: with Postgres stopped, UMRS /readiness was 503 (postgres
# unavailable) while the gateway's /ready said {"ready": true}: the
# aggregate asked UMRS /health, the liveness probe, which stays 200.


def _fake_umrs(monkeypatch: pytest.MonkeyPatch, routes: dict[str, httpx.Response]) -> list[str]:
    asked: list[str] = []

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            asked.append(url)
            for suffix, response in routes.items():
                if url.endswith(suffix):
                    return response
            return httpx.Response(404)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    return asked


def test_ready_aggregate_is_not_ready_when_umrs_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _fake_umrs(
        monkeypatch,
        {
            "/health": httpx.Response(200, json={"status": "ok"}),
            "/readiness": httpx.Response(
                503, json={"ready": False, "unavailable_required": ["postgres"]}
            ),
        },
    )
    resp = client.get("/ready-aggregate")
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    assert "postgres" in body["checks"]["umrs"]
    assert any(url.endswith("/readiness") for url in asked)


def test_ready_aggregate_is_ready_when_umrs_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_umrs(
        monkeypatch,
        {
            "/health": httpx.Response(200, json={"status": "ok"}),
            "/readiness": httpx.Response(200, json={"ready": True}),
        },
    )
    resp = client.get("/ready-aggregate")
    assert resp.status_code == 200
    assert resp.json() == {
        "ready": True,
        "checks": {"gateway": "ok", "auth_sidecar": "ok", "umrs": "ok"},
    }


# Cycle-3 N5 (2026-10-21): an issuer-signed token whose identity claim has an
# odd shape was a 500 (list/int/dict project_id, non-ASCII sub), a dropped
# connection (CR/LF in sub, project_id, a membership or the email), or an admin
# ("roles": "notadmin" matched "admin" as a substring). Now a typed 401, and a
# string role is one role.
_ODD_IDENTITY_CLAIMS = [
    {"sub": "alice", "project_id": ["pa", "pb"]},
    {"sub": "alice", "project_id": 7},
    {"sub": "alice", "project_id": {"x": 1}},
    {"sub": "alice", "project_id": "pa\r\nX-Evil: 1"},
    {"sub": "alice", "project_id": "pa,pb"},
    {"sub": "alice\nX-Verified-Is-Admin: true", "project_id": "pa"},
    {"sub": "アリス", "project_id": "pa"},
    {"sub": "alice", "project_id": "pa", "project_memberships": [{"a": 1}]},
    {"sub": "alice", "project_id": "pa", "project_memberships": ["pa", "pb\nX: 1"]},
    {"sub": "alice", "project_id": "pa", "roles": {"admin": True}},
]


@pytest.mark.parametrize("claims", _ODD_IDENTITY_CLAIMS)
def test_n5_odd_identity_claim_is_a_typed_401(claims: dict[str, object]) -> None:
    resp = client.get("/validate", headers={"Authorization": f"Bearer {_make_token(claims)}"})
    assert resp.status_code == 401
    assert "claim" in resp.json()["detail"]
    assert "x-user-id" not in resp.headers


@pytest.mark.parametrize("claims", _ODD_IDENTITY_CLAIMS)
def test_n5_odd_identity_claim_is_anonymous_on_validate_optional(
    claims: dict[str, object],
) -> None:
    resp = client.get(
        "/validate-optional",
        headers={"Authorization": f"Bearer {_make_token(claims)}"},
    )
    assert resp.status_code == 200
    assert "x-user-id" not in resp.headers  # no partial identity upstream


def test_n5_string_role_is_one_role_not_a_substring_match() -> None:
    token = _make_token({"sub": "alice", "project_id": "pa", "roles": "notadmin"})
    resp = client.get("/validate", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.headers["x-verified-is-admin"] == "false"


@pytest.mark.parametrize("email", ["a@b\r\nX: y", ["a@b"]])
def test_n5_unusable_email_is_dropped(email: object) -> None:
    token = _make_token({"sub": "alice", "project_id": "pa", "email": email})
    resp = client.get("/validate", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert "x-user-email" not in resp.headers


def test_n5_numeric_membership_ids_still_accepted() -> None:
    token = _make_token({"sub": "alice", "project_id": "pa", "project_memberships": [1, 2]})
    resp = client.get("/validate", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.headers["x-verified-project-memberships"] == "1,2"


# ---------------------------------------------------------------------------
# ADR-097: the request's project is checked against live memberships
# (2026-10-22: a user added to a project could not act in it until an admin
# minted a new token; a removed user kept access until the token expired).
# ---------------------------------------------------------------------------


class _LiveUmrs:
    """UMRS GET /umrs/projects as the sidecar sees it; counts the lookups."""

    def __init__(self, memberships: object = None, status: int = 200, fail: bool = False):
        self.memberships = memberships
        self.status = status
        self.fail = fail
        self.calls = 0
        self.disabled: object = None  # None = an older UMRS: no `disabled` field
        self.params: dict[str, str] = {}

    def body(self) -> dict[str, object]:
        body: dict[str, object] = {"memberships": self.memberships}
        if self.disabled is not None:
            body["disabled"] = self.disabled
        return body


@pytest.fixture
def live_umrs(monkeypatch: pytest.MonkeyPatch) -> _LiveUmrs:
    umrs = _LiveUmrs(memberships=["default", "example-project-b"])

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get(
            self,
            url: str,
            params: dict[str, str] | None = None,
            headers: dict[str, str] | None = None,
        ) -> httpx.Response:
            assert url.endswith("/umrs/projects")
            assert headers and headers["X-User-ID"]
            umrs.calls += 1
            umrs.params = dict(params or {})
            if umrs.fail:
                raise httpx.ConnectError("umrs down")
            return httpx.Response(umrs.status, json=umrs.body())

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(sidecar_main, "GATEWAY_PROJECT_MEMBERSHIP", "live")
    monkeypatch.setattr(sidecar_main, "_membership_cache", {})
    return umrs


def _ask(
    project: str | None = None,
    *,
    sub: str = "researcher2",
    token_project: str = "default",
    roles: list[str] | None = None,
    path: str = "/validate",
) -> httpx.Response:
    claims: dict[str, object] = {"project_id": token_project, "roles": roles or []}
    if sub:
        claims["sub"] = sub
    headers = {"Authorization": f"Bearer {_make_token(claims)}"}
    if project is not None:
        headers["X-Requested-Project-ID"] = project
    resp: httpx.Response = client.get(path, headers=headers)
    return resp


def test_token_mode_ignores_the_requested_project(
    live_umrs: _LiveUmrs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sidecar_main, "GATEWAY_PROJECT_MEMBERSHIP", "token")
    resp = _ask("example-project-b")
    assert resp.status_code == 200
    assert resp.headers["x-verified-project-id"] == "default"
    assert live_umrs.calls == 0


def test_live_mode_acts_in_a_requested_live_membership(live_umrs: _LiveUmrs) -> None:
    resp = _ask("example-project-b")
    assert resp.status_code == 200
    assert resp.headers["x-verified-project-id"] == "example-project-b"
    assert resp.headers["x-project-id"] == "example-project-b"
    assert resp.headers["x-verified-project-memberships"] == "default,example-project-b"


def test_live_mode_refuses_a_project_the_user_is_not_in(live_umrs: _LiveUmrs) -> None:
    resp = _ask("secret-lab")
    assert resp.status_code == 403
    assert resp.headers["x-auth-error"] == "project_not_member"
    assert "x-verified-project-id" not in resp.headers


def test_a_membership_added_after_the_token_counts_without_a_new_token(
    live_umrs: _LiveUmrs, monkeypatch: pytest.MonkeyPatch
) -> None:
    live_umrs.memberships = ["default"]
    assert _ask("example-project-b").status_code == 403
    live_umrs.memberships = ["default", "example-project-b"]  # umrs admin user add-to-project
    monkeypatch.setattr(sidecar_main, "MEMBERSHIP_CACHE_TTL_SEC", 0.0)
    assert _ask("example-project-b").headers["x-verified-project-id"] == "example-project-b"


def test_memberships_are_cached_per_user_for_the_ttl(live_umrs: _LiveUmrs) -> None:
    for _ in range(3):
        assert _ask("example-project-b").status_code == 200
    assert live_umrs.calls == 1


def test_lookup_failure_fails_closed_for_a_switch_but_keeps_the_token_project(
    live_umrs: _LiveUmrs,
) -> None:
    live_umrs.fail = True
    switched = _ask("example-project-b")
    assert switched.status_code == 503
    assert switched.headers["x-auth-error"] == "membership_unavailable"
    assert _ask().headers["x-verified-project-id"] == "default"


def test_an_older_umrs_without_memberships_is_a_failed_lookup(live_umrs: _LiveUmrs) -> None:
    live_umrs.memberships = None
    assert _ask("example-project-b").status_code == 503


def test_live_mode_keeps_a_drifted_token_project_and_enforce_refuses_it(
    live_umrs: _LiveUmrs, monkeypatch: pytest.MonkeyPatch
) -> None:
    live_umrs.memberships = ["example-project-b"]  # removed from `default` after minting
    assert _ask().headers["x-verified-project-id"] == "default"
    monkeypatch.setattr(sidecar_main, "GATEWAY_PROJECT_MEMBERSHIP", "enforce")
    resp = _ask()
    assert resp.status_code == 403
    assert resp.headers["x-auth-error"] == "project_membership_revoked"


def test_enforce_mode_fails_closed_when_the_lookup_fails(
    live_umrs: _LiveUmrs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sidecar_main, "GATEWAY_PROJECT_MEMBERSHIP", "enforce")
    live_umrs.fail = True
    assert _ask().status_code == 503


def test_an_admin_may_act_in_any_project(live_umrs: _LiveUmrs) -> None:
    resp = _ask("any-project", roles=["admin"])
    assert resp.headers["x-verified-project-id"] == "any-project"
    # 2026-10-23 (Amendment D-8): no membership is needed, but the admin's
    # standing is looked up (was: no lookup, so a disabled admin passed)
    assert live_umrs.calls == 1


def test_a_token_without_a_user_stays_in_its_project(
    live_umrs: _LiveUmrs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sidecar_main, "JWT_REQUIRE_SUB", False)
    assert _ask("example-project-b", sub="").status_code == 403
    assert _ask(sub="").headers["x-verified-project-id"] == "default"
    assert live_umrs.calls == 0


def test_an_unsafe_requested_project_is_refused(live_umrs: _LiveUmrs) -> None:
    resp = _ask("a,b")
    assert resp.status_code == 403
    assert resp.headers["x-auth-error"] == "invalid_project_id"


def test_validate_optional_treats_a_refused_switch_as_anonymous(live_umrs: _LiveUmrs) -> None:
    resp = _ask("secret-lab", path="/validate-optional")
    assert resp.status_code == 200
    assert "x-user-id" not in resp.headers
    ok = _ask("example-project-b", path="/validate-optional")
    assert ok.headers["x-verified-project-id"] == "example-project-b"


# ---------------------------------------------------------------------------
# ADR-097 Amendment D-8 (2026-10-23): a disabled account's tokens stop working
# within the membership cache window (they used to work until expiry, 30 days
# by default; an admin token outlived its owner's disable).
# ---------------------------------------------------------------------------


def _disable(umrs: _LiveUmrs) -> None:
    umrs.disabled = True
    umrs.memberships = []  # what UMRS answers for a disabled account


def test_live_mode_refuses_a_disabled_accounts_own_project(live_umrs: _LiveUmrs) -> None:
    _disable(live_umrs)
    resp = _ask()
    assert resp.status_code == 403
    assert resp.headers["x-auth-error"] == "account_disabled"
    assert "x-verified-project-id" not in resp.headers


def test_enforce_mode_names_the_disable_not_a_revoked_membership(
    live_umrs: _LiveUmrs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sidecar_main, "GATEWAY_PROJECT_MEMBERSHIP", "enforce")
    _disable(live_umrs)
    resp = _ask()
    assert resp.status_code == 403
    assert resp.headers["x-auth-error"] == "account_disabled"


def test_a_disabled_admins_token_is_refused(live_umrs: _LiveUmrs) -> None:
    _disable(live_umrs)
    resp = _ask("any-project", roles=["admin"])
    assert resp.status_code == 403
    assert resp.headers["x-auth-error"] == "account_disabled"


def test_the_disable_verdict_is_cached_and_a_re_enable_takes_effect(
    live_umrs: _LiveUmrs, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable(live_umrs)
    assert _ask().status_code == 403
    assert _ask().status_code == 403
    assert live_umrs.calls == 1  # cached like memberships
    live_umrs.disabled = False
    live_umrs.memberships = ["default"]
    monkeypatch.setattr(sidecar_main, "MEMBERSHIP_CACHE_TTL_SEC", 0.0)
    assert _ask().status_code == 200


def test_a_disable_takes_effect_once_the_cached_standing_expires(
    live_umrs: _LiveUmrs, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _ask().status_code == 200  # cached: in standing
    _disable(live_umrs)  # umrs-server admin user disable
    assert _ask().status_code == 200  # within the cache window
    monkeypatch.setattr(sidecar_main, "MEMBERSHIP_CACHE_TTL_SEC", 0.0)
    assert _ask().headers["x-auth-error"] == "account_disabled"


def test_disabled_alone_is_enough_evidence(live_umrs: _LiveUmrs) -> None:
    live_umrs.disabled = True
    live_umrs.memberships = None
    assert _ask().headers["x-auth-error"] == "account_disabled"


def test_an_older_umrs_without_the_disabled_field_reads_as_enabled(
    live_umrs: _LiveUmrs,
) -> None:
    assert live_umrs.disabled is None
    assert _ask("example-project-b").headers["x-verified-project-id"] == "example-project-b"


def test_token_mode_does_no_lookup_so_a_disable_waits_for_expiry(
    live_umrs: _LiveUmrs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sidecar_main, "GATEWAY_PROJECT_MEMBERSHIP", "token")
    _disable(live_umrs)
    assert _ask().status_code == 200
    assert live_umrs.calls == 0


def test_an_admin_lookup_failure_passes_in_live_and_fails_closed_in_enforce(
    live_umrs: _LiveUmrs, monkeypatch: pytest.MonkeyPatch
) -> None:
    live_umrs.fail = True
    assert _ask("any-project", roles=["admin"]).status_code == 200
    monkeypatch.setattr(sidecar_main, "GATEWAY_PROJECT_MEMBERSHIP", "enforce")
    resp = _ask("any-project", roles=["admin"])
    assert resp.status_code == 503
    assert resp.headers["x-auth-error"] == "membership_unavailable"


def test_validate_optional_and_download_refuse_a_disabled_account(
    live_umrs: _LiveUmrs,
) -> None:
    _disable(live_umrs)
    optional = _ask(path="/validate-optional")
    assert optional.status_code == 200
    assert "x-user-id" not in optional.headers  # anonymous upstream
    download = _ask(path="/validate-download/default/out.txt")
    assert download.status_code == 403
    assert download.headers["x-auth-error"] == "account_disabled"


def test_the_audit_log_refuses_a_disabled_admin(live_umrs: _LiveUmrs) -> None:
    _disable(live_umrs)
    resp = _ask(roles=["admin"], path="/audit")
    assert resp.status_code == 403
    assert resp.headers["x-auth-error"] == "account_disabled"


def test_the_standing_lookup_skips_the_project_listing(live_umrs: _LiveUmrs) -> None:
    """2026-10-23 review fix (Amendment D-8): an admin's lookup ran UMRS's
    every-row project listing, which could outlast the 3 s lookup timeout;
    the sidecar now asks for the standing alone."""
    assert _ask(roles=["admin"]).status_code == 200
    assert live_umrs.params == {"standing_only": "true"}


def test_an_older_umrs_ignoring_standing_only_still_answers(live_umrs: _LiveUmrs) -> None:
    # an older UMRS ignores the parameter and sends the full listing body
    live_umrs.disabled = True
    live_umrs.memberships = []
    assert _ask(roles=["admin"]).headers["x-auth-error"] == "account_disabled"
