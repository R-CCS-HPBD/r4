"""``r4 refresh`` — refresh an access token using the stored refresh token.

This is a stub for Phase 2 token lifecycle.  Currently it validates
that credentials exist and displays their expiry.  Actual refresh-token
exchange will be implemented when the UMRS token-exchange endpoint
(``REFRESH_TOKEN``) is available.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command

#: The token comes from a pipe, never an argument (G-1; 2026-10-15).
_RELOGIN_HINT = "Re-run `printf '%s' \"$NEW_TOKEN\" | r4 auth login --api-key-stdin` to {verb}."


@command
class RefreshCommand(CommandHandler):
    """``r4 refresh`` — refresh access token."""

    name = "refresh"
    help = "Refresh access token"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        p.add_argument("--profile", default="default", help="Profile name")
        return p

    def handle(self, args: Namespace) -> int:
        from ..auth import decode_jwt_claims, get_auth_manager

        profile = args.profile
        auth = get_auth_manager(profile)

        # Mirror auth status: fall back to the profile serving the active
        # source when the requested one has no credentials (e.g. the
        # legacy "default" slug vs the source-matched rccs-cloud profile).
        if not auth.is_authenticated():
            try:
                from ..source import resolve_active_source

                active = resolve_active_source()
            except Exception:
                active = None
            if active is not None and active.name != profile:
                alt = get_auth_manager(active.name)
                if alt.is_authenticated():
                    profile = active.name
                    auth = alt

        if not auth.is_authenticated():
            print(
                f"✗ No credentials for profile '{profile}'. Run `r4 auth login` first.",
                file=sys.stderr,
            )
            return 1

        # Check if token is a JWT and display expiry.
        token_info = auth.token_store.load(profile)
        raw = token_info.access_token if token_info else auth.api_key_manager.get_api_key(profile)

        if raw:
            claims = decode_jwt_claims(raw)
            if claims and "exp" in claims:
                import time

                remaining = claims["exp"] - time.time()
                if remaining > 0:
                    hours = int(remaining // 3600)
                    mins = int((remaining % 3600) // 60)
                    print(f"Current token expires in {hours}h {mins}m")
                    print("Token refresh via UMRS is not yet implemented (Phase 2).")
                    print(_RELOGIN_HINT.format(verb="update credentials"))
                    return 0
                else:
                    print("Token is EXPIRED.")
                    print(_RELOGIN_HINT.format(verb="re-authenticate"))
                    return 1

        print("Token refresh via UMRS is not yet implemented (Phase 2).")
        print(_RELOGIN_HINT.format(verb="update credentials"))
        return 0
