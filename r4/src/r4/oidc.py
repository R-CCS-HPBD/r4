"""
OIDC Authorization Code Flow with PKCE — server-side callback (A-103).

Implements OAuth 2.0 Authorization Code Grant with PKCE (RFC 7636) for CLI
authentication.  The redirect URI points to the gateway's HTTPS endpoint
(``/auth/cli-callback``), which serves a small HTML trampoline that relays
the authorization code to a temporary localhost HTTP server on the user's
machine.

Flow:
    1. CLI starts a localhost HTTP server and opens the browser.
    2. Browser → Okta authorization endpoint.
    3. Okta redirects to ``https://<gateway>/auth/cli-callback?code=…&state=…``.
    4. The gateway trampoline page JS-redirects to
       ``http://localhost:<port>/callback?code=…&state=…``.
    5. CLI captures the code, exchanges it at Okta's token endpoint with PKCE.

The redirect URI registered in Okta is always HTTPS + FQDN, satisfying
institutional security requirements.  The localhost hop is a browser-local
navigation that never appears as an Okta redirect URI.

Environment variables:
- R4_OIDC_ISSUER:       OIDC issuer URL (required)
- R4_OIDC_CLIENT_ID:    Client ID (required)
- R4_OIDC_SCOPE:        OAuth scopes (default: "openid profile email")
- R4_OIDC_CALLBACK_PORT: Localhost port for code relay (default: 18338)
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import logging
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx

from .auth import AuthManager, TokenInfo
from .errors import ConfigurationError

logger = logging.getLogger(__name__)

# Maximum time to wait for user authorization (seconds)
DEFAULT_AUTH_TIMEOUT = 300

# Default localhost port for the code-relay callback server
DEFAULT_CALLBACK_PORT = 18338


@dataclass
class OIDCConfig:
    """OIDC provider configuration."""

    issuer: str
    client_id: str
    scope: str = "openid profile email"

    # Discovered endpoints (populated from .well-known/openid-configuration)
    authorization_endpoint: Optional[str] = None
    token_endpoint: Optional[str] = None
    userinfo_endpoint: Optional[str] = None

    @classmethod
    def from_env(
        cls,
        issuer: Optional[str] = None,
        client_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> "OIDCConfig":
        """Load configuration from environment variables, with optional overrides.

        Args:
            issuer: Override for R4_OIDC_ISSUER (e.g. from profile store).
            client_id: Override for R4_OIDC_CLIENT_ID (e.g. from profile store).
            scope: Override for R4_OIDC_SCOPE.
        """
        issuer = issuer or os.environ.get("R4_OIDC_ISSUER")
        client_id = client_id or os.environ.get("R4_OIDC_CLIENT_ID")

        if not issuer:
            raise ConfigurationError(
                "R4_OIDC_ISSUER environment variable is required for OIDC authentication.\n"
                "Set it, or run: r4 auth login --oidc --oidc-issuer <URL> --oidc-client-id <ID>"
            )
        if not client_id:
            raise ConfigurationError(
                "R4_OIDC_CLIENT_ID environment variable is required for OIDC authentication.\n"
                "Set it, or run: r4 auth login --oidc --oidc-issuer <URL> --oidc-client-id <ID>"
            )

        scope = scope or os.environ.get("R4_OIDC_SCOPE", "openid profile email")

        return cls(issuer=issuer, client_id=client_id, scope=scope)


@dataclass
class OIDCLoginResult:
    """Result of OIDC authentication."""

    success: bool
    token: Optional[TokenInfo] = None
    error: Optional[str] = None
    error_description: Optional[str] = None


def _generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _build_redirect_uri(gateway_url: str) -> str:
    """Build the HTTPS redirect URI on the gateway for Okta callback."""
    return gateway_url.rstrip("/") + "/auth/cli-callback"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Receives the authorization code relayed from the gateway trampoline."""

    # Populated by the server before starting
    expected_state: str = ""
    result: dict = {}  # noqa: RUF012
    server_ready: threading.Event  # set when result is captured

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        # Validate state to prevent CSRF
        returned_state = params.get("state", [None])[0]
        if returned_state != self.expected_state:
            self._respond_error("Invalid state parameter — possible CSRF attack.")
            self.result.update({"error": "invalid_state", "error_description": "State mismatch"})
            self.server_ready.set()
            return

        if "error" in params:
            error = params["error"][0]
            desc = params.get("error_description", [error])[0]
            self._respond_error(f"Authorization failed: {desc}")
            self.result.update({"error": error, "error_description": desc})
            self.server_ready.set()
            return

        code = params.get("code", [None])[0]
        if not code:
            self._respond_error("No authorization code received.")
            self.result.update({"error": "no_code", "error_description": "Missing code"})
            self.server_ready.set()
            return

        self.result.update({"code": code})
        self._respond_success()
        self.server_ready.set()

    # Suppress access logs to avoid noisy output
    def log_message(self, fmt: str, *args: object) -> None:
        logger.debug(fmt, *args)

    def _respond_success(self) -> None:
        body = (
            "<html><body><h2>Login successful!</h2>"
            "<p>You can close this tab and return to the terminal.</p>"
            "</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def _respond_error(self, message: str) -> None:
        body = f"<html><body><h2>Login failed</h2><p>{message}</p></body></html>"
        self.send_response(400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())


class OIDCAuthCodeFlow:
    """
    OAuth 2.0 Authorization Code Grant with PKCE via server-side callback.

    The redirect URI registered in Okta is the gateway's HTTPS endpoint.
    A lightweight trampoline page on the gateway relays the authorization
    code to a localhost HTTP server where the CLI captures it.
    """

    def __init__(
        self,
        config: Optional[OIDCConfig] = None,
        http_client: Optional[httpx.Client] = None,
        gateway_url: Optional[str] = None,
    ):
        self.config = config or OIDCConfig.from_env()
        self._client = http_client or httpx.Client(timeout=30.0)
        self._discovered = False
        self._gateway_url = gateway_url or os.environ.get("R4_GATEWAY_URL", "")

    def _require_gateway_url(self) -> str:
        """Return the configured gateway URL or raise for browser login flows."""
        if self._gateway_url:
            return self._gateway_url
        raise ConfigurationError(
            "R4_GATEWAY_URL environment variable (or gateway_url param) is required "
            "for OIDC browser login. Set it to your gateway's HTTPS URL, e.g. "
            "https://data0.ai.r-ccs.riken.jp/rccs"
        )

    def _discover_endpoints(self) -> None:
        """Discover OIDC endpoints from .well-known/openid-configuration."""
        if self._discovered:
            return

        well_known_url = urljoin(
            self.config.issuer.rstrip("/") + "/", ".well-known/openid-configuration"
        )

        try:
            response = self._client.get(well_known_url)
            response.raise_for_status()
            data = response.json()

            self.config.authorization_endpoint = data.get("authorization_endpoint")
            self.config.token_endpoint = data.get("token_endpoint")
            self.config.userinfo_endpoint = data.get("userinfo_endpoint")

            if not self.config.authorization_endpoint:
                raise ConfigurationError(
                    f"OIDC provider at {self.config.issuer} does not expose authorization_endpoint"
                )
            if not self.config.token_endpoint:
                raise ConfigurationError(
                    f"OIDC provider at {self.config.issuer} does not expose token_endpoint"
                )

            self._discovered = True
            logger.debug("Discovered OIDC endpoints from %s", well_known_url)

        except httpx.HTTPStatusError as e:
            raise ConfigurationError(
                f"Failed to discover OIDC endpoints: {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            raise ConfigurationError(f"Failed to connect to OIDC provider: {e}") from e

    def login(
        self,
        timeout: int = DEFAULT_AUTH_TIMEOUT,
        callback_port: int = DEFAULT_CALLBACK_PORT,
    ) -> OIDCLoginResult:
        """Run the full Authorization Code + PKCE flow."""
        self._discover_endpoints()

        code_verifier, code_challenge = _generate_pkce_pair()
        state = secrets.token_urlsafe(32)

        # The redirect URI registered in Okta — always HTTPS + FQDN.
        redirect_uri = _build_redirect_uri(self._require_gateway_url())

        # Build authorization URL
        auth_params = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": redirect_uri,
                "scope": self.config.scope,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                # Tell the trampoline page which localhost port to relay to.
                "cli_port": callback_port,
            }
        )
        auth_url = f"{self.config.authorization_endpoint}?{auth_params}"

        # Start localhost server, open browser, wait for code relay
        result, error = self._await_callback(callback_port, state, auth_url, timeout)
        if error:
            return error

        # Exchange code — redirect_uri must match what was sent to Okta.
        return self._exchange_code(result["code"], redirect_uri, code_verifier)

    def _await_callback(
        self,
        callback_port: int,
        state: str,
        auth_url: str,
        timeout: int,
    ) -> tuple[dict, Optional[OIDCLoginResult]]:
        """Start localhost server, open browser, wait for code relay."""
        server_ready, result = threading.Event(), {}
        handler_cls = type(
            "_H",
            (_CallbackHandler,),
            {
                "expected_state": state,
                "result": result,
                "server_ready": server_ready,
            },
        )
        try:
            httpd = http.server.HTTPServer(("127.0.0.1", callback_port), handler_cls)
        except OSError as e:
            msg = f"Cannot bind localhost:{callback_port}: {e}. Set R4_OIDC_CALLBACK_PORT."
            return {}, OIDCLoginResult(success=False, error="port_in_use", error_description=msg)

        threading.Thread(target=httpd.handle_request, daemon=True).start()
        webbrowser.open(auth_url)
        server_ready.wait(timeout=timeout)
        httpd.server_close()

        if not server_ready.is_set():
            return {}, OIDCLoginResult(
                success=False,
                error="timeout",
                error_description="Authorization timed out. Please try again.",
            )
        if "error" in result:
            return {}, OIDCLoginResult(
                success=False,
                error=result["error"],
                error_description=result.get("error_description", "Unknown error"),
            )
        return result, None

    @staticmethod
    def _token_from_response(data: dict) -> TokenInfo:
        """Build a TokenInfo from an OAuth token-endpoint JSON response."""
        expires_at = None
        if "expires_in" in data:
            expires_at = time.time() + data["expires_in"]
        return TokenInfo(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_at=expires_at,
            refresh_token=data.get("refresh_token"),
            scope=data.get("scope"),
        )

    def _exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OIDCLoginResult:
        """Exchange authorization code for access/refresh tokens."""
        return self._post_token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.config.client_id,
                "code_verifier": code_verifier,
            }
        )

    def _post_token_request(
        self,
        data: dict,
        *,
        preserve_refresh: Optional[str] = None,
        save_to: Optional[AuthManager] = None,
    ) -> OIDCLoginResult:
        """POST to the token endpoint and return an OIDCLoginResult."""
        try:
            response = self._client.post(self.config.token_endpoint, data=data)
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            return self._error_result(e)

        if response.status_code == 200:
            token = self._token_from_response(response.json())
            token.refresh_token = token.refresh_token or preserve_refresh
            if save_to:
                save_to.save_token(token)
            return OIDCLoginResult(success=True, token=token)

        err = response.json() if response.content else {}
        return OIDCLoginResult(
            success=False,
            error=err.get("error", "token_request_failed"),
            error_description=err.get(
                "error_description",
                f"Token request failed (HTTP {response.status_code})",
            ),
        )

    @staticmethod
    def _error_result(exc: Exception) -> OIDCLoginResult:
        """Build an OIDCLoginResult from an httpx exception."""
        err: dict = {}
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.content:
            err = exc.response.json()
        return OIDCLoginResult(
            success=False,
            error=err.get("error", "token_request_error"),
            error_description=err.get("error_description", str(exc)),
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()


def oidc_login(
    auth_manager: AuthManager,
    config: Optional[OIDCConfig] = None,
    timeout: int = DEFAULT_AUTH_TIMEOUT,
    print_fn: Callable[[str], None] = print,
    gateway_url: Optional[str] = None,
) -> OIDCLoginResult:
    """
    Perform OIDC Authorization Code login with PKCE.

    Opens the user's default browser to the IdP login page.  Okta redirects
    to the gateway's ``/auth/cli-callback`` trampoline (HTTPS + FQDN),
    which relays the authorization code to a temporary localhost server.

    Args:
        auth_manager: AuthManager to store the token.
        config: Optional OIDC configuration (uses env vars if not provided).
        timeout: Maximum time to wait for user authorization.
        print_fn: Function to print user instructions.
        gateway_url: Gateway HTTPS URL (falls back to R4_GATEWAY_URL env).

    Returns:
        OIDCLoginResult with success status.
    """
    callback_port = int(os.environ.get("R4_OIDC_CALLBACK_PORT", str(DEFAULT_CALLBACK_PORT)))
    flow = OIDCAuthCodeFlow(config, gateway_url=gateway_url)

    try:
        print_fn("Opening browser for login...")
        result = flow.login(timeout=timeout, callback_port=callback_port)

        if result.success and result.token:
            auth_manager.save_token(result.token)
            print_fn("✓ Login successful!")
        else:
            print_fn(f"✗ Login failed: {result.error_description}")

        return result

    finally:
        flow.close()


def refresh_token(
    auth_manager: AuthManager,
    config: Optional[OIDCConfig] = None,
) -> OIDCLoginResult:
    """Refresh an expired access token using the stored refresh token."""
    token = auth_manager.token_store.load(auth_manager.profile)
    if not token or not token.refresh_token:
        return OIDCLoginResult(
            success=False,
            error="no_refresh_token",
            error_description="No refresh token available. Please login again.",
        )

    flow = OIDCAuthCodeFlow(config or OIDCConfig.from_env())
    try:
        flow._discover_endpoints()
        return flow._post_token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": flow.config.client_id,
            },
            preserve_refresh=token.refresh_token,
            save_to=auth_manager,
        )
    except Exception as e:
        return OIDCLoginResult(success=False, error="refresh_error", error_description=str(e))
    finally:
        flow.close()
