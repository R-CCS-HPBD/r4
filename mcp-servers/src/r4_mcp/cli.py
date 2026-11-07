"""MCP Servers CLI.

Provides command-line interface for running MCP servers.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable


def main(argv: list[str] | None = None) -> None:
    """Main CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _COMMAND_HANDLERS.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)
    handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="R4 MCP Servers",
        prog="r4-mcp",
    )
    subparsers = parser.add_subparsers(dest="command", help="Server to run")
    _add_filesystem_parser(subparsers)
    _add_database_parser(subparsers)
    _add_webfetch_parser(subparsers)
    _add_code_parser(subparsers)
    _add_platform_parser(subparsers)
    _add_rag_proxy_parser(subparsers)
    _add_vision_parser(subparsers)
    _add_custom_parser(subparsers)
    _add_remote_proxy_parser(subparsers)
    subparsers.add_parser("list", help="List available servers")
    return parser


def _add_filesystem_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("filesystem", help="Filesystem MCP server")
    parser.add_argument("--root", default=".", help="Root directory")
    parser.add_argument("--readonly", action="store_true", help="Read-only mode")
    parser.add_argument(
        "--max-size",
        type=int,
        default=10 * 1024 * 1024,
        help="Max file size (bytes)",
    )


def _add_database_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("database", help="Database MCP server")
    parser.add_argument("--dsn", required=True, help="Database connection string")
    parser.add_argument("--readonly", action="store_true", help="Read-only mode")
    parser.add_argument("--max-results", type=int, default=1000, help="Max result rows")
    parser.add_argument("--timeout", type=int, default=30, help="Query timeout")


def _add_webfetch_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("webfetch", help="Web fetch MCP server")
    parser.add_argument("--allowlist", help="Comma-separated allowed domains")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout")
    parser.add_argument(
        "--max-size",
        type=int,
        default=1024 * 1024,
        help="Max content size (bytes)",
    )


def _add_code_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("code", help="Code execution MCP server")
    parser.add_argument("--timeout", type=int, default=30, help="Execution timeout")
    parser.add_argument(
        "--max-output",
        type=int,
        default=10000,
        help="Max output length",
    )


def _add_platform_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("platform", help="Platform operations MCP server")
    parser.add_argument(
        "--gateway-url",
        default="http://localhost:8000",
        help="Gateway base URL",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Gateway API key / bearer token; defaults to R4_GATEWAY_TOKEN or R4_API_KEY",
    )
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout")


def _add_rag_proxy_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("rag-proxy", help="RAG query proxy MCP server")
    parser.add_argument(
        "--gateway-url",
        default="http://localhost:8000",
        help="Gateway base URL",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Gateway API key / bearer token; defaults to R4_GATEWAY_TOKEN or R4_API_KEY",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="Default project ID for queries; defaults to R4_PROJECT_ID",
    )
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout")


def _add_vision_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("vision", help="Vision MCP server (SAM3)")
    parser.add_argument(
        "--model-path",
        default=None,
        help=(
            "Path to pre-staged SAM3 weights; defaults to UMRS_SAM3_MODEL_PATH or VISION_MODEL_PATH"
        ),
    )
    parser.add_argument(
        "--device",
        default="cuda",
        choices=["cuda", "cpu"],
        help="Inference device (default: cuda)",
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/vision_outputs",
        help="Directory for output files",
    )
    parser.add_argument(
        "--max-image-size",
        type=int,
        default=50 * 1024 * 1024,
        help="Maximum input image size in bytes (default: 50MB)",
    )


def _add_custom_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("custom", help="Custom user-mounted MCP servers")
    parser.add_argument(
        "--config-dir",
        default="/mcp-servers",
        help="Directory containing custom MCP config.json files",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate custom MCP manifests and exit",
    )
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)


def _add_remote_proxy_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "remote-proxy",
        help="Bridge standard remote MCP servers into the UMRS registry dialect",
        description=(
            "Expose streamable-HTTP MCP upstreams (context7, deepwiki, …) behind "
            "GET /<name>/tools and POST /<name>/tools/call so they can be "
            "registered in UMRS via `r4 mcp register`."
        ),
    )
    parser.add_argument("--config", help="mcpServers-style config (http entries)")
    parser.add_argument("--name", help="single upstream name (with --url)")
    parser.add_argument("--url", help="single upstream URL (with --name)")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help="'Key: Value' header for the single upstream (repeatable)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50800)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--tools-ttl", type=float, default=300.0)
    parser.add_argument("--log-level", default="INFO")


def _run_filesystem(args: argparse.Namespace) -> None:
    from .servers.filesystem import FilesystemServer

    server = FilesystemServer(
        root=args.root,
        readonly=args.readonly,
        max_file_size=args.max_size,
    )
    server.run()


def _run_database(args: argparse.Namespace) -> None:
    from .servers.database import DatabaseServer

    server = DatabaseServer(
        connection_string=args.dsn,
        read_only=args.readonly,
        max_results=args.max_results,
        timeout_seconds=args.timeout,
    )
    server.run()


def _run_webfetch(args: argparse.Namespace) -> None:
    from .servers.webfetch import WebFetchServer

    allowed_domains = None
    if args.allowlist:
        allowed_domains = [domain.strip() for domain in args.allowlist.split(",")]

    server = WebFetchServer(
        allowed_domains=allowed_domains,
        max_content_length=args.max_size,
        timeout_seconds=args.timeout,
    )
    server.run()


def _run_code(args: argparse.Namespace) -> None:
    from .servers.code import CodeExecutionServer

    server = CodeExecutionServer(
        timeout_seconds=args.timeout,
        max_output_length=args.max_output,
    )
    server.run()


def _run_platform(args: argparse.Namespace) -> None:
    from .servers.platform import PlatformServer

    server = PlatformServer(
        gateway_url=args.gateway_url,
        api_key=args.api_key,
        timeout_seconds=args.timeout,
    )
    server.run()


def _run_rag_proxy(args: argparse.Namespace) -> None:
    from .servers.rag_proxy import RAGProxyServer

    server = RAGProxyServer(
        gateway_url=args.gateway_url,
        api_key=args.api_key,
        project_id=args.project_id,
        timeout_seconds=args.timeout,
    )
    server.run()


def _run_vision(args: argparse.Namespace) -> None:
    from .servers.vision import create_vision_server

    server = create_vision_server(
        model_type="sam3",
        model_path=args.model_path,
        output_dir=args.output_dir,
        device=args.device,
    )
    server.max_image_size = args.max_image_size
    server.run()


def _run_custom(args: argparse.Namespace) -> None:
    from .runners.custom_mcp import main as custom_main

    custom_args = [
        "--config-dir",
        args.config_dir,
        "--transport",
        args.transport,
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if args.validate_only:
        custom_args.append("--validate-only")
    sys.exit(custom_main(custom_args))


def _run_remote_proxy(args: argparse.Namespace) -> None:
    import logging

    from .runners import remote_proxy

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    specs: list[remote_proxy.UpstreamSpec] = []
    if args.config:
        specs = remote_proxy.load_upstream_specs(args.config, timeout=args.timeout)
    if args.name and args.url:
        headers = dict(h.split(":", 1) for h in args.header)
        specs.append(
            remote_proxy.UpstreamSpec(
                name=args.name,
                url=args.url,
                headers={k: v.strip() for k, v in headers.items()},
                timeout=args.timeout,
            )
        )
    if not specs:
        sys.exit("remote-proxy: no http upstreams configured (use --config or --name/--url)")
    remote_proxy.serve(args.host, args.port, specs, tools_ttl=args.tools_ttl)


def _run_list(_args: argparse.Namespace) -> None:
    print("Available MCP servers:")
    print("  filesystem  - Sandboxed file system access")
    print("  database    - PostgreSQL query execution")
    print("  webfetch    - URL fetching with domain restrictions")
    print("  code        - Python code execution in sandbox")
    print("  platform    - Platform operations (endpoints, jobs, models)")
    print("  rag-proxy   - RAG document search via Gateway")
    print("  vision      - Vision model server (SAM3 segmentation)")
    print("  custom      - User-mounted custom MCP servers")
    print("  remote-proxy - Bridge remote MCP servers into the UMRS registry dialect")


_COMMAND_HANDLERS: dict[str | None, Callable[[argparse.Namespace], None]] = {
    "filesystem": _run_filesystem,
    "database": _run_database,
    "webfetch": _run_webfetch,
    "code": _run_code,
    "platform": _run_platform,
    "rag-proxy": _run_rag_proxy,
    "vision": _run_vision,
    "custom": _run_custom,
    "remote-proxy": _run_remote_proxy,
    "list": _run_list,
}


if __name__ == "__main__":
    main()
