"""``r4 health`` command handler (Plan 2 scaffold).

This is the first handler extracted from the ``r4.cli`` monolith into the
new :mod:`r4._cli` registry. It demonstrates the pattern; subsequent
command groups (chat, rag, model, ...) follow the same structure.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace
from typing import TYPE_CHECKING

from ._helpers import _print_active_context, _print_readiness_report, _probe_umrs_direct
from .base import CommandHandler
from .diagnostics import (
    check_account,
    diagnostics_exit_code,
    emit_account_status,
    emit_full_diagnostics,
    health_context,
    health_payload,
    print_json,
    probe_umrs_direct_payload,
    run_full_diagnostics,
)
from .registry import command

if TYPE_CHECKING:
    from ..config import Config
    from ..source import Source


def _with_direct_umrs(payload: dict[str, object], direct_umrs: bool) -> dict[str, object]:
    if direct_umrs:
        payload["direct_umrs"] = probe_umrs_direct_payload("http://localhost:8000")
    return payload


def _emit_health_failure(config, error: Exception, *, as_json: bool, direct_umrs: bool) -> None:
    payload = _with_direct_umrs(
        {"ready": False, "error": str(error), "context": health_context(config)},
        direct_umrs,
    )
    if as_json:
        print_json(payload)
        return

    print(
        f"✗ Gateway readiness check failed for profile "
        f"'{config.profile}' against {config.gateway_url}: {error}",
        file=sys.stderr,
    )
    _print_active_context(config, heading="Checked context")
    if direct_umrs:
        _probe_umrs_direct()


def _emit_health_success(config, readiness: dict, *, as_json: bool, direct_umrs: bool) -> None:
    payload = _with_direct_umrs(health_payload(config, readiness), direct_umrs)
    if as_json:
        print_json(payload)
        return

    _print_readiness_report(readiness)
    _print_active_context(config, heading="Checked context")
    if direct_umrs:
        _probe_umrs_direct()


def _public_health_context(config: Config, source: Source) -> dict[str, object]:
    """The checked context of an active public source (K1): its own fields."""
    return {
        "profile": config.profile,
        "source": source.name,
        "kind": "public",
        "endpoint_url": source.url,
        "provider": source.provider,
        "model": config.model,
        "auth_configured": bool(config.endpoint_api_key),
    }


def _run_public_health(source: Source, config: Config, *, as_json: bool) -> int:
    """``r4 health`` on an active public source: probe ITS model list, not the
    localhost platform gateway (K1, 2026-10-21)."""
    from ..model_routing import probe_source_models

    models, error = probe_source_models(source, timeout=10)
    payload: dict[str, object] = {
        "ready": error is None,
        "models": models,
        "context": _public_health_context(config, source),
    }
    if error is not None:
        payload["error"] = error
    if as_json:
        print_json(payload)
    elif error is None:
        print(f"✓ Public source '{source.name}' is reachable ({len(models)} model(s))")
    else:
        print(f"✗ Public source '{source.name}' check failed: {error}", file=sys.stderr)
    if not as_json:
        _print_active_context(config, heading="Checked context")
    return 0 if error is None else 1


def _run_health_quick(args: Namespace) -> int:
    from ..client import Client
    from ..errors import R4Error
    from ._helpers import active_source_view

    config, public_source = active_source_view(args)
    as_json = bool(getattr(args, "as_json", False))
    if public_source is not None:
        return _run_public_health(public_source, config, as_json=as_json)
    client = Client(config=config)
    direct_umrs = bool(getattr(args, "direct_umrs", False))

    try:
        readiness = client.readiness()
    except R4Error as exc:
        _emit_health_failure(config, exc, as_json=as_json, direct_umrs=direct_umrs)
        return 1

    _emit_health_success(config, readiness, as_json=as_json, direct_umrs=direct_umrs)
    return 0 if readiness.get("ready") else 1


def _run_health_full(args: Namespace) -> int:
    from ..config import get_config

    checks = run_full_diagnostics(get_config())
    emit_full_diagnostics(checks, as_json=bool(getattr(args, "as_json", False)))
    return diagnostics_exit_code(checks)


def _run_health_account(args: Namespace) -> int:
    from ..config import get_config

    status = check_account(get_config())
    emit_account_status(status, as_json=bool(getattr(args, "as_json", False)))
    return 0 if status.get("ok") else 1


def _run_health(args: Namespace) -> int:
    command = getattr(args, "health_command", None) or "quick"
    if command == "full":
        return _run_health_full(args)
    if command == "account":
        return _run_health_account(args)
    return _run_health_quick(args)


# ---------------------------------------------------------------------------
# Multi-source health
# ---------------------------------------------------------------------------


def _run_health_all_sources_quick(args: Namespace) -> int:
    """Probe all registered platform sources and display per-source status."""
    from ..source import get_platform_sources, get_source_store

    as_json = bool(getattr(args, "as_json", False))
    sources = get_platform_sources()
    if not sources:
        return _no_sources_error(as_json)

    active_name = get_source_store().get_active()
    results = []
    for source in sources:
        result = _probe_source_quick(source)
        result["active"] = source.name == active_name
        results.append(result)

    if as_json:
        print_json({"sources": results})
    else:
        _print_source_health_table(results)

    return 0 if all(r.get("ready") for r in results) else 1


def _no_sources_error(as_json: bool) -> int:
    if as_json:
        print_json({"sources": [], "error": "no platform sources registered"})
    else:
        print("No platform sources registered.")
        print("  Add one with: r4 source add <name> --url <url> --kind platform")
    return 1


def _probe_source_quick(source) -> dict[str, object]:
    """Probe a single source gateway and return a result dict."""
    import time

    from ..client import Client
    from ..config import get_config
    from ..errors import R4Error
    from ..source_context import config_for_source

    config = config_for_source(source, get_config())  # its own credential only (S1)
    client = Client(config=config)
    start = time.time()
    try:
        readiness = client.readiness()
        latency_ms = int((time.time() - start) * 1000)
        return {
            "name": source.name,
            "url": source.url,
            "ready": bool(readiness.get("ready")),
            "latency_ms": latency_ms,
        }
    except (R4Error, Exception) as exc:
        latency_ms = int((time.time() - start) * 1000)
        return {
            "name": source.name,
            "url": source.url,
            "ready": False,
            "latency_ms": latency_ms,
            "error": str(exc),
        }


def _print_source_health_table(results: list[dict[str, object]]) -> None:
    """Print a compact per-source health table."""
    print("Source Health:\n")
    print(f"  {'':2}{'SOURCE':<16} {'STATUS':<10} {'LATENCY':<10} {'URL'}")
    print(f"  {'':2}{'-' * 14}   {'-' * 8}   {'-' * 8}   {'-' * 40}")
    for r in results:
        marker = "* " if r.get("active") else "  "
        ready = r.get("ready")
        status = "✓ ready" if ready else "✗ down"
        latency = f"{r.get('latency_ms', '?')}ms"
        url = r.get("url", "")
        error_hint = f"  ({r['error']})" if r.get("error") else ""
        print(f"  {marker}{r['name']:<16} {status:<10} {latency:<10} {url}{error_hint}")


def _run_health_all_sources_full(args: Namespace) -> int:
    """Run full diagnostics against all registered platform sources."""
    from ..source import get_platform_sources, get_source_store

    as_json = bool(getattr(args, "as_json", False))
    sources = get_platform_sources()
    if not sources:
        return _no_sources_error(as_json)

    active_name = get_source_store().get_active()
    all_results = _collect_full_diagnostics(sources, active_name)

    if as_json:
        print_json({"sources": all_results})
    else:
        _print_full_diagnostics_per_source(all_results)

    any_fail = any(diagnostics_exit_code(info["checks"]) != 0 for info in all_results.values())
    return 1 if any_fail else 0


def _collect_full_diagnostics(sources, active_name: str | None) -> dict:
    """Run full diagnostics for each source and return results keyed by name."""
    from ..config import get_config
    from ..source_context import config_for_source

    all_results = {}
    for source in sources:
        config = config_for_source(source, get_config())  # its own credential only (S1)
        checks = run_full_diagnostics(config)
        all_results[source.name] = {
            "url": source.url,
            "active": source.name == active_name,
            "checks": checks,
        }
    return all_results


def _print_full_diagnostics_per_source(all_results: dict) -> None:
    """Render full diagnostics output grouped by source."""
    for name, info in all_results.items():
        active_mark = " (active)" if info["active"] else ""
        title = f"Source: {name}{active_mark}  ({info['url']})"
        emit_full_diagnostics(
            info["checks"],
            as_json=False,
            title=title,
        )
        print()


def _add_common_flags(parser: ArgumentParser, *, direct_umrs: bool = True) -> None:
    if direct_umrs:
        parser.add_argument(
            "--direct-umrs",
            action="store_true",
            help=(
                "Also probe UMRS health directly (bypassing the gateway). "
                "Useful for distinguishing 'gateway down' from 'platform down'."
            ),
        )
    parser.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")
    parser.add_argument(
        "--all-sources",
        action="store_true",
        help="Probe all registered platform sources instead of just the active one",
    )


@command
class HealthCommand(CommandHandler):
    """``r4 health`` — Gateway readiness probe."""

    name = "health"
    help = "Check platform health and account status"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        _add_common_flags(p)
        sub = p.add_subparsers(
            dest="health_command",
            title="subcommands",
            metavar="<health-command>",
        )
        quick_p = sub.add_parser("quick", help="Quick Gateway readiness check")
        _add_common_flags(quick_p)
        full_p = sub.add_parser("full", help="Comprehensive platform diagnostics")
        _add_common_flags(full_p, direct_umrs=False)
        account_p = sub.add_parser("account", help="Check local credentials against Gateway")
        _add_common_flags(account_p, direct_umrs=False)
        return p

    def handle(self, args: Namespace) -> int:
        # Multi-source mode: dispatch to all-sources handlers
        if getattr(args, "all_sources", False):
            command = getattr(args, "health_command", None) or "quick"
            if command == "full":
                return _run_health_all_sources_full(args)
            return _run_health_all_sources_quick(args)
        return _run_health(args)


def cmd_health(args):
    """Handle health command."""
    rc = _run_health(args)
    if rc:
        sys.exit(rc)
