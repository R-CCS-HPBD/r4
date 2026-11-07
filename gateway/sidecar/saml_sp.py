"""
SAML Service Provider for the R4 Gateway Auth Sidecar.

Implements SAML 2.0 SP that authenticates browser users via an institutional
IdP (e.g. Okta SAML), then issues short-lived HS256 JWTs consumed by the
existing /validate pipeline.

Flow:
  1. Browser hits a protected route → NGINX auth_request → sidecar /validate
  2. No valid token or cookie → 401
  3. NGINX error_page 401 → /saml/login
  4. Sidecar redirects to IdP SSO URL with SAML AuthnRequest
  5. User authenticates at IdP
  6. IdP POSTs SAML Response → /saml/acs
  7. Sidecar validates assertion, mints JWT, sets httpOnly session cookie
  8. Redirect to original URL; /validate now reads JWT from cookie

Environment variables — see module-level constants below.
"""

from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jose import jwt as jose_jwt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SAML_ENABLED: bool = os.environ.get(
    "SAML_ENABLED",
    "false",
).lower() in {"1", "true", "yes"}

# IdP (Identity Provider) settings
SAML_IDP_METADATA_URL: str = os.environ.get("SAML_IDP_METADATA_URL", "")
SAML_IDP_ENTITY_ID: str = os.environ.get("SAML_IDP_ENTITY_ID", "")
SAML_IDP_SSO_URL: str = os.environ.get("SAML_IDP_SSO_URL", "")
SAML_IDP_SLO_URL: str = os.environ.get("SAML_IDP_SLO_URL", "")
SAML_IDP_X509_CERT: str = os.environ.get("SAML_IDP_X509_CERT", "")

# SP (Service Provider — this sidecar) settings
SAML_SP_ENTITY_ID: str = os.environ.get("SAML_SP_ENTITY_ID", "")
SAML_SP_ACS_URL: str = os.environ.get("SAML_SP_ACS_URL", "")
SAML_SP_SLO_URL: str = os.environ.get("SAML_SP_SLO_URL", "")

# Session parameters
SAML_SESSION_DURATION: int = int(os.environ.get("SAML_SESSION_DURATION", "28800"))
SAML_COOKIE_NAME: str = os.environ.get("SAML_COOKIE_NAME", "_r4_saml")
SAML_COOKIE_SECURE: bool = os.environ.get(
    "SAML_COOKIE_SECURE",
    "false",
).lower() in {"1", "true", "yes"}
SAML_WANT_ASSERTIONS_SIGNED: bool = os.environ.get(
    "SAML_WANT_ASSERTIONS_SIGNED",
    "true",
).lower() in {"1", "true", "yes"}

# JWT secret for minting session tokens — must match the sidecar JWT_SECRET.
_JWT_SECRET: str = os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
_JWT_DEFAULT_PROJECT: str = os.environ.get("JWT_DEFAULT_PROJECT", "default")


# ---------------------------------------------------------------------------
# python3-saml helpers
# ---------------------------------------------------------------------------


def _onelogin_saml_settings() -> dict:
    """Build the python3-saml settings dict from environment variables."""
    sp: dict = {
        "entityId": SAML_SP_ENTITY_ID,
        "assertionConsumerService": {
            "url": SAML_SP_ACS_URL,
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
        },
        "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
    }
    if SAML_SP_SLO_URL:
        sp["singleLogoutService"] = {
            "url": SAML_SP_SLO_URL,
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        }

    idp: dict = {}
    if SAML_IDP_ENTITY_ID:
        idp["entityId"] = SAML_IDP_ENTITY_ID
    if SAML_IDP_SSO_URL:
        idp["singleSignOnService"] = {
            "url": SAML_IDP_SSO_URL,
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        }
    if SAML_IDP_SLO_URL:
        idp["singleLogoutService"] = {
            "url": SAML_IDP_SLO_URL,
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        }
    if SAML_IDP_X509_CERT:
        idp["x509cert"] = SAML_IDP_X509_CERT

    security: dict = {
        "authnRequestsSigned": False,
        "wantAssertionsSigned": SAML_WANT_ASSERTIONS_SIGNED,
        "wantNameId": True,
        "wantNameIdEncrypted": False,
        "wantAssertionsEncrypted": False,
        "signMetadata": False,
    }

    return {"sp": sp, "idp": idp, "security": security, "strict": True}


def _build_saml_request_data(request: Request) -> dict:
    """Build the request-data dict expected by python3-saml."""
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    scheme = forwarded_proto or request.url.scheme
    return {
        "https": "on" if scheme == "https" else "off",
        "http_host": request.headers.get("host", request.url.netloc),
        "server_port": request.url.port or (443 if scheme == "https" else 80),
        "script_name": request.url.path,
        "get_data": dict(request.query_params),
        "post_data": {},
    }


# ---------------------------------------------------------------------------
# JWT minting
# ---------------------------------------------------------------------------


def _mint_session_jwt(
    *,
    user_id: str,
    email: str,
    name: str,
    project_id: str,
    roles: list[str],
) -> str:
    """Mint a short-lived HS256 JWT from validated SAML attributes."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "email": email,
        "name": name,
        "project_id": project_id,
        "roles": roles,
        "iat": now,
        "exp": now + SAML_SESSION_DURATION,
        "iss": "r4-gateway-saml-sp",
    }
    return jose_jwt.encode(payload, _JWT_SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# Attribute helpers
# ---------------------------------------------------------------------------

_NAMEID_CLAIM = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier"
_EMAIL_CLAIM = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
_NAME_CLAIM = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"


def _first_attr(attrs: dict, *keys: str) -> str | None:
    """Return the first non-empty attribute value matching any key."""
    for key in keys:
        values = attrs.get(key)
        if values:
            return values[0] if isinstance(values, list) else str(values)
    return None


def _is_safe_redirect(url: str) -> bool:
    """Only allow relative paths to prevent open-redirect via RelayState."""
    if not url:
        return False
    parsed = urlparse(url)
    return not parsed.scheme and not parsed.netloc


# ---------------------------------------------------------------------------
# Public: cookie extraction for /validate fallback
# ---------------------------------------------------------------------------


def extract_saml_session_token(request: Request) -> str | None:
    """Extract the SAML session JWT from the cookie, if present."""
    return request.cookies.get(SAML_COOKIE_NAME)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/saml", tags=["saml"])


@router.get("/login")
async def saml_login(
    request: Request,
    redirect_to: str | None = None,
):
    """Initiate SAML authentication by redirecting to the IdP."""
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="python3-saml is not installed; SAML auth is unavailable",
        ) from None

    req_data = _build_saml_request_data(request)
    saml_auth = OneLogin_Saml2_Auth(req_data, _onelogin_saml_settings())

    relay_state = redirect_to or request.headers.get("referer", "/")
    sso_url = saml_auth.login(return_to=relay_state)
    return RedirectResponse(url=sso_url, status_code=302)


@router.post("/acs")
async def saml_acs(request: Request):
    """Assertion Consumer Service — validates the IdP SAML response."""
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="python3-saml is not installed; SAML auth is unavailable",
        ) from None

    form_data = await request.form()
    req_data = _build_saml_request_data(request)
    req_data["post_data"] = dict(form_data)

    saml_auth = OneLogin_Saml2_Auth(req_data, _onelogin_saml_settings())
    saml_auth.process_response()
    errors = saml_auth.get_errors()

    if errors:
        reason = saml_auth.get_last_error_reason()
        logger.warning("SAML assertion failed: %s (reason: %s)", errors, reason)
        raise HTTPException(
            status_code=401,
            detail=f"SAML validation failed: {', '.join(errors)}",
        )

    if not saml_auth.is_authenticated():
        raise HTTPException(status_code=401, detail="SAML authentication failed")

    attrs = saml_auth.get_attributes()
    name_id = saml_auth.get_nameid()

    user_id = _first_attr(attrs, "sub", "uid", _NAMEID_CLAIM) or name_id or ""
    email = _first_attr(attrs, "email", _EMAIL_CLAIM) or name_id or ""
    name = _first_attr(attrs, "displayName", _NAME_CLAIM, "firstName") or email
    raw_roles = attrs.get("roles", attrs.get("groups", []))
    roles = raw_roles if isinstance(raw_roles, list) else [raw_roles]
    project_id = _first_attr(attrs, "project_id", "project") or _JWT_DEFAULT_PROJECT

    session_jwt = _mint_session_jwt(
        user_id=user_id,
        email=email,
        name=name,
        project_id=project_id,
        roles=roles,
    )

    relay_state = str(form_data.get("RelayState", "/"))
    if not _is_safe_redirect(relay_state):
        relay_state = "/"

    response = RedirectResponse(url=relay_state, status_code=302)
    response.set_cookie(
        key=SAML_COOKIE_NAME,
        value=session_jwt,
        max_age=SAML_SESSION_DURATION,
        httponly=True,
        secure=SAML_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    logger.info("SAML login success for user=%s email=%s", user_id, email)
    return response


@router.get("/metadata")
async def saml_metadata():
    """Return SP SAML metadata XML for IdP registration."""
    try:
        from onelogin.saml2.settings import OneLogin_Saml2_Settings
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="python3-saml is not installed; SAML auth is unavailable",
        ) from None

    settings = OneLogin_Saml2_Settings(
        _onelogin_saml_settings(),
        sp_validation_only=True,
    )
    metadata = settings.get_sp_metadata()
    errors = settings.validate_metadata(metadata)
    if errors:
        raise HTTPException(status_code=500, detail=f"SP metadata invalid: {errors}")

    return Response(content=metadata, media_type="application/xml")


@router.get("/logout")
async def saml_logout():
    """Clear the SAML session cookie."""
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(key=SAML_COOKIE_NAME, path="/")
    return response
