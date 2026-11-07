"""Structured error codes for R4 CLI.

Machine-stable exit codes and error identifiers that scripts, CI systems,
and automation can rely on. Human-readable messages may change between
releases; error codes will not.

Exit Code Convention:
    0   — success
    1   — general/unspecified error (legacy default)
    2   — usage/argument error (argparse default)
    10  — authentication failure
    11  — authorization failure (forbidden)
    12  — resource not found
    13  — connectivity / service unavailable
    14  — timeout
    15  — conflict / already exists
    16  — validation error (bad input)
    17  — quota / rate limit exceeded
    20  — configuration error

Error ID Convention:
    Dot-separated: <domain>.<category>.<detail>
    Examples:
        auth.token_expired
        endpoint.not_found
        gateway.unreachable
        rag.upload_too_large
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Numeric Error Code Ranges ---
# Reserved ranges for platform-wide error classification.
# These numeric codes appear in structured error envelopes and audit logs.
#
# | Range     | Domain       |
# |-----------|--------------|
# | 1000–1099 | auth         |
# | 1100–1199 | endpoint     |
# | 1200–1299 | RAG          |
# | 1300–1399 | MCP          |
# | 1400–1499 | storage / S3 |
# | 1500–1599 | Slurm / HPC  |
# | 1600–1699 | model        |
# | 1700–1799 | pipeline     |
# | 1900–1999 | internal     |
# | 2000–2099 | config       |


@dataclass(frozen=True, slots=True)
class CLIError:
    """Structured CLI error with machine-stable identifiers.

    Attributes:
        code:       Process exit code (0=success, 1=general, 2=usage, 10+=specific).
        error_id:   Dot-separated machine-stable identifier (e.g. ``auth.failed``).
        message:    Human-readable description (may change between releases).
        hint:       Optional remediation hint shown to the user.
        retryable:  Whether the operation can be retried with the same inputs.
        severity:   ``error`` (fatal), ``warning`` (degraded), ``info`` (advisory).
        numeric:    Platform-wide numeric error code (see range table above).
    """

    code: int
    error_id: str
    message: str
    hint: str = ""
    retryable: bool = False
    severity: str = "error"
    numeric: int = 0

    def __str__(self) -> str:
        return self.message


# --- Authentication (10) — numeric 1000–1099 ---
AUTH_FAILED = CLIError(10, "auth.failed", "Authentication failed", numeric=1000)
AUTH_TOKEN_EXPIRED = CLIError(
    10, "auth.token_expired", "Authentication token has expired", retryable=True, numeric=1001
)
AUTH_NO_CREDENTIALS = CLIError(10, "auth.no_credentials", "No credentials configured", numeric=1002)

# --- Authorization (11) — numeric 1000–1099 ---
FORBIDDEN = CLIError(11, "auth.forbidden", "Access denied", numeric=1010)
ENDPOINT_FORBIDDEN = CLIError(
    11,
    "endpoint.forbidden",
    "Endpoint provisioning denied",
    hint="Check that your token has endpoint provisioning permissions. Run `r4 auth status`.",
    numeric=1100,
)

# --- Not Found (12) — numeric varies by domain ---
NOT_FOUND = CLIError(12, "resource.not_found", "Resource not found", numeric=1900)
ENDPOINT_NOT_FOUND = CLIError(12, "endpoint.not_found", "Endpoint not found", numeric=1101)
PROJECT_NOT_FOUND = CLIError(12, "project.not_found", "Project not found", numeric=1902)

# --- Connectivity (13) — retryable ---
GATEWAY_UNREACHABLE = CLIError(
    13,
    "gateway.unreachable",
    "Gateway is not reachable",
    hint="Check GATEWAY_URL in your config. Run `r4 doctor` for diagnostics.",
    retryable=True,
    numeric=1903,
)
SERVICE_UNAVAILABLE = CLIError(
    13, "service.unavailable", "Service temporarily unavailable", retryable=True, numeric=1904
)
RAG_UNAVAILABLE = CLIError(
    13, "rag.unavailable", "RAG service is not available", retryable=True, numeric=1200
)
MCP_UNAVAILABLE = CLIError(
    13, "mcp.unavailable", "MCP registry is not available", retryable=True, numeric=1300
)

# --- Timeout (14) — retryable ---
TIMEOUT = CLIError(14, "request.timeout", "Request timed out", retryable=True, numeric=1905)
PROVISION_TIMEOUT = CLIError(
    14,
    "endpoint.provision_timeout",
    "Endpoint provisioning timed out",
    retryable=True,
    numeric=1102,
)

# --- Conflict (15) ---
ALREADY_EXISTS = CLIError(15, "resource.already_exists", "Resource already exists", numeric=1906)

# --- Validation (16) ---
VALIDATION_ERROR = CLIError(16, "input.validation_error", "Input validation failed", numeric=1907)

# --- Quota (17) — retryable ---
RATE_LIMITED = CLIError(
    17, "request.rate_limited", "Rate limit exceeded", retryable=True, numeric=1908
)

# --- Configuration (20) — numeric 2000–2099 ---
CONFIG_ERROR = CLIError(
    20,
    "config.invalid",
    "Configuration error",
    hint="Run `r4 onboard` to reconfigure or `r4 doctor` for diagnostics.",
    numeric=2000,
)
CONFIG_NO_PROJECT = CLIError(
    20,
    "config.no_project",
    "No project selected",
    hint="Run `r4 project switch <id>` to select a project.",
    numeric=2001,
)


def classify_http_error(status_code: int, message: str = "") -> CLIError:
    """Map an HTTP status code to the best matching CLIError."""
    if status_code == 401:
        return AUTH_FAILED
    if status_code == 403:
        return FORBIDDEN
    if status_code == 404:
        return NOT_FOUND
    if status_code == 408 or status_code == 504:
        return TIMEOUT
    if status_code == 409:
        return ALREADY_EXISTS
    if status_code == 422:
        return VALIDATION_ERROR
    if status_code == 429:
        return RATE_LIMITED
    if status_code == 503:
        return SERVICE_UNAVAILABLE
    return CLIError(1, f"http.{status_code}", message or f"HTTP {status_code}")
