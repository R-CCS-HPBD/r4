"""Auth sidecar for NGINX Gateway validation, audit, and file access control."""

import hashlib
import hmac
import logging
import os
import re
import time
from datetime import datetime
from html import escape as html_escape
from typing import Any, NamedTuple
from urllib.parse import quote, urlencode

import asyncpg
import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_DEV_JWT_SECRET = "dev-secret-change-in-production"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# =============================================================================
# Configuration
# =============================================================================

# JWT settings — must match the Python Gateway's JWT_SECRET
JWT_SECRET = os.environ.get("JWT_SECRET", DEFAULT_DEV_JWT_SECRET)
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_ISSUER = os.environ.get("JWT_ISSUER", "")  # empty = no issuer check
JWT_AUDIENCE = os.environ.get("JWT_AUDIENCE", "")  # empty = no audience check
JWT_REQUIRE_EXP = os.environ.get("JWT_REQUIRE_EXP", "true").lower() == "true"
JWT_REQUIRE_SUB = os.environ.get("JWT_REQUIRE_SUB", "true").lower() == "true"
GATEWAY_ALLOW_INSECURE_DEV_AUTH = os.environ.get(
    "GATEWAY_ALLOW_INSECURE_DEV_AUTH", "false"
).strip().lower() in {"1", "true", "yes", "on"}

# OIDC / JWKS settings for RS256 token validation (e.g. Okta)
JWT_JWKS_URL = os.environ.get(
    "JWT_JWKS_URL", ""
)  # e.g. https://dev-XXX.okta.com/oauth2/default/v1/keys

# Default project_id assigned to IdP tokens that lack a project claim.
# Set this when ALL users share a single project (common for institutional Okta).
JWT_DEFAULT_PROJECT = os.environ.get("JWT_DEFAULT_PROJECT", "")  # e.g. "default"

# S3/MinIO settings
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://minio:9000")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "minioadmin")
S3_BUCKET = os.environ.get("S3_BUCKET", "r4-data")
PRESIGNED_TTL_SECONDS = int(os.environ.get("PRESIGNED_TTL_SECONDS", "3600"))

# Database
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://r4:r4-dev@postgres:5432/r4")

# UMRS authorization
UMRS_BASE_URL = os.environ.get("UMRS_BASE_URL", "http://umrs:8000").rstrip("/")
UMRS_DOWNLOAD_ACL_TIMEOUT_SEC = float(os.environ.get("UMRS_DOWNLOAD_ACL_TIMEOUT_SEC", "5"))
# ADR-097: which project a request acts in is decided here, per request,
# against the user's live memberships (UMRS GET /umrs/projects). token =
# the token's project only (pre-ADR-097); live = the client may select any
# live membership; enforce = the token's own project must be live too.
GATEWAY_PROJECT_MEMBERSHIP = os.environ.get("GATEWAY_PROJECT_MEMBERSHIP", "token").strip().lower()
MEMBERSHIP_CACHE_TTL_SEC = float(os.environ.get("GATEWAY_MEMBERSHIP_CACHE_TTL_SEC", "30"))
MEMBERSHIP_LOOKUP_TIMEOUT_SEC = float(os.environ.get("GATEWAY_MEMBERSHIP_TIMEOUT_SEC", "3"))

# Environment
GATEWAY_ENVIRONMENT = os.environ.get("GATEWAY_ENVIRONMENT", "development")
SIDECAR_BIND_HOST = os.environ.get("SIDECAR_BIND_HOST", os.environ.get("GATEWAY_HOST", "127.0.0.1"))


def _is_loopback_host(host: str) -> bool:
    """Return True when ``host`` binds only to the local machine."""
    return host.strip().lower() in LOOPBACK_HOSTS


def validate_auth_config(
    *,
    jwt_secret: str = JWT_SECRET,
    jwt_jwks_url: str = JWT_JWKS_URL,
    gateway_environment: str = GATEWAY_ENVIRONMENT,
    bind_host: str = SIDECAR_BIND_HOST,
    allow_insecure_dev_auth: bool = GATEWAY_ALLOW_INSECURE_DEV_AUTH,
) -> None:
    """Reject unsafe sidecar auth defaults before serving requests."""
    # When JWKS is configured (RS256/OIDC), the shared secret is not used
    if jwt_jwks_url:
        return
    if jwt_secret != DEFAULT_DEV_JWT_SECRET:
        return

    error = _default_secret_validation_error(
        gateway_environment,
        bind_host,
        allow_insecure_dev_auth,
    )
    if error is not None:
        raise RuntimeError(error)


# Well-known default credentials that must be overridden in production.
_WEAK_S3_CREDENTIALS = {"minioadmin", "minioadmin123", "admin", "password", ""}


def validate_s3_config(
    *,
    access_key: str = S3_ACCESS_KEY,
    secret_key: str = S3_SECRET_KEY,
    endpoint: str = S3_ENDPOINT,
    environment: str = GATEWAY_ENVIRONMENT,
) -> None:
    """Reject well-known default S3 credentials in staging/production."""
    if environment.lower() in {"development", "dev", "test"}:
        return
    if access_key.lower() in _WEAK_S3_CREDENTIALS or secret_key in _WEAK_S3_CREDENTIALS:
        raise RuntimeError(
            "S3_ACCESS_KEY and S3_SECRET_KEY must be explicitly configured "
            "in staging/production environments"
        )


def _default_secret_validation_error(
    gateway_environment: str,
    bind_host: str,
    allow_insecure_dev_auth: bool,
) -> str | None:
    """Return an error message for unsafe built-in secret usage."""
    environment = gateway_environment.lower()
    is_local_dev = environment in {"development", "dev", "test"}
    is_loopback = _is_loopback_host(bind_host)
    if is_loopback and (is_local_dev or allow_insecure_dev_auth):
        return None

    if allow_insecure_dev_auth and not is_loopback:
        return "GATEWAY_ALLOW_INSECURE_DEV_AUTH requires SIDECAR_BIND_HOST to bind only to loopback"
    if is_local_dev and not is_loopback:
        return (
            "The built-in development JWT secret may only be used with a loopback SIDECAR_BIND_HOST"
        )
    return "JWT_SECRET must be explicitly configured when GATEWAY_ENVIRONMENT is not development or test"


# =============================================================================
# Application
# =============================================================================

app = FastAPI(
    title="Gateway Auth Sidecar",
    description="Authentication and audit service for R4 Platform Gateway",
    version="1.0.0",
)

# WP-12 round 2: optional OTel (no-op unless OTEL_EXPORTER_OTLP_ENDPOINT).
try:
    from otel import setup_otel

    setup_otel(app, service_label="gateway-auth-sidecar")
except Exception:  # noqa: BLE001 — tracing must never gate the sidecar boot
    import logging as _logging

    _logging.getLogger(__name__).warning("OTel sidecar boot skipped", exc_info=True)

# Mount SAML SP routes when enabled
try:
    from saml_sp import SAML_ENABLED, extract_saml_session_token
    from saml_sp import router as saml_router

    if SAML_ENABLED:
        app.include_router(saml_router)
        logger.info("SAML SP endpoints mounted at /saml/*")
except ImportError:
    SAML_ENABLED = False
    logger.debug("saml_sp module not available; SAML disabled")

# Database pool
db_pool: asyncpg.Pool | None = None


# =============================================================================
# Lifecycle
# =============================================================================


@app.on_event("startup")
async def startup():
    global db_pool
    validate_auth_config()
    validate_s3_config()
    try:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=10,
        )
        # Ensure audit table exists
        await ensure_audit_table()
    except Exception as e:
        logger.warning("Database connection failed: %s", e)
        # Continue without database - audit will be skipped


@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        await db_pool.close()


async def ensure_audit_table():
    """Create audit table if it doesn't exist."""
    if not db_pool:
        return

    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS gateway_audit_log (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                client_ip TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                user_id TEXT,
                action TEXT NOT NULL,
                resource_id TEXT,
                bytes_transferred BIGINT,
                response_status INTEGER,
                duration_ms INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_audit_timestamp
                ON gateway_audit_log (timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_user_id
                ON gateway_audit_log (user_id, timestamp DESC);
        """)


# =============================================================================
# Models
# =============================================================================


class AuditEntry(BaseModel):
    client_ip: str
    method: str
    path: str
    user_id: str | None = None
    action: str
    resource_id: str | None = None
    bytes_transferred: int | None = None
    response_status: int | None = None
    duration_ms: int | None = None


class ValidationResponse(BaseModel):
    valid: bool
    user_id: str | None = None
    scopes: list[str] = []


# =============================================================================
# Audit Logging
# =============================================================================


async def log_audit(entry: AuditEntry):
    """Write audit log entry to database."""
    if not db_pool:
        return

    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO gateway_audit_log
                (client_ip, method, path, user_id, action,
                 resource_id, bytes_transferred, response_status, duration_ms)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                entry.client_ip,
                entry.method,
                entry.path,
                entry.user_id,
                entry.action,
                entry.resource_id,
                entry.bytes_transferred,
                entry.response_status,
                entry.duration_ms,
            )
    except Exception as e:
        logger.warning("Audit log error: %s", e)


# =============================================================================
# Token Validation
# =============================================================================


def _build_jwt_decode_options() -> tuple[dict, dict]:
    """Return (options, kwargs) for jose.jwt.decode."""
    options = {
        "verify_exp": JWT_REQUIRE_EXP,
        "verify_iss": bool(JWT_ISSUER),
        "verify_aud": bool(JWT_AUDIENCE),
    }
    kwargs: dict = {}
    if JWT_ISSUER:
        kwargs["issuer"] = JWT_ISSUER
    if JWT_AUDIENCE:
        kwargs["audience"] = JWT_AUDIENCE
    return options, kwargs


_PROJECT_CLAIM_NAMES = ("project_id", "project", "tenant_id", "tenant")

# Claims that carry a list of projects the user belongs to.
_MEMBERSHIP_CLAIM_NAMES = (
    "project_memberships",
    "project_ids",
    "projects",
    "allowed_projects",
)


# Identity claims become HTTP header values (X-User-ID, X-Project-ID, the
# comma-joined memberships list). An issuer-signed token whose identity claim
# has any other shape (a list, an object, a control character, non-ASCII) is
# the token's fault: a typed 401. It was a 500 from header encoding, or a
# dropped connection for a CR/LF (cycle-3 N5, 2026-10-21).
_HEADER_SAFE_ID = re.compile(r"[\x21-\x7e]{1,256}")


def _invalid_claim(name: str) -> HTTPException:
    return HTTPException(status_code=401, detail=f"Token '{name}' claim is not a valid identifier")


def _checked_id(value: object, name: str, *, project: bool = False) -> str:
    """``value`` as a header-safe identifier, else a typed 401."""
    if not isinstance(value, str) or not _HEADER_SAFE_ID.fullmatch(value):
        raise _invalid_claim(name)
    if project and "," in value:
        raise _invalid_claim(name)
    return value


def _membership_id(value: object, name: str) -> str:
    # Numeric membership ids were accepted (str()) before N5; they still are.
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    return _checked_id(value, name, project=True)


def _resolve_project_memberships(payload: dict) -> list[str]:
    """Return the project membership list from the token, if any."""
    for name in _MEMBERSHIP_CLAIM_NAMES:
        value = payload.get(name)
        if isinstance(value, list) and value:
            return [_membership_id(v, name) for v in value]
    return []


def _resolve_project_id(payload: dict) -> str:
    """Extract project_id from any of the supported claim names.

    Falls back to the first entry in the project membership list when
    no single-project claim is present but the token carries a
    ``project_memberships`` (or equivalent) claim.
    """
    for name in _PROJECT_CLAIM_NAMES:
        value = payload.get(name)
        if value:
            return _checked_id(value, name, project=True)

    memberships = _resolve_project_memberships(payload)
    if memberships:
        return memberships[0]

    if JWT_DEFAULT_PROJECT:
        return JWT_DEFAULT_PROJECT

    raise HTTPException(status_code=401, detail="Token missing project_id claim")


def _resolve_user_id(payload: dict[str, Any]) -> str:
    user_id = payload.get("sub") or payload.get("user_id") or ""
    if not user_id:
        if JWT_REQUIRE_SUB:
            raise HTTPException(status_code=401, detail="Token missing required 'sub' claim")
        return ""
    return _checked_id(user_id, "sub")


def _resolve_roles(payload: dict[str, Any]) -> list[str]:
    """Roles as a list of strings. A bare string is one role: ``"admin" in
    "notadmin"`` was a substring match that made any such token an admin."""
    roles = payload.get("roles", [])
    if isinstance(roles, str):
        return [roles]
    if not isinstance(roles, list):
        raise HTTPException(status_code=401, detail="Token 'roles' claim is not a list")
    return [role for role in roles if isinstance(role, str)]


def _resolve_email(payload: dict[str, Any]) -> str:
    """The email is informational (X-User-Email): an unusable one is dropped."""
    email = payload.get("email", "")
    if isinstance(email, str) and _HEADER_SAFE_ID.fullmatch(email):
        return email
    return ""


def _extract_sidecar_claims(payload: dict) -> dict:
    """Enforce required claims and return a claims dict."""
    if JWT_REQUIRE_EXP and "exp" not in payload:
        raise HTTPException(status_code=401, detail="Token missing required 'exp' claim")

    user_id = _resolve_user_id(payload)
    memberships = _resolve_project_memberships(payload)

    return {
        "user_id": user_id,
        "project_id": _resolve_project_id(payload),
        "email": _resolve_email(payload),
        "roles": _resolve_roles(payload),
        "project_memberships": memberships,
        "exp": payload.get("exp"),
    }


# Cached JWKS key set (populated lazily on first RS256 request)
_jwks_cache: dict | None = None


def _fetch_jwks() -> dict:
    """Fetch the JWKS key set from the configured endpoint."""
    resp = httpx.get(JWT_JWKS_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _find_jwks_key(jwks_data: dict, kid: str) -> dict | None:
    """Find a key by kid in the JWKS data."""
    for key_data in jwks_data.get("keys", []):
        if key_data.get("kid") == kid:
            return key_data
    return None


def _get_jwks_key(token: str) -> dict:
    """Resolve the signing key from the JWKS endpoint for RS256 validation."""
    global _jwks_cache
    if _jwks_cache is None:
        _jwks_cache = _fetch_jwks()

    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    if not kid:
        raise HTTPException(status_code=401, detail="Token header missing 'kid'")

    key_data = _find_jwks_key(_jwks_cache, kid)
    if key_data is not None:
        return key_data

    # kid not found — refresh cache once (key rotation)
    _jwks_cache = _fetch_jwks()
    key_data = _find_jwks_key(_jwks_cache, kid)
    if key_data is not None:
        return key_data

    raise HTTPException(status_code=401, detail=f"No JWKS key found for kid={kid}")


def _resolve_key_and_algorithms(token: str) -> tuple:
    """Return (key, algorithms) for JWT decoding.

    When ``JWT_JWKS_URL`` is configured, tokens that carry a ``kid``
    header are validated against the JWKS endpoint (RS256).  Tokens
    without ``kid`` fall back to the shared HS256 secret so that
    API-key JWTs (``umrs-server admin token generate-jwt``) continue
    to work alongside OIDC tokens.
    """
    if JWT_JWKS_URL:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("kid"):
                return _get_jwks_key(token), ["RS256"]
        except JWTError:
            pass
        # No kid header → fall through to HS256
    return JWT_SECRET, [JWT_ALGORITHM]


def validate_jwt(token: str) -> dict:
    """
    Validate JWT token locally and return claims.

    Supports two modes:
    - **HS256 (dev/API-key)**: Validates with shared ``JWT_SECRET``.
      Issuer/audience checks are skipped because API-key JWTs are
      not issued by an OIDC provider.
    - **RS256 / JWKS (Okta/OIDC)**: Validates with public keys fetched
      from ``JWT_JWKS_URL``.

    Returns dict with user_id, project_id, email, roles on success.
    Raises HTTPException on failure.
    """
    try:
        key, algorithms = _resolve_key_and_algorithms(token)
        using_jwks = algorithms == ["RS256"] and JWT_JWKS_URL

        if using_jwks:
            options, kwargs = _build_jwt_decode_options()
        else:
            # HS256 API-key tokens: skip issuer/audience (OIDC-specific)
            options = {"verify_exp": JWT_REQUIRE_EXP, "verify_iss": False, "verify_aud": False}
            kwargs = {}

        payload = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            options=options,
            **kwargs,
        )
    except HTTPException:
        raise
    except JWTError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc

    return _extract_sidecar_claims(payload)


def extract_token(authorization: str | None) -> str | None:
    """Extract bearer token from Authorization header."""
    if not authorization:
        return None

    if authorization.startswith("Bearer "):
        return authorization[7:]

    return authorization


# Cookie consumed by browser sessions (set by /auth/web-login or the SPA).
R4_JWT_COOKIE_NAME = os.environ.get("R4_JWT_COOKIE_NAME", "r4_jwt")


def extract_cookie_token(request: Request) -> str | None:
    """Extract a bearer token from the r4_jwt browser cookie."""
    token = request.cookies.get(R4_JWT_COOKIE_NAME, "").strip()
    return token or None


# =============================================================================
# Presigned URL Generation
# =============================================================================


def generate_presigned_url(
    bucket: str,
    key: str,
    ttl_seconds: int = PRESIGNED_TTL_SECONDS,
    method: str = "GET",
) -> str:
    """
    Generate S3 presigned URL using AWS Signature v2.

    This is a simplified implementation. For production, use boto3 or minio SDK.

    WP-16 round 2: TTL<=0 rejects at generation — a born-expired URL is a
    well-formed mistake every minute spent debugging it validates. Request
    surfaces clamp with 400/422 at their own boundaries.
    """
    if ttl_seconds <= 0:
        raise ValueError(f"ttl_seconds must be positive (got {ttl_seconds})")
    expires = int(time.time()) + ttl_seconds

    # String to sign (AWS Signature v2)
    string_to_sign = f"{method}\n\n\n{expires}\n/{bucket}/{key}"

    # Calculate signature
    signature = hmac.new(
        S3_SECRET_KEY.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()

    import base64

    signature_b64 = base64.b64encode(signature).decode("utf-8")

    # Build URL
    params = urlencode(
        {
            "AWSAccessKeyId": S3_ACCESS_KEY,
            "Expires": expires,
            "Signature": signature_b64,
        }
    )

    return f"{S3_ENDPOINT}/{bucket}/{quote(key)}?{params}"


def validate_download_key(path: str, project_id: str) -> str:
    """Return a safe project-scoped S3 key for a download request."""
    key = path.strip("/")
    if not key:
        raise HTTPException(status_code=400, detail="Missing file key")
    if ".." in key.split("/"):
        raise HTTPException(status_code=400, detail="Invalid file key")
    if not key.startswith(f"{project_id}/"):
        raise HTTPException(status_code=403, detail="File is outside caller project")
    return key


def _sign_identity_hmac(secret: str, user_id: str, project_id: str, is_admin: bool) -> str:
    """Sign the identity tuple. KEEP IN SYNC with
    r4_contracts.handshake_hmac.sign_claims — the canonical tuple format is
    contract-defined; the sidecar image does not package r4-contracts (narrow
    ansible build context), so it carries this stdlib-only twin. Guarded by
    umrs/tests/test_sidecar_sign_parity.
    """
    import hashlib
    import hmac

    canonical = f"v1\n{user_id}\n{project_id}\n{1 if is_admin else 0}".encode()
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def _sign_workflow_author_hmac(secret: str, user_id: str, project_id: str, author: bool) -> str:
    """ADR-027 wf-claim signature on its OWN basis — KEEP IN SYNC with
    r4_contracts.handshake_hmac.sign_workflow_author_claim (same twin
    arrangement as the identity signer; same parity test)."""
    import hashlib
    import hmac

    canonical = f"v1wf\n{user_id}\n{project_id}\n{1 if author else 0}".encode()
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def _sign_memberships_hmac(
    secret: str, user_id: str, project_id: str, is_admin: bool, memberships: list[str]
) -> str:
    """H-1 (2026-10-18) memberships signature on its OWN basis — KEEP IN SYNC
    with r4_contracts.handshake_hmac.sign_memberships (same twin arrangement;
    parity: umrs/tests/test_h1r_memberships_sign_parity.py). The canonical
    list is comma-split, trimmed, deduplicated and sorted."""
    import hashlib
    import hmac

    parts = {part.strip() for item in memberships for part in str(item).split(",")}
    listed = ",".join(sorted(part for part in parts if part))
    admin = 1 if is_admin else 0
    canonical = f"memberships-v1|{user_id}\n{project_id}\n{admin}\n{listed}".encode()
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def _signed_headers(
    secret: str, user_id: str, project_id: str, is_admin: bool, claims: dict
) -> dict[str, str]:
    """The HMAC headers for one identity (secret configured)."""
    # ADR-027: uploads require the workflow_author role (admins implied).
    workflow_author = is_admin or "workflow_author" in (claims.get("roles") or [])
    headers = {
        "X-Verified-HMAC": _sign_identity_hmac(secret, user_id, project_id, is_admin),
        "X-Verified-Workflow-Author-HMAC": _sign_workflow_author_hmac(
            secret, user_id, project_id, workflow_author
        ),
    }
    memberships = [str(m) for m in claims.get("project_memberships") or []]
    if memberships:
        # UMRS honours X-Verified-Project-Memberships only with this signature.
        headers["X-Verified-Memberships-HMAC"] = _sign_memberships_hmac(
            secret, user_id, project_id, is_admin, memberships
        )
    return headers


def _verified_project_headers(claims: dict) -> dict[str, str]:
    project_id = claims["project_id"]
    user_id = str(claims.get("user_id") or "")
    is_admin = "admin" in (claims.get("roles") or [])
    # ADR-027: uploads require the workflow_author role (admins implied).
    workflow_author = is_admin or "workflow_author" in (claims.get("roles") or [])
    headers = {
        "X-User-ID": user_id,
        "X-Project-ID": project_id,
        "X-Verified-Project-ID": project_id,
        "X-Verified-Is-Admin": "true" if is_admin else "false",
        "X-Verified-Workflow-Author": "true" if workflow_author else "false",
    }
    if claims.get("project_memberships"):
        headers["X-Verified-Project-Memberships"] = ",".join(
            map(str, claims["project_memberships"])
        )
    secret = os.environ.get("UMRS_HANDSHAKE_HMAC_SECRET", "").strip()
    if secret:
        headers.update(_signed_headers(secret, user_id, project_id, is_admin, claims))
    return headers


def _raise_for_umrs_presign_status(response: httpx.Response) -> None:
    if response.status_code in {401, 403, 404}:
        raise HTTPException(status_code=response.status_code, detail=_umrs_error_detail(response))
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=_umrs_error_detail(response))


def _presigned_url_from_umrs_response(response: httpx.Response, key: str) -> str:
    _raise_for_umrs_presign_status(response)
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502, detail="UMRS download ACL returned invalid JSON"
        ) from exc

    authorized_key = data.get("key")
    if authorized_key != key:
        raise HTTPException(status_code=502, detail="UMRS download ACL returned mismatched key")

    presigned_url = data.get("url")
    if not isinstance(presigned_url, str) or not presigned_url:
        raise HTTPException(status_code=502, detail="UMRS download ACL returned no presigned URL")
    return presigned_url


async def authorize_download_with_umrs(key: str, claims: dict) -> str:
    """Ask UMRS to authorize and presign a project-scoped download key."""
    payload = {
        "operation": "download",
        "key": key,
        "bucket": S3_BUCKET,
        "ttl_sec": PRESIGNED_TTL_SECONDS,
    }
    url = f"{UMRS_BASE_URL}/umrs/files/presign"
    try:
        async with httpx.AsyncClient(timeout=UMRS_DOWNLOAD_ACL_TIMEOUT_SEC) as client:
            response = await client.post(
                url,
                json=payload,
                headers=_verified_project_headers(claims),
            )
    except httpx.TimeoutException as exc:
        logger.warning("UMRS download ACL timed out for key %s", key)
        raise HTTPException(status_code=503, detail="UMRS download ACL timed out") from exc
    except httpx.RequestError as exc:
        logger.warning("UMRS download ACL unavailable for key %s: %s", key, exc)
        raise HTTPException(status_code=503, detail="UMRS download ACL unavailable") from exc

    return _presigned_url_from_umrs_response(response, key)


def _umrs_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text or "UMRS download ACL denied request"
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
    return "UMRS download ACL denied request"


# =============================================================================
# Live project membership (ADR-097)
# =============================================================================


class Standing(NamedTuple):
    """The caller's live standing as UMRS reports it: the live memberships
    and (ADR-097 Amendment D-8, 2026-10-23) whether the account is disabled."""

    memberships: list[str]
    disabled: bool


_MEMBERSHIP_CACHE_MAX = 4096
_membership_cache: dict[str, tuple[float, Standing]] = {}


def _membership_ids(data: object) -> list[str] | None:
    """The ``memberships`` list of a UMRS projects listing; None when absent
    or not a list (an older UMRS, or one that could not read it)."""
    value = data.get("memberships") if isinstance(data, dict) else None
    if not isinstance(value, list):
        return None
    return [
        v for v in value if isinstance(v, str) and _HEADER_SAFE_ID.fullmatch(v) and "," not in v
    ]


def _standing(data: object) -> Standing | None:
    """The standing a UMRS projects listing reports; None when it reports
    none. ``disabled: true`` alone is enough evidence (memberships are then
    empty); an older UMRS sends no ``disabled`` and is read as enabled."""
    if isinstance(data, dict) and data.get("disabled") is True:
        return Standing([], True)
    memberships = _membership_ids(data)
    return None if memberships is None else Standing(memberships, False)


# The standing alone, no project listing (an admin's listing is every row,
# which can outlast the lookup timeout). An older UMRS ignores the parameter
# and answers the full listing, memberships and `disabled` included.
_STANDING_ONLY = {"standing_only": "true"}


async def _fetch_live_standing(claims: dict[str, Any]) -> Standing | None:
    """Ask the venue's UMRS for the caller's live standing; None on any failure."""
    try:
        async with httpx.AsyncClient(timeout=MEMBERSHIP_LOOKUP_TIMEOUT_SEC) as client:
            response = await client.get(
                f"{UMRS_BASE_URL}/umrs/projects",
                params=_STANDING_ONLY,
                headers=_verified_project_headers(claims),
            )
        if response.status_code != 200:
            logger.warning("live membership lookup: UMRS answered %s", response.status_code)
            return None
        return _standing(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("live membership lookup failed: %s", exc)
        return None


async def live_standing(claims: dict[str, Any]) -> Standing | None:
    """The caller's live standing, cached for MEMBERSHIP_CACHE_TTL_SEC per
    user (a change, a disable included, takes effect within that window).
    Failures are not cached."""
    user = claims["user_id"]
    now = time.monotonic()
    hit = _membership_cache.get(user)
    if hit and now - hit[0] < MEMBERSHIP_CACHE_TTL_SEC:
        return hit[1]
    value = await _fetch_live_standing(claims)
    if value is None:
        return None
    if len(_membership_cache) >= _MEMBERSHIP_CACHE_MAX:
        _membership_cache.clear()
    _membership_cache[user] = (now, value)
    return value


def _refused(status: int, code: str, message: str) -> HTTPException:
    # nginx auth_request passes only 401/403 through (anything else is its
    # own 500); X-Auth-Error carries the reason to the client's error page.
    return HTTPException(status_code=status, detail=message, headers={"X-Auth-Error": code})


def _membership_refusal(
    target: str, token_project: str, live: list[str] | None, mode: str
) -> HTTPException | None:
    """Why ``target`` may not be the request's project (ADR-097 D-3/D-4); None = allowed."""
    if live is None:
        if mode == "enforce" or target != token_project:
            return _refused(
                503, "membership_unavailable", "project membership could not be checked"
            )
        return None
    if target in live:
        return None
    if target != token_project:
        return _refused(403, "project_not_member", f"not a member of project '{target}'")
    if mode == "enforce":
        return _refused(
            403, "project_membership_revoked", f"no longer a member of project '{target}'"
        )
    logger.warning("membership drift: token project %s is not a live membership", target)
    return None


def _requested_project(requested: str | None) -> str:
    """The client's ``X-Project-ID`` (as nginx forwards it), checked like a claim."""
    value = (requested or "").strip()
    if value and (not _HEADER_SAFE_ID.fullmatch(value) or "," in value):
        raise _refused(403, "invalid_project_id", "requested project id is not valid")
    return value


async def _caller_standing(claims: dict[str, Any]) -> Standing | None:
    """The caller's standing; None when the lookup failed. No user id
    (JWT_REQUIRE_SUB=false): nothing to look up, so the token's project is
    the only one it may act in."""
    if not claims["user_id"]:
        return Standing([claims["project_id"]], False)
    return await live_standing(claims)


def _standing_refusal(standing: Standing | None, mode: str, *, admin: bool) -> None:
    """Raise when the account may not act at all (ADR-097 Amendment D-8).

    A disabled account is refused whatever its token says, admin included.
    An admin needs no membership, so an unknown standing refuses an admin
    only in ``enforce``, where a failed lookup refuses every request (D-4);
    a non-admin's unknown standing is left to ``_membership_refusal``.
    """
    if standing is not None and standing.disabled:
        raise _refused(403, "account_disabled", "this account is disabled")
    if standing is None and admin and mode == "enforce":
        raise _refused(503, "membership_unavailable", "project membership could not be checked")


async def resolve_request_project(claims: dict[str, Any], requested: str | None) -> dict[str, Any]:
    """ADR-097: ``claims`` with the project this request acts in.

    ``token`` mode returns the token's project unchanged, with no lookup (a
    disable then takes effect at token expiry). Otherwise a disabled account
    is refused (Amendment D-8), and the requested project (or the token's)
    must be a live membership; admins may act in any project. The signed
    memberships list becomes the live one.
    """
    mode = GATEWAY_PROJECT_MEMBERSHIP
    if mode not in ("live", "enforce"):
        return claims
    target = _requested_project(requested) or claims["project_id"]
    admin = "admin" in claims["roles"]
    standing = await _caller_standing(claims)
    _standing_refusal(standing, mode, admin=admin)
    if admin:
        return {**claims, "project_id": target}
    live = standing.memberships if standing is not None else None
    refusal = _membership_refusal(target, claims["project_id"], live, mode)
    if refusal is not None:
        raise refusal
    memberships = live if live is not None else claims["project_memberships"]
    return {**claims, "project_id": target, "project_memberships": memberships}


# =============================================================================
# Routes
# =============================================================================

UMRS_BASE_URL = os.environ.get("UMRS_BASE_URL", "http://127.0.0.1:8082")


@app.get("/health")
async def health():
    """Health check endpoint."""
    db_status = "connected" if db_pool else "disabled"
    return {
        "status": "healthy",
        "service": "auth-sidecar",
        "database": db_status,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _umrs_readiness_check(resp: httpx.Response) -> str:
    """One ``checks["umrs"]`` value from UMRS ``/readiness``: "ok", or why not."""
    if resp.status_code == 200:
        return "ok"
    try:
        missing = resp.json().get("unavailable_required") or []
    except ValueError:
        missing = []
    if missing:
        return "not ready: " + ", ".join(str(m) for m in missing)
    return f"http {resp.status_code}"


@app.get("/ready-aggregate")
async def ready_aggregate():
    """Aggregate readiness check: auth sidecar + UMRS HTTP sidecar.

    Returns 200 with ``ready: true`` only when UMRS itself reports ready
    (``/readiness``: its required stores, Postgres first, answer). UMRS
    down or not ready degrades to ``ready: false`` with a descriptive
    ``checks`` map so ``r4 health`` can report the real problem.

    2026-10-21: this asked UMRS ``/health``, the liveness probe, which
    stays 200 with Postgres stopped, so ``/ready`` said ready while every
    store-backed route failed.
    """
    checks = {"gateway": "ok", "auth_sidecar": "ok"}

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{UMRS_BASE_URL}/readiness")
            checks["umrs"] = _umrs_readiness_check(resp)
    except Exception:
        checks["umrs"] = "unreachable"
    ready = checks["umrs"] == "ok"

    return JSONResponse(
        content={"ready": ready, "checks": checks},
        status_code=200 if ready else 503,
    )


def _client_ip(request: Request) -> str:
    """Extract client IP from request, defaulting to 'unknown'."""
    return request.client.host if request.client else "unknown"


@app.get("/validate")
async def validate_request(
    request: Request,
    x_original_uri: str | None = Header(None),
    x_original_method: str | None = Header(None),
    authorization: str | None = Header(None),
    x_requested_project_id: str | None = Header(None),
):
    """
    Validate authentication for NGINX auth_request.

    On success returns 200 with X-User-ID and X-Project-ID headers
    that NGINX captures via auth_request_set and forwards to upstreams.
    """
    token = extract_token(authorization)
    client_ip = _client_ip(request)
    method = x_original_method or "UNKNOWN"
    path = x_original_uri or "unknown"

    # Fallback: browser cookie session (set by /auth/web-login or the SPA)
    if not token:
        token = extract_cookie_token(request)

    # Fallback: check SAML session cookie when no Authorization header
    if not token and SAML_ENABLED:
        token = extract_saml_session_token(request)

    if not token:
        await log_audit(
            AuditEntry(
                client_ip=client_ip,
                method=method,
                path=path,
                action="auth_missing",
            )
        )
        raise HTTPException(status_code=401, detail="Missing authorization header")

    claims = await resolve_request_project(validate_jwt(token), x_requested_project_id)

    await log_audit(
        AuditEntry(
            client_ip=client_ip,
            method=method,
            path=path,
            user_id=claims["user_id"],
            action="auth_success",
        )
    )

    response = Response(status_code=200)
    response.headers.update(_verified_project_headers(claims))
    if claims.get("email"):
        response.headers["X-User-Email"] = claims["email"]
    return response


@app.get("/validate-optional")
async def validate_optional(
    request: Request,
    x_original_uri: str | None = Header(None),
    x_original_method: str | None = Header(None),
    authorization: str | None = Header(None),
    x_requested_project_id: str | None = Header(None),
):
    """
    Validate authentication optionally for NGINX auth_request.

    Always returns 200.  When a valid token is present, the response
    includes ``X-User-ID`` and project headers so NGINX can forward
    them to the upstream.  Without a token (or with an invalid one)
    returns 200 with no identity headers — the upstream sees an
    unauthenticated request.

    Used for endpoints like ``/connect-info`` that work without auth
    but return richer data when the caller is identified.
    """
    token = extract_token(authorization)

    if not token:
        token = extract_cookie_token(request)

    if not token and SAML_ENABLED:
        token = extract_saml_session_token(request)

    response = Response(status_code=200)

    if token:
        try:
            claims = await resolve_request_project(validate_jwt(token), x_requested_project_id)
            response.headers.update(_verified_project_headers(claims))
            if claims.get("email"):
                response.headers["X-User-Email"] = claims["email"]
        except Exception:
            pass  # Invalid token → treat as unauthenticated

    return response


@app.get("/validate-download/{path:path}")
async def validate_download(
    path: str,
    request: Request,
    x_original_uri: str | None = Header(None),
    authorization: str | None = Header(None),
    x_requested_project_id: str | None = Header(None),
):
    """
    Validate download request and return X-Accel-Redirect.

    Returns headers for NGINX to serve the file.
    """
    token = extract_token(authorization)

    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization")

    claims = await resolve_request_project(validate_jwt(token), x_requested_project_id)
    key = validate_download_key(path, claims["project_id"])

    presigned_url = await authorize_download_with_umrs(key, claims)

    # Log download
    await log_audit(
        AuditEntry(
            client_ip=request.client.host if request.client else "unknown",
            method="GET",
            path=f"/gw/files/download/{key}",
            user_id=claims["user_id"],
            action="file_download",
            resource_id=key,
        )
    )

    # Return X-Accel-Redirect for NGINX
    response = Response(status_code=200)
    response.headers["X-Accel-Redirect"] = f"/internal/s3/?url={quote(presigned_url)}"
    response.headers["Content-Disposition"] = f'attachment; filename="{os.path.basename(path)}"'
    return response


@app.get("/audit")
async def get_audit_log(
    request: Request,
    authorization: str | None = Header(None),
    limit: int = 100,
    user_id: str | None = None,
):
    """
    Retrieve audit log entries.

    Admin-only endpoint.
    """
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization")

    # ensures the caller is authenticated, and (live/enforce) not disabled
    claims = await resolve_request_project(validate_jwt(token), None)

    # Enforce admin role
    if "admin" not in (claims.get("roles") or []):
        raise HTTPException(status_code=403, detail="Admin role required to access audit logs")

    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    async with db_pool.acquire() as conn:
        if user_id:
            rows = await conn.fetch(
                """
                SELECT * FROM gateway_audit_log
                WHERE user_id = $1
                ORDER BY timestamp DESC
                LIMIT $2
                """,
                user_id,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM gateway_audit_log
                ORDER BY timestamp DESC
                LIMIT $1
                """,
                limit,
            )

    return [dict(row) for row in rows]


# =============================================================================
# OIDC CLI Callback Trampoline
# =============================================================================
# The gateway receives the Okta OIDC callback at /auth/cli-callback
# (HTTPS + FQDN) and serves a small HTML page that JS-redirects the
# authorization code to the r4 CLI's localhost HTTP server.
# This endpoint requires NO authentication — the user is mid-OIDC flow.

# Default localhost port for the CLI callback relay
_CLI_CALLBACK_DEFAULT_PORT = 18338


@app.get("/auth/cli-callback")
async def cli_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    """Trampoline: relay Okta's authorization code to the CLI's localhost."""
    # Okta preserves extra query params (like cli_port) through the flow.
    cli_port = request.query_params.get("cli_port", str(_CLI_CALLBACK_DEFAULT_PORT))
    try:
        cli_port_int = int(cli_port)
        if not (1024 <= cli_port_int <= 65535):
            raise ValueError
    except ValueError:
        cli_port_int = _CLI_CALLBACK_DEFAULT_PORT

    if error:
        # Relay the error to the CLI's localhost server
        params = urlencode({"error": error, "error_description": error_description, "state": state})
        localhost_url = f"http://localhost:{cli_port_int}/callback?{params}"
    elif code:
        params = urlencode({"code": code, "state": state})
        localhost_url = f"http://localhost:{cli_port_int}/callback?{params}"
    else:
        return Response(
            content="<html><body><h2>Error</h2><p>Missing authorization code.</p></body></html>",
            media_type="text/html",
            status_code=400,
        )

    # Serve a minimal HTML page that JS-redirects to localhost.
    # The meta-refresh is a fallback for browsers with JS disabled.
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="2;url={localhost_url}">
  <title>R4 Login</title>
</head>
<body>
  <h2>Redirecting to r4 CLI&hellip;</h2>
  <p>If you are not redirected automatically,
     <a href="{localhost_url}">click here</a>.</p>
  <script>window.location.replace({_js_string(localhost_url)});</script>
</body>
</html>"""
    return Response(content=html, media_type="text/html")


def _js_string(s: str) -> str:
    """Escape a string for safe embedding in a JS string literal."""
    return (
        '"'
        + s.replace("\\", "\\\\").replace('"', '\\"').replace("<", "\\x3c").replace(">", "\\x3e")
        + '"'
    )


# =============================================================================
# Browser Web Login — token-cookie session for the r4-web console
# =============================================================================
# Deployments without SAML have no browser-navigable login flow (OIDC here
# is a CLI localhost relay).  /auth/web-login lets a browser establish an
# r4_jwt cookie session from a platform token (`umrs-server admin token
# generate-jwt ...` or `r4 auth token show`).  GET serves the form; POST
# validates the token server-side before setting the cookie.

# Same-origin path + query only: RFC 3986 pchar/query characters, no second
# leading slash or backslash (``//host`` and ``/\host`` are protocol-relative
# redirects in browsers), and none of ``" < > ` { } |`` (the target is echoed
# into the form; 2026-09-25 review: the old ``[\x21-\x7e]`` class let
# ``/"><script>`` through unescaped).
_SAFE_REDIRECT_RE = re.compile(r"/(?![/\\])[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*")

# The login page runs no script and posts only to itself.
_WEB_LOGIN_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _safe_redirect_target(raw: str) -> str:
    """Allow only same-origin absolute paths as post-login redirects."""
    # fullmatch: a `$` anchor would accept a trailing newline ("/app\n").
    if raw and _SAFE_REDIRECT_RE.fullmatch(raw) and not raw.startswith("/auth/web-login"):
        return raw
    return "/app/"


@app.get("/auth/web-login")
async def web_login_page(redirect_to: str = "/app/"):
    """Serve the browser login form (unauthenticated by design)."""
    if SAML_ENABLED:
        # SAML deployments have a real IdP flow — defer to it.
        params = urlencode({"redirect_to": _safe_redirect_target(redirect_to)})
        return Response(status_code=302, headers={"Location": f"/saml/login?{params}"})

    target = html_escape(_safe_redirect_target(redirect_to), quote=True)
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>R4 Console Login</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0;
           display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px;
            padding: 2rem; max-width: 26rem; width: 100%; }}
    h1 {{ font-size: 1.25rem; margin: 0 0 0.5rem; }}
    p {{ color: #94a3b8; font-size: 0.875rem; }}
    input[type=password] {{ width: 100%; box-sizing: border-box; padding: 0.5rem;
        background: #0f172a; color: #e2e8f0; border: 1px solid #334155; border-radius: 6px; }}
    button {{ margin-top: 1rem; width: 100%; padding: 0.5rem; background: #2563eb;
        color: white; border: 0; border-radius: 6px; cursor: pointer; font-weight: 600; }}
    .err {{ color: #f87171; font-size: 0.875rem; min-height: 1.25rem; }}
    code {{ background: #0f172a; padding: 0.1rem 0.3rem; border-radius: 4px; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>R4 Console Login</h1>
    <p>Paste a platform token. Get one with
       <code>r4 auth token show</code> or ask an admin for
       <code>umrs-server admin token generate-jwt</code>.</p>
    <form method="post" action="/auth/web-login">
      <input type="hidden" name="redirect_to" value="{target}">
      <input type="password" name="token" placeholder="Platform token (JWT)"
             autocomplete="off" required autofocus>
      <div class="err"></div>
      <button type="submit">Sign in</button>
    </form>
  </div>
</body>
</html>"""
    return Response(content=html, media_type="text/html", headers=_WEB_LOGIN_HEADERS)


@app.post("/auth/web-login")
async def web_login_submit(request: Request):
    """Validate the submitted token and establish the r4_jwt cookie session."""
    form = await request.form()
    token = str(form.get("token", "")).strip()
    target = _safe_redirect_target(str(form.get("redirect_to", "")))
    client_ip = _client_ip(request)

    if not token:
        raise HTTPException(status_code=400, detail="Missing token")

    try:
        claims = validate_jwt(token)
    except HTTPException:
        await log_audit(
            AuditEntry(
                client_ip=client_ip,
                method="POST",
                path="/auth/web-login",
                action="web_login_failed",
            )
        )
        raise

    await log_audit(
        AuditEntry(
            client_ip=client_ip,
            method="POST",
            path="/auth/web-login",
            user_id=claims["user_id"],
            action="web_login_success",
        )
    )

    response = Response(status_code=302, headers={"Location": target})
    max_age = _cookie_max_age_from_claims(claims)
    response.set_cookie(
        R4_JWT_COOKIE_NAME,
        token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="strict",
        secure=os.environ.get("R4_JWT_COOKIE_SECURE", "false").lower() == "true",
    )
    return response


def _cookie_max_age_from_claims(claims: dict) -> int:
    """Cookie lifetime bounded by token expiry (default/cap 8 hours)."""
    cap = 8 * 3600
    exp = claims.get("exp")
    if isinstance(exp, (int, float)):
        remaining = int(exp - time.time())
        if remaining > 0:
            return min(remaining, cap)
    return cap


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8081,
        log_level="info",
    )
