"""``r4 search`` command handler (bug.memo D3 Stage 1, 2026-04-25)."""

from __future__ import annotations

import asyncio
import json
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter

from ._helpers import _die
from .base import CommandHandler
from .registry import command


def build_search_parser(subparsers, *, name: str = "search") -> ArgumentParser:
    """Build the full search subparser tree.

    Entry point of ``r4 search``.
    """
    sp = subparsers.add_parser(
        name,
        help="Agentic multi-hop search",
        description="Run an agentic search over RAG, code, and optionally the web.",
        epilog=(
            "Examples:\n"
            f'  r4 {name} "find workflow upload validation"\n'
            f'  r4 {name} "Slurm crash recovery" --scope code_only --depth deep\n'
            f'  r4 {name} "batch-3 CSV columns" --scope lab_only -p my-project'
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    sp.add_argument("query", nargs="?", help="Search query")
    sp.add_argument("--project", "-p", help="Project ID for RAG search")
    sp.add_argument(
        "--depth",
        "-d",
        choices=["shallow", "moderate", "deep"],
        default="moderate",
        help="Search depth (default: moderate)",
    )
    sp.add_argument(
        "--scope",
        "-s",
        choices=["rag_only", "web_only", "code_only", "lab_only", "all"],
        default="all",
        help=(
            "Search scope: lab_only = lab data + RAG only; all includes lab + web + code (default)"
        ),
    )
    sp.add_argument("--max-steps", type=int, default=5, help="Max search steps")
    sp.add_argument("--max-sources", type=int, default=10, help="Max sources to return")
    sp.add_argument("--no-citations", action="store_true", help="Disable citations")
    sp.add_argument("--no-web", action="store_true", help="Disable web search")
    sp.add_argument("--no-rag", action="store_true", help="Disable RAG search")
    sp.add_argument("--no-code", action="store_true", help="Disable code search")
    sp.add_argument("--stream", action="store_true", help="Stream results")
    sp.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")
    sp.add_argument(
        "--source",
        metavar="NAME",
        help="Endpoint source for LLM-mediated steps (see `r4 source list`)",
    )
    return sp


@command
class SearchCommand(CommandHandler):
    """``r4 search`` — agentic multi-hop search."""

    name = "search"
    help = "Agentic multi-hop search"

    def register(self, subparsers) -> ArgumentParser:
        return build_search_parser(subparsers, name=self.name)

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="search")

        from .agent import _apply_source_override

        _apply_source_override(getattr(args, "source", None))

        return cmd_search(args)


def cmd_search(args):
    """Handle search command (A-901 to A-912)."""
    from ..client import Client
    from ..search.core import SearchDepth, SearchScope
    from ..search.pipeline import AgenticSearchPipeline

    client = Client()
    _check_gateway_ready(client)
    query = _search_query(args)
    if not query:
        _die("No query provided", action="search")

    config_kwargs = _search_config_kwargs(args, SearchDepth, SearchScope)
    pipeline = AgenticSearchPipeline(client, **config_kwargs)
    _attach_search_progress(pipeline, args)
    if args.stream:
        return _run_stream_search(pipeline, query)

    result = asyncio.run(pipeline.search(query))
    _print_search_result(result, args)
    return _search_exit_code(result)


def _check_gateway_ready(client) -> None:
    try:
        status = client.readiness()
        if not status.get("ready", False):
            msg = status.get("message", "Service not ready")
            print(f"  [warn] Gateway not ready: {msg}", file=sys.stderr)
    except Exception as e:
        gw_url = client._config.gateway_url
        print(
            f"  [error] Cannot reach gateway at {gw_url}: {e}\n"
            f"  hint: Set R4_GATEWAY_URL or start the platform gateway.",
            file=sys.stderr,
        )
        sys.exit(1)


def _search_query(args: Namespace) -> str:
    if not args.query:
        args.query = sys.stdin.read().strip()
    return args.query


def _search_config_kwargs(args: Namespace, search_depth, search_scope) -> dict:
    return {
        "project_id": args.project,
        "depth": search_depth(args.depth),
        "scope": search_scope(args.scope),
        "max_steps": args.max_steps,
        "max_sources": args.max_sources,
        "include_citations": not args.no_citations,
        "enable_web": not args.no_web,
        "enable_rag": not args.no_rag,
        "enable_code": not args.no_code,
    }


def _attach_search_progress(pipeline, args: Namespace) -> None:
    if args.as_json:
        return

    def on_event(event):
        print(f"  [{event.event_type}] {event.message}")

    pipeline.on_event(on_event)


def _run_stream_search(pipeline, query: str) -> int:
    async def run_stream():
        async for chunk in pipeline.search_stream(query):
            print(chunk, end="", flush=True)
        print()

    asyncio.run(run_stream())
    return 0


def _print_search_result(result, args: Namespace) -> None:
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return
    print(result.format_with_citations())
    print("\n---")
    print(
        f"Steps: {result.search_steps} | Sources: {len(result.sources)} | "
        f"Confidence: {result.confidence:.0%} | Time: {result.latency_ms}ms"
    )


def _search_exit_code(result) -> int:
    if not result.sources and result.confidence <= 0:
        return 1
    return 0
