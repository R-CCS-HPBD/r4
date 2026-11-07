"""``r4 source`` command — manage endpoint sources.

Sources are named backends (UMRS platform instances or public providers)
that can be selected at runtime via ``--source`` or ``R4_SOURCE``.

Examples:
    r4 source add rccs-cloud --url http://umrs-front:8000 --kind platform
    r4 source add ai4s --url http://umrs-front:8002 --kind platform
    r4 source add claude --url https://api.anthropic.com --kind public
    r4 source list
    r4 source use ai4s
    r4 source remove claude
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace
from typing import TYPE_CHECKING

from .base import CommandHandler
from .registry import command

if TYPE_CHECKING:  # noqa: E402
    from ..source import Source

_PLAIN_FLAG_WARNING = (
    "--api-key leaves the key in shell history and process lists (G-1); "
    "prefer --api-key-env VAR or --api-key-stdin next time"
)


def _resolve_api_key_material(args) -> tuple[str | None, str | None, str | None]:
    """Resolve the key from env / stdin / flag.

    Returns (key, warning, error). Precedence: --api-key-env, --api-key-stdin,
    --api-key (deprecated path, warns — G-1). No key means (None, None, None).
    """
    env_var = getattr(args, "api_key_env", None)
    if env_var:
        import os

        key = os.environ.get(env_var)
        if not key:
            return None, None, f"environment variable {env_var!r} is empty or unset"
        return key, None, None
    if getattr(args, "api_key_stdin", False):
        import getpass
        import sys as _sys

        try:
            key = getpass.getpass("API key: ") if _sys.stdin.isatty() else _sys.stdin.read().strip()
        except (EOFError, OSError) as exc:
            return None, None, f"failed to read API key: {exc}"
        if not key:
            return None, None, "empty API key on stdin"
        return key, None, None
    key = getattr(args, "api_key", None)
    if key:
        return key, _PLAIN_FLAG_WARNING, None
    return None, None, None


def _is_credentialed_http_unsafe(url: str) -> bool:
    """True when the URL is explicit http:// to a non-localhost host (G-4)."""
    from urllib.parse import urlsplit

    text = url if "://" in url else f"https://{url}"
    parts = urlsplit(text)
    if parts.scheme.lower() != "http":
        return False
    host = (parts.hostname or "").lower()
    return host not in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _reserved_name_error(name: str) -> str | None:
    """Decision D-8 (onboarding plan 2026-10-15): r4 keeps ``site`` and ``local``."""
    from ..source import RESERVED_SOURCE_NAMES, is_reserved_source_name

    if not is_reserved_source_name(name):
        return None
    kept = " and ".join(f"'{n}'" for n in sorted(RESERVED_SOURCE_NAMES))
    return (
        f"Error: '{name}' is a reserved source name (r4 keeps {kept} for itself); "
        "choose another name."
    )


def _never_pushed(src) -> str | None:
    """Why a source can never be pushed (and so never needs an unpush), else None.

    A platform source's credential is a deployment login, which is never sent
    to another deployment; a loopback source is reachable only from this
    machine (onboarding plan S1 / Phase 4 boundary).
    """
    from ..source_context import is_loopback_url

    if src.kind == "platform":
        return "it is a platform source: a deployment's login is never sent to another deployment"
    if is_loopback_url(src.url):
        return "its URL is on this machine (loopback): a remote session cannot reach it"
    return None


def _deployment_login_reason(src) -> str | None:
    """Why a public source's key is really a deployment login, else None.

    Its URL is a route of a registered platform source's deployment (for
    example ``https://b.example/gw/llm`` registered as public), or its key
    is a JWT naming a gateway (``gateway_url`` claim). Either way pushing it
    would send one deployment's login to another (onboarding plan D-8).
    """
    from ..source import get_platform_sources
    from ..source_context import _jwt_claim, within_deployment

    owner = next((p for p in get_platform_sources() if within_deployment(src.url, p.url)), None)
    if owner is not None:
        return (
            f"its URL is on platform source '{owner.name}''s deployment: a deployment's "
            "login is never sent to another deployment"
        )
    if _jwt_claim(src.api_key, "gateway_url"):
        return "its key is a deployment login (a token that names a gateway)"
    return None


def _url_error(url: str, provider: str | None = None) -> str | None:
    """Refuse a --url r4 could never call: it must be http(s) with a host.

    ``source add bad --url notaurl`` (or ``ftp://x``) used to be stored with
    exit 0, then every later command failed far from the cause (c2-cli,
    2026-10-21). Bedrock picks its own endpoint, so its --url is not checked.
    """
    from urllib.parse import urlsplit

    if provider == "bedrock":
        return None
    parts = urlsplit(url or "")
    if parts.scheme in ("http", "https") and parts.hostname:
        return None
    return (
        f"Error: --url must be an http:// or https:// URL with a host (got {url!r}), "
        "e.g. https://api.openai.com/v1"
    )


def _add_key_material(args) -> tuple[str | None, str | None]:
    """(api_key, error) for ``source add``: G-1 key material plus the G-4 http check."""
    api_key, warning, error = _resolve_api_key_material(args)
    if error:
        return None, f"Error: {error}"
    if warning:
        print(f"Warning: {warning}", file=sys.stderr)
    if (
        api_key
        and _is_credentialed_http_unsafe(args.url)
        and not getattr(args, "allow_insecure_http", False)
    ):
        return None, (
            "Error: refusing to store a credential for a non-localhost http:// URL "
            "(G-4). Use https, or pass --allow-insecure-http with intent."
        )
    return api_key, None


def _source_from_args(args, api_key: str | None) -> Source:
    from ..source import Source

    optional = {
        field: getattr(args, attr, None) or None
        for field, attr in (
            ("provider", "provider"),
            ("aws_region", "aws_region"),
            ("aws_profile", "aws_profile"),
            ("description", "description"),
            ("zmq_host", "zmq_host"),
            ("zmq_control_port", "zmq_port"),
            ("zmq_tunnel_mode", "zmq_tunnel"),
            ("default_model", "default_model"),
            ("project", "project"),
        )
    }
    return Source(
        name=args.name, kind=args.kind, url=args.url.rstrip("/"), api_key=api_key, **optional
    )


def _moves_deployment(existing, source) -> bool:
    """Whether updating ``existing`` to ``source`` points the name at another deployment."""
    from ..source_context import same_deployment

    return existing is not None and not same_deployment(existing.url, source.url)


#: Fields `source add` never fills from the row it updates: the row's identity.
_ADD_IDENTITY_FIELDS = frozenset({"name", "kind", "url"})


def _keep_unnamed_fields(existing: Source | None, source: Source) -> list[str]:
    """Fill each field ``source`` leaves unset from ``existing`` (same kind,
    same deployment); return the names of the fields kept. A row of another
    kind keeps nothing: a platform login never becomes a public key."""
    from dataclasses import fields

    if existing is None or existing.kind != source.kind:
        return []
    kept: list[str] = []
    for field in fields(source):
        old = getattr(existing, field.name)
        if (
            field.name in _ADD_IDENTITY_FIELDS
            or old is None
            or getattr(source, field.name) is not None
        ):
            continue
        setattr(source, field.name, old)
        kept.append("key" if field.name == "api_key" else field.name.replace("_", " "))
    return kept


def _merge_or_forget(existing: Source | None, source: Source, replacing: bool) -> list[str]:
    """Before ``source`` overwrites ``existing``: a replace or a re-point to
    another deployment drops the old row's stored credentials; any other
    update keeps the fields ``source`` leaves unset. Returns the kept fields."""
    if existing is not None and (replacing or _moves_deployment(existing, source)):
        from ..source import forget_replaced_source

        forget_replaced_source(source.name, existing.url, source.url)
        return []
    return _keep_unnamed_fields(existing, source)


def _wants_key(args) -> bool:
    return bool(
        getattr(args, "api_key", None)
        or getattr(args, "api_key_env", None)
        or getattr(args, "api_key_stdin", False)
    )


def _set_changes(args, src) -> tuple[dict, str | None]:
    """The fields ``r4 source set`` changes on ``src``, or an error message."""
    changes = {
        field: getattr(args, field)
        for field in ("default_model", "project")
        if getattr(args, field, None)
    }
    if _wants_key(args):
        args.url = src.url  # the G-4 http check is against the row's own URL
        key, error = _add_key_material(args)
        if error:
            return {}, error
        changes["api_key"] = key
    return changes, None


_SET_LABELS = {"api_key": "credential", "default_model": "default model", "project": "project"}


def _add_key_flags(parser) -> None:
    """``--api-key-stdin`` / ``--api-key-env`` / ``--api-key`` and the G-4 opt-in."""
    parser.add_argument(
        "--api-key",
        help="API key. WARNING: visible in shell history and process lists — prefer "
        "--api-key-env or --api-key-stdin",
    )
    parser.add_argument(
        "--api-key-env",
        metavar="VAR",
        help="Read the API key from environment variable VAR (preferred)",
    )
    parser.add_argument(
        "--api-key-stdin",
        action="store_true",
        help="Read the API key from stdin/prompt (preferred for interactive use)",
    )
    parser.add_argument(
        "--allow-insecure-http",
        action="store_true",
        help="Permit a credentialed http:// URL (off by default; https required)",
    )


def _source_is_authenticated(src) -> bool:
    """Return True if the source carries an API key or a matching r4 profile is authenticated."""
    if src.api_key:
        return True
    try:
        from ..auth import get_auth_manager

        return get_auth_manager(src.name).is_authenticated()
    except Exception:
        return False


def _unpush_after_remove(name: str) -> None:
    """If the removed source was pushed, unpush it server-side (G-2: no stale copies)."""
    try:
        from ..client import Client
        from ..user_sources import UserSourcesClient

        result = UserSourcesClient(Client()).unpush(name)
        print(f"  Unpushed remotely (leases revoked: {result.get('leases_revoked', 0)})")
    except Exception as exc:  # noqa: BLE001 — not pushed or remote down; honest note
        print(f"  note: no sealed remote row removed ({exc})", file=sys.stderr)


REMOTE_NOT_REQUESTED = object()


def _emit_sources_json(sources, active, remote=REMOTE_NOT_REQUESTED) -> None:
    """Print the source list as JSON."""
    import json as _json

    data = []
    for src in sources:
        row = {
            "name": src.name,
            "kind": src.kind,
            "url": src.url,
            "active": src.name == active,
            "provider": src.provider,
            "description": src.description,
            "has_api_key": bool(src.api_key),
            "default_model": src.default_model,
            "project": src.project,
        }
        if remote is not REMOTE_NOT_REQUESTED:
            row["remote"] = _remote_badge(src, remote)
        data.append(row)
    print(_json.dumps(data, indent=2, default=str))


def _key_fingerprint(value: str) -> str:
    """Same short fingerprint the server computes at push (dup, 6 lines)."""
    import hashlib as _hashlib

    return "sha256:" + _hashlib.sha256(value.encode()).hexdigest()[:8]


def _fetch_remote_map() -> dict[str, dict] | None:
    """list-mine rows keyed by name; None when remote unreachable."""
    try:
        from ..client import Client
        from ..user_sources import UserSourcesClient

        rows = UserSourcesClient(Client()).list_mine(with_leases=True)
        return {str(row.get("name")): row for row in rows if isinstance(row, dict)}
    except Exception as exc:  # noqa: BLE001 — remote unreachable
        print(f"note: sealed remote unreachable ({exc})", file=sys.stderr)
        return None


def _lease_countdown(row: dict, *, _now=None) -> str | None:
    """'47m' until the nearest live lease expires (server-derived, R2-4)."""
    from datetime import datetime, timezone

    raw = row.get("next_lease_expiry")
    if not raw:
        return None
    now = _now or datetime.now(timezone.utc)
    try:
        expiry = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    seconds = max(0, int((expiry - now).total_seconds()))
    if seconds >= 3600:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


def _badge_base(src, row: dict) -> str:
    """Base state for one remote row: pushed (fp match) | stale | tombstoned."""
    if row.get("tombstoned_at"):
        return "tombstoned"
    remote_fp = str(row.get("key_fingerprint") or "")
    if src.api_key and remote_fp and _key_fingerprint(src.api_key) != remote_fp:
        return "stale"
    return "pushed"


def _remote_badge(src, remote: dict[str, dict] | None) -> str:
    """Sealed/server-side row state for one local source (design §3.7).

    Honest states: ``pushed`` (fingerprints match), ``stale``, ``tombstoned``,
    ``-`` (never pushed), ``?`` (remote unreachable — labeled, never silent).
    countdown on active leases appended when present (R2-4).
    """
    if remote is None:
        return "?"
    row = remote.get(src.name)
    if row is None:
        return "-"
    badge = _badge_base(src, row)
    countdown = _lease_countdown(row)
    if countdown and row.get("active_leases"):
        return f"{badge} ({countdown})"
    return badge


def _print_sources_table(sources, active, remote=REMOTE_NOT_REQUESTED) -> None:
    """Print the source table (active first, then authenticated; optional REMOTE column)."""
    sources = sorted(
        sources,
        key=lambda src: (src.name != active, not _source_is_authenticated(src), src.name),
    )
    head = f"{'':2}{'NAME':<16}{'KIND':<10}{'URL':<45}{'AUTH':5} {'DESCRIPTION'}"
    sep = f"{'':2}{'-' * 14}  {'-' * 8}  {'-' * 43}  {'-' * 3}  {'-' * 20}"
    if remote is not REMOTE_NOT_REQUESTED:
        head = f"{'':2}{'NAME':<16}{'KIND':<10}{'REMOTE':<14}{'URL':<39}{'AUTH':5} {'DESCRIPTION'}"
        sep = f"{'':2}{'-' * 14}  {'-' * 8}  {'-' * 12}  {'-' * 37}  {'-' * 3}  {'-' * 20}"
    print(head)
    print(sep)
    for src in sources:
        marker = "* " if src.name == active else "  "
        desc = src.description or ""
        provider_hint = f" ({src.provider})" if src.provider else ""
        auth_mark = "✓" if _source_is_authenticated(src) else ""
        if remote is not REMOTE_NOT_REQUESTED:
            print(
                f"{marker}{src.name:<16}{src.kind:<10}{_remote_badge(src, remote):<14}"
                f"{src.url:<39}{auth_mark:5}{desc}{provider_hint}"
            )
        else:
            print(
                f"{marker}{src.name:<16}{src.kind:<10}{src.url:<45}{auth_mark:5}{desc}{provider_hint}"
            )
    if active:
        print(f"\n  Active: {active}")
    else:
        print("\n  No active source. Use `r4 source use <name>` to set one.")


@command
class SourceCommand(CommandHandler):
    """``r4 source`` — manage endpoint sources."""

    name = "source"
    help = "Manage endpoint sources (platform backends and public providers)"

    def register(self, subparsers) -> ArgumentParser:
        from argparse import RawDescriptionHelpFormatter

        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Manage named endpoint sources. Each source is a backend "
                "that can serve LLM requests — either a platform UMRS "
                "instance or a public provider (OpenAI, Bedrock, Claude, etc.)."
            ),
            epilog=(
                "Examples:\n"
                "  r4 source add rccs --url http://umrs-front:8000 --kind platform\n"
                "  r4 source add claude --url https://api.anthropic.com --kind public\n"
                "  r4 source add-ollama --use\n"
                "  r4 source list\n"
                "  r4 source use rccs\n"
                "  r4 endpoint up --source ai4s --model Qwen/Qwen2.5-7B\n"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = p.add_subparsers(dest="source_command", metavar="<source-command>")
        sub.required = True

        # source list
        sl = sub.add_parser("list", help="List configured sources")
        sl.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")
        sl.add_argument(
            "--remote",
            action="store_true",
            help="Also show sealed (server-pushed) state per source (R1-6)",
        )

        audit_p = sub.add_parser(
            "audit", help="Show the audit trail for one pushed source (owner-scoped)"
        )
        audit_p.add_argument("name", help="Source name (must be pushed)")
        audit_p.add_argument("--limit", "-n", type=int, default=20)
        audit_p.add_argument("--json", "-j", action="store_true", dest="as_json")

        # source push / unpush (R1-6, design §3.7)
        push_p = sub.add_parser(
            "push",
            help="Seal this source's credential to the server (owner-scoped, for remote sessions)",
        )
        push_p.add_argument("name", help="Source name (must exist locally with an api_key)")
        push_p.add_argument(
            "--allow-paths",
            default="",
            help="Comma-opt-ins: files,batches,images,audio,fine_tuning (off by default)",
        )
        push_p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
        push_p.add_argument("--json", "-j", action="store_true", dest="as_json")

        unpush_p = sub.add_parser(
            "unpush", help="Tombstone a pushed source and revoke referencing leases"
        )
        unpush_p.add_argument("name", help="Source name to unpush")
        unpush_p.add_argument("--json", "-j", action="store_true", dest="as_json")

        # source add
        add_p = sub.add_parser("add", help="Add or update a source")
        add_p.add_argument("name", help="Source name (e.g. 'rccs-cloud', 'ai4s', 'claude')")
        add_p.add_argument("--url", required=True, help="Base URL of the source")
        add_p.add_argument(
            "--kind",
            choices=["platform", "public"],
            default="platform",
            help="Source kind (default: platform)",
        )
        add_p.add_argument(
            "--api-key",
            help="API key (for public sources). WARNING: visible in shell history "
            "and process lists — prefer --api-key-env or --api-key-stdin",
        )
        add_p.add_argument(
            "--api-key-env",
            metavar="VAR",
            help="Read the API key from environment variable VAR (preferred)",
        )
        add_p.add_argument(
            "--api-key-stdin",
            action="store_true",
            help="Read the API key from stdin/prompt (preferred for interactive use)",
        )
        add_p.add_argument(
            "--allow-insecure-http",
            action="store_true",
            help="Permit a credentialed http:// URL (off by default; https required)",
        )
        add_p.add_argument(
            "--provider",
            help="Provider type: bedrock, anthropic, or omit for OpenAI-compatible",
        )
        add_p.add_argument("--aws-region", help="AWS region (for Bedrock)")
        add_p.add_argument("--aws-profile", help="AWS profile (for Bedrock)")
        add_p.add_argument("--description", help="Optional description")
        # ZMQ connection details for platform sources (synced to umrs)
        add_p.add_argument(
            "--zmq-host",
            help="ZMQ server host for umrs (default: extracted from --url)",
        )
        add_p.add_argument(
            "--zmq-port",
            type=int,
            help="ZMQ control port for umrs (default: 5555)",
        )
        add_p.add_argument(
            "--zmq-tunnel",
            action="store_true",
            default=False,
            help="Use SSH tunnel mode for umrs ZMQ connection",
        )
        add_p.add_argument(
            "--default-model",
            metavar="NAME",
            help="Model r4 uses for this source when a command names none "
            "(--model, model@source and R4_MODEL win)",
        )
        add_p.add_argument(
            "--project",
            metavar="ID",
            help="Project sent to this source as X-Project-ID (R4_PROJECT_ID wins)",
        )
        add_p.add_argument(
            "--replace",
            action="store_true",
            help="Replace an existing source of this name: the old row and the "
            "credentials stored for it are dropped, the new row is written in one step",
        )

        # source add-ollama (shortcut for a local Ollama endpoint)
        ao_p = sub.add_parser(
            "add-ollama",
            help="Add a local Ollama source (public, OpenAI-compatible)",
        )
        ao_p.add_argument(
            "name",
            nargs="?",
            default="ollama",
            help="Source name (default: ollama)",
        )
        ao_p.add_argument(
            "--url",
            default="http://localhost:11434",
            help="Ollama base URL (default: http://localhost:11434)",
        )
        ao_p.add_argument("--description", help="Optional description")
        ao_p.add_argument(
            "--use",
            action="store_true",
            help="Set as the active source after adding",
        )

        # source set (2026-10-15): change named fields; every other field is kept
        set_p = sub.add_parser(
            "set",
            help="Change fields of an existing source (its key, default model, project); "
            "every field you do not name is kept",
        )
        set_p.add_argument("name", help="Source name")
        _add_key_flags(set_p)
        set_p.add_argument("--default-model", metavar="NAME", help="Model r4 uses for this source")
        set_p.add_argument("--project", metavar="ID", help="Project sent to this source")

        # source remove
        rm_p = sub.add_parser("remove", help="Remove a source")
        rm_p.add_argument("name", help="Source name to remove")

        # source use
        use_p = sub.add_parser("use", help="Set the active source")
        use_p.add_argument("name", nargs="?", help="Source name (omit to clear active source)")

        return p

    _HANDLERS = {
        "list": "_handle_list",
        "add": "_handle_add",
        "add-ollama": "_handle_add_ollama",
        "set": "_handle_set",
        "remove": "_handle_remove",
        "use": "_handle_use",
        "push": "_handle_push",
        "unpush": "_handle_unpush",
        "audit": "_handle_audit",
    }

    def handle(self, args: Namespace) -> int:
        cmd = args.source_command
        method = self._HANDLERS.get(cmd)
        if method is None:
            print(f"Unknown source command: {cmd}", file=sys.stderr)
            return 1
        return getattr(self, method)(args)

    def _handle_list(self, args: Namespace = None) -> int:
        from ..source import get_source_store

        store = get_source_store()
        sources = store.list()
        active = store.get_active()

        remote = REMOTE_NOT_REQUESTED
        if args and getattr(args, "remote", False):
            remote = _fetch_remote_map()

        if args and getattr(args, "as_json", False):
            _emit_sources_json(sources, active, remote)
            return 0

        if not sources:
            print("No sources configured.")
            print("  Add one with: r4 source add <name> --url <url> --kind platform|public")
            return 0

        _print_sources_table(sources, active, remote)
        return 0

    def _handle_add(self, args: Namespace) -> int:
        # The name is checked before any key is read (stdin stays unread).
        error = _reserved_name_error(args.name) or _url_error(
            args.url, getattr(args, "provider", None)
        )
        api_key = None
        if error is None:
            api_key, error = _add_key_material(args)
        if error:
            print(error, file=sys.stderr)
            return 1
        return self._commit_add(args, _source_from_args(args, api_key))

    def _commit_add(self, args: Namespace, source: Source) -> int:
        """Persist the source, sync the UMRS profile, auto-activate on first add.

        A source of the same name is updated in place, as before 2026-10-15:
        since 2026-10-21 every field this add does not name (its stored key,
        provider, description, …) is KEPT, as ``r4 source set`` does — a
        re-add that left the optional key blank silently wiped a working
        credential (connect#4).
        With ``--replace``, or when the update moves the row to another
        deployment, the old row's stored credentials (UMRS profile and token,
        the r4 login kept under its name, its CURVE session files) are
        dropped first, so none of them can reach the new URL; the registry is
        written in one atomic step either way.
        """
        from ..source import get_source_store, sync_umrs_connection_profile

        store = get_source_store()
        existing = store.get(args.name)
        replacing = existing is not None and bool(getattr(args, "replace", False))
        kept = _merge_or_forget(existing, source, replacing)
        store.add(source)

        verb = "Replaced" if replacing else ("Updated" if existing else "Added")
        print(f"{verb} source '{source.name}' ({source.kind}: {source.url})")
        if kept:
            print(f"  Kept from the existing row: {', '.join(kept)} (use --replace to drop them).")

        # Sync UMRS connection profile for platform sources
        profile_path = sync_umrs_connection_profile(source)
        if profile_path:
            print(f"  Synced umrs profile: {profile_path}")

        # If this is the first source, auto-activate it
        if not store.get_active():
            store.set_active(source.name)
            print("  Set as active source (first registered).")
        return 0

    def _handle_add_ollama(self, args: Namespace) -> int:
        from ..source import Source, get_source_store

        name = getattr(args, "name", None) or "ollama"
        url = (getattr(args, "url", None) or "http://localhost:11434").rstrip("/")
        refusal = _reserved_name_error(name) or _url_error(url)
        if refusal:
            print(refusal, file=sys.stderr)
            return 1
        source = Source(
            name=name,
            kind="public",
            url=url,
            provider="ollama",
            description=getattr(args, "description", None) or "Local Ollama",
        )

        store = get_source_store()
        existing = store.get(name)
        store.add(source)

        verb = "Updated" if existing else "Added"
        print(f"{verb} source '{source.name}' (public: {source.url})")

        # Activate when requested, or automatically for the first source.
        if getattr(args, "use", False) or not store.get_active():
            store.set_active(name)
            print(f"  Set as active source: {name}")
        else:
            print(f"  hint: run `r4 source use {name}` to make it active.")
        print(f"  hint: `r4 model list --source {name}` to see available models.")
        return 0

    def _handle_set(self, args: Namespace) -> int:
        """``r4 source set NAME``: change the named fields, keep every other one.

        The non-destructive way to give a source its own credential (the
        login hints name it): ZMQ, tunnel, description, provider and the
        UMRS connection profile stay as they are. A new key drops the CURVE
        session kept under the old one. The key is never printed.
        """
        from dataclasses import replace

        from ..source import _forget_bound_sessions, get_source_store, sync_umrs_token

        store = get_source_store()
        src = store.get(args.name)
        if src is None:
            print(f"Error: source '{args.name}' not found.", file=sys.stderr)
            return 1
        changes, error = _set_changes(args, src)
        if error or not changes:
            print(
                error
                or "Error: nothing to set — pass --api-key-stdin (or --api-key-env VAR), "
                "--default-model or --project.",
                file=sys.stderr,
            )
            return 1
        updated = replace(src, **changes)
        store.add(updated)
        if "api_key" in changes:
            sync_umrs_token(updated)
            _forget_bound_sessions((updated.url,))
        labels = ", ".join(_SET_LABELS[field] for field in changes)
        print(f"Updated source '{args.name}': {labels} (every other field kept).")
        return 0

    def _handle_remove(self, args: Namespace) -> int:
        from ..source import get_source_store, remove_source_credential_artifacts

        store = get_source_store()
        src = store.get(args.name)
        if src is None or not store.remove(args.name):
            print(f"Source '{args.name}' not found.", file=sys.stderr)
            return 1
        print(f"Removed source '{args.name}'.")
        artifacts = remove_source_credential_artifacts(args.name, src.url)
        touched = [k for k, removed in artifacts.items() if removed]
        if touched:
            print(f"  Removed credential artifacts: {', '.join(touched)}")
        # A platform or loopback source was never pushed: no gateway is
        # contacted for it (onboarding plan 2026-10-15, D-8 boundary).
        if _never_pushed(src) is None:
            _unpush_after_remove(args.name)
        return 0

    @staticmethod
    def _push_precondition_error(src, name: str) -> str | None:
        """Local refusal reasons, checked before any network call; None = ok to push.

        Not found, a platform or loopback source (never pushed: onboarding
        plan S1 / D-8), a public source whose URL or key is a deployment's
        login, no key, credentialed plain http (G-4).
        """
        if src is None:
            return f"Error: source '{name}' not found locally."
        never = _never_pushed(src) or _deployment_login_reason(src)
        if never:
            return f"Error: refusing to push source '{name}': {never}."
        if not src.api_key:
            return f"Error: source '{name}' has no api_key — nothing to push."
        if _is_credentialed_http_unsafe(src.url):
            return "Error: refusing to push a credentialed non-localhost http:// source (G-4)."
        return None

    def _confirm_push(self, src) -> bool:
        fp = _key_fingerprint(src.api_key)
        confirm = (
            input(f"Push '{src.name}' ({src.url}, {fp}) to this platform? [y/N] ").strip().lower()
        )
        return confirm == "y"

    def _do_push(self, src, allow_paths: list[str], *, as_json: bool = False) -> int:
        """Server push + result render (network isolated here for CCN)."""
        provider_kind = src.provider or ("openai_compat" if src.kind == "public" else src.kind)
        from ..client import Client
        from ..user_sources import UserSourcesClient

        try:
            result = UserSourcesClient(Client()).push(
                name=src.name,
                provider_kind=provider_kind,
                base_url=src.url,
                api_key=src.api_key,
                allow_paths=allow_paths,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Error: push failed: {exc}", file=sys.stderr)
            return 1
        if as_json:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "name": src.name,
                        "allow_host": result.get("allow_host"),
                        "key_fingerprint": result.get("key_fingerprint"),
                        "seal_key_id": result.get("seal_key_id"),
                        "replaced": bool(result.get("replaced")),
                    }
                )
            )
            return 0
        replaced = " (replaced)" if result.get("replaced") else ""
        print(f"Pushed '{src.name}'{replaced}.")
        print(f"  allow_host: {result.get('allow_host')}")
        print(f"  fingerprint: {result.get('key_fingerprint')}  seal: {result.get('seal_key_id')}")
        return 0

    def _handle_push(self, args: Namespace) -> int:
        from ..source import get_source_store

        src = get_source_store().get(args.name)
        error = self._push_precondition_error(src, args.name)
        if error:
            print(error, file=sys.stderr)
            return 1
        if not getattr(args, "yes", False) and not self._confirm_push(src):
            print("Aborted.")
            return 1
        allow_paths = [p for p in (args.allow_paths or "").split(",") if p]
        return self._do_push(src, allow_paths, as_json=bool(getattr(args, "as_json", False)))

    def _handle_unpush(self, args: Namespace) -> int:
        from ..client import Client
        from ..user_sources import UserSourcesClient

        try:
            result = UserSourcesClient(Client()).unpush(args.name)
        except Exception as exc:  # noqa: BLE001
            print(f"Error: unpush failed: {exc}", file=sys.stderr)
            return 1
        revoked = int(result.get("leases_revoked", 0) or 0)
        if getattr(args, "as_json", False):
            print(json.dumps({"ok": True, "name": args.name, "leases_revoked": revoked}))
            return 0
        print(f"Unpushed '{args.name}' (leases revoked: {revoked}).")
        return 0

    @staticmethod
    def _audit_payload(client, name: str, limit: int) -> tuple[str | None, list[dict]]:
        """Rows + audit entries; (None, ...) when the source is not pushed."""
        rows = client.list_mine(with_leases=True)
        row = next((r for r in rows if r.get("name") == name), None)
        if row is None:
            return None, []
        entries = client.audit(source_id=str(row.get("id")), limit=limit)
        return row, entries

    @staticmethod
    def _print_audit_rows(name: str, row: dict, entries: list[dict]) -> None:
        import json as _json

        print(f"audit: {name} ({row.get('allow_host')})  fingerprint {row.get('key_fingerprint')}")
        print("-" * 72)
        for entry in entries:
            when = str(entry.get("created_at", ""))[:19].replace("T", " ")
            detail = _json.dumps(entry.get("detail") or {}, separators=(",", ":"), default=str)
            print(f"{when}  {entry.get('action'):<8} {entry.get('actor_type'):<8} {detail[:70]}")

    def _handle_audit(self, args: Namespace) -> int:
        import json as _json

        from ..client import Client
        from ..user_sources import UserSourcesClient

        client = UserSourcesClient(Client())
        try:
            row, entries = self._audit_payload(client, args.name, getattr(args, "limit", 20))
        except Exception as exc:  # noqa: BLE001
            print(f"Error: sealed remote request failed: {exc}", file=sys.stderr)
            return 1
        if row is None:
            print(f"Error: source '{args.name}' is not pushed (nothing to audit).", file=sys.stderr)
            return 1
        if getattr(args, "as_json", False):
            print(_json.dumps(entries, indent=2, default=str))
            return 0
        if not entries:
            print(f"(no audit rows for '{args.name}')")
            return 0
        self._print_audit_rows(args.name, row, entries)
        return 0

    def _handle_use(self, args: Namespace) -> int:
        from ..source import get_source_store, set_umrs_active_profile

        store = get_source_store()
        name = getattr(args, "name", None)

        if not name:
            store.set_active(None)
            print("Cleared active source.")
            return 0

        source = store.get(name)
        if not source:
            print(
                f"Source '{name}' not found. Use `r4 source list` to see available sources.",
                file=sys.stderr,
            )
            return 1

        store.set_active(name)
        print(f"Active source: {name} ({source.kind}: {source.url})")

        # Sync UMRS active profile for platform sources
        if source.kind == "platform":
            if set_umrs_active_profile(name):
                print(f"  Synced umrs active profile: {name}")
            else:
                print(
                    f"  Note: no umrs profile '{name}' found. "
                    f"Create one with: r4 source add {name} --url {source.url}"
                )
        return 0
