"""``r4 config`` command handler (bug.memo D3 Phase 2g, 2026-04-25)."""

from __future__ import annotations

import json
import os
import sys
from argparse import ArgumentParser, Namespace
from typing import Any

from .base import CommandHandler
from .registry import command


def _has_context_updates(args: Namespace) -> bool:
    """Return whether ``r4 config context`` should persist any changes."""
    return any(
        (
            getattr(args, "project", None) is not None,
            getattr(args, "clear_project", False),
            getattr(args, "umrs_username", None) is not None,
            getattr(args, "clear_user", False),
            getattr(args, "default_venue", None) is not None,
            getattr(args, "clear_default_venue", False),
        )
    )


def _sync_project_selection(project_id: str | None, config) -> None:
    """Keep the local project registry aligned with the saved profile context."""
    from ..project import ProjectConfig, get_project_manager

    project_manager = get_project_manager()
    if not project_id:
        project_manager.set_current(None)
        return

    existing = project_manager.get(project_id)
    project_manager.create(
        ProjectConfig(
            project_id=project_id,
            name=existing.name if existing else None,
            gateway_url=(
                existing.gateway_url if existing and existing.gateway_url else config.gateway_url
            ),
            model=existing.model if existing and existing.model else config.model,
            metadata=existing.metadata if existing else {},
        )
    )
    project_manager.set_current(project_id)


def _print_context_override_note() -> None:
    """Warn when environment variables override the saved local context."""
    overrides: list[str] = []
    if os.environ.get("R4_PROJECT_ID"):
        overrides.append("R4_PROJECT_ID")
    if os.environ.get("UMRS_USERNAME"):
        overrides.append("UMRS_USERNAME")
    if overrides:
        joined = ", ".join(overrides)
        print(f"  Note: environment overrides are active: {joined}")


def _print_local_context(config, *, heading: str) -> None:
    """Render the local user/project context succinctly."""
    from ..config import ProfileConfigStore

    venue = ProfileConfigStore().load(config.profile).get("session_default_venue")
    print(f"{heading}:")
    print(f"  Profile: {config.profile}")
    print(f"  Project: {config.project_id or '(not set)'}")
    print(f"  UMRS Username: {config.umrs_username or '(not set)'}")
    print(f"  Default session venue: {venue or '(local)'}")
    _print_context_override_note()


def _emit_local_context(config: Any, *, heading: str, as_json: bool) -> None:
    """``config context`` output: the text view, or with ``--json`` one JSON
    document. ``--json`` used to be honoured only by ``context list`` and was
    silently ignored here (c2-cli, 2026-10-21)."""
    if not as_json:
        _print_local_context(config, heading=heading)
        return
    from ..config import ProfileConfigStore

    venue = ProfileConfigStore().load(config.profile).get("session_default_venue")
    doc = {
        "profile": config.profile,
        "project_id": config.project_id,
        "umrs_username": config.umrs_username,
        "session_default_venue": venue,
    }
    print(json.dumps(doc, indent=2))


def _profile_value(settings: dict[str, object], key: str, default: str = "(not set)") -> str:
    value = settings.get(key)
    if value in (None, ""):
        return default
    return str(value)


def _context_list_rows(config) -> list[dict[str, object]]:
    """Collect saved profile contexts for human and JSON rendering."""
    from ..auth import get_auth_manager
    from ..config import ProfileConfigStore
    from ..project import get_project_manager

    profiles = ProfileConfigStore().list_profiles()
    projects = {project.project_id: project for project in get_project_manager().list()}
    rows: list[dict[str, object]] = []

    for profile, settings in profiles.items():
        project_id = settings.get("project_id")
        project_name = None
        if isinstance(project_id, str) and project_id in projects:
            project_name = projects[project_id].name
        try:
            auth_configured = get_auth_manager(profile).is_authenticated()
        except Exception:
            auth_configured = False
        rows.append(
            {
                "active": profile == config.profile,
                "profile": profile,
                "project_id": project_id,
                "project_name": project_name,
                "umrs_username": settings.get("umrs_username"),
                "gateway_url": settings.get("gateway_url"),
                "model": settings.get("model"),
                "auth_configured": auth_configured,
            }
        )
    return rows


def _handle_context_list(args: Namespace, config) -> int:
    """List saved local contexts for all profiles."""
    rows = _context_list_rows(config)
    if getattr(args, "as_json", False):
        print(json.dumps({"active_profile": config.profile, "contexts": rows}, indent=2))
        return 0

    if not rows:
        print("No saved profile contexts.")
        print("  Run `r4 config init --profile <name> ...` to create one.")
        return 0

    print("Saved profile contexts:")
    for row in rows:
        _print_context_row(row)
    _print_context_override_note()
    return 0


def _print_context_row(row: dict[str, object]) -> None:
    """One `config context list` row (rendering only; extracted for the gate)."""
    marker = "*" if row["active"] else " "
    project = row.get("project_id") or "(not set)"
    project_name = row.get("project_name")
    if project_name:
        project = f"{project} ({project_name})"
    print(f"{marker} {row['profile']}")
    print(f"    Project: {project}")
    print(f"    UMRS Username: {row.get('umrs_username') or '(not set)'}")
    print(f"    Gateway: {row.get('gateway_url') or '(default)'}")
    print(f"    Model: {row.get('model') or '(default)'}")
    print(f"    Auth configured: {'yes' if row.get('auth_configured') else 'no'}")


def _handle_show(config) -> int:
    """Render the existing read-only config view."""
    from ..config import ProfileConfigStore
    from ._helpers import _print_active_context

    store = ProfileConfigStore()
    persisted = bool(store.load(config.profile))
    suffix = "" if persisted else "  (defaults — run `r4 config init` to persist)"
    print(f"Current configuration:{suffix}")
    _print_active_context(config, heading="Active runtime context")
    print(f"  Verify SSL: {config.verify_ssl}")
    print(f"  UMRS Username: {config.umrs_username or '(not set)'}")
    print(f"  Timeout: {config.timeout}s")
    print(f"  HPC Mode: {config.is_hpc}")
    return 0


def _venue_context_updates(args: Namespace) -> tuple[dict[str, object], str | None]:
    """Map --default-venue/--clear-default-venue onto store updates.

    Returns (updates, error): error non-empty means rc=2 without persisting.
    """
    if getattr(args, "clear_default_venue", False):
        return {"session_default_venue": None}, None
    if getattr(args, "default_venue", None) is None:
        return {}, None
    from ..companion.session_store import validate_venue

    try:
        return {"session_default_venue": validate_venue(args.default_venue)}, None
    except ValueError as exc:
        return {}, str(exc)


def _warm_context_updates(args: Namespace) -> dict[str, object]:
    """Map --project/--user flags onto store updates (pre-venue contract)."""
    updates: dict[str, object] = {}
    if args.clear_project:
        updates["project_id"] = None
    elif args.project is not None:
        updates["project_id"] = args.project
    if args.clear_user:
        updates["umrs_username"] = None
    elif args.umrs_username is not None:
        updates["umrs_username"] = args.umrs_username
    return updates


def _handle_worktree_mode(args: Namespace) -> int:
    """``r4 config worktree auto|off|show`` (ADR-108 W1c)."""
    from ..companion.session_worktree import (
        MODE_ENV,
        profile_mode,
        remember_profile_mode,
    )

    action = getattr(args, "worktree_action", "show") or "show"
    if action != "show":
        remember_profile_mode(action)
        print(f"✓ worktree mode default: {action}")
    stored = profile_mode() or "off"
    env = os.environ.get(MODE_ENV, "").strip()
    note = f" ({MODE_ENV}={env} overrides it in this shell)" if env else ""
    print(f"worktree mode: {stored}{note}")
    print("  auto: the agent moves into a git worktree before its first write")
    print("        (your checkout is never written)")
    return 0


def _handle_context(args: Namespace, config) -> int:
    """Show or update the saved local user/project context."""
    from ..config import ProfileConfigStore, reload_config

    if getattr(args, "context_action", None) == "list":
        return _handle_context_list(args, config)

    as_json = bool(getattr(args, "as_json", False))
    if not _has_context_updates(args):
        _emit_local_context(config, heading="Current local context", as_json=as_json)
        if not as_json:
            print("  Use `r4 config context --project <id> --user <name>` to update it.")
            print("  Use `r4 config context list` to show all saved contexts.")
        return 0

    store = ProfileConfigStore()
    updates = _warm_context_updates(args)
    venue_updates, venue_error = _venue_context_updates(args)
    if venue_error:
        print(venue_error, file=sys.stderr)
        return 2
    updates.update(venue_updates)

    store.save(config.profile, **updates)

    if "project_id" in updates:
        _sync_project_selection(updates["project_id"], config)

    updated = reload_config(profile=config.profile)
    _emit_local_context(
        updated,
        heading=f"Updated local context for profile '{updated.profile}'",
        as_json=as_json,
    )
    return 0


@command
class ConfigCommand(CommandHandler):
    """``r4 config`` — read-only view of the active profile."""

    name = "config"
    help = "Configuration"

    def register(self, subparsers) -> ArgumentParser:
        from argparse import RawDescriptionHelpFormatter

        from ..config import gateway_url_arg

        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Inspect the active profile, update saved local context, "
                "or initialize a new profile."
            ),
            epilog=(
                "Examples:\n"
                "  r4 config show\n"
                "  r4 config context --project demo --user alice\n"
                "  r4 config sandbox on\n"
                "  r4 config init --profile default --gateway-url http://localhost:8080"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = p.add_subparsers(
            dest="config_command",
            title="subcommands",
            metavar="<config-command>",
        )
        sub.required = True
        sub.add_parser("show", help="Show current config")
        # ADR-101 D-2: the profile's sandbox default (config.json only; the
        # project layers under .r4/ never set it).
        sandbox_p = sub.add_parser(
            "sandbox",
            help="Show or set the local tool sandbox default (on|off|show)",
        )
        sandbox_p.add_argument(
            "sandbox_action",
            nargs="?",
            choices=("on", "off", "show"),
            default="show",
            help="on/off writes the profile default; show (default) prints it and the backend",
        )
        # ADR-108 W1c: auto = the agent works in a git worktree by itself.
        worktree_p = sub.add_parser(
            "worktree",
            help="Show or set the worktree mode (auto|off|show): auto isolates the agent's writes",
        )
        worktree_p.add_argument(
            "worktree_action",
            nargs="?",
            choices=("auto", "off", "show"),
            default="show",
            help="auto/off writes the profile default; show (default) prints the mode in effect",
        )

        context_p = sub.add_parser(
            "context",
            help="Show or update local user/project context",
        )
        context_p.add_argument(
            "context_action",
            nargs="?",
            choices=("list",),
            help="Use 'list' to show saved contexts for all profiles",
        )
        context_p.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )
        project_group = context_p.add_mutually_exclusive_group()
        project_group.add_argument(
            "--project",
            help="Persist the default project ID for the active profile",
        )
        project_group.add_argument(
            "--clear-project",
            action="store_true",
            help="Clear the saved default project ID for the active profile",
        )
        user_group = context_p.add_mutually_exclusive_group()
        user_group.add_argument(
            "--user",
            "--umrs-username",
            dest="umrs_username",
            help="Persist the default UMRS username for the active profile",
        )
        user_group.add_argument(
            "--clear-user",
            action="store_true",
            help="Clear the saved default UMRS username for the active profile",
        )
        venue_group = context_p.add_mutually_exclusive_group()
        venue_group.add_argument(
            "--default-venue",
            help=(
                "Persist the default session venue (local | frontend | cluster:<name>). "
                "Session-start pickers pre-select this; first divergence asks a confirm."
            ),
        )
        venue_group.add_argument(
            "--clear-default-venue",
            action="store_true",
            help="Clear the saved default session venue",
        )

        # ``r4 config init`` (Phase 2l, 2026-04-27 — formerly top-level
        # ``r4 init``). Dispatch is remapped to the ``init`` handler in
        # :mod:`r4._cli.init` via ``cli.main``.
        init_p = sub.add_parser("init", help="Set up a working R4 profile")
        init_p.add_argument("--profile", default="default", help="Profile name")
        init_p.add_argument("--gateway-url", type=gateway_url_arg, help="Gateway base URL")
        init_p.add_argument("--project", help="Default project ID")
        init_p.add_argument("--model", help="Default model name")
        init_p.add_argument("--api-key", help="API key for direct authentication")
        init_p.add_argument("--oidc", action="store_true", help="Use OIDC browser login")
        init_p.add_argument("--timeout", type=int, default=300, help="OIDC auth timeout (seconds)")
        init_p.add_argument(
            "--umrs-username",
            help="Default UMRS username for job and data operations",
        )
        init_verify = init_p.add_mutually_exclusive_group()
        init_verify.add_argument(
            "--verify-ssl",
            dest="verify_ssl",
            action="store_true",
            default=None,
            help="Verify TLS certificates when calling the Gateway",
        )
        init_verify.add_argument(
            "--no-verify-ssl",
            dest="verify_ssl",
            action="store_false",
            help="Disable TLS certificate verification",
        )
        init_p.add_argument(
            "--skip-health-check",
            action="store_true",
            help="Skip the gateway readiness check",
        )
        return p

    def handle(self, args: Namespace) -> int:
        from ..config import get_config
        from ._helpers import active_source_view

        if args.config_command == "show":
            return _handle_show(active_source_view(args)[0])  # the ACTIVE source (K1)

        config = get_config()

        if args.config_command == "context":
            return _handle_context(args, config)
        if args.config_command == "sandbox":
            from .sandbox_prompt import handle_config_sandbox

            return handle_config_sandbox(args, config)
        if args.config_command == "worktree":
            return _handle_worktree_mode(args)

        print("Unknown config command", file=sys.stderr)
        return 1


def cmd_config(args):
    """Handle config commands."""
    from ..config import get_config
    from ._helpers import active_source_view

    if args.config_command == "show":
        _handle_show(active_source_view(args)[0])  # the ACTIVE source (K1)
        return

    config = get_config()

    if args.config_command == "context":
        _handle_context(args, config)
        return

    print("Unknown config command", file=sys.stderr)
    sys.exit(1)
