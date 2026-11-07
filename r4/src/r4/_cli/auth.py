"""``r4 auth`` umbrella command (Phase 2m, 2026-04-28).

Owns the ``r4 auth {login,logout,status,refresh}`` argparse tree. Dispatch
remaps to the standalone ``login``/``logout``/``auth-status`` handlers
in :mod:`r4.cli.main` (see the ``cmd == "auth"`` branch).
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter

from .base import CommandHandler
from .registry import command


@command
class AuthCommand(CommandHandler):
    name = "auth"
    help = "Authentication: login / logout / status / refresh"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Log in, log out, inspect credentials, or refresh tokens for an R4 profile."
            ),
            epilog=(
                "Examples:\n"
                "  printf '%s' \"$TOKEN\" | r4 auth login --gateway-url http://genai:8080 "
                "--api-key-stdin\n"
                "  r4 auth login --oidc\n"
                "  r4 auth status\n"
                "  r4 auth refresh"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = p.add_subparsers(
            dest="auth_command",
            title="subcommands",
            metavar="<auth-command>",
        )
        sub.required = True

        login_p = sub.add_parser("login", help="Authenticate with API")
        from ..config import gateway_url_arg
        from .login import add_api_key_arguments

        add_api_key_arguments(login_p)
        login_p.add_argument("--oidc", action="store_true", help="Use OIDC browser login")
        login_p.add_argument("--oidc-issuer", help="OIDC issuer URL (saves to profile)")
        login_p.add_argument("--oidc-client-id", help="OIDC client ID (saves to profile)")
        login_p.add_argument("--timeout", type=int, default=300, help="OIDC auth timeout (seconds)")
        login_p.add_argument("--profile", default="default", help="Profile name")
        login_p.add_argument(
            "--gateway-url",
            type=gateway_url_arg,
            help="Gateway base URL (saves to profile)",
        )
        login_p.add_argument("--project", help="Default project ID")
        login_p.add_argument("--model", help="Default model name")
        login_p.add_argument(
            "--umrs-username",
            help="Default UMRS username for job and data operations",
        )
        login_p.add_argument(
            "--full",
            action="store_true",
            help="Extended setup: model prompt, R4.md scaffolding, health checks",
        )

        from .logout import LogoutCommand, add_logout_arguments

        add_logout_arguments(sub.add_parser("logout", help=LogoutCommand.help))

        status_p = sub.add_parser("status", help="Show identity, expiry, and bootstrap freshness")
        status_p.add_argument("--profile", default="default", help="Profile name")

        refresh_p = sub.add_parser(
            "refresh", help="Refresh access token using stored refresh token"
        )
        refresh_p.add_argument("--profile", default="default", help="Profile name")

        return p

    def handle(self, args: Namespace) -> int:
        # Should not be reached — ``cli.main`` remaps ``cmd`` to the
        # specific subcommand handler before this would fire.
        from ._helpers import _die

        _die("`r4 auth` requires a subcommand (login/logout/status/refresh)")
        return 1
