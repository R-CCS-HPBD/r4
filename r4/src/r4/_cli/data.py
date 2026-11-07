"""``r4 data`` command group — data intake board (U3) + science catalog.

``r4 data intake`` renders the connector inventory and sync status from the
UMRS dashboard overview endpoint. ``r4 data catalog …`` exposes the ADR-031
catalogue plane (physical-existence authority): search/read/stats with the
server-hydrated cross-plane ``links`` block. The CLI renders server-side
truth only — no client-side consistency inference.
"""

from __future__ import annotations

import argparse
import json
import sys
from argparse import ArgumentParser, Namespace
from typing import Any

from .base import CommandHandler
from .registry import command


@command
class DataCommand(CommandHandler):
    """``r4 data`` — data intake and catalog utilities."""

    name = "data"
    help = "Data intake board and catalog utilities"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description="Data intake board (connectors, sync state) and catalog utilities.",
            epilog=(
                "Examples:\n"
                "  r4 data intake                 Connector board overview\n"
                "  r4 data catalog search xrd     Search science assets (+ knowledge links)\n"
                "  r4 data catalog read proj/raw.csv --source extracted\n"
                "  r4 data catalog stats          Object counts by state/modality\n"
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        sub = parser.add_subparsers(
            dest="data_command",
            title="subcommands",
            metavar="<data-command>",
        )
        sub.required = True

        sub_intake = sub.add_parser("intake", help="Connector intake board")
        sub_intake.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_catalog = sub.add_parser(
            "assets",
            help=(
                "Data catalogue: what physically exists (ADR-031 catalogue plane"
                " — asset registry, not the Scientific Catalog; `r4 catalog`"
                " is that one)"
            ),
        )
        # D-39 rename: the collision-avoidance leg — the asset registry
        # subcommand is `assets`; `catalog` stays as a working alias
        # (rename notice, never a break).
        sub.add_parser(
            "catalog",
            help=(
                "Deprecated alias for `assets` (the data catalogue;"
                " `r4 catalog` = the Scientific Catalog)"
            ),
        )
        c_sub = sub_catalog.add_subparsers(
            dest="catalog_command",
            title="data catalogue commands",
            metavar="<catalog-command>",
        )
        c_sub.required = True

        p_search = c_sub.add_parser(
            "search", help="Search catalog assets (metadata + cross-plane links)"
        )
        p_search.add_argument("query", nargs="?", default="", help="Lexical search text")
        p_search.add_argument("--modality", "-m", default=None)
        p_search.add_argument("--state", "-s", default=None)
        p_search.add_argument("--tag", "-t", default=None)
        p_search.add_argument("--prefix", default=None, help="s3_key prefix within project")
        p_search.add_argument("--limit", "-n", type=int, default=20)
        p_search.add_argument("--json", "-j", action="store_true", dest="as_json")

        p_read = c_sub.add_parser(
            "read", help="Read one catalog asset (byte-bounded; auto prefers extracted text)"
        )
        p_read.add_argument("s3_key", help="S3 object key (from catalog search)")
        p_read.add_argument("--offset", type=int, default=0)
        p_read.add_argument("--length", type=int, default=1_048_576)
        p_read.add_argument("--source", default="auto", choices=("auto", "raw", "extracted"))
        p_read.add_argument("--json", "-j", action="store_true", dest="as_json")

        p_stats = c_sub.add_parser("stats", help="Catalog asset counts by state and modality")
        p_stats.add_argument("--json", "-j", action="store_true", dest="as_json")

        return parser

    def handle(self, args: Namespace) -> int:
        sub = getattr(args, "data_command", None)
        if sub == "intake":
            return _cmd_intake(args)
        if sub in ("catalog", "assets"):
            if sub == "catalog":
                print(
                    "note: `r4 data catalog` is now `r4 data assets` (the data "
                    "catalogue, ADR-031); `r4 catalog` is the Scientific Catalog "
                    "(D-39). The alias keeps working.",
                    file=sys.stderr,
                )
            return _cmd_catalog(args)
        print("Usage: r4 data <intake|assets>", file=sys.stderr)
        return 1


def _catalog_request(args: Namespace, client: "Any") -> dict:
    """Dispatch the verb to the matching DataCatalogClient call."""
    verb = getattr(args, "catalog_command", None)
    if verb == "search":
        return client.data_catalog.search(
            query=args.query or "",
            limit=args.limit,
            modality=args.modality,
            state=args.state,
            tag=args.tag,
            prefix=args.prefix,
        )
    if verb == "read":
        return client.data_catalog.read(
            args.s3_key, offset=args.offset, length=args.length, source=args.source
        )
    return client.data_catalog.stats()


def _catalog_emit(args: Namespace, verb: str, data: dict) -> int:
    """Render a catalog payload as JSON or the human board."""
    if args.as_json:
        print(json.dumps(data, indent=2, default=str))
        return 0
    if verb == "search":
        _render_catalog_search(data)
    elif verb == "read":
        _render_catalog_read(data)
    else:
        _render_catalog_stats(data)
    return 0


def _cmd_catalog(args: Namespace) -> int:
    from ..client import Client
    from ..errors import R4Error

    client = Client()
    verb = getattr(args, "catalog_command", None) or "stats"
    try:
        data = _catalog_request(args, client)
    except R4Error as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Error: catalog request failed: {exc}", file=sys.stderr)
        return 1
    return _catalog_emit(args, verb, data)


def _catalog_links_line(links: dict | None) -> str:
    """One compact line for the ADR-031 cross-plane links block (honest on absence)."""
    if not links:
        return "links: none (no canonical document minted)"
    doc = links.get("document_id") or "?"
    method = links.get("link_method") or "?"
    chunks = links.get("chunk_nodes", 0)
    vectors = links.get("vector_rows") or []
    models = sorted({str(v.get("model")) for v in vectors if v.get("model")})
    vector_text = f"{len(vectors)} vector row(s)"
    if models:
        vector_text += f" [{', '.join(models)}]"
    return f"links: document={doc} (via {method}) · chunk_nodes={chunks} · {vector_text}"


def _render_catalog_search(data: dict) -> None:
    results = data.get("results") or []
    print(f"Catalog search  q={data.get('query', '')!r}  project={data.get('project_id', '?')}")
    print("=" * 72)
    if not results:
        print("(no catalog assets match)")
        return
    for row in results:
        print(f" {row.get('s3_key', '?')}")
        print(
            f"   state={row.get('state', '?')} modality={row.get('detected_modality', '?')}"
            f" relevance={row.get('relevance_score', 0.0):.3f}"
        )
        summary = (row.get("summary") or "").strip().splitlines()
        if summary and summary[0]:
            print(f"   summary: {summary[0][:100]}")
        if row.get("tags"):
            print(f"   tags: {', '.join(row['tags'])}")
        print(f"   {_catalog_links_line(row.get('links'))}")


def _render_catalog_read(data: dict) -> None:
    print(
        f"{data.get('s3_key', '?')}  bytes {data.get('offset', 0)}+"
        f"{data.get('length', 0)}/{data.get('total_size', '?')}"
        f"  source={data.get('read_source', '?')}"
        f"{'  [truncated]' if data.get('truncated') else ''}"
    )
    print("=" * 72)
    text = data.get("text") or ""
    print(text if text.strip() else "(no text payload at this range)")
    print("-" * 72)
    print(_catalog_links_line(data.get("links")))


def _render_catalog_stats(data: dict) -> None:
    print(f"Catalog stats  project={data.get('project_id', '?')}")
    print("=" * 72)
    print(f"objects        : {data.get('object_count', 0)}")
    for label, section in (("states", "states"), ("modalities", "modalities")):
        rows = data.get(section) or {}
        body = ", ".join(f"{k}: {v}" for k, v in sorted(rows.items())) or "none"
        print(f"{label:<14} : {body}")


_STATUS_GLYPH = {"ok": "✓", "unavailable": "✗", "na": "·"}


def _cmd_intake(args: Namespace) -> int:
    from ..client import Client

    client = Client()
    try:
        overview = client.dashboard.overview()
    except Exception as exc:  # noqa: BLE001
        print(f"Error: dashboard unreachable: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(overview.get("connectors"), indent=2, default=str))
        return 0

    _render_intake_board(overview)
    return 0


def _render_intake_board(overview: dict) -> None:
    """Connector cards: availability probe + (placeholder) sync state."""
    connectors = overview.get("connectors")

    print("Data intake board")
    print("=" * 72)
    if connectors is None:
        print("connectors     : unavailable (dashboard section missing)")
        return

    sync_text = connectors.get("sync_state") or "n/a"
    for line in _connector_rows(connectors, sync_text):
        print(line)
    _render_summary_footer(overview, sync_text)


def _connector_rows(connectors: dict, sync_text: str) -> list[str]:
    """One rendered card per connector (probe availability + sync state)."""
    available = set(connectors.get("available_types") or [])
    unavailable = set(connectors.get("unavailable") or [])
    details = connectors.get("details") or {}
    sync_map = connectors.get("sync") or {}
    rows: list[str] = []
    for name in sorted(available | unavailable):
        rows.append(_card_row(details, name, name in available, sync_text))
        state = sync_map.get(name)
        if state:
            rows.append(_sync_line(state))
    return rows


def _card_row(details: dict, name: str, ok: bool, sync_text: str) -> str:
    """Top row of one connector card (probe availability + class)."""
    glyph = _STATUS_GLYPH["ok"] if ok else _STATUS_GLYPH["unavailable"]
    info = details.get(name) or {}
    err = f"  ({info['error']})" if not ok and info.get("error") else ""
    return (
        f" {glyph} {name:<14} {_STATUS_GLYPH['na']} sync: {sync_text}"
        f"  class: {info.get('class', '-')}{err}"
    )


def _sync_line(state: dict) -> str:
    """Indented sync-state row under a connector card (P2)."""
    dlq = f", dlq {state.get('dlq')}" if state.get("dlq") else ""
    return (
        f"     ↳ {state.get('status', '?')} @ {state.get('last_sync_at', '?')}"
        f"  docs {state.get('docs_synced', 0)}, errors {state.get('errors', 0)}{dlq}"
    )


def _render_summary_footer(overview: dict, sync_text: str) -> None:
    """Aggregate job/workflow/health lines below the connector cards."""
    print("-" * 72)
    print(
        f"jobs           : {overview.get('total_jobs', '?')} total, "
        f"{overview.get('running_jobs', '?')} running, "
        f"{overview.get('failed_jobs', '?')} failed"
    )
    workflows = overview.get("workflows")
    if workflows:
        print(
            f"workflows      : {workflows.get('total', '?')} executions, "
            f"{workflows.get('running', '?')} running, "
            f"{workflows.get('failed', '?')} failed"
        )
    print(f"system health  : {overview.get('system_health', '?')}")
    print(f"\nSync cursors/DLQ/health per source: pending P2 (shows '{sync_text}').")
