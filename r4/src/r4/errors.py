"""
R4 SDK Errors.

Exception classes for the R4 SDK (R4).
"""

from __future__ import annotations

from typing import Any, Optional


class R4Error(Exception):
    """Base exception for all R4 errors."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    @property
    def hint(self) -> Optional[str]:
        """Actionable recovery hint for the user, if any."""
        return self.details.get("hint")


#: The sign-in remedy for console (TUI) surfaces. ``hint`` stays the CLI
#: recipe; a console relaying an auth failure shows this instead, so a
#: signed-out user reads where to sign in rather than a bare "Authentication
#: failed" (slash-b#8, 2026-10-21).
TUI_SIGN_IN_HINT = (
    "not signed in, or the gateway rejected the token — /connect (or /login <token>) to sign in"
)


class AuthenticationError(R4Error):
    """Raised when authentication fails (401/403)."""

    def __init__(
        self,
        message: str = "Authentication failed",
        *,
        status_code: Optional[int] = None,
    ):
        hint = (
            "Run `printf '%s' \"$TOKEN\" | r4 auth login --api-key-stdin` to set "
            "credentials (the token comes from a pipe, never an argument). "
            "Generate a token with: umrs-server admin token generate-jwt"
            " --user <you> --project <project-id>"
        )
        if status_code == 403:
            hint = (
                "Access denied. Check that your token has the required project membership, "
                "or point r4 at a project it covers: `r4 config context --project <project-id>`."
            )
        details: dict[str, Any] = {"hint": hint, "status_code": status_code}
        if status_code != 403:
            details["tui_hint"] = TUI_SIGN_IN_HINT
        super().__init__(message, details)

    @property
    def tui_hint(self) -> Optional[str]:
        """The console's sign-in remedy (None for a 403: signing in again does not help)."""
        return self.details.get("tui_hint")


class ConfigurationError(R4Error):
    """Raised when client or provider configuration is invalid."""

    def __init__(self, message: str):
        super().__init__(message)


class RateLimitError(R4Error):
    """Raised when rate limit is exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: Optional[int] = None,
    ):
        super().__init__(message, {"retry_after": retry_after})
        self.retry_after = retry_after


# Typed 503 codes that mean "no model endpoint to serve this"; any other
# typed 503 is a platform service being down, where provisioning does nothing.
_ENDPOINT_UNAVAILABLE_CODES = frozenset(
    {
        "llm.unavailable",
        "endpoints.unavailable",
        "models.unavailable",
        "chatapi.unavailable",
        "provider.unavailable",
    }
)


def _unavailable_hint(error_code: Optional[str]) -> str:
    if error_code and error_code not in _ENDPOINT_UNAVAILABLE_CODES:
        service = error_code.split(".", 1)[0]
        return (
            f"The platform's {service} service is unavailable. This is not a model "
            "endpoint, so provisioning one will not help. Retry shortly; if it "
            "persists, run `r4 doctor` and tell the operator."
        )
    return (
        "No active endpoint available. Run `r4 endpoint up` to provision one, "
        "or `r4 endpoint list` to check endpoint status."
    )


class APIError(R4Error):
    """Raised when API returns an error."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
        error_code: Optional[str] = None,
    ):
        details: dict[str, Any] = {
            "status_code": status_code,
            "response_body": response_body,
        }
        # Typed platform code (x-platform-error-code header, r4-contracts
        # taxonomy) — lets callers branch on `companionvenue.source_not_pushed`
        # instead of parsing message text.
        if error_code:
            details["code"] = error_code
        if status_code == 502:
            details["hint"] = (
                "Gateway received an invalid response from the model endpoint. "
                "Run `r4 endpoint list` to verify the endpoint is healthy, "
                "or `r4 endpoint up <model>` to reprovision."
            )
        elif status_code == 503:
            details["hint"] = _unavailable_hint(error_code)
        elif status_code == 504:
            details["hint"] = (
                "Gateway timed out waiting for the model (common with long thinking "
                "or multi-tool turns). The companion will retry automatically. "
                "If the problem persists, try a faster model or shorten the request."
            )
        super().__init__(message, details)
        self.status_code = status_code
        self.response_body = response_body


class R4ConnectionError(R4Error):
    """Raised when connection to the API fails."""

    def __init__(self, message: str = "Failed to connect to API"):
        super().__init__(message)


class R4TimeoutError(R4Error):
    """Raised when a request times out."""

    def __init__(self, message: str = "Request timed out"):
        super().__init__(message)


class ValidationError(R4Error):
    """Raised when input validation fails."""

    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message, {"field": field})
        self.field = field


class ModelRouteRefusedError(R4Error):
    """The ADR-062 gate refused a public inference route for a sensitive project.

    A subclass so callers can tell this refusal from other client errors: a
    plan agent reported any R4Error as a route refusal (2026-10-09).
    """


class ModelNotFoundError(R4Error):
    """Raised when requested model is not available."""

    def __init__(self, model: str):
        super().__init__(f"Model not found: {model}", {"model": model})
        self.model = model


class ProjectNotFoundError(R4Error):
    """Raised when requested project is not found."""

    def __init__(self, project_id: str):
        super().__init__(f"Project not found: {project_id}", {"project_id": project_id})
        self.project_id = project_id


def gateway_unreachable_hint(gateway_url: str) -> str:
    """Return an actionable one-liner for gateway connectivity failures.

    Distinguishes between a local tunnel (likely not forwarding, or the
    remote gateway process is down) and a remote URL (likely firewall,
    DNS, or the service itself). See bug.memo H1 (2026-04-25).
    """
    from urllib.parse import urlparse

    parsed = urlparse(gateway_url)
    host = parsed.hostname or "?"
    port = parsed.port or ("443" if parsed.scheme == "https" else "80")
    if host in {"localhost", "127.0.0.1", "::1"}:
        return (
            f"Hint: gateway is {gateway_url} (localhost). Check that your "
            f"SSH tunnel is forwarding :{port}, AND that the remote gateway "
            f"process is running (e.g. `ssh HOST 'ss -tlnp | grep :{port}'`)."
        )
    return (
        f"Hint: cannot reach gateway at {gateway_url}. Verify the URL in "
        f"`r4 config show`, network reachability to {host}:{port}, and that "
        f"the gateway service is up."
    )
