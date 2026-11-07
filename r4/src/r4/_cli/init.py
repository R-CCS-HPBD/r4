"""``r4 init`` command handler (bug.memo D3 Phase 2g, 2026-04-25)."""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace

from ..source_locality import display_url
from ._helpers import _configure_profile_auth, _print_active_context, _print_readiness_report
from .base import CommandHandler
from .registry import command


@command
class InitCommand(CommandHandler):
    """``r4 init`` — configure a profile and validate gateway readiness."""

    name = "init"
    help = "Set up a working R4 profile"

    def register(self, subparsers) -> ArgumentParser:
        from ..config import gateway_url_arg

        p = subparsers.add_parser(self.name, help=self.help)
        p.add_argument("--profile", default="default", help="Profile name")
        p.add_argument("--gateway-url", type=gateway_url_arg, help="Gateway base URL")
        p.add_argument("--project", help="Default project ID")
        p.add_argument("--model", help="Default model name")
        p.add_argument("--api-key", help="API key for direct authentication")
        p.add_argument("--oidc", action="store_true", help="Use OIDC browser login")
        p.add_argument("--timeout", type=int, default=300, help="OIDC auth timeout (seconds)")
        p.add_argument(
            "--umrs-username",
            help="Default UMRS username for job and data operations",
        )
        verify_group = p.add_mutually_exclusive_group()
        verify_group.add_argument(
            "--verify-ssl",
            dest="verify_ssl",
            action="store_true",
            default=None,
            help="Verify TLS certificates when calling the Gateway",
        )
        verify_group.add_argument(
            "--no-verify-ssl",
            dest="verify_ssl",
            action="store_false",
            help="Disable TLS certificate verification",
        )
        p.add_argument(
            "--skip-health-check",
            action="store_true",
            help="Skip the gateway readiness check",
        )
        return p

    def handle(self, args: Namespace) -> int:
        import warnings

        warnings.warn(
            "`r4 init` is deprecated. Use `r4 auth login --gateway-url <URL>` instead.",
            DeprecationWarning,
            stacklevel=1,
        )
        print(
            "Note: `r4 init` is deprecated. "
            "Use `printf '%s' \"$TOKEN\" | r4 auth login --gateway-url <URL> --api-key-stdin` "
            "instead.",
            file=sys.stderr,
        )

        from ..cli import (
            _configure_profile_auth,
            _print_active_context,
            _print_readiness_report,
        )
        from ..client import Client
        from ..config import Config, ProfileConfigStore, set_config
        from ..project import ProjectConfig, get_project_manager

        store = ProfileConfigStore()
        stored = store.load(args.profile)

        gateway_url = args.gateway_url or str(stored.get("gateway_url") or "http://localhost:8080")
        project_id = args.project or stored.get("project_id")
        model = args.model or str(stored.get("model") or "rivault-gpt-oss-static")
        verify_ssl = (
            args.verify_ssl
            if args.verify_ssl is not None
            else str(stored.get("verify_ssl", True)).lower() in ("true", "1", "yes", "on")
        )
        umrs_username = args.umrs_username or stored.get("umrs_username")

        store.save(
            args.profile,
            gateway_url=gateway_url,
            project_id=project_id,
            model=model,
            verify_ssl=verify_ssl,
            umrs_username=umrs_username,
        )

        if project_id:
            project_manager = get_project_manager()
            existing = project_manager.get(project_id)
            project_manager.create(
                ProjectConfig(
                    project_id=project_id,
                    name=existing.name if existing else None,
                    gateway_url=gateway_url,
                    model=model,
                    metadata=existing.metadata if existing else {},
                )
            )
            project_manager.set_current(project_id)

        print(f"Saved profile '{args.profile}' configuration:")
        print(f"  Gateway URL: {display_url(gateway_url)}")
        print(f"  Project ID: {project_id or '(not set)'}")
        print(f"  Model: {model}")
        print(f"  Verify SSL: {verify_ssl}")
        if umrs_username:
            print(f"  UMRS Username: {umrs_username}")

        auth_configured = _configure_profile_auth(
            args.profile,
            api_key=args.api_key,
            oidc=args.oidc,
            timeout=args.timeout,
            prompt_if_missing=sys.stdin.isatty() and sys.stdout.isatty(),
        )

        config = Config.from_env(profile=args.profile)
        set_config(config)

        errors = config.validate()
        if not auth_configured:
            errors.append(
                f"No credentials configured for profile '{args.profile}'. "
                "Use --api-key, --oidc, or run 'r4 auth login'."
            )

        if args.skip_health_check:
            print("Skipped gateway readiness check")
        else:
            try:
                readiness = Client(config=config).readiness()
            except Exception as e:
                errors.append(f"Gateway readiness check failed: {e}")
            else:
                _print_readiness_report(readiness)
                if not readiness.get("ready"):
                    errors.append(readiness.get("message") or "Gateway is not ready")

        if errors:
            print("Initialization incomplete:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            print(
                f"\nNote: profile '{args.profile}' WAS saved to "
                "~/.config/r4/profiles.json.\n"
                f"      Run `r4 health` once the gateway is reachable, or re-run\n"
                f"      `r4 config init --skip-health-check` to suppress this check.",
                file=sys.stderr,
            )
            return 1

        print(f"✓ Profile '{args.profile}' is ready")
        _print_active_context(config)

        # Auto-install shell tab completion
        from .completion import install_completion

        msg = install_completion("r4")
        print(f"  {msg}")

        return 0


def cmd_init(args):
    """Configure a profile and validate that it can reach the Gateway."""
    from ..client import Client
    from ..config import Config, ProfileConfigStore, set_config
    from ..project import ProjectConfig, get_project_manager

    store = ProfileConfigStore()
    stored = store.load(args.profile)

    gateway_url = args.gateway_url or str(stored.get("gateway_url") or "http://localhost:8080")
    project_id = args.project or stored.get("project_id")
    model = args.model or str(stored.get("model") or "rivault-gpt-oss-static")
    verify_ssl = (
        args.verify_ssl
        if args.verify_ssl is not None
        else str(stored.get("verify_ssl", True)).lower() in ("true", "1", "yes", "on")
    )
    umrs_username = args.umrs_username or stored.get("umrs_username")

    store.save(
        args.profile,
        gateway_url=gateway_url,
        project_id=project_id,
        model=model,
        verify_ssl=verify_ssl,
        umrs_username=umrs_username,
    )

    if project_id:
        project_manager = get_project_manager()
        existing = project_manager.get(project_id)
        project_manager.create(
            ProjectConfig(
                project_id=project_id,
                name=existing.name if existing else None,
                gateway_url=gateway_url,
                model=model,
                metadata=existing.metadata if existing else {},
            )
        )
        project_manager.set_current(project_id)

    print(f"Saved profile '{args.profile}' configuration:")
    print(f"  Gateway URL: {display_url(gateway_url)}")
    print(f"  Project ID: {project_id or '(not set)'}")
    print(f"  Model: {model}")
    print(f"  Verify SSL: {verify_ssl}")
    if umrs_username:
        print(f"  UMRS Username: {umrs_username}")

    auth_configured = _configure_profile_auth(
        args.profile,
        api_key=args.api_key,
        oidc=args.oidc,
        timeout=args.timeout,
        prompt_if_missing=sys.stdin.isatty() and sys.stdout.isatty(),
    )

    config = Config.from_env(profile=args.profile)
    set_config(config)

    errors = config.validate()
    if not auth_configured:
        errors.append(
            f"No credentials configured for profile '{args.profile}'. "
            f"Use --api-key, --oidc, or run 'r4 auth login'."
        )

    if args.skip_health_check:
        print("Skipped gateway readiness check")
    else:
        try:
            readiness = Client(config=config).readiness()
        except Exception as e:
            errors.append(f"Gateway readiness check failed: {e}")
        else:
            _print_readiness_report(readiness)
            if not readiness.get("ready"):
                errors.append(readiness.get("message") or "Gateway is not ready")

    if errors:
        print("Initialization incomplete:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        # bug.memo H5 (2026-04-25): make the next-step explicit — the
        # profile file IS saved even when the health-check fails, so
        # users don't need to retry `r4 init` from a different directory.
        print(
            f"\nNote: profile '{args.profile}' WAS saved to ~/.config/r4/profiles.json.\n"
            f"      Run `r4 health` once the gateway is reachable, or re-run\n"
            f"      `r4 config init --skip-health-check` to suppress this check.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"✓ Profile '{args.profile}' is ready")
    _print_active_context(config)
