"""``r4 mcp`` command handler (bug.memo D3 Phase 2i, 2026-04-25).

Ported from ``r4.cli.cmd_mcp``. Behaviour is byte-identical; the
handler function still lives in ``r4.cli`` and is imported lazily.
The argparse parser stays in ``_build_parser`` in ``r4.cli``.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace

from ._helpers import _die
from .base import CommandHandler
from .registry import command


@command
class MCPCommand(CommandHandler):
    """``r4 mcp`` — MCP server management."""

    name = "mcp"
    help = "MCP server management"

    def register(self, subparsers) -> ArgumentParser:
        from argparse import RawDescriptionHelpFormatter

        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description="Register servers, inspect tools, and manage user-layer Hatch! packages.",
            epilog=(
                "Examples:\n"
                "  r4 mcp list\n"
                "  r4 mcp register my-server --url http://localhost:9000\n"
                "  r4 mcp hatch create my-package"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = p.add_subparsers(
            dest="mcp_command",
            title="subcommands",
            metavar="<mcp-command>",
        )
        sub.required = True

        # mcp list (X-207)
        mcp_list = sub.add_parser("list", help="List MCP servers and tools")
        mcp_list.add_argument("--servers", "-s", action="store_true", help="List servers only")
        mcp_list.add_argument("--tools", "-t", action="store_true", help="List tools only")
        mcp_list.add_argument("--server", help="Filter by server name")
        mcp_list.add_argument("--healthy", action="store_true", help="Show only healthy servers")
        mcp_list.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        # mcp register (X-206)
        mcp_register = sub.add_parser("register", help="Register an MCP server")
        mcp_register.add_argument("name", help="Server name")
        mcp_register.add_argument("--url", "-u", required=True, help="Server URL")
        mcp_register.add_argument("--description", "-d", default="", help="Server description")
        mcp_register.add_argument(
            "--auth-type", choices=["none", "bearer", "api_key"], default="none"
        )
        mcp_register.add_argument("--auth-token", help="Auth token (for bearer/api_key)")
        mcp_register.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        mcp_unregister = sub.add_parser("unregister", help="Unregister an MCP server")
        mcp_unregister.add_argument("server_id", help="Server ID to unregister")

        mcp_info = sub.add_parser("info", help="Show server details")
        mcp_info.add_argument("server_id", help="Server ID")
        mcp_info.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        mcp_health = sub.add_parser("health", help="Check server health")
        mcp_health.add_argument("server_id", help="Server ID to check")

        # Hatchling MCP subcommands (A-703 to A-710)
        hatch = sub.add_parser(
            "hatch",
            help="Hatchling package management (user-layer MCP server development)",
            description=(
                "Manage Hatch! packages — user-authored MCP server bundles that "
                "publish tools/resources into mini-rivault instances. See "
                "mcp-servers/README.md and docs/r4/ for the authoring guide."
            ),
            epilog=(
                "Examples:\n"
                "  r4 mcp hatch create my-package\n"
                "  r4 mcp hatch install ./my-package\n"
                "  r4 mcp hatch connect my-package"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        hatch_sub = hatch.add_subparsers(
            dest="hatch_command",
            title="subcommands",
            metavar="<hatch-command>",
        )
        hatch_sub.required = True

        h_create = hatch_sub.add_parser("create", help="Create new Hatch! package")
        h_create.add_argument("name", help="Package name")
        h_create.add_argument("--description", "-d", default="", help="Package description")
        h_create.add_argument("--output", "-o", help="Output directory (default: current)")

        h_install = hatch_sub.add_parser("install", help="Install Hatch! package")
        h_install.add_argument("package", help="Package name or path")
        h_install.add_argument(
            "--from",
            # NOT dest="source": the top-level parser's global ``--source NAME``
            # (an r4 endpoint source) shares that attribute, and the
            # subcommand default would make every install try to activate an
            # r4 source literally named "registry".
            dest="hatch_from",
            choices=["registry", "local", "git"],
            default="registry",
            help="Installation source",
        )
        h_install.add_argument("--url", help="Git URL (for --from git)")
        h_install.add_argument("--registry", help="Registry URL/path to use for --from registry")

        h_search = hatch_sub.add_parser("search", help="Search available Hatch! packages")
        h_search.add_argument("query", nargs="?", default="", help="Search query")
        h_search.add_argument("--registry", help="Registry URL/path to search")
        h_search.add_argument("--limit", type=int, default=20, help="Maximum results")
        h_search.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        h_registry = hatch_sub.add_parser("registry", help="Inspect configured Hatch registries")
        h_registry_sub = h_registry.add_subparsers(
            dest="registry_command",
            metavar="<registry-command>",
        )
        h_registry_sub.required = True
        h_registry_list = h_registry_sub.add_parser("list", help="List configured registries")
        h_registry_list.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        h_connect = hatch_sub.add_parser("connect", help="Connect installed package")
        h_connect.add_argument("package", help="Package name to connect")

        h_list = hatch_sub.add_parser("list", help="List installed packages")
        h_list.add_argument(
            "--available",
            "-a",
            action="store_true",
            help="Also show available packages from registry",
        )
        h_list.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        h_disconnect = hatch_sub.add_parser("disconnect", help="Disconnect package")
        h_disconnect.add_argument("package", help="Package name to disconnect")

        h_uninstall = hatch_sub.add_parser("uninstall", help="Uninstall package")
        h_uninstall.add_argument("package", help="Package name to uninstall")
        h_uninstall.add_argument("--force", "-f", action="store_true", help="Skip confirmation")

        h_share = hatch_sub.add_parser("share", help="Share package with project")
        h_share.add_argument("package", help="Package name to share")
        h_share.add_argument("--project", "-p", required=True, help="Target project ID")

        return p

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="mcp")

        cmd_mcp(args)
        return 0


def cmd_mcp(args):
    """Handle MCP commands (X-206, X-207)."""
    import sys

    from ..client import Client

    handler = _mcp_command_handlers().get(args.mcp_command)
    if handler is None:
        print(
            "Unknown mcp command. Use: list, register, unregister, info, health, hatch",
            file=sys.stderr,
        )
        sys.exit(1)
    return handler(args, Client())


def _json_print(data) -> None:
    import json

    print(json.dumps(data, indent=2, default=str))


def _print_mcp_server(server) -> None:
    if not isinstance(server, dict):
        print(f"  • {server}")
        return
    name = server.get("name", "unknown")
    status = server.get("status", "unknown")
    status_icon = "✅" if status == "healthy" else "⚠️"
    print(f"  {status_icon} {name}")
    print(f"      ID: {server.get('server_id') or server.get('id', 'N/A')}")
    print(f"      URL: {server.get('url', 'N/A')}")
    print(f"      Tools: {len(server.get('tools', []))}")


def _print_mcp_servers(servers: list) -> None:
    if not servers:
        print("No MCP servers available.")
        return
    print("MCP Servers:")
    print("-" * 40)
    for server in servers:
        _print_mcp_server(server)


def _print_mcp_tools(tools: list) -> None:
    if not tools:
        print("No MCP tools available.")
        return
    print("\nMCP Tools:")
    print("-" * 60)
    print(f"{'TOOL NAME':<30}  {'SERVER':<15}  DESCRIPTION")
    print("-" * 60)
    for tool in tools:
        desc = tool.description[:40] + "..." if len(tool.description) > 40 else tool.description
        print(f"{tool.name:<30}  {tool.server:<15}  {desc}")


def _cmd_mcp_list(args, client):
    if args.servers or not args.tools:
        servers = client.mcp.list_servers()
        if args.as_json:
            _json_print({"servers": servers})
        else:
            _print_mcp_servers(servers)
    if args.tools or not args.servers:
        tools = client.mcp.list_tools(server=args.server)
        data = {"tools": [tool.__dict__ for tool in tools]}
        if args.as_json:
            _json_print(data)
        else:
            _print_mcp_tools(tools)


def _cmd_mcp_register(args, client):
    from r4.mcp import discover_tools

    auth_config = {"token": args.auth_token} if args.auth_token else {}
    tools = discover_tools(args.url, auth_config)
    result = client.mcp.register_server(
        name=args.name,
        url=args.url,
        tools=tools,
        description=args.description,
        auth_type=args.auth_type,
        auth_config=auth_config,
    )
    if args.as_json:
        _json_print(result)
        return
    print(f"✓ Registered MCP server: {args.name}")
    print(f"  Server ID: {result.get('server_id', 'N/A')}")
    names = ", ".join(str(t.get("name", "?")) for t in tools)
    print(f"  Tools: {len(tools)}" + (f" ({names})" if names else ""))
    if not tools:
        print(
            f"  ⚠ {args.url}/tools listed no tools, so the agent cannot call it. "
            "Is the URL an r4 MCP bridge (r4 mcp hatch, or the remote-proxy runner)?"
        )


def _cmd_mcp_unregister(args, client):
    import sys

    if client.mcp.unregister_server(args.server_id):
        print(f"✓ Unregistered MCP server: {args.server_id}")
        return
    print("✗ Failed to unregister server", file=sys.stderr)
    sys.exit(1)


def _print_mcp_info(info: dict) -> None:
    print(f"MCP Server: {info.get('name', 'unknown')}")
    print("=" * 50)
    print(f"  ID:          {info.get('id')}")
    print(f"  URL:         {info.get('url')}")
    print(f"  Status:      {info.get('status')}")
    print(f"  Description: {info.get('description', 'N/A')}")
    print(f"  Auth Type:   {info.get('auth_type', 'none')}")
    if info.get("last_health_check"):
        print(f"  Last Health: {info['last_health_check']}")
    _print_mcp_info_tools(info.get("tools", []))


def _print_mcp_info_tools(tools: list) -> None:
    if not tools:
        return
    print(f"\n  Tools ({len(tools)}):")
    for tool in tools:
        name = tool.get("name", "unknown") if isinstance(tool, dict) else tool
        print(f"    • {name}")


def _cmd_mcp_info(args, client):
    info = client.mcp.get_server_info(args.server_id)
    if args.as_json:
        _json_print(info)
    else:
        _print_mcp_info(info)


def _cmd_mcp_health(args, client):
    import sys
    from datetime import datetime, timezone
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    from r4.errors import APIError

    try:
        result = client.mcp.check_server_health(args.server_id)
    except APIError as exc:
        if exc.status_code != 404:
            raise
        # Registry miss — try local probe against the servers list.
        # Default/Hatch packages live only locally; they have no UMRS registry
        # entry so the gateway per-server route cannot resolve them.
        servers = client.mcp.list_servers()
        match = next(
            (
                s
                for s in servers
                if s.get("id") == args.server_id or s.get("name") == args.server_id
            ),
            None,
        )
        if match is None:
            print(
                f"MCP server '{args.server_id}' not found in the UMRS registry or "
                "local probe cache.\n"
                "  Local cached defaults do not expose per-server health; only "
                "UMRS-registered servers (added via `r4 mcp register ...`) support "
                "`r4 mcp health`.",
                file=sys.stderr,
            )
            sys.exit(1)
        url = match.get("url", "")
        checked_at = datetime.now(timezone.utc).isoformat()
        healthy = False
        if url:
            try:
                req = Request(url, method="HEAD")
                with urlopen(req, timeout=5):
                    healthy = True
            except URLError:
                healthy = False
        result = {
            "server": match.get("name", args.server_id),
            "server_id": match.get("id", args.server_id),
            "url": url,
            "healthy": healthy,
            "status": "healthy" if healthy else "unhealthy (local probe)",
            "checked_at": checked_at,
        }
    healthy = result.get("healthy", False)
    icon = "✅" if healthy else "❌"
    print(f"{icon} Server health: {'healthy' if healthy else 'unhealthy'}")
    print(f"   Status: {result.get('status', 'unknown')}")
    if result.get("url"):
        print(f"   URL:    {result['url']}")
    if result.get("checked_at"):
        print(f"   Checked: {result['checked_at']}")


def _cmd_mcp_hatch(args, _client):
    cmd_mcp_hatch(args)


def _mcp_command_handlers():
    return {
        "list": _cmd_mcp_list,
        "register": _cmd_mcp_register,
        "unregister": _cmd_mcp_unregister,
        "info": _cmd_mcp_info,
        "health": _cmd_mcp_health,
        "hatch": _cmd_mcp_hatch,
    }


def cmd_mcp_hatch(args):
    """Handle Hatchling MCP package commands (A-703 to A-710)."""
    import sys

    from ..client import Client

    handler = _hatch_command_handlers().get(args.hatch_command)
    if handler is None:
        print(
            "Unknown hatch command. Use: create, install, connect, list, "
            "disconnect, uninstall, share",
            file=sys.stderr,
        )
        sys.exit(1)
    return handler(args, Client())


def _hatch_imports():
    from ..integrations.hatchling import (
        HatchlingBridge,
        HatchSecurityPolicy,
        PackageSource,
        create_package_template,
    )

    return HatchSecurityPolicy, HatchlingBridge, PackageSource, create_package_template


def _hatch_bridge(client, registry: str | None = None):
    HatchSecurityPolicy, HatchlingBridge, _PackageSource, _create_template = _hatch_imports()
    security_policy = HatchSecurityPolicy(allowed_registries=[registry]) if registry else None
    return HatchlingBridge(client, security_policy=security_policy)


def _cmd_hatch_create(args, _client):
    from pathlib import Path

    _HatchSecurityPolicy, _HatchlingBridge, _PackageSource, create_template = _hatch_imports()
    output_dir = Path(args.output) if args.output else None
    try:
        pkg_path = create_template(
            args.name,
            output_dir=output_dir,
            description=args.description,
        )
    except ValueError as error:
        _die(str(error), action="mcp hatch create")
    print(f"✓ Created Hatch! package: {args.name}")
    print(f"  Location: {pkg_path}")
    print("\nNext steps:")
    print(f"  1. Edit {pkg_path}/server.py to add your tools")
    print(f"  2. Install: r4 mcp hatch install {pkg_path}")
    print(f"  3. Connect: r4 mcp hatch connect {args.name}")


def _hatch_install_source(args):
    from pathlib import Path

    _HatchSecurityPolicy, _HatchlingBridge, PackageSource, _create_template = _hatch_imports()
    source_url_by_kind = {
        "git": args.url,
        "local": args.package,
        "registry": args.registry,
    }
    package_name = Path(args.package).name if args.hatch_from == "local" else args.package
    return package_name, PackageSource(args.hatch_from), source_url_by_kind[args.hatch_from]


def _cmd_hatch_install(args, client):
    import asyncio

    bridge = _hatch_bridge(client, args.registry)
    package_name, source, source_url = _hatch_install_source(args)
    try:
        pkg_info = asyncio.run(
            bridge.install_package(
                package_name,
                source=source,
                source_url=source_url,
                on_progress=lambda msg: print(f"  {msg}"),
            )
        )
    except (ValueError, RuntimeError) as error:
        _die(str(error), action="mcp hatch install")
    print(f"✓ Installed: {pkg_info.name} v{pkg_info.version}")
    print(f"  Location: {pkg_info.install_path}")
    if pkg_info.tools:
        print(f"  Tools: {', '.join(pkg_info.tools)}")
    print(f"\nConnect with: r4 mcp hatch connect {pkg_info.name}")


def _print_hatch_search_results(packages: list[dict]) -> None:
    if not packages:
        print("No Hatch! packages found.")
        return
    print("Available Hatch! Packages:")
    print("-" * 80)
    print(f"{'NAME':<25} {'VERSION':<10} {'REGISTRY':<24} DESCRIPTION")
    print("-" * 80)
    for package in packages:
        name = str(package.get("name", "?"))
        version = str(package.get("version", ""))
        registry = str(package.get("registry_name", package.get("registry_url", "")))
        desc = _truncate_hatch_description(str(package.get("description", "")))
        print(f"{name:<25} {version:<10} {registry:<24} {desc}")


def _truncate_hatch_description(description: str) -> str:
    return description[:35] + "..." if len(description) > 38 else description


def _cmd_hatch_search(args, client):
    import asyncio

    bridge = _hatch_bridge(client, args.registry)
    try:
        packages = asyncio.run(
            bridge.search_available_packages(
                args.query,
                limit=args.limit,
                registry_url=args.registry,
            )
        )
    except ValueError as error:
        _die(str(error), action="mcp hatch search")
    if args.as_json:
        _json_print(packages)
    else:
        _print_hatch_search_results(packages)


def _print_hatch_registries(sources: list) -> None:
    print("Configured Hatch Registries:")
    print("-" * 80)
    print(f"{'NAME':<28} {'KIND':<10} URL")
    print("-" * 80)
    for source in sources:
        print(f"{source.display_name:<28} {source.kind:<10} {source.url}")


def _cmd_hatch_registry(args, client):
    bridge = _hatch_bridge(client)
    sources = bridge.list_registry_sources()
    if args.registry_command == "list":
        data = [source.to_dict() for source in sources]
        if args.as_json:
            _json_print(data)
        else:
            _print_hatch_registries(sources)


def _cmd_hatch_connect(args, client):
    import asyncio

    bridge = _hatch_bridge(client)
    try:
        success = asyncio.run(bridge.connect_package(args.package))
    except ValueError as error:
        _die(str(error), action="mcp hatch connect")
    if not success:
        _die(f"Failed to connect {args.package}", action="mcp hatch connect")
    print(f"✓ Connected: {args.package}")
    print("  Tools now available via 'r4 mcp list'")


def _print_hatch_packages(packages: list) -> None:
    if not packages:
        print("No installed Hatch! packages.")
        print("Install with: r4 mcp hatch install <package>")
        return
    print("Installed Hatch! Packages:")
    print("-" * 60)
    print(f"{'NAME':<25} {'VERSION':<10} {'STATUS':<12} TOOLS")
    print("-" * 60)
    for package in packages:
        tools_str = str(len(package.tools)) if package.tools else "0"
        print(f"{package.name:<25} {package.version:<10} {package.status.value:<12} {tools_str}")


def _cmd_hatch_list(args, client):
    bridge = _hatch_bridge(client)
    packages = bridge.list_packages(include_available=args.available)
    data = [package.to_dict() for package in packages]
    if args.as_json:
        _json_print(data)
    else:
        _print_hatch_packages(packages)


def _cmd_hatch_disconnect(args, client):
    import asyncio
    import sys

    success = asyncio.run(_hatch_bridge(client).disconnect_package(args.package))
    if success:
        print(f"✓ Disconnected: {args.package}")
        return
    print(f"Package not connected: {args.package}", file=sys.stderr)
    sys.exit(1)


def _cmd_hatch_uninstall(args, client):
    import sys

    if not args.force:
        confirm = input(f"Uninstall {args.package}? [y/N] ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return
    success = _hatch_bridge(client).uninstall_package(args.package)
    if success:
        print(f"✓ Uninstalled: {args.package}")
        return
    print(f"Package not found: {args.package}", file=sys.stderr)
    sys.exit(1)


def _cmd_hatch_share(args, client):
    import asyncio

    bridge = _hatch_bridge(client)
    try:
        success = asyncio.run(bridge.share_package(args.package, args.project))
    except (ValueError, RuntimeError) as error:
        _die(str(error), action="mcp hatch share")
    if success:
        print(f"✓ Shared {args.package} with project {args.project}")
    else:
        _die("Failed to share package", action="mcp hatch share")


def _hatch_command_handlers():
    return {
        "create": _cmd_hatch_create,
        "install": _cmd_hatch_install,
        "search": _cmd_hatch_search,
        "registry": _cmd_hatch_registry,
        "connect": _cmd_hatch_connect,
        "list": _cmd_hatch_list,
        "disconnect": _cmd_hatch_disconnect,
        "uninstall": _cmd_hatch_uninstall,
        "share": _cmd_hatch_share,
    }
