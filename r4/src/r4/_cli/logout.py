"""``r4 logout`` command handler (bug.memo D3 Phase 2g, 2026-04-25)."""

from __future__ import annotations

import os
from argparse import ArgumentParser, Namespace
from typing import TYPE_CHECKING

from .base import CommandHandler
from .registry import command

if TYPE_CHECKING:
    from ..auth import AuthManager
    from ..curve_session import CurveSession


def _resolve_logout_profile(args: Namespace) -> str:
    """Resolve the profile to log out from.

    Priority: --profile flag > ``R4_PROFILE`` > active source > 'default'.
    Before 2026-10-21 the flag counted only when ``sys.argv`` held the bare
    word ``--profile``: ``--profile=alice`` and ``R4_PROFILE=alice`` logged
    out the ACTIVE source's account and left alice's key working.
    """
    import os

    explicit = getattr(args, "profile", None) or os.environ.get("R4_PROFILE")
    if explicit:
        return str(explicit)
    try:
        from ..source import resolve_active_source

        src = resolve_active_source()
        if src:
            return src.name
    except Exception:
        pass
    return "default"


def _clear_umrs_token(profile: str) -> None:
    """Remove the umrs token file so umrs also loses access."""
    try:
        from ..source import _umrs_connections_dir

        token_path = _umrs_connections_dir() / f"{profile}.token"
        if token_path.exists():
            token_path.unlink()
    except Exception:
        pass


def _clear_source_row_key(profile: str) -> bool:
    """Drop the login copy ``r4 auth login`` synced onto the platform source
    row of the same name; True when there was one.

    Before 2026-10-21 logout left it: config resolution prefers the active
    source's key, so every later r4 process and the companion bridge kept
    sending ``Bearer <the logged-out JWT>`` and ``auth status`` still named
    the user. A public source's key is a provider key, not a login — kept.
    """
    from ..source import get_source_store, sync_umrs_token

    store = get_source_store()
    src = store.get(profile)
    if src is None or src.kind != "platform" or not src.api_key:
        return False
    src.api_key = None
    store.add(src)
    sync_umrs_token(src)
    return True


def _stored_login_secrets(profile: str) -> set[str]:
    """The credential values stored for ``profile`` (auth store + its row)."""
    from ..auth import APIKeyManager, TokenStore
    from ..source import get_source_store

    secrets: set[str] = set()
    profiles = APIKeyManager()._load_config().get("profiles", {})
    key = profiles.get(profile, {}).get("api_key") if isinstance(profiles, dict) else None
    token = TokenStore().load(profile)
    row = get_source_store().get(profile)
    row_key = row.api_key if row is not None and row.kind == "platform" else None
    for value in (key, token.access_token if token else None, row_key):
        if isinstance(value, str) and value:
            secrets.add(value)
    return secrets


def _purge_auth_store_copies(secrets: set[str]) -> bool:
    """Delete every auth-store profile (API key or token) holding one of ``secrets``."""
    from ..auth import APIKeyManager, TokenStore

    keys, tokens = APIKeyManager(), TokenStore()
    purged = False
    for name, entry in keys._load_config().get("profiles", {}).items():
        if isinstance(entry, dict) and entry.get("api_key") in secrets:
            purged = keys.delete_api_key(name) or purged
    for name in tokens.list_profiles():
        token = tokens.load(name)
        if token is not None and token.access_token in secrets:
            purged = tokens.delete(name) or purged
    return purged


def _purge_source_row_copies(secrets: set[str]) -> bool:
    """Clear every platform source row (and its umrs token) holding one of ``secrets``."""
    from ..source import get_source_store

    purged = False
    for row in get_source_store().list():
        if row.kind == "platform" and row.api_key in secrets:
            purged = _clear_source_row_key(row.name) or purged
            _clear_umrs_token(row.name)
    return purged


def _purge_login_copies(secrets: set[str]) -> bool:
    """A login is one credential however many profiles or rows it was copied to.

    ``r4 source add lab --url GW`` then ``r4 auth login --gateway-url GW``
    stores the key under profile ``default`` AND on row ``lab`` (login
    dedups by URL). Before 2026-10-21 either logout form left the other
    copy, and every later r4 process kept authenticating with it.
    """
    if not secrets:
        return False
    purged = _purge_auth_store_copies(secrets)
    return _purge_source_row_copies(secrets) or purged


def _profile_session_urls(profile: str) -> set[str]:
    """The profile's gateway and its row's URL, as given and normalized."""
    from ..config import ProfileConfigStore, _normalize_gateway_url
    from ..source import get_source_store

    row = get_source_store().get(profile)
    gateway = ProfileConfigStore().load(profile).get("gateway_url")
    raw = (str(gateway) if gateway else None, row.url if row else None)
    return {u for r in raw if r for u in (r, _normalize_gateway_url(r)) if u}


def _forget_profile_sessions(profile: str) -> list[CurveSession]:
    """Delete the CURVE session files kept for the profile's gateway (local
    only) and return the live ones deleted, so logout can report them."""
    from ..curve_session import CurveSessionManager, session_file_name_for

    forgotten: list[CurveSession] = []
    try:
        for url in _profile_session_urls(profile):
            name = session_file_name_for(url)
            mgr = CurveSessionManager(umrs_base_url=url, session_file_name=name)
            session = mgr.forget_session()
            if session is not None:
                forgotten.append(session)
    except Exception:  # noqa: BLE001 - local cleanup never fails a logout
        pass
    return forgotten


def _clear_s3cfg(profile: str) -> bool:
    """Delete ``<umrs cfg>/s3cfg_<umrs_username>``; True when there was one.

    The legacy ``R4_WRITE_S3CFG=1`` login path writes the profile's storage
    access key and secret there. Before 2026-10-21 logout left the file, so
    the S3 secret outlived the login.
    """
    try:
        from ..config import ProfileConfigStore
        from ..source import _resolve_umrs_cfg_dir

        username = str(ProfileConfigStore().load(profile).get("umrs_username") or "")
        if not username or os.path.basename(username) != username:
            return False
        path = _resolve_umrs_cfg_dir() / f"s3cfg_{username}"
        if not path.is_file():
            return False
        path.unlink()
        return True
    except Exception:  # noqa: BLE001 - local cleanup never fails a logout
        return False


def _revoke(session: CurveSession, proof: tuple[str, str] | None) -> bool:
    """``--revoke-server-session``: revoke ``session`` only on a proven match.

    ``proof`` is ``(credential, home)`` (``AuthManager.revocation_credential``),
    None without the flag. A refusal names why; the local delete stands.
    """
    import sys

    from ..curve_session import CurveSessionManager, revoke_refusal

    if proof is None:
        return False
    refusal = revoke_refusal(session, *proof)
    if refusal is None and CurveSessionManager.revoke_on_server(session, proof[0]):
        print(f"✓ CURVE session {session.session_id} revoked at {session.umrs_base_url}")
        return True
    head = "Not revoking" if refusal else "Could not revoke"
    why = refusal or "the server did not confirm it"
    print(f"⚠ {head} CURVE session {session.session_id} on the server: {why}.", file=sys.stderr)
    return False


def _report_session(session: CurveSession) -> None:
    """Say a CURVE session was deleted here and how to end its server side.

    Stab cycle 3 (2026-10-22): logout sends no credential anywhere. The
    printed command carries a placeholder, never a token.
    """
    import sys
    import time

    from ..curve_session import display_url, revoke_command

    until = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(session.expires_at))
    where = display_url(session.umrs_base_url) or "an unrecorded deployment"
    lines = [
        f"ℹ CURVE session {session.session_id} ({where}) was deleted on this machine, "
        f"the only place its key was kept. Its server side also expires on its own TTL "
        f"({until})."
    ]
    if session.umrs_base_url:
        lines.append(
            "  To revoke it on the server now, with a token for that deployment:\n"
            f"  {revoke_command(session)}"
        )
    print("\n".join(lines), file=sys.stderr)


def _end_sessions(sessions: list[CurveSession], proof: tuple[str, str] | None) -> None:
    """Report every deleted CURVE session once; revoke on request (:func:`_revoke`)."""
    seen: set[str] = set()
    for session in sessions:
        if session.session_id in seen:
            continue
        seen.add(session.session_id)
        if not _revoke(session, proof):
            _report_session(session)


def _clear_stored_login(profile: str, manager: AuthManager) -> bool:
    """Delete every local copy of the login; True when one existed."""
    secrets = _stored_login_secrets(profile)
    cleared = _clear_s3cfg(profile)
    cleared = manager.logout() or cleared
    cleared = _clear_source_row_key(profile) or cleared
    _clear_umrs_token(profile)
    return _purge_login_copies(secrets) or cleared


def _logout(profile: str, *, revoke_server_session: bool = False) -> None:
    """Clear every stored copy of the profile's login and say what happened.

    Never sends a credential (stab cycle 3, 2026-10-22), except with
    ``revoke_server_session`` and a proven match (:func:`_revoke`).
    """
    from ..auth import get_auth_manager

    manager = get_auth_manager(profile)
    proof = manager.revocation_credential() if revoke_server_session else None
    cleared = _clear_stored_login(profile, manager)
    sessions = list(manager.forgotten_curve_sessions) + _forget_profile_sessions(profile)
    _end_sessions(sessions, proof)
    if cleared:
        print(f"✓ Logged out from profile: {profile}")
    else:
        print(f"ℹ Already logged out (profile: {profile})")


_REVOKE_HELP = (
    "Also revoke the deleted CURVE sessions on their server, sending the stored "
    "credential only where the session file records it, its subject is this "
    "login and the credential provably belongs to that deployment (its JWT "
    "gateway_url claim, or the gateway the login that stored this very "
    "credential named with --gateway-url and was not refused with HTTP 401). "
    "Without it logout sends nothing "
    "and prints the revoke command."
)


def add_logout_arguments(p: ArgumentParser) -> None:
    """The arguments of ``r4 logout`` and ``r4 auth logout``."""
    p.add_argument(
        "--profile",
        default=None,
        help="Profile name (default: $R4_PROFILE, else the active source, else 'default')",
    )
    p.add_argument("--revoke-server-session", action="store_true", help=_REVOKE_HELP)


def _run(args: Namespace) -> None:
    revoke = bool(getattr(args, "revoke_server_session", False))
    _logout(_resolve_logout_profile(args), revoke_server_session=revoke)


@command
class LogoutCommand(CommandHandler):
    """``r4 logout`` — clear stored credentials for a profile."""

    name = "logout"
    help = "Clear credentials (local only; sends nothing without --revoke-server-session)"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        add_logout_arguments(p)
        return p

    def handle(self, args: Namespace) -> int:
        _run(args)
        return 0


def cmd_logout(args):
    """Handle logout command."""
    _run(args)
