"""``r4 onboard`` command handler.

Guided first-run setup that walks the user through gateway URL, auth,
project selection, model selection, and R4.md scaffolding.
Inspired by goal-driven profile initialization flows.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command


def _prompt(msg: str, default: str = "") -> str:
    """Read a line from stdin with an optional default."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{msg}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(130)
    return answer or default


def _yes_no(msg: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = _prompt(f"{msg} ({hint})", "y" if default else "n")
    return answer.lower() in ("y", "yes")


def _run_onboard(args: Namespace) -> int:
    """Run the guided onboarding flow.

    Walks the user through: gateway URL, auth, project, model, R4.md
    scaffolding, and an optional health check.  Inspired by openclaw
    ``openclaw onboard --install-daemon`` and openclaude's profile
    initialization flows.
    """
    print("=" * 64)
    print("  R4 Onboarding Wizard")
    print("=" * 64)
    print()
    print("This wizard helps you configure R4 for the first time:")
    print("  1. Gateway connection and authentication")
    print("  2. Default project and model")
    print("  3. Repository briefing file (R4.md)")
    print("  4. Health check (optional)")
    print()

    inputs = _collect_onboard_inputs()
    profile = getattr(args, "profile", "default") or "default"

    # Step 1: Save profile
    # ProfileConfigStore: ``ProfileStore`` never existed in r4.config, so
    # `r4 onboard` raised ImportError here before 2026-10-15.
    from ..config import ProfileConfigStore

    store = ProfileConfigStore()
    _save_onboard_profile(profile, store, inputs)
    _sync_onboard_source(
        profile,
        inputs["gateway_url"],
        inputs.get("api_key", ""),
        username=inputs.get("umrs_username"),
    )

    # Step 2: OIDC if no API key
    api_key = inputs.get("api_key", "")
    if not api_key:
        _maybe_configure_oidc(profile, api_key)

    # Step 3: R4.md scaffolding
    _maybe_scaffold_briefing()

    # Step 4: Optional health check
    _maybe_run_onboard_health_check(profile, inputs.get("project_id", ""))

    # Branded finale (Aurora theme from packages/r4-design)
    from r4.ui_theme import make_console

    console = make_console()
    console.print()
    console.rule("[r4.title] Onboarding complete")
    console.print("[r4.subtitle]The platform is connected, your profile is saved,[/r4.subtitle]")
    console.print("[r4.subtitle]and the first commands below confirm the setup.[/r4.subtitle]")
    console.print()
    console.print("[r4.accent]Next steps:[/r4.accent]")
    console.print("  [r4.tool]r4 doctor[/r4.tool]              — verify everything works")
    console.print('  [r4.tool]r4 chat "Hello!"[/r4.tool]       — try a quick chat')
    console.print("  [r4.tool]r4 model list[/r4.tool]    — see available models")
    console.print(
        '  [r4.tool]r4 project loop start <id> "<goal>"[/r4.tool]'
        "  — launch an autonomous research loop"
    )
    console.print()
    return 0


def _collect_onboard_inputs() -> dict[str, str]:
    gateway_url = _prompt("Gateway URL", "http://localhost:8080")
    api_key = _prompt("API key (leave blank for OIDC or none)", "")
    project_id = _prompt("Default project ID (optional)", "")
    model = _prompt("Default model", "rivault-gpt-oss-static")
    umrs_username = _prompt("UMRS username (optional)", "")
    return {
        "gateway_url": gateway_url,
        "api_key": api_key,
        "project_id": project_id,
        "model": model,
        "umrs_username": umrs_username,
    }


def _maybe_configure_oidc(profile: str, api_key: str) -> None:
    if api_key or not _yes_no("Use OIDC browser login?", default=False):
        return
    try:
        from ..auth import get_auth_manager
        from ..oidc import oidc_login

        mgr = get_auth_manager(profile)
        result = oidc_login(mgr)
        if result.success:
            print("  ✓ OIDC authentication configured")
        else:
            print(f"  ✗ OIDC login failed: {result.error_description}", file=sys.stderr)
    except ImportError:
        print("  ✗ OIDC support requires httpx: pip install httpx", file=sys.stderr)
    except Exception as exc:
        print(f"  ✗ OIDC setup failed: {exc}", file=sys.stderr)


def _save_onboard_profile(profile: str, store, inputs: dict[str, str]) -> None:
    from ..auth import LOGIN_BINDING, credential_digest
    from ..config import _normalize_gateway_url

    home = _normalize_gateway_url(inputs["gateway_url"]) or None
    key = inputs["api_key"] if home else ""
    store.save(
        profile,
        gateway_url=inputs["gateway_url"],
        # Where this login belongs (onboarding plan S1, 2026-10-15), bound to
        # the key it names (stab cycle 3: `r4 logout --revoke-server-session`).
        login_gateway_url=home,
        **{LOGIN_BINDING: credential_digest(key) if key else None},
        api_key=inputs["api_key"] or None,
        project_id=inputs["project_id"] or None,
        model=inputs["model"],
        umrs_username=inputs["umrs_username"] or None,
    )


def _sync_onboard_source(
    profile: str,
    gateway_url: str,
    api_key: str,
    *,
    username: str | None = None,
) -> None:
    from dataclasses import replace

    from ..config import _normalize_gateway_url
    from ..source import Source, get_source_store, is_reserved_source_name

    # Normalize URL so umrs profile matches Config.from_env() output
    gateway_url = _normalize_gateway_url(gateway_url)

    source_store = get_source_store()
    existing_source = source_store.get(profile) or _platform_source_at(source_store, gateway_url)
    source_api_key = api_key or None

    if existing_source and existing_source.kind == "platform":
        # Every other field of the row survives a login (2026-10-15): its
        # default model and project, OIDC and CURVE settings, ZMQ, provider.
        updated_source = replace(
            existing_source, url=gateway_url.rstrip("/"), api_key=source_api_key
        )
        _save_onboard_source(
            existing_source.name, source_store, updated_source, created=False, username=username
        )
    elif not existing_source and is_reserved_source_name(profile):
        print(
            f"  note: '{profile}' is a reserved source name (decision D-8); "
            "no source row was written for this login."
        )
    elif not existing_source:
        new_source = Source(
            name=profile,
            kind="platform",
            url=gateway_url.rstrip("/"),
            api_key=source_api_key,
        )
        _save_onboard_source(profile, source_store, new_source, created=True, username=username)


def _platform_source_at(source_store, gateway_url: str):
    """Deduplicate: the platform source already pointing at this URL, if any —
    a login updates that row instead of creating a duplicate."""
    normalized = gateway_url.rstrip("/")
    for src in source_store.list():
        if src.kind == "platform" and src.url.rstrip("/") == normalized:
            return src
    return None


def _save_onboard_source(
    profile: str,
    source_store,
    source,
    *,
    created: bool,
    username: str | None = None,
) -> None:
    from ..source import set_umrs_active_profile, sync_umrs_connection_profile

    source_store.add(source)
    source_store.set_active(profile)
    sync_umrs_connection_profile(source, username=username)
    set_umrs_active_profile(profile)
    verb = "Created source and" if created else "Synced"
    print(f"  ✓ {verb} umrs profile '{profile}'")


def _maybe_scaffold_briefing() -> None:
    from ..briefing import find_r4_md, scaffold_r4_md

    if find_r4_md() is None and _yes_no(
        "\nCreate an R4.md agent briefing in the current directory?"
    ):
        try:
            path = scaffold_r4_md()
            print(f"  ✓ Created {path}")
        except FileExistsError:
            print("  – R4.md already exists")


def _maybe_run_onboard_health_check(profile: str, project_id: str) -> None:
    if not _yes_no("\nRun a quick health check?"):
        return
    from ..client import Client
    from ..config import Config

    client = Client(config=Config.from_env(profile))
    _check_gateway_readiness(client)
    _check_rag_readiness(client, project_id)
    _check_mcp_readiness(client)


def _check_gateway_readiness(client) -> None:
    try:
        readiness = client.readiness()
        if readiness.get("ready"):
            print("  ✓ Gateway is ready")
        else:
            print("  ✗ Gateway is not fully ready")
    except Exception as exc:
        print(f"  ✗ Gateway probe failed: {exc}", file=sys.stderr)


def _check_rag_readiness(client, project_id: str) -> None:
    try:
        stats = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/rag/stats",
            params={"project_id": project_id or "default"},
        )
        print(f"  ✓ RAG service reachable ({stats.get('documents', 0)} docs indexed)")
    except Exception:
        print("  – RAG service not reachable (may not be deployed yet)")


def _check_mcp_readiness(client) -> None:
    try:
        servers = client.get(f"{getattr(client, '_api_prefix', '/gw')}/mcp/servers")
        count = len(servers) if isinstance(servers, list) else 0
        print(f"  ✓ MCP registry reachable ({count} servers registered)")
    except Exception:
        print("  – MCP registry not reachable (may not be deployed yet)")


@command
class OnboardCommand(CommandHandler):
    """``r4 onboard`` — guided first-run setup."""

    name = "onboard"
    help = "Guided setup wizard for first-time users"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        p.add_argument("--profile", default="default", help="Profile name")
        return p

    def handle(self, args: Namespace) -> int:
        return _run_onboard(args)
