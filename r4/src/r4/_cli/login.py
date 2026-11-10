"""``r4 login`` command handler (bug.memo D3 Phase 2g, 2026-04-25).

Ported from ``r4.cli.cmd_login``. Behaviour is byte-identical; the
helper functions still live in ``r4.cli`` and are imported lazily
inside :meth:`handle` to avoid bootstrap-time circular imports.
"""

from __future__ import annotations

import logging
import re
import sys
from argparse import ArgumentParser, Namespace
from typing import Any

from .base import CommandHandler
from .registry import command

logger = logging.getLogger(__name__)


@command
class LoginCommand(CommandHandler):
    """``r4 login`` — configure credentials for a profile."""

    name = "login"
    help = "Authenticate with API"

    def register(self, subparsers) -> ArgumentParser:
        from ..config import gateway_url_arg

        p = subparsers.add_parser(self.name, help=self.help)
        add_api_key_arguments(p)
        p.add_argument("--oidc", action="store_true", help="Use OIDC browser login")
        p.add_argument("--oidc-issuer", help="OIDC issuer URL (saves to profile)")
        p.add_argument("--oidc-client-id", help="OIDC client ID (saves to profile)")
        p.add_argument(
            "--provider",
            help="OIDC provider preset (gakunin|orcid|github) — fills issuer/scope",
        )
        p.add_argument("--timeout", type=int, default=300, help="OIDC auth timeout (seconds)")
        p.add_argument("--profile", default="default", help="Profile name")
        p.add_argument(
            "--gateway-url",
            type=gateway_url_arg,
            help="Gateway base URL (saves to profile)",
        )
        p.add_argument("--project", help="Default project ID")
        p.add_argument("--model", help="Default model name")
        p.add_argument(
            "--umrs-username",
            help="Default UMRS username for job and data operations",
        )
        p.add_argument(
            "--full",
            action="store_true",
            help="Extended setup: model prompt, R4.md scaffolding, health checks",
        )
        return p

    def handle(self, args: Namespace) -> int:
        from ._helpers import _configure_profile_auth

        # The key from --api-key-stdin / --api-key-env / --api-key (2026-10-15)
        if not _resolve_login_api_key(args):
            return 1

        # Extract settings embedded in JWT before prompting/saving
        _populate_args_from_jwt(args)

        # Sanitize profile name — it becomes a filesystem path component
        # (<profile>.json / <profile>.token). Tokens forwarded through chat
        # apps (Slack etc.) can silently embed invisible unicode separators
        # (e.g. U+2028) inside the profile claim; treat such tokens as valid
        # but never as filename characters.
        raw_profile = getattr(args, "profile", None)
        args.profile = _sanitize_profile_name(raw_profile)
        if raw_profile and args.profile != raw_profile:
            print(f"  ⚠ sanitized profile name to '{args.profile}' (invisible unicode removed)")

        # D4: expand a named OIDC provider preset into issuer/scope/client_id
        _apply_provider_preset(args)

        # Prompt for missing inputs when running interactively
        _prompt_missing_login_inputs(args)

        # --- Save profile settings if provided (merged init behavior) ---
        _save_login_profile_settings(args)

        # --oidc-issuer or --oidc-client-id implies --oidc
        oidc = (
            args.oidc
            or bool(getattr(args, "oidc_issuer", None))
            or bool(getattr(args, "oidc_client_id", None))
        )

        if not _configure_profile_auth(
            args.profile,
            api_key=args.api_key,
            oidc=oidc,
            timeout=args.timeout,
            prompt_if_missing=True,
        ):
            print(
                f"✗ No credentials configured for profile: {args.profile}",
                file=sys.stderr,
            )
            return 1
        _record_login_home(args)

        # --- Display JWT claims for UX ---
        _display_jwt_info(args.profile, oidc=oidc)

        # --- Auto-populate umrs_username from JWT if not explicitly set ---
        _auto_populate_umrs_username(args)

        # --- Verify identity via Gateway whoami (a 401 fails the command) ---
        if not _report_login_outcome(args.profile, _verify_identity_via_whoami(args.profile)):
            return 1
        _finish_login(args)
        return 0


def _finish_login(args: Namespace) -> None:
    """After a login the gateway did not reject: context, umrs sync, sessions."""
    from ..config import get_config
    from ._helpers import _print_active_context

    active_config = get_config()
    if active_config.profile == args.profile:
        _print_active_context(active_config)
    else:
        print(f"  Active profile: {active_config.profile}")
        print("  To use this profile:")
        print(f"    export R4_PROFILE={args.profile}")
        print("  Or per-command:")
        print(f"    R4_PROFILE={args.profile} r4 health")

    _sync_umrs_client_profile(args)
    _ensure_umrs_user_config(args)
    _try_zmq_session_bootstrap_with_profile(args.profile)
    _sync_projects_from_server(args.profile)
    if getattr(args, "full", False):
        _run_full_setup(args.profile)


def add_api_key_arguments(parser: ArgumentParser) -> None:
    """The key flags shared by ``r4 login`` and ``r4 auth login``.

    Same semantics as ``r4 source add``: ``--api-key-stdin`` reads the key
    from a pipe (or a hidden prompt on a terminal) and never echoes it,
    ``--api-key-env VAR`` reads it from the environment, and ``--api-key``
    still works with the G-1 warning (it leaves the key in shell history and
    process lists).
    """
    parser.add_argument(
        "--api-key",
        "-k",
        help="API key or onboarding token. WARNING: visible in shell history and "
        "process lists — prefer --api-key-stdin or --api-key-env",
    )
    parser.add_argument(
        "--api-key-stdin",
        action="store_true",
        help="Read the API key or token from stdin (a pipe), or a hidden prompt",
    )
    parser.add_argument(
        "--api-key-env",
        metavar="VAR",
        help="Read the API key or token from environment variable VAR",
    )


#: Words a user types when asking for usage, never a credential (2026-10-21:
#: the console's ``/login help`` stored "help" as the key and said login ok).
_HELP_WORDS = frozenset({"help", "-h", "--help", "?", "usage"})


def _non_credential_reason(key: str | None) -> str | None:
    """Why ``key`` cannot be an API key or token, or None when it may be one.

    Only obvious non-credentials: a help word, a flag, or text with spaces.
    Any other string is stored as before — opaque gateway API keys have no
    fixed shape, and the gateway (``r4 auth status``) is the judge.
    """
    if key is None:
        return None
    if key.strip().lower() in _HELP_WORDS:
        return "that is a help request, not a token"
    if key.startswith("-"):
        return "it looks like a command-line flag, not a token"
    if any(ch.isspace() for ch in key.strip()):
        return "a token has no spaces"
    return None


def _refuse_non_credential(key: str | None) -> bool:
    """Print the refusal (never the key) and return True when ``key`` is refused."""
    reason = _non_credential_reason(key)
    if reason is None:
        return False
    print(
        f"Error: refusing to store that as a credential — {reason}. Nothing was saved.\n"
        "  usage: printf '%s' \"$TOKEN\" | r4 auth login --api-key-stdin",
        file=sys.stderr,
    )
    return True


def _resolve_login_api_key(args: Namespace) -> bool:
    """Put the key from the chosen key flag into ``args.api_key``.

    False (after printing the error) when ``--api-key-stdin`` or
    ``--api-key-env`` yield no key, or the key is an obvious non-credential
    (a help word, a flag, text with spaces). The key itself is never printed.
    """
    from .source import _resolve_api_key_material

    if not (getattr(args, "api_key_stdin", False) or getattr(args, "api_key_env", None)):
        if getattr(args, "api_key", None):
            from .source import _PLAIN_FLAG_WARNING

            print(f"Warning: {_PLAIN_FLAG_WARNING}", file=sys.stderr)
        return not _refuse_non_credential(getattr(args, "api_key", None))
    key, _warning, error = _resolve_api_key_material(args)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return False
    if _refuse_non_credential(key):
        return False
    args.api_key = key
    return True


def _apply_provider_preset(args: Namespace) -> None:
    """Expand --provider=<name> into --oidc-issuer/--oidc-client-id/--oidc-scope.

    The preset never overwrites an explicit ``--oidc-client-id`` given on the
    command line. After fill, ``--provider`` doubles as implying ``--oidc``.
    """
    name = getattr(args, "provider", None)
    if not name:
        return

    from ..oidc_providers import get_provider
    from ._helpers import _die

    try:
        provider = get_provider(name)
    except KeyError:
        _die(
            f"Unknown OIDC provider: {name}. Presets: gakunin, orcid, github.",
            action="auth login",
            error_id="auth.provider_unknown",
        )

    if not getattr(args, "oidc_issuer", None):
        args.oidc_issuer = provider.issuer
    if not getattr(args, "oidc_scope", None):
        args.oidc_scope = provider.scope
    if not getattr(args, "oidc_client_id", None):
        _die(
            f"--oidc-client-id is required when using --provider {provider.name}".replace(
                "  ", " "
            ),
            action="auth login",
            error_id="auth.provider_missing_client_id",
            hint=provider.registration_hint,
        )
    args.oidc = True


_PROFILE_NAME_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_profile_name(name: str | None) -> str | None:
    """Return *name* stripped to filesystem-safe profile characters.

    Profile names become path components (``<profile>.json`` / ``.token`` /
    UMRS connection files). Claim strings pasted through rich-text channels
    (Slack, Docs, chat apps) can silently embed invisible separators such as
    U+2028 LINE SEPARATOR — never allow those into filenames.
    """
    if not name:
        return name
    return _PROFILE_NAME_SAFE_CHARS.sub("", name.strip()) or "default"


def _populate_args_from_jwt(args: Namespace) -> None:
    """Extract gateway_url, project, and profile from JWT claims.

    When ``--api-key`` is provided, decode the JWT and backfill any
    args fields that the user did not set explicitly via flags.
    This lets an admin embed connection hints in the token so end users
    can login with just ``printf '%s' "$JWT" | r4 auth login --api-key-stdin``.
    OIDC tokens lack these claims — the client falls through to flags.
    """
    api_key = getattr(args, "api_key", None)
    if not api_key:
        return

    from ..auth import decode_jwt_claims

    claims = decode_jwt_claims(api_key)
    if claims is None:
        return

    # Backfill profile first — it affects where everything else is stored.
    if not getattr(args, "profile", None) or args.profile == "default":
        jwt_profile = claims.get("profile")
        if jwt_profile:
            args.profile = jwt_profile
            print(f"  ← profile '{jwt_profile}' (from JWT)")
    if not getattr(args, "gateway_url", None):
        _backfill_jwt_claim(args, "gateway_url", "gateway_url", claims, "gateway-url")
    if not getattr(args, "project", None):
        _backfill_jwt_claim(args, "project", "project_id", claims, "project")


def _backfill_jwt_claim(
    args: Namespace,
    attr: str,
    claim_key: str,
    claims: dict,
    label: str,
) -> None:
    """Set *attr* on *args* from *claims[claim_key]* if present, and print."""
    val = claims.get(claim_key)
    if val:
        setattr(args, attr, val)
        print(f"  \u2190 {label} '{val}' (from JWT)")


def _prompt_missing_login_inputs(args: Namespace) -> None:
    """If stdin is a TTY and key fields are absent, prompt for them interactively."""
    import sys

    # Pre-fill args from stored profile when not explicitly set by flags.
    from ..config import ProfileConfigStore

    stored = ProfileConfigStore().load(args.profile)
    # The gateway this login names itself (flag or JWT claim) — where it
    # belongs; the prefill below is only the profile's last value.
    args.login_gateway = getattr(args, "gateway_url", None)
    # Where the login this one replaces was recorded (a token refresh keeps it).
    args.recorded_home = _previous_login_home(args.profile, stored)
    # What it stored: a credential this login merely reuses is not bound.
    args.prior_credentials = _prior_credentials(args.profile)
    if not getattr(args, "gateway_url", None) and stored.get("gateway_url"):
        args.gateway_url = stored["gateway_url"]
    if not getattr(args, "project", None) and stored.get("project"):
        args.project = stored["project"]

    if not sys.stdin.isatty():
        return

    _prompt_gateway_and_project(args)
    _prompt_auth_method(args)


def _prompt_gateway_and_project(args: Namespace) -> None:
    """Prompt for gateway URL and project ID if not set."""
    if not getattr(args, "gateway_url", None):
        # The default is where a login naming no gateway is recorded
        # (_unnamed_login_home: the profile's source, the home the replaced
        # login recorded, R4_GATEWAY_URL or the active source), not a fixed
        # localhost (2026-10-15). Enter names nothing, as a piped login; a
        # typed URL is where this login belongs.
        default = _unnamed_login_home(args) or "http://localhost:8080"
        val = input(f"Gateway URL [{default}]: ").strip()
        args.gateway_url = val or default
        if val:
            args.login_gateway = val
    if not getattr(args, "project", None):
        val = input("Default project ID (press Enter to skip): ").strip()
        if val:
            args.project = val


def _prompt_auth_method(args: Namespace) -> None:
    """Prompt for auth method if neither api_key nor oidc is set."""
    if getattr(args, "api_key", None) or getattr(args, "oidc", False):
        return
    from ..auth import get_auth_manager

    auth = get_auth_manager(args.profile)
    if not auth.is_authenticated():
        ans = input("Auth method — (1) API key / JWT  (2) OIDC browser [1]: ").strip() or "1"
        if ans == "2":
            args.oidc = True


def _previous_login_home(profile: str, stored: dict) -> str | None:
    """Where the login a new login replaces records it belongs, else None.

    ``login_gateway_url``, else the ``gateway_url`` claim of its key — only
    while a login is stored: after ``r4 auth logout`` the next login is a
    new one, not a refresh.
    """
    from ..config import _jwt_gateway_claim

    key = stored.get("api_key")
    if not key and not _has_stored_token(profile):
        return None
    home = stored.get("login_gateway_url") or _jwt_gateway_claim(key)
    return str(home) if home else None


def _prior_credentials(profile: str) -> tuple[str, ...] | None:
    """The credentials ``profile`` stores before this login (None = unreadable)."""
    from ..auth import AuthManager

    try:
        return AuthManager(profile=profile).stored_credentials()
    except Exception:  # noqa: BLE001 — fail closed: this login binds nothing
        return None


def _login_credential(args: Namespace) -> str:
    """The credential this login stored, else "" (none, or one it reused).

    The key it was given (``--api-key``/``-stdin``/``-env``), else the token
    or prompted key it newly stored (:func:`_prior_credentials`).
    """
    from ..auth import AuthManager

    if getattr(args, "api_key", None):
        return str(args.api_key)
    prior = getattr(args, "prior_credentials", None)
    current = AuthManager(profile=args.profile).stored_login_credential()
    return "" if prior is None or current in prior else current


def _login_binding(args: Namespace, named: object) -> str | None:
    """The :data:`r4.auth.LOGIN_BINDING` this login records, else None.

    Only a login that NAMES its gateway (flag, JWT claim, prompt) binds the
    credential it stored to it: ``r4 logout --revoke-server-session`` sends a
    claimless credential only to the gateway bound to that very credential
    (stab cycle 3, round 5). An inferred home binds nothing.
    """
    from ..auth import credential_digest

    credential = _login_credential(args) if named else ""
    return credential_digest(credential) if credential else None


def _has_stored_token(profile: str) -> bool:
    """Whether an OIDC token is stored for ``profile`` (an unreadable store
    counts as one, so its login's record is kept)."""
    from ..auth import TokenStore

    try:
        return TokenStore().load(profile) is not None
    except Exception:  # noqa: BLE001 — fail closed: keep the recorded home
        return True


def _profile_source_url(profile: str) -> str | None:
    """The URL of the platform source a login's profile is named after
    (``r4 login --profile <source>``: a login made for that source), unless
    it is the profile r4 runs under."""
    import os

    from ..config import DEFAULT_PROFILE
    from ..source import get_source_store

    try:
        if profile == (os.environ.get("R4_PROFILE") or DEFAULT_PROFILE):
            return None
        src = get_source_store().get(profile)
    except Exception:  # noqa: BLE001 — an unknown home is recorded as none
        return None
    return src.url if src is not None and src.kind == "platform" else None


def _effective_login_gateway(profile: str) -> str | None:
    """The gateway a login for ``profile`` is used against when it names none.

    The platform source the profile is named after
    (:func:`_profile_source_url`); else the gateway r4 resolves for the
    profile right now (``R4_GATEWAY_URL``, the active source, the profile's
    own) — but not the built-in ``http://localhost:8080`` unless
    ``R4_GATEWAY_URL`` or the active source names it (:func:`_names_gateway`).
    """
    from ..config import Config

    source_url = _profile_source_url(profile)
    if source_url:
        return source_url
    try:
        gateway = Config.from_env(profile=profile).gateway_url
        return gateway if _names_gateway(gateway) else None
    except Exception:  # noqa: BLE001 — an unknown home is recorded as none
        return None


def _unnamed_login_home(args: Namespace) -> str | None:
    """Where a login that names no gateway belongs, else None (unknown).

    The platform source the profile is named after; else the home the login
    it replaces recorded (a token refresh keeps it even while another
    deployment is active — NEW-A-2); else :func:`_effective_login_gateway`.
    """
    return (
        _profile_source_url(args.profile)
        or getattr(args, "recorded_home", None)
        or _effective_login_gateway(args.profile)
    )


def _names_gateway(gateway: str | None) -> bool:
    """Whether ``gateway`` is evidence of where a login belongs (NEW-A-1).

    Any address but the built-in ``http://localhost:8080`` is. That one is
    only when ``R4_GATEWAY_URL`` or an active platform source names it: a
    profile or repo TOML often kept it as the old prompt default, and a
    Config has it when nothing is configured at all.
    """
    import os

    from ..config import _BUILTIN_GATEWAY_URL
    from ..source import resolve_active_source
    from ..source_context import same_deployment

    if not gateway:
        return False
    if not same_deployment(gateway, _BUILTIN_GATEWAY_URL) or os.environ.get("R4_GATEWAY_URL"):
        return True
    active = resolve_active_source()
    return active is not None and active.kind == "platform"


def _record_login_home(args: Namespace) -> None:
    """Record where this login belongs: ``login_gateway_url`` in the profile.

    The gateway the login names (``--gateway-url``, the JWT claim, a URL
    typed at the prompt), else :func:`_unnamed_login_home`. With none,
    nothing is recorded and the home is unknown (NEW-A-1: the built-in
    default is no evidence). ``Config.from_env`` then never sends this
    login, its project or its user to another deployment — also for a piped
    ``--api-key-stdin`` login, which prompts for nothing (onboarding plan
    S1, 2026-10-15). A login naming its gateway also binds the credential
    it stored to it (:func:`_login_binding`); any other drops the binding.
    """
    from ..auth import LOGIN_BINDING
    from ..config import ProfileConfigStore, _normalize_gateway_url

    named = getattr(args, "login_gateway", None)
    gateway = named or _unnamed_login_home(args)
    home = _normalize_gateway_url(str(gateway)) if gateway else None
    binding = _login_binding(args, named and home)
    ProfileConfigStore().save(args.profile, login_gateway_url=home, **{LOGIN_BINDING: binding})
    if home and not named:
        _note_kept_home(args.profile, home)


def _note_kept_home(profile: str, home: str) -> None:
    """Say so when a login naming no gateway stayed with the home its
    profile recorded while r4 targets another deployment, and how to move it."""
    from ..source_context import same_deployment

    target = _effective_login_gateway(profile)
    if not target or same_deployment(target, home):
        return
    flag = "" if profile == "default" else f" --profile {profile}"
    print(
        f"Note: this login is kept for {home}, where the profile's login was made. "
        f"To use it for {target} instead: printf '%s' \"$TOKEN\" | "
        f"r4 auth login{flag} --gateway-url {target} --api-key-stdin",
        file=sys.stderr,
    )


def _sync_projects_from_server(profile: str) -> None:
    """Adopt server-visible projects into the local registry after login
    (maintainer ask 2026-10-20: project and circle state syncs from the
    server at login). Best-effort — a server/network failure never breaks
    the login; existing entries and the current selection stay untouched."""
    try:
        from ..client import Client
        from ..config import Config
        from ..project import ProjectConfig, get_project_manager
        from ..project_sync import sync_server_projects

        result = sync_server_projects(
            Client(config=Config.from_env(profile)),
            get_project_manager(),
            project_config_cls=ProjectConfig,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort post-login step
        print(f"  ⚠ project sync skipped: {str(exc)[:140]}", file=sys.stderr)
        return
    adopted = result["adopted"]
    if adopted:
        print(f"  ✓ Synced {len(adopted)} project(s) from server: {', '.join(adopted)}")
    elif result["known"]:
        print(f"  ✓ Projects in sync from server ({len(result['known'])} already known)")


def _auto_populate_umrs_username(args: Namespace) -> None:
    """Extract user identity from JWT claims and save as umrs_username.

    Skipped when the user explicitly set ``--umrs-username`` or the
    ``UMRS_USERNAME`` env var.  Uses ``sub`` (preferred) or ``email``
    (fallback), matching the same precedence as Gateway's
    ``resolve_user_id()`` and UMRS ZMQ session ``_extract_jwt_identity()``.
    """
    import os

    # Don't override explicit user setting
    if getattr(args, "umrs_username", None) or os.environ.get("UMRS_USERNAME"):
        return

    from ..auth import get_auth_manager
    from ..config import ProfileConfigStore

    store = ProfileConfigStore()
    stored = store.load(args.profile)

    auth = get_auth_manager(args.profile)
    claims = _resolve_jwt_claims(auth, args.profile)

    if claims is None:
        return

    username = claims.get("sub") or claims.get("user_id") or claims.get("email")
    if not username:
        return

    # Always update — the user may be re-logging with a different identity
    # on the same profile (e.g. switching from mnagaso to test_user_1).
    prev = stored.get("umrs_username")
    if prev == username:
        return  # No change

    store.save(args.profile, **{**stored, "umrs_username": username})
    print(f"  ✓ UMRS username: {username} (from JWT)")


def _resolve_jwt_claims(auth: object, profile: str) -> dict | None:
    """Decode JWT claims from the profile's stored token or API key."""
    from ..auth import decode_jwt_claims

    token = auth.token_store.load(profile)
    if token is not None:
        return decode_jwt_claims(token.access_token)
    api_key = auth.api_key_manager.get_api_key(profile)
    return decode_jwt_claims(api_key) if api_key else None


REJECTED = "rejected"
VERIFIED = "verified"
UNVERIFIED = "unverified"


def _verify_identity_via_whoami(profile: str) -> str:
    """Server-verify the logged-in identity via the live usage identity echo.

    Returns ``"verified"``, ``"rejected"`` (the gateway answered 401: the
    key is not valid there) or ``"unverified"`` (no gateway, unreachable,
    any other answer). If the server-resolved ``user_id`` differs from the
    locally stored ``umrs_username`` (e.g. OIDC subject → local username
    mapping), the profile is updated. Never raises: a login is saved even
    when it cannot be checked.
    """
    from ..config import ProfileConfigStore

    store = ProfileConfigStore()
    stored = store.load(profile)
    gateway_url = stored.get("gateway_url", "")
    if not gateway_url:
        return UNVERIFIED

    from ..auth import get_auth_manager

    auth_header = get_auth_manager(profile).get_auth_header()
    if not auth_header:
        return UNVERIFIED
    data = _whoami(str(gateway_url), auth_header)
    if not isinstance(data, dict):
        return data or UNVERIFIED
    server_user_id = data.get("user_id", "")
    if not server_user_id:
        return UNVERIFIED
    if server_user_id != stored.get("umrs_username", ""):
        store.save(profile, **{**stored, "umrs_username": server_user_id})
    print(f"  ✓ Identity verified: {server_user_id}")
    return VERIFIED


def _whoami(gateway_url: str, auth_header: dict[str, str]) -> dict[str, object] | str | None:
    """The gateway's identity echo, ``"rejected"`` on HTTP 401, else None."""
    import json as _json
    import urllib.error
    import urllib.request

    try:
        from .._http import probe_api_prefix
        from ..config import _normalize_gateway_url

        url = _normalize_gateway_url(gateway_url).rstrip("/")
        # ``/gw/auth/whoami`` never had a server route (plan H-10.2); the
        # usage identity echo answers the same verified ``user_id``.
        req = urllib.request.Request(f"{url}{probe_api_prefix(url)}/usage/me", method="GET")
        for k, v in auth_header.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode())
        return data if isinstance(data, dict) else None
    except urllib.error.HTTPError as exc:
        return REJECTED if exc.code == 401 else None
    except Exception:
        return None  # Non-fatal — Gateway may be unreachable


def _report_login_outcome(profile: str, verdict: object) -> bool:
    """Say what the login achieved; False when the gateway rejected the key.

    Before 2026-10-21 a key the gateway answered 401 to printed
    "✓ Credentials configured" and exited 0. It is still saved (offline
    login is by design), but the command now says so and fails.
    """
    if verdict == REJECTED:
        _unbind_rejected_login(profile)
        print(
            f"✗ Credentials saved for profile: {profile}, but the gateway rejected them "
            "(HTTP 401) — check the key, then run `r4 auth login` again",
            file=sys.stderr,
        )
        return False
    print(f"✓ Credentials configured for profile: {profile}")
    if verdict != VERIFIED:
        print("  ⚠ not verified: the gateway could not be asked (offline or no gateway URL)")
    return True


def _unbind_rejected_login(profile: str) -> None:
    """Drop the :data:`r4.auth.LOGIN_BINDING` of a login its gateway answered 401.

    A 401 from that very deployment is no proof the credential belongs there:
    ``r4 logout --revoke-server-session`` must not send it there on the
    strength of this login (stab cycle 3, round 6, F1). The key itself stays
    saved (offline login is by design).
    """
    from ..auth import LOGIN_BINDING
    from ..config import ProfileConfigStore

    ProfileConfigStore().save(profile, **{LOGIN_BINDING: None})


def _try_zmq_session_bootstrap_with_profile(profile: str) -> None:
    """Bootstrap ZMQ CURVE session using the profile's gateway_url.

    Unlike ``_try_zmq_session_bootstrap`` in ``_helpers.py`` (which uses
    only ``UMRS_BASE_URL`` env var), this reads the gateway_url from the
    profile so the CURVE session is registered through the Gateway.
    """
    import os

    deploy_mode = os.environ.get("R4_DEPLOY_MODE", "").lower()
    transport_mode = os.environ.get("R4_TRANSPORT_MODE", "").lower()
    if deploy_mode == "cloud" or transport_mode == "gateway":
        return

    try:
        from ..auth import get_auth_manager
        from ..config import ProfileConfigStore
        from ..curve_session import CurveSessionManager

        # Get JWT token
        auth = get_auth_manager(profile)
        token_info = auth.token_store.load(profile)
        if token_info and token_info.access_token:
            jwt_token = token_info.access_token
        else:
            jwt_token = auth.api_key_manager.get_api_key(profile)
        if not jwt_token:
            return

        # Use gateway_url from profile if available
        stored = ProfileConfigStore().load(profile)
        gateway_url = stored.get("gateway_url", "")

        mgr = CurveSessionManager(umrs_base_url=gateway_url or None)
        session = mgr.ensure_session(jwt_token)
        if session:
            print("  ✓ ZMQ session established")
    except Exception as exc:
        print(f"  ⚠ ZMQ session bootstrap skipped: {exc}")


def _sync_umrs_client_profile(args: Namespace) -> None:
    """Sync the r4 profile to a umrs connection profile.

    This replaces the need for a separate ``umrs login`` when using the
    r4 CLI.  The umrs profile is written to
    ``~/.config/umrs/connections/<profile>.json`` so that companion tools,
    Jupyter helpers, and the ``umrs`` CLI all share the same credentials.
    """
    try:
        from ..source import get_umrs_active_profile_name
        from .onboard import _sync_onboard_source

        gateway_url = _resolve_gateway_url(args)
        if not gateway_url:
            return

        resolved_key = _resolve_profile_api_key(args)
        _sync_onboard_source(
            args.profile,
            gateway_url,
            resolved_key,
            username=_load_resolved_username(args.profile),
        )

        # Verify the active pointer was actually written; some environments
        # silently write to a different config root (e.g. mixed installs).
        active = get_umrs_active_profile_name()
        if active != args.profile:
            print(
                f"  ⚠ UMRS active profile is '{active}', expected '{args.profile}'. "
                "Run `umrs profile use <name>` to switch manually.",
                file=sys.stderr,
            )

        # Auto-discover ZMQ ports from gateway's /health endpoint
        _autodiscover_zmq_ports(args.profile, gateway_url, resolved_key)
    except Exception as exc:
        # Never fail login because of umrs sync, but surface the issue
        # so users can diagnose mixed-install or permission problems.
        print(
            f"  ⚠ UMRS profile sync skipped: {exc}",
            file=sys.stderr,
        )


def _resolve_gateway_url(args: Namespace) -> str:
    """Return the gateway URL from args or the stored profile."""
    gateway_url = getattr(args, "gateway_url", None)
    if gateway_url:
        return gateway_url
    from ..config import ProfileConfigStore

    stored = ProfileConfigStore().load(args.profile)
    return stored.get("gateway_url", "")


def _resolve_profile_api_key(args: Namespace) -> str:
    """Resolve an API key or OIDC access token for the profile."""
    api_key = getattr(args, "api_key", None)
    if api_key:
        return api_key
    from ..auth import get_auth_manager

    mgr = get_auth_manager(args.profile)
    api_key = mgr.api_key_manager.get_api_key(args.profile)
    if api_key:
        return api_key
    try:
        token_info = mgr.token_store.load(args.profile)
        if token_info and token_info.access_token:
            return token_info.access_token
    except Exception:
        pass
    return ""


def _ensure_umrs_user_config(args: Namespace) -> None:
    """Create a default umrs user config if none exists.

    After ``r4 auth login`` syncs the connection profile and s3cfg, the
    umrs still needs a user config JSON (``~/.config/umrs/users/<user>.json``)
    to be fully operational.  This mirrors the logic of ``umrs login``
    but runs non-interactively (skip if the file already exists).
    """
    try:
        username = _load_resolved_username(args.profile)
        if not username:
            return

        import json as _json_mod
        import os
        from pathlib import Path

        cfg_dir = _resolve_umrs_cfg_dir()
        users_dir = Path(cfg_dir) / "users"
        cfg_path = users_dir / f"{username}.json"

        if cfg_path.exists():
            _write_user_config_pointer(cfg_dir, cfg_path)
            return

        # Build minimal user config template.
        s3cfg_path = os.path.join(cfg_dir, f"s3cfg_{username}")
        cfg = _build_default_user_config(username, s3cfg_path)

        from .._atomic import atomic_write_text

        # 0600 from the first byte (it was -rw-r--r-- until a later chmod).
        text = _json_mod.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
        atomic_write_text(cfg_path, text, mode=0o600)

        _write_user_config_pointer(cfg_dir, cfg_path)
        print(f"  ✓ User config created → {cfg_path}")
    except Exception:
        pass  # Never fail login because of user config creation


def _load_resolved_username(profile: str) -> str | None:
    """Load the umrs_username from the profile store (set by auto-populate or whoami)."""
    from ..config import ProfileConfigStore

    return ProfileConfigStore().load(profile).get("umrs_username") or None


def _resolve_umrs_cfg_dir() -> str:
    """Resolve the UMRS config directory (mirrors umrs.shared.config logic)."""
    import os

    override = os.environ.get("UMRS_CONFIG")
    if override:
        return os.path.realpath(os.path.expanduser(override))
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return os.path.realpath(os.path.join(os.path.expanduser(xdg), "umrs"))
    return os.path.expanduser("~/.config/umrs")


def _write_user_config_pointer(cfg_dir: str, cfg_path) -> None:
    """Write pointer file so umrs knows the active user config."""
    from pathlib import Path

    try:
        pointer = Path(cfg_dir) / "current_user_config"
        pointer.write_text(str(cfg_path), encoding="utf-8")
    except Exception:
        pass


def _build_default_user_config(username: str, s3cfg_path: str) -> dict:
    """Build a minimal umrs user config template."""
    return {
        "username": username,
        "fullname": "",
        "s3_configuration_file": s3cfg_path,
        "experiences": [
            {
                "xp_name": "default",
                "sql_username": username,
                "modalities": [
                    {
                        "modality_name": "text",
                        "s3": {
                            "bucket_alias": f"{username}-text",
                            "data": {
                                "type": "inline",
                                "mime_type": "text/plain",
                            },
                        },
                        "model": {
                            "model_name": "intfloat/e5-base-v2",
                            "vector_size": "768",
                            "model_type": "TransformerEncoder",
                            "return_type": None,
                        },
                        "sql": {
                            "store_embeddings": "yes",
                            "table_name": "text",
                            "meta_data": [
                                {"name": "keywords", "type": "TEXT"},
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _autodiscover_zmq_ports(profile: str, gateway_url: str, api_key: str) -> None:
    """Query the gateway connect-info endpoint for ZMQ params and update source/profile."""
    try:
        from ..config import _normalize_gateway_url
        from ..source import get_source_store, sync_umrs_connection_profile

        url = _normalize_gateway_url(gateway_url).rstrip("/")
        jwt_token = _resolve_jwt_token(profile, api_key)

        zmq_info = _fetch_connect_info(url, jwt_token=jwt_token)
        if zmq_info is None:
            zmq_info = _fetch_zmq_from_health(url)
        if zmq_info is None:
            return

        store = get_source_store()
        source = store.get(profile)
        if source is None or source.kind != "platform":
            return

        changed = _apply_zmq_fields(source, zmq_info)

        if changed:
            store.add(source)
            sync_umrs_connection_profile(source)
            services = zmq_info.get("_services", {})
            _apply_discovered_services(source.name, services)
            _print_zmq_summary(zmq_info, services)
        elif zmq_info.get("_services"):
            _apply_discovered_services(source.name, zmq_info["_services"])

        _deliver_s3cfg(zmq_info, profile)
    except Exception:
        pass  # Auto-discovery is best-effort


def _deliver_s3cfg(zmq_info: dict, profile: str) -> None:
    """Connect-info s3cfg leg (D-16-gated write inside; notice otherwise)."""
    s3cfg_content = zmq_info.get("_s3cfg_content")
    if not s3cfg_content:
        return
    username = _load_resolved_username(profile)
    wrote = _write_s3cfg_from_connect_info(
        profile,
        s3cfg_content,
        username,
        zmq_info.get("_services"),
    )
    if not wrote:
        print(
            "  ℹ S3 credentials not written locally (D-16): use "
            "`r4 files` for bucket data — storage is reached through "
            "UMRS. Set R4_WRITE_S3CFG=1 only for the legacy transition."
        )


def _resolve_jwt_token(profile: str, api_key: str) -> str | None:
    """Resolve a JWT token from api_key or the profile's auth manager."""
    if api_key:
        return api_key
    try:
        from ..auth import get_auth_manager

        mgr = get_auth_manager(profile)
        token_info = mgr.token_store.load(profile)
        if token_info and token_info.access_token:
            return token_info.access_token
    except Exception:
        pass
    return None


def _try_set_zmq_field(source, attr: str, value: object, stale_values: tuple = (None, "")) -> bool:
    """Set a ZMQ field on source if current value is stale."""
    if value is None:
        return False
    cur = getattr(source, attr, None)
    if cur in stale_values:
        setattr(source, attr, value)
        return True
    return False


def _apply_zmq_fields(source, zmq_info: dict) -> bool:
    """Apply ZMQ fields from connect-info to the source object. Returns True if changed."""
    changed = False

    control_port = zmq_info.get("control_port")
    if isinstance(control_port, int) and control_port:
        changed |= _try_set_zmq_field(source, "zmq_control_port", control_port, (None, 0, 5555))

    auth_type = zmq_info.get("auth_type")
    if isinstance(auth_type, str) and auth_type not in ("", "none"):
        changed |= _try_set_zmq_field(source, "zmq_auth_type", auth_type, (None, "", "none"))

    tunnel_mode = zmq_info.get("tunnel_mode")
    if isinstance(tunnel_mode, bool):
        changed |= _try_set_zmq_field(source, "zmq_tunnel_mode", tunnel_mode, (None,))

    zmq_host = zmq_info.get("host")
    if isinstance(zmq_host, str) and zmq_host and zmq_host != "0.0.0.0":
        changed |= _try_set_zmq_field(source, "zmq_host", zmq_host)

    changed |= _apply_institutional_hosts(source, zmq_info)
    changed |= _apply_default_models(source, zmq_info)

    return changed


def _clean_names(values: list[Any]) -> list[str]:
    """Non-empty stripped strings, in order; anything else dropped."""
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


def _clean_default_models(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep string role aliases and a list-of-strings ``fallback`` only."""
    clean: dict[str, Any] = {
        str(role): alias.strip()
        for role, alias in raw.items()
        if role != "fallback" and isinstance(alias, str) and alias.strip()
    }
    fallback = raw.get("fallback")
    names = _clean_names(fallback) if isinstance(fallback, list) else []
    if names:
        clean["fallback"] = names
    return clean


def _apply_default_models(source: Any, zmq_info: dict[str, Any]) -> bool:
    """Store the site's service-tier defaults; fill an unset default model.

    ADR-106 D-9: connect-info advertises role aliases (``svc-chat``, …).
    The user's own choice wins: ``default_model`` is only filled while it
    is unset. A reply without the field changes nothing (an older UMRS).
    """
    raw = zmq_info.get("_default_models")
    if not isinstance(raw, dict):
        return False
    clean = _clean_default_models(raw)
    changed = bool(source.default_models != clean)
    source.default_models = clean
    chat = clean.get("chat")
    if chat and not source.default_model:
        source.default_model = chat
        changed = True
    return changed


def _apply_institutional_hosts(source, zmq_info: dict) -> bool:
    """Store the site's institutional-provider allowlist on the source.

    ADR-062 amendment (2026-10-15): connect-info advertises it, the venue
    and lease gates enforce it — the source locality classification must
    match them exactly. ``[]`` is a real advertisement (no institutional
    providers) — None and [] differ, so compare against the stored value
    as-is. Returns True when the source changed.

    A reply WITHOUT the field (an older UMRS, a proxy that strips it) is
    not an advertisement: the stored list stays. Fail closed — resetting
    to None would bring the packaged default back, a widening the site
    never asked for when it had advertised ``[]`` (2026-10-16). Entries
    that are not strings are dropped, never stringified (``None`` must not
    become a host named ``"none"``).
    """
    hosts = zmq_info.get("_institutional_hosts")
    if not isinstance(hosts, list):
        return False
    normalized = [h.strip().lower() for h in hosts if isinstance(h, str) and h.strip()]
    if source.institutional_hosts == normalized:
        return False
    source.institutional_hosts = normalized
    return True


def _service_s3_url(services: dict | None) -> str | None:
    """Object-store URL from connect-info services.

    ``s3_url`` is the canonical key (ADR-055); ``minio_url`` is what servers
    predating the rename emit and is read as the fallback.
    """
    services = services or {}
    return services.get("s3_url") or services.get("minio_url")


def _print_zmq_summary(zmq_info: dict, services: dict) -> None:
    """Print summary of ZMQ auto-configuration."""
    parts = []
    if zmq_info.get("control_port"):
        parts.append(f"port={zmq_info['control_port']}")
    auth_type = zmq_info.get("auth_type")
    if auth_type and auth_type != "none":
        parts.append(f"auth={auth_type}")
    if zmq_info.get("tunnel_mode"):
        parts.append("tunnel")
    s3_url = _service_s3_url(services)
    if s3_url:
        parts.append(f"s3={s3_url}")
    print(f"  ✓ ZMQ auto-configured: {', '.join(parts)}")


def _apply_discovered_services(profile_name: str, services: dict) -> None:
    """Update minio_url/rag_url in the umrs connection profile JSON."""

    if not services:
        return
    try:
        import json as _json

        from ..source import _resolve_umrs_cfg_dir

        connections_dir = _resolve_umrs_cfg_dir() / "connections"
        profile_path = connections_dir / f"{profile_name}.json"
        if not profile_path.is_file():
            return
        data = _json.loads(profile_path.read_text(encoding="utf-8"))
        changed = False
        for key in ("s3_url", "minio_url", "rag_url"):
            if services.get(key):
                if data.get("services", {}).get(key) != services[key]:
                    data.setdefault("services", {})[key] = services[key]
                    changed = True
        if changed:
            profile_path.write_text(_json.dumps(data, indent=2) + "\n")
            profile_path.chmod(0o600)
    except Exception:
        pass


def _write_s3cfg_from_connect_info(
    profile: str,
    s3cfg_content: str,
    username: str | None,
    services: dict | None,
) -> bool:
    """Write the s3cfg delivered by connect-info to the local filesystem.

    D-16 (2026-09-22): storage credentials no longer reach clients by
    default — client data movement goes through UMRS (``r4 files`` /
    ZeroMQ FILE/OBJECT). Set ``R4_WRITE_S3CFG=1`` only for the transition
    window where a legacy consumer still needs the file. Returns True
    when a write happened (login prints a notice otherwise).
    """
    import os

    if os.environ.get("R4_WRITE_S3CFG", "").strip().lower() not in ("1", "true", "yes"):
        logger.info(
            "s3cfg delivery skipped (D-16): storage is reached through UMRS "
            "(r4 files); set R4_WRITE_S3CFG=1 for the legacy transition window"
        )
        return False

    if not s3cfg_content:
        return False
    if not username:
        username = _extract_username_from_s3cfg(s3cfg_content)
    if not username:
        return False

    from ..source import _resolve_umrs_cfg_dir

    s3cfg_dir = _resolve_umrs_cfg_dir()
    s3cfg_path = s3cfg_dir / f"s3cfg_{username}"
    existing = _read_file_safe(s3cfg_path)

    s3cfg_content = _rewrite_s3cfg_minio_host(s3cfg_content, services)

    if existing and existing.strip() == s3cfg_content.strip():
        return True

    _write_file_secure(s3cfg_dir, s3cfg_path, s3cfg_content, existing)
    return True


def _read_file_safe(path) -> str:
    """Read a file, returning empty string on missing/error."""
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    return ""


def _write_file_secure(s3cfg_dir, s3cfg_path, content: str, existing: str) -> None:
    """Write content to s3cfg_path with 0600 permissions."""
    try:
        from pathlib import Path

        from .._atomic import atomic_write_text

        # The S3 secret is 0600 from the first byte (before 2026-10-21: umask
        # mode, then chmod).
        atomic_write_text(Path(s3cfg_path), content, mode=0o600)
        label = "updated" if existing else "written to"
        print(f"  ✓ S3 credentials {label}: {s3cfg_path}")
    except Exception as exc:
        print(f"  ⚠ Failed to write S3 credentials: {exc}")


def _extract_username_from_s3cfg(s3cfg_content: str) -> str | None:
    """Extract access_key from s3cfg content as fallback username."""
    import configparser

    try:
        cfg = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        cfg.read_string(s3cfg_content)
        return cfg.get("default", "access_key", fallback=None)
    except Exception:
        return None


def _rewrite_s3cfg_minio_host(
    s3cfg_content: str,
    services: dict | None,
) -> str:
    """Rewrite host_base/host_bucket in s3cfg to match the S3 URL from services.

    ``s3_url`` is the canonical key (ADR-055); ``minio_url`` is the key older
    servers emit and is read as the fallback.
    """
    import configparser

    s3_url = _service_s3_url(services)
    if not s3_url:
        return s3cfg_content
    try:
        from urllib.parse import urlparse

        parsed = urlparse(s3_url)
        target_host = parsed.hostname
        target_port = parsed.port
        use_https = parsed.scheme == "https"
        if not target_host:
            return s3cfg_content
        target = f"{target_host}:{target_port}" if target_port else target_host
        cfg = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        cfg.read_string(s3cfg_content)
        if cfg.has_option("default", "host_base"):
            old_host = cfg.get("default", "host_base").strip()
            if old_host != target:
                s3cfg_content = s3cfg_content.replace(old_host, target)
        s3cfg_content = s3cfg_content.replace(
            "use_https = True", f"use_https = {use_https}"
        ).replace("use_https = False", f"use_https = {use_https}")
    except Exception:
        pass
    return s3cfg_content


def _fetch_connect_info(gateway_base_url: str, jwt_token: str | None = None) -> dict | None:
    """Fetch ZMQ params and service URLs from /gw/ops/connect-info.

    Returns a dict with ``zmq`` info (host, control_port, etc.) and
    optionally ``services`` (minio_url, rag_url).  When *jwt_token* is
    provided the request is authenticated, and the response may include
    ``s3cfg_content`` for local credential setup.  Returns None on failure.
    """
    import json as _json
    import urllib.request

    try:
        from .._http import probe_api_prefix

        req = urllib.request.Request(
            f"{gateway_base_url}{probe_api_prefix(gateway_base_url)}/ops/connect-info",
            method="GET",
        )
        if jwt_token:
            req.add_header("Authorization", f"Bearer {jwt_token}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode())
        zmq = data.get("zmq")
        if zmq is None:
            return None
        # Attach services alongside zmq info for the caller
        services = data.get("services")
        if services and isinstance(services, dict):
            zmq["_services"] = services
        # Attach s3cfg content if delivered
        s3cfg_content = data.get("s3cfg_content")
        if s3cfg_content:
            zmq["_s3cfg_content"] = s3cfg_content
        # ADR-062 amendment (2026-10-15): the site's institutional-provider
        # allowlist — stored on the platform source so source locality is
        # classified against the site's list (never user input). Absent
        # stays absent ([] is a real advertisement; see
        # _apply_institutional_hosts for why absence keeps the stored list).
        hosts = data.get("institutional_provider_hosts")
        if isinstance(hosts, list):
            zmq["_institutional_hosts"] = hosts
        # ADR-106 D-9: the service-tier role aliases this site serves.
        default_models = data.get("default_models")
        if isinstance(default_models, dict):
            zmq["_default_models"] = default_models
        return zmq
    except Exception:
        return None


def _fetch_zmq_from_health(gateway_base_url: str) -> dict | None:
    """Fall back to /health site_capabilities for control port only."""
    import json as _json
    import urllib.request

    try:
        req = urllib.request.Request(
            f"{gateway_base_url}/health",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode())
        caps = data.get("site_capabilities", {})
        zmq_port = caps.get("zmq_control_port")
        if zmq_port and isinstance(zmq_port, int):
            return {"control_port": zmq_port}
    except Exception:
        pass
    return None


def _run_full_setup(profile: str) -> None:
    """Extended setup: model prompt, R4.md scaffolding, health checks."""
    import sys

    if not sys.stdin.isatty():
        return
    from ..config import ProfileConfigStore
    from .onboard import (
        _maybe_scaffold_briefing,
        _prompt,
        _yes_no,
    )

    # Prompt for model if not already set
    store = ProfileConfigStore()
    stored = store.load(profile)
    if not stored.get("model"):
        model = _prompt("Default model", "rivault-gpt-oss-static")
        if model:
            store.save(profile, **{**stored, "model": model})
            print(f"  Model: {model}")

    # R4.md scaffolding
    _maybe_scaffold_briefing()

    # Health checks
    if _yes_no("\nRun a quick health check?"):
        from ..client import Client
        from ..config import Config
        from .onboard import (
            _check_gateway_readiness,
            _check_mcp_readiness,
            _check_rag_readiness,
        )

        client = Client(config=Config.from_env(profile))
        _check_gateway_readiness(client)
        _check_rag_readiness(client, stored.get("project_id", ""))
        _check_mcp_readiness(client)


def _save_login_profile_settings(args: Namespace) -> None:
    """Persist gateway-url, project, model, umrs-username, OIDC settings to profile store."""
    gateway_url = getattr(args, "gateway_url", None)
    project = getattr(args, "project", None)
    model = getattr(args, "model", None)
    umrs_username = getattr(args, "umrs_username", None)
    oidc_issuer = getattr(args, "oidc_issuer", None)
    oidc_client_id = getattr(args, "oidc_client_id", None)
    oidc_requested = bool(getattr(args, "oidc", False) or oidc_issuer or oidc_client_id)

    has_settings = any(
        [
            gateway_url,
            project,
            model,
            umrs_username,
            oidc_issuer,
            oidc_client_id,
            oidc_requested,
        ]
    )
    if not has_settings:
        return

    from ..config import ProfileConfigStore

    store = ProfileConfigStore()
    stored = store.load(args.profile)

    # Build updates dict from non-None args
    field_map = {
        "gateway_url": gateway_url,
        "project_id": project,
        "model": model,
        "umrs_username": umrs_username,
        "oidc_issuer": oidc_issuer,
        "oidc_client_id": oidc_client_id,
        "oidc_scope": getattr(args, "oidc_scope", None),
    }
    updates = {k: v for k, v in field_map.items() if v}
    if oidc_requested:
        updates["api_key"] = None

    merged = {**stored, **updates}
    store.save(args.profile, **merged)

    # Print saved settings
    display_map = {
        "Gateway URL": gateway_url,
        "Project ID": project,
        "Model": model,
        "UMRS Username": umrs_username,
        "OIDC Issuer": oidc_issuer,
        "OIDC Client ID": oidc_client_id,
    }
    for label, value in display_map.items():
        if value:
            print(f"  {label}: {value}")


def _display_jwt_info(profile: str, *, oidc: bool = False) -> None:
    """Decode and display JWT claims for UX (never authoritative)."""
    from ..auth import decode_jwt_claims, get_auth_manager

    auth = get_auth_manager(profile)
    token = auth.token_store.load(profile)
    if token is None:
        api_key = auth.api_key_manager.get_api_key(profile)
        if not api_key:
            return
        claims = decode_jwt_claims(api_key)
        auth_source = "API key (JWT)"
    else:
        claims = decode_jwt_claims(token.access_token)
        auth_source = "OIDC token" if oidc else "API key (JWT)"

    if claims is None:
        return

    print(f"  Logged in via: {auth_source}")
    _print_jwt_claims(claims)


def _print_jwt_claims(claims: dict) -> None:
    """Print relevant JWT claims for user feedback."""
    import time

    claim_labels = [
        ("sub", "Account"),
        ("email", "Email  "),
        ("project_id", "Project"),
        ("gateway_url", "Gateway"),
        ("profile", "Profile"),
        ("iss", "Issuer "),
    ]
    for key, label in claim_labels:
        if key in claims:
            print(f"    {label}: {claims[key]}")

    if "exp" in claims:
        remaining = claims["exp"] - time.time()
        if remaining > 0:
            hours = int(remaining // 3600)
            mins = int((remaining % 3600) // 60)
            print(f"    Expires: {hours}h {mins}m")
        else:
            print("    Token EXPIRED")


def cmd_login(args):
    """Handle login command."""
    from ..config import get_config
    from ._helpers import _configure_profile_auth, _print_active_context

    _populate_args_from_jwt(args)
    _prompt_missing_login_inputs(args)
    _save_login_profile_settings(args)

    # --oidc-issuer or --oidc-client-id implies --oidc
    oidc = (
        args.oidc
        or bool(getattr(args, "oidc_issuer", None))
        or bool(getattr(args, "oidc_client_id", None))
    )

    if not _configure_profile_auth(
        args.profile,
        api_key=args.api_key,
        oidc=oidc,
        timeout=args.timeout,
        prompt_if_missing=True,
    ):
        print(f"✗ No credentials configured for profile: {args.profile}", file=sys.stderr)
        sys.exit(1)
    _record_login_home(args)

    _display_jwt_info(args.profile, oidc=oidc)

    _auto_populate_umrs_username(args)
    if not _report_login_outcome(args.profile, _verify_identity_via_whoami(args.profile)):
        sys.exit(1)
    active_config = get_config()
    if active_config.profile == args.profile:
        _print_active_context(active_config)
    else:
        print(f"  Active profile: {active_config.profile}")
        print("  To use this profile:")
        print(f"    export R4_PROFILE={args.profile}")
        print("  Or per-command:")
        print(f"    R4_PROFILE={args.profile} r4 health")

    _sync_umrs_client_profile(args)
    _ensure_umrs_user_config(args)
    _try_zmq_session_bootstrap_with_profile(args.profile)
    _sync_projects_from_server(args.profile)
    if getattr(args, "full", False):
        _run_full_setup(args.profile)
