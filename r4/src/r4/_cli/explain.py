"""``r4 explain`` — look up meaning and remediation for error codes."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command

# Error ID → (cause, remediation)
_EXPLANATIONS: dict[str, tuple[str, str]] = {
    # Authentication (10)
    "auth.failed": (
        "The platform rejected the supplied credentials.",
        "Run `r4 auth login` to re-authenticate.",
    ),
    "auth.token_expired": (
        "The authentication token has expired.",
        "Run `r4 auth login` to obtain a fresh token.",
    ),
    "auth.no_credentials": (
        "No credentials are configured for the active profile.",
        "Run `r4 auth login` or `r4 onboard` to set up credentials.",
    ),
    # Authorization (11)
    "auth.forbidden": (
        "You do not have permission for this operation.",
        "Check project membership. Contact the project admin if access is needed.",
    ),
    "endpoint.forbidden": (
        "Endpoint provisioning was denied by the platform.",
        "Verify that your token has endpoint provisioning permissions. "
        "Run `r4 auth status` to check.",
    ),
    # Not Found (12)
    "resource.not_found": (
        "The requested resource does not exist.",
        "Check spelling and identifiers. Run the relevant `list` subcommand.",
    ),
    "endpoint.not_found": (
        "No endpoint matching the given name or ID was found.",
        "Run `r4 endpoint list` to see available endpoints.",
    ),
    "project.not_found": (
        "No project matching the given ID was found.",
        "Run `r4 project list` to see available projects.",
    ),
    # Connectivity (13)
    "gateway.unreachable": (
        "The R4 Gateway is not responding.",
        "Verify GATEWAY_URL in your config. Run `r4 doctor` for diagnostics.",
    ),
    "service.unavailable": (
        "A backend service is temporarily unavailable.",
        "Wait briefly and retry. If persistent, check `r4 doctor`.",
    ),
    "rag.unavailable": (
        "The RAG service is not available.",
        "Ensure an endpoint with RAG support is provisioned. Run `r4 doctor` for diagnostics.",
    ),
    "mcp.unavailable": (
        "The MCP registry is not reachable.",
        "Ensure the platform is running. Run `r4 doctor` for diagnostics.",
    ),
    # Timeout (14)
    "request.timeout": (
        "The request to the platform timed out.",
        "Retry after a short wait. For long operations, check status asynchronously.",
    ),
    "endpoint.provision_timeout": (
        "Endpoint provisioning did not complete within the timeout.",
        "Check cluster load with `r4 endpoint list`. "
        "The provisioning may still be in progress — re-check with `r4 endpoint status`.",
    ),
    # Conflict (15)
    "resource.already_exists": (
        "A resource with the same identifier already exists.",
        "Use a different name, or remove the existing resource first.",
    ),
    # Validation (16)
    "input.validation_error": (
        "The input failed validation.",
        "Check the required fields and their formats.",
    ),
    # Quota (17)
    "request.rate_limited": (
        "The rate limit has been exceeded.",
        "Wait and retry. Consider reducing request frequency.",
    ),
    # Configuration (20)
    "config.invalid": (
        "The R4 configuration is invalid or incomplete.",
        "Run `r4 onboard` to reconfigure or `r4 doctor` for diagnostics.",
    ),
    "config.no_project": (
        "No project is selected in the current profile.",
        "Run `r4 project switch <id>` to select a project.",
    ),
    # UMRS structured errors
    "request.bad_request": (
        "The request was malformed.",
        "Check the request payload format and required fields.",
    ),
    "upstream.bad_gateway": (
        "An upstream service returned an invalid response.",
        "The upstream service may be starting up. Retry after a few seconds.",
    ),
    "upstream.timeout": (
        "An upstream service did not respond in time.",
        "The upstream service may be overloaded. Retry after a brief wait.",
    ),
    "server.internal_error": (
        "An unexpected internal error occurred on the server.",
        "Check UMRS server logs if you have access, or report the issue.",
    ),
}


def _lookup_error_meta(error_id: str) -> dict[str, object] | None:
    """Find CLIError metadata by error_id from the error_codes module."""
    from . import error_codes

    for name in dir(error_codes):
        obj = getattr(error_codes, name)
        if isinstance(obj, error_codes.CLIError) and obj.error_id == error_id:
            return {
                "exit_code": obj.code,
                "retryable": obj.retryable,
                "severity": obj.severity,
                "numeric": obj.numeric,
                "hint": obj.hint,
            }
    return None


def _explain(error_id: str, *, as_json: bool = False) -> int:
    """Look up and print an explanation for the given error ID."""
    entry = _EXPLANATIONS.get(error_id)
    if entry is None:
        if as_json:
            print(json.dumps({"error_id": error_id, "found": False}))
        else:
            print(f"Unknown error ID: {error_id}", file=sys.stderr)
            print("\nRun `r4 explain --list` to see all known error IDs.")
        return 1

    cause, remediation = entry
    meta = _lookup_error_meta(error_id) or {}

    if as_json:
        result = {
            "error_id": error_id,
            "found": True,
            "cause": cause,
            "remediation": remediation,
        }
        if meta:
            result["exit_code"] = meta.get("exit_code")
            result["retryable"] = meta.get("retryable", False)
            result["severity"] = meta.get("severity", "error")
            result["numeric"] = meta.get("numeric", 0)
        print(json.dumps(result, indent=2))
    else:
        print(f"Error: {error_id}\n")
        print(f"  Cause:     {cause}")
        print(f"  Fix:       {remediation}")
        if meta:
            retryable = "yes" if meta.get("retryable") else "no"
            print(f"  Retryable: {retryable}")
            print(f"  Severity:  {meta.get('severity', 'error')}")
            if meta.get("numeric"):
                print(f"  Code:      {meta['numeric']}")
    return 0


def _list_errors(*, as_json: bool = False) -> int:
    """List all known error IDs."""
    if as_json:
        entries = [
            {"error_id": eid, "cause": cause, "remediation": fix}
            for eid, (cause, fix) in sorted(_EXPLANATIONS.items())
        ]
        print(json.dumps(entries, indent=2))
    else:
        print("Known error IDs:\n")
        for eid in sorted(_EXPLANATIONS):
            print(f"  {eid}")
    return 0


@command
class ExplainCommand(CommandHandler):
    """``r4 explain`` — look up error codes and remediation steps."""

    name = "explain"
    help = "Explain an error code and show remediation"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        p.add_argument("error_id", nargs="?", help="Error ID to explain (e.g. auth.failed)")
        p.add_argument(
            "--list", "-l", action="store_true", dest="list_all", help="List all known error IDs"
        )
        p.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")
        return p

    def handle(self, args: Namespace) -> int:
        as_json = bool(getattr(args, "as_json", False))
        if getattr(args, "list_all", False):
            return _list_errors(as_json=as_json)
        error_id = getattr(args, "error_id", None)
        if not error_id:
            print("Usage: r4 explain <error_id>", file=sys.stderr)
            print("       r4 explain --list", file=sys.stderr)
            return 2
        return _explain(error_id, as_json=as_json)
