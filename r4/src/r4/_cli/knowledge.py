"""``r4 knowledge`` command tree (ADR-013, INV-13).

Knowledge portability commands for exporting, importing, and managing
knowledge bundles (``.kb`` format).

Subtree::

    r4 knowledge
    ├── export <project>   Export project knowledge to a .kb bundle
    ├── import <bundle>    Import a .kb bundle into local knowledge store
    ├── ls                 List the project's knowledge, with who added it (K1.3)
    ├── bundles            List local knowledge bundles (alias: list)
    ├── info <bundle>      Show bundle manifest details
    ├── validate <bundle>  Validate bundle integrity
    ├── delete <artifact>  Delete an artifact and everything derived (MD-16)
    ├── review [action]    Review proposed knowledge (K1.5; see knowledge_review)
    └── allowlist          Web sites URL/repo ingest may fetch (knowledge_allowlist)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..graph_constraints_store import load_constraints
from ..knowledge_bundle import (
    BUNDLE_ACCEPTED_VERSIONS,
    KnowledgeBundleManifest,
    KnowledgeBundleReader,
    validate_bundle,
)
from ._helpers import _current_project_id
from .base import CommandHandler
from .knowledge_allowlist import handle_allowlist, register_allowlist
from .knowledge_ls import handle_ls, register_ls
from .knowledge_review import handle_review, register_review
from .registry import command

logger = logging.getLogger(__name__)


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _csv_list(raw: str | None) -> list[str]:
    """Comma-separated CLI list → stripped tokens."""
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


def _graph_rows(graphs: list[dict], constraints: dict) -> list[dict]:
    """Documents rows for the graphs section: state marker + metadata."""
    from ..graph_constraints_store import graph_state

    discovery = "on" if constraints.get("discovery", True) else "off"
    rows = [
        {
            "id": "discovery-toggle",
            "title": f"⚙ auto-discovery: {discovery}",
            "label": "press d to toggle",
        }
    ]
    for graph in graphs:
        state = graph_state(graph.get("name", ""), constraints)
        marker = {"include": "[green]+[/green]", "exclude": "[red]−[/red]"}.get(
            state, "[dim]·[/dim]"
        )
        rows.append(
            {
                "id": graph.get("graph_id"),
                "title": f"{marker} {graph.get('name', graph.get('graph_id'))}",
                "label": f"{graph.get('kind', 'graph')} · {graph.get('project_id') or 'shared'}",
            }
        )
    return rows


def _graph_detail(knowledge: Any, graph_id: str, constraints: dict) -> dict:
    """Detail body for a graph row: registry metadata + selection state."""
    from ..graph_constraints_store import graph_state

    try:
        graph = knowledge.graph_info(graph_id)
    except Exception:  # noqa: BLE001
        graph = {"graph_id": graph_id, "name": graph_id}
    body = {
        "graph_id": graph.get("graph_id"),
        "name": graph.get("name"),
        "kind": graph.get("kind"),
        "status": graph.get("status"),
        "project_id": graph.get("project_id"),
        "description": graph.get("description"),
        "owner_id": graph.get("owner_id"),
        "selection_state": graph_state(graph.get("name", graph_id), constraints),
        "discovery": constraints.get("discovery", True),
    }
    return {"title": f"graph: {graph.get('name', graph_id)}", "body": body}


# Corpus commands and graph commands accept both spellings (2026-10-09:
# graph-* took --project-id, upload/query took a required --project).
_PROJECT_HELP = "Project ID (default: current project)"


def _resolve_project_arg(args: argparse.Namespace) -> bool:
    """Fill ``args.project`` from the current project; False (said) when none."""
    args.project = args.project or _current_project_id()
    if args.project:
        return True
    print(
        "No project specified. Pass -p <project-id> or run: r4 project switch <id>",
        file=sys.stderr,
    )
    return False


def _confirm(prompt: str) -> bool:
    """Interactive yes/no; no terminal answer (EOF) is a no."""
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


#: The order rows are printed in (the server's count keys).
_DELETE_COUNT_KEYS = (
    "artifacts",
    "nodes",
    "edges",
    "embeddings",
    "ir",
    "findings",
    "events_detached",
)


def print_delete_result(result: Any) -> None:
    """Counts per row kind, one line per object, and the server's notes.

    Shared by ``r4 knowledge delete`` and ``r4 knowledge docs delete``.
    """
    label = f"document {result.document_id} (artifact {result.artifact_id})"
    what = label if result.document_id else f"artifact {result.artifact_id}"
    print(f"✓ Deleted {what} from project {result.project_id}")
    counts = result.counts
    for key in (*_DELETE_COUNT_KEYS, *sorted(set(counts) - set(_DELETE_COUNT_KEYS))):
        print(f"  {key:<16}: {counts.get(key, 0)}")
    for obj in result.objects:
        detail = f" — {obj['detail']}" if obj.get("detail") else ""
        mark = "⚠" if obj.get("outcome") == "failed" else " "
        print(f"{mark} object {obj.get('key', '')}: {obj.get('outcome', '?')}{detail}")
    for note in result.notes:
        print(f"  note: {note}")


def _print_graph_selection_note(payload: dict) -> None:
    """Print the K3 graph-selection truth when present (0069 multi-graph)."""
    selected = (payload.get("query") or {}).get("selected_graphs") or []
    excluded = (payload.get("query") or {}).get("excluded_graphs") or []
    if not selected and not excluded:
        return
    note = f"graphs: {', '.join(selected) or '(none)'}"
    if excluded:
        note += f"  (excluded: {', '.join(excluded)})"
    print(note)


class _ExploreProviders:
    """Provider set for the knowledge explorer TUI (multi-graph picker).

    Shared graph-selection state lives here: TUI edits persist and are
    consumed by CLI query defaults + Companion tool fallbacks.
    """

    def __init__(self, knowledge: Any, project_id: str) -> None:
        from ..graph_constraints_store import load_constraints
        from ..knowledge_explorer.data import (
            fetch_detail,
            fetch_documents,
            fetch_project_data,
        )

        self._knowledge = knowledge
        self._project_id = project_id
        self._constraints = load_constraints()
        self._fetch_detail = fetch_detail
        self._fetch_documents = fetch_documents
        self._fetch_project_data = fetch_project_data

    def project_data(self) -> dict:
        data = self._fetch_project_data(self._knowledge, self._project_id)
        try:
            data["graphs"] = self._knowledge.graphs(project_id=self._project_id)
        except Exception:  # noqa: BLE001
            data["graphs"] = []
        return data

    def documents(self, section: str | None) -> list[dict]:
        if section == "graphs":
            return _graph_rows(self._knowledge.graphs() or [], self._constraints)
        return self._fetch_documents(self._knowledge, self._project_id, section)

    def detail(self, section: str | None, doc_id: str) -> dict:
        if section == "graphs":
            return _graph_detail(self._knowledge, doc_id, self._constraints)
        return self._fetch_detail(self._knowledge, self._project_id, section, doc_id)

    def toggle_graph(self, graph_id: str) -> dict:
        from ..graph_constraints_store import (
            save_constraints,
        )
        from ..graph_constraints_store import toggle_graph as _cycle

        graph = next(
            (g for g in (self._knowledge.graphs() or []) if g.get("graph_id") == graph_id),
            {"graph_id": graph_id, "name": graph_id},
        )
        self._constraints = _cycle(graph.get("name", graph_id), self._constraints)
        save_constraints(self._constraints)
        return self._constraints

    def toggle_discovery(self) -> dict:
        from ..graph_constraints_store import save_constraints

        self._constraints = {
            **self._constraints,
            "discovery": not self._constraints.get("discovery", True),
        }
        save_constraints(self._constraints)
        return self._constraints

    def export(self) -> object:
        return self._knowledge.export(self._project_id)

    def as_kwargs(self) -> dict[str, Any]:
        """ExploreApp wiring for this provider set."""
        return {
            "project_data_provider": self.project_data,
            "documents_provider": self.documents,
            "detail_provider": self.detail,
            "export_provider": self.export,
            "toggle_graph_provider": self.toggle_graph,
            "toggle_discovery_provider": self.toggle_discovery,
        }


def _resolve_query_graph_args(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    """(include, exclude) graph tokens for a query.

    Explicit CLI flags win; otherwise fall back to the shared selection
    (TUI picker / Companion session).
    """
    graphs = _csv_list(getattr(args, "graphs", None))
    exclude = _csv_list(getattr(args, "exclude_graphs", None))
    if not graphs and not exclude:
        saved = load_constraints()
        graphs = saved.get("include") or []
        exclude = saved.get("exclude") or []
    return graphs, exclude


def _fetch_knowledge_graph(
    client: Any, project_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Best-effort graph fetch → (entities, relations).

    Reads through ``KnowledgeClient.get_graph`` — there is no
    ``client.knowledge_graph`` attribute on the R4 client.

    The UMRS payload is ``{"nodes": [...], "edges": [...]}``; older
    ``entities``/``relations`` keys are accepted as a compat fallback. A
    payload shape with neither emits a stderr warning instead of handing
    the export an always-empty graph (#206).
    """
    try:
        graph = client.knowledge.get_graph(project_id=project_id)
        entities = graph.get("nodes", graph.get("entities", []))
        relations = graph.get("edges", graph.get("relations", []))
        known_keys = {"nodes", "edges", "entities", "relations"}
        if not (entities or relations) and not known_keys & set(graph or {}):
            print(
                "WARNING: knowledge graph payload has no nodes/edges keys — "
                f"got keys {sorted((graph or {}).keys()) or '[]'}; exporting no graph rows.",
                file=sys.stderr,
            )
        return entities, relations
    except Exception as exc:
        print(f"WARNING: failed to fetch knowledge graph: {exc}", file=sys.stderr)
        logger.warning("Failed to fetch knowledge graph: %s", exc)
        return [], []


def _fetch_catalog_objects(client: Any, project_id: str) -> list[dict[str, Any]]:
    """Best-effort science catalog fetch ([] on failure)."""
    try:
        catalog = client.data_catalog.list(project_id=project_id)
        return catalog if isinstance(catalog, list) else []
    except Exception as exc:
        logger.warning("Failed to fetch science catalog: %s", exc)
        return []


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write a JSONL file (UTF-8, unescaped unicode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: Any) -> None:
    """Write an indented JSON file (UTF-8, unescaped unicode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _persist_bundle_to_local_store(
    manifest: "KnowledgeBundleManifest",
    *,
    chunks: list[dict[str, Any]],
    embeddings: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    catalog_objects: list[dict[str, Any]],
) -> Path:
    """Write an imported bundle into the local knowledge store dir.

    Normalizes to the canonical .kb layout (documents/, embeddings/, graph/,
    catalog/) so a re-import of the persisted directory roundtrips through
    ``KnowledgeBundleReader`` without loss.
    """
    local_kb_dir = Path.home() / ".config" / "r4" / "knowledge" / manifest.project_id
    local_kb_dir.mkdir(parents=True, exist_ok=True)
    (local_kb_dir / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    if chunks:
        _write_jsonl(local_kb_dir / "documents" / "chunks.jsonl", chunks)
    if embeddings:
        _write_jsonl(local_kb_dir / "embeddings" / "embeddings.jsonl", embeddings)
    if entities or relations:
        _write_json(local_kb_dir / "graph" / "entities.json", entities)
        _write_json(local_kb_dir / "graph" / "relations.json", relations)
    if catalog_objects:
        _write_json(local_kb_dir / "catalog" / "catalog.json", catalog_objects)
    return local_kb_dir


def _print_raw_outcome(result: dict[str, Any]) -> None:
    """Raw objects stored + re-bound / carried but unbound / declined."""
    parts = [
        f"{result[key]} {label}"
        for key, label in (
            ("raw_restored", "restored"),
            ("raw_unbound", "unbound (row already present)"),
            ("raw_declined", "declined"),
        )
        if result.get(key)
    ]
    if parts:
        print(f"  Raw objects: {', '.join(parts)}")


def _verification_label(result: dict[str, Any]) -> str:
    """Whether the signature covered the tables too (bundles exported since
    2026-09-24, verified by a server that checks them)."""
    if not result.get("verified"):
        return " (unverified)"
    if result.get("tables_verified"):
        return " (verified)"
    return " (manifest verified; table contents not pinned)"


def _print_canonical_matches(matches: list[dict[str, Any]], shown: int = 5) -> None:
    """Name documents already present under another id (ADR-032 #1; the
    import reports them, it never merges)."""
    if not matches:
        return
    print(f"  Same document already present under another id: {len(matches)}")
    for match in matches[:shown]:
        print(
            f"    {match.get('incoming_artifact_id')} = local "
            f"{match.get('local_artifact_id')} ({match.get('document_id')})"
        )
    if len(matches) > shown:
        print(f"    … {len(matches) - shown} more (--json lists all)")


@command
class KnowledgeCommand(CommandHandler):
    """``r4 knowledge`` — Knowledge bundle export/import (ADR-013)."""

    name = "knowledge"
    help = "Knowledge bundle export/import (ADR-013, INV-13)"

    def register(self, subparsers) -> argparse.ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Export, import, and manage knowledge bundles (.kb format). "
                "Knowledge bundles are portable archives of a project's "
                "knowledge base (documents + embeddings + graph + catalog)."
            ),
            epilog=(
                "Examples:\n"
                "  r4 knowledge export my-project --output my-project.kb\n"
                "  r4 knowledge import my-project.kb\n"
                "  r4 knowledge ls --since 7d\n"
                "  r4 knowledge bundles\n"
                "  r4 knowledge info my-project.kb\n"
                "  r4 knowledge validate my-project.kb\n"
                "  r4 knowledge delete art-0123abcd -p my-project"
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        sub = parser.add_subparsers(
            dest="knowledge_command",
            title="subcommands",
            metavar="<knowledge-command>",
        )
        sub.required = True

        # ---- export ---------------------------------------------------------
        p_export = sub.add_parser(
            "export",
            help="Export project knowledge to a .kb bundle",
        )
        p_export.add_argument(
            "project_id",
            nargs="?",
            help="Project ID to export (defaults to current project)",
        )
        p_export.add_argument(
            "--output",
            "-o",
            required=True,
            help="Output path (.kb file or directory)",
        )
        p_export.add_argument(
            "--include-graph",
            action="store_true",
            default=True,
            help="Include knowledge graph rows (nodes.jsonl/edges.jsonl; default: true)",
        )
        p_export.add_argument(
            "--include-catalog",
            action="store_true",
            default=True,
            help="Include science catalog rows (catalog.jsonl; default: true)",
        )

        # ---- import ---------------------------------------------------------
        p_import = sub.add_parser(
            "import",
            help="Import a .kb bundle (.kb.tar.gz or directory) into your project",
        )
        p_import.add_argument(
            "bundle",
            help="Path to .kb bundle (.kb.tar.gz archive or directory)",
        )
        p_import.add_argument(
            "--project-id",
            "-p",
            help="Override project ID (legacy flat-layout local import only)",
        )
        p_import.add_argument(
            "--allow-unsigned",
            action="store_true",
            help="Import an unsigned / other-installation bundle anyway (explicit opt-out)",
        )
        p_import.add_argument(
            "--no-raw",
            action="store_true",
            dest="no_raw",
            help="Take the knowledge but decline the bundle's raw objects",
        )
        p_import.add_argument("--json", "-j", action="store_true", dest="as_json")

        # ---- export-download ------------------------------------------------
        p_dl = sub.add_parser(
            "export-download",
            help="Download a finished server export (.kb.tar.gz)",
        )
        p_dl.add_argument(
            "export",
            help="Export job id (current project) or the archive_key from the export receipt",
        )
        p_dl.add_argument("--output", "-o", default=None, help="Destination file")

        # ---- ls (K1.3) / bundles (was: list) --------------------------------
        register_ls(sub)
        sub.add_parser(
            "bundles",
            aliases=["list"],
            help="List local knowledge bundles (.kb) under ~/.config/r4/knowledge",
        )

        # ---- info -----------------------------------------------------------
        p_info = sub.add_parser(
            "info",
            help="Show bundle manifest details",
        )
        p_info.add_argument(
            "bundle",
            help="Path to .kb bundle (file or directory)",
        )

        # ---- validate -------------------------------------------------------
        p_validate = sub.add_parser(
            "validate",
            help="Validate bundle integrity",
        )
        p_validate.add_argument(
            "bundle",
            help="Path to .kb bundle (file or directory)",
        )

        # ---- search ---------------------------------------------------------
        # (was 'query' — renamed 2026-09-13: the WP-15 P2 fold owns the
        # 'query' verb for the corpus rag flow; the K3 facade search keeps
        # its full arg surface here. See the duplicate-subparser collision
        # argparse only tolerated pre-3.11.)
        p_query = sub.add_parser(
            "search",
            help="Unified search across UMRS knowledge backends (K3 facade)",
        )
        p_query.add_argument("query", help="Free-text query")
        p_query.add_argument(
            "project_id",
            nargs="?",
            help="Project ID (defaults to current project)",
        )
        p_query.add_argument(
            "--scope",
            default="all",
            choices=["all", "catalog", "knowledge_graph", "embeddings", "rag"],
            help="Backend scope (default: all)",
        )
        p_query.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Max results (default: 20)",
        )
        p_query.add_argument(
            "--graphs",
            help=(
                "Comma-separated knowledge graphs (ids or names) to query — "
                "which GRAPHS, distinct from --scope which picks a backend type"
            ),
        )
        p_query.add_argument(
            "--exclude-graphs",
            dest="exclude_graphs",
            help="Comma-separated knowledge graphs to exclude (wins over --graphs)",
        )
        p_query.add_argument("--json", "-j", action="store_true", dest="as_json")

        # ---- explore --------------------------------------------------------
        p_explore = sub.add_parser(
            "explore",
            help="Interactive three-pane knowledge explorer TUI (U4)",
        )
        p_explore.add_argument(
            "project_id",
            nargs="?",
            help="Project ID (defaults to current project)",
        )

        # ---- restore -----------------------------------------------------------
        p_restore = sub.add_parser(
            "restore",
            help="Admin: restore a .kb bundle from a server-side directory (K4)",
        )
        p_restore.add_argument(
            "bundle_dir",
            help="Server-side bundle directory (contains manifest.json)",
        )
        p_restore.add_argument(
            "--retarget",
            dest="project_id",
            help="Retarget all restored rows to this project ID",
        )
        p_restore.add_argument(
            "--no-verify",
            action="store_true",
            help="Skip manifest hash/HMAC verification (writes still idempotent)",
        )
        p_restore.add_argument(
            "--allow-unsigned",
            action="store_true",
            help="Restore an unsigned/unverifiable bundle anyway (explicit opt-out)",
        )
        p_restore.add_argument("--json", "-j", action="store_true", dest="as_json")

        # ---- graphs (registry, 0069) -----------------------------------------
        p_graphs = sub.add_parser(
            "graphs",
            help="List registered knowledge graphs",
        )
        p_graphs.add_argument(
            "--project-id",
            "--project",
            "-p",
            help="Filter by project (materializes its default graph)",
        )
        p_graphs.add_argument(
            "--all",
            action="store_true",
            help="Include archived graphs",
        )
        p_graphs.add_argument(
            "--query",
            "-q",
            help="Name/description search",
        )
        p_graphs.add_argument("--json", "-j", action="store_true", dest="as_json")

        p_graph = sub.add_parser(
            "graph",
            help="Show one knowledge graph (id or name)",
        )
        p_graph.add_argument("graph_id", help="Graph id or exact name")
        p_graph.add_argument("--json", "-j", action="store_true", dest="as_json")

        p_gcreate = sub.add_parser(
            "graph-create",
            help="Create a named knowledge graph",
        )
        p_gcreate.add_argument("name", help="Human-readable graph name")
        p_gcreate.add_argument("--description", "-d", default="")
        p_gcreate.add_argument(
            "--project-id",
            "--project",
            "-p",
            default=None,
            help="Optionally associate with a project",
        )
        p_gcreate.add_argument("--kind", "-k", default="research")
        p_gcreate.add_argument("--json", "-j", action="store_true", dest="as_json")

        p_garchive = sub.add_parser(
            "graph-archive",
            help="Archive a knowledge graph (soft delete; archive is the only delete)",
        )
        p_garchive.add_argument("graph_id", help="Graph id or name")

        p_gaccess = sub.add_parser(
            "graph-access",
            help="Show or set who may read a knowledge graph (user/project/group/system)",
        )
        p_gaccess.add_argument("graph_id", help="Graph id or name")
        p_gaccess.add_argument(
            "--set",
            dest="access_set",
            action="append",
            metavar="SCOPE",
            help="Replace list (repeatable): user:<id>, project:<id>, circle:<proj>/<id>, system",
        )
        p_gaccess.add_argument(
            "--private",
            action="store_true",
            help="Clear the list: only the owning project may read the graph",
        )

        p_gingest = sub.add_parser(
            "graph-ingest",
            help="Ingest files/URLs into a NAMED knowledge graph",
        )
        p_gingest.add_argument("graph", help="Target graph (id or exact name)")
        p_gingest.add_argument(
            "--file",
            "-f",
            action="append",
            dest="files",
            default=[],
            help="Local file/dir path (repeatable)",
        )
        p_gingest.add_argument(
            "--url",
            "-u",
            action="append",
            dest="urls",
            default=[],
            help="HTTP URL to fetch in the egress-controlled durable job (repeatable)",
        )
        p_gingest.add_argument(
            "--project-id",
            "--project",
            "-p",
            default=None,
            help="Project context for artifacts (default: current project)",
        )
        p_gingest.add_argument(
            "--proposal-id",
            default=None,
            help="Approved graph-extension proposal (executes it)",
        )
        p_gingest.add_argument("--json", "-j", action="store_true", dest="as_json")

        # ---- upload (P1b fold: canonical corpus surface) --------------------
        p_up = sub.add_parser(
            "upload",
            help="Upload a document into the project corpus",
        )
        p_up.add_argument("file", help="File to upload")
        p_up.add_argument("--project", "--project-id", "-p", help=_PROJECT_HELP)

        # ---- delete (ADR-093 MD-16) -----------------------------------------
        p_del = sub.add_parser(
            "delete",
            help="Delete a knowledge artifact and everything derived from it",
            description=(
                "Delete one knowledge artifact: its row, nodes, vectors, edges, "
                "IR rows and findings in one transaction (its events are kept, "
                "unlinked), then its stored bytes unless another artifact still "
                "references them. Asks for confirmation unless --yes."
            ),
        )
        p_del.add_argument("artifact_id", help="Artifact id (e.g. art-…)")
        p_del.add_argument("--project", "--project-id", "-p", help=_PROJECT_HELP)
        p_del.add_argument("--yes", "-y", action="store_true", help="Do not ask to confirm")
        p_del.add_argument("--json", "-j", action="store_true", dest="as_json")

        # ---- bulk-upload ----------------------------------------------------
        p_bulk = sub.add_parser(
            "bulk-upload",
            help="Upload all supported files from a directory",
        )
        p_bulk.add_argument("directory", help="Directory to scan")
        p_bulk.add_argument("--project", "--project-id", "-p", help=_PROJECT_HELP)
        p_bulk.add_argument("--recursive", "-r", action="store_true", help="Scan subdirectories")
        p_bulk.add_argument(
            "--dry-run",
            action="store_true",
            help="List files that would be uploaded without uploading",
        )
        p_bulk.add_argument("--json", "-j", action="store_true", dest="as_json")

        # ---- docs -----------------------------------------------------------
        p_docs = sub.add_parser(
            "docs",
            help="Manage corpus documents in a project",
        )
        d_sub = p_docs.add_subparsers(
            dest="docs_command",
            title="subcommands",
            metavar="<docs-command>",
        )
        d_sub.required = True

        p_docs_list = d_sub.add_parser("list", help="List corpus documents")
        p_docs_list.add_argument(
            "--project",
            "-p",
            default=None,
            help="Project ID (defaults to the current project from `r4 project switch`)",
        )
        p_docs_list.add_argument(
            "--json",
            dest="as_json",
            action="store_true",
            help="JSON output",
        )

        p_docs_del = d_sub.add_parser("delete", help="Delete a corpus document")
        p_docs_del.add_argument("document_id", help="Document ID to delete")
        p_docs_del.add_argument("--project", "-p", help="Project ID")
        p_docs_del.add_argument("--yes", "-y", action="store_true")

        # ---- stats ----------------------------------------------------------
        p_stats = sub.add_parser("stats", help="Show corpus statistics")
        p_stats.add_argument("--project", "-p", help="Project ID (uses current if not set)")
        p_stats.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        # ---- query (P1b/P2 fold: canonical corpus surface) ------------------
        p_query = sub.add_parser(
            "query",
            help="Query documents in the project corpus (RAG)",
        )
        p_query.add_argument("query", help="Query text")
        p_query.add_argument("--project", "--project-id", "-p", help=_PROJECT_HELP)
        p_query.add_argument("--top-k", "-k", type=int, default=5)
        p_query.add_argument(
            "--threshold",
            type=float,
            default=None,
            help="Legacy absolute similarity cut (0-1). Omitted by default: the server's "
            "adaptive cut and sufficiency verdict decide (ADR-093 D-10)",
        )
        p_query.add_argument("--no-sources", action="store_true")

        # ---- inventory (UX §9: catalogue + canonical badges) ----------------
        p_inv = sub.add_parser(
            "inventory",
            help="List catalogue objects with canonical knowledge badges",
        )
        p_inv.add_argument("--project", "-p", help="Project ID (uses current if not set)")
        p_inv.add_argument("--query", "-q", default="", help="Lexical grep over catalogue metadata")
        p_inv.add_argument("--limit", type=int, default=50)
        p_inv.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        # ---- health (UX §8: reconciliation counts, server-side state) --------
        p_health = sub.add_parser(
            "health",
            help="Plane reconciliation health: per-state counts per asset",
        )
        p_health.add_argument("--project", "-p", help="Project ID (uses current if not set)")
        p_health.add_argument("--sample", type=int, default=20)
        p_health.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        # ---- review (knowledge plan K1.5) -----------------------------------
        register_review(sub)
        # ---- allowlist: web sites URL/repo ingest may fetch (2026-10-25) ----
        register_allowlist(sub)

        return parser

    def handle(self, args: argparse.Namespace) -> int:
        handlers = {
            "review": handle_review,
            "allowlist": handle_allowlist,
            "ls": handle_ls,
            "export": self._handle_export,
            "import": self._handle_import,
            "bundles": self._handle_list,
            "list": self._handle_list,
            "info": self._handle_info,
            "validate": self._handle_validate,
            "search": self._handle_search,
            "explore": self._handle_explore,
            "restore": self._handle_restore,
            "export-download": self._handle_export_download,
            "graphs": self._handle_graphs,
            "graph": self._handle_graph,
            "graph-create": self._handle_graph_create,
            "graph-archive": self._handle_graph_archive,
            "graph-access": self._handle_graph_access,
            "graph-ingest": self._handle_graph_ingest,
            "upload": self._handle_upload,
            "delete": self._handle_delete,
            "bulk-upload": self._handle_bulk_upload,
            "docs": self._handle_docs,
            "stats": self._handle_stats,
            "query": self._handle_query,
            "inventory": self._handle_inventory,
            "health": self._handle_health,
        }
        handler = handlers.get(args.knowledge_command)
        if handler is None:
            print(f"Unknown knowledge command: {args.knowledge_command}", file=sys.stderr)
            return 1
        return handler(args)

    # ---- health (UX §8) ------------------------------------------------------

    _STATE_BADGES = {
        "COMPLETE": "[complete]",
        "EMBEDDING_PENDING": "[pending embeddings]",
        "PARTIAL": "[PARTIAL]",
        "ORPHANED": "[ORPHANED]",
    }

    def _print_health_counts(self, project: str, report: dict) -> None:
        counts = report.get("counts") or {}
        print(
            f"project {project}: COMPLETE {counts.get('COMPLETE', 0)} · "
            f"EMBEDDING_PENDING {counts.get('EMBEDDING_PENDING', 0)} · "
            f"PARTIAL {counts.get('PARTIAL', 0)} · "
            f"ORPHANED {counts.get('ORPHANED', 0)}"
        )
        if report.get("catalogue_available") is False:
            note = "catalogue plane not readable — counts computed on canonical+vectors only"
            print(f"  ({note})")

    def _print_health_assets(self, report: dict) -> None:
        for asset in report.get("assets") or []:
            badge = self._STATE_BADGES.get(asset.get("state"), "[?]")
            reasons = asset.get("reasons") or []
            note = f" — {reasons[0]}" if reasons else ""
            print(f"  • {asset.get('document_id')} {badge}{note}")

    def _handle_health(self, args: argparse.Namespace) -> int:
        """Server-side reconciliation counts + asset-level states."""
        from ..client import Client

        if not _resolve_project_arg(args):
            return 1
        project = args.project
        try:
            report = Client().knowledge.reconcile_project(project, sample=args.sample)
        except Exception as exc:
            print(f"✗ Failed to fetch reconciliation state: {exc}", file=sys.stderr)
            logger.debug("knowledge health failed", exc_info=True)
            return 1

        if args.as_json:
            _print_json(report)
            return 0
        self._print_health_counts(project, report)
        self._print_health_assets(report)
        return 0

    # ---- inventory (UX §9 + §1) ----------------------------------------------

    def _canonical_badge(self, links: dict | None) -> str:
        """ASCII badge (plain print — no rich Console in this tree)."""
        if not links:
            return "[catalogue only]"
        vectors = links.get("vector_rows") or []
        if not vectors:
            return "[canonized · no vectors yet]"
        return "[searchable]"

    def _handle_inventory(self, args: argparse.Namespace) -> int:
        """Catalogue rows + canonical knowledge badges (server-side links)."""
        if not _resolve_project_arg(args):
            return 1
        project = args.project
        rows = self._fetch_inventory_rows(project, args)
        if rows is None:
            return 1
        if args.as_json:
            _print_json({"project_id": project, "rows": rows})
            return 0
        if not rows:
            print(f"No catalogue objects for project “{project}”")
            return 0
        for row in rows:
            print(self._format_inventory_row(row))
        return 0

    def _fetch_inventory_rows(self, project: str, args: argparse.Namespace) -> list | None:
        """Fetch catalogue rows (and apply the --query filter); None on failure."""
        from ..client import Client

        try:
            rows = Client().data_catalog.list(project, limit=args.limit)
        except Exception as exc:
            print(f"✗ Failed to list catalogue: {exc}", file=sys.stderr)
            logger.debug("knowledge inventory failed", exc_info=True)
            return None
        if args.query:
            rows = [r for r in rows if self._row_matches_query(r, args.query.lower())]
        return rows

    @staticmethod
    def _row_matches_query(r: dict, q: str) -> bool:
        """True if the row matches the free-text inventory query."""
        fields = (r.get("s3_key", ""), r.get("summary", ""), r.get("deterministic_summary", ""))
        return any(q in str(f).lower() for f in fields) or any(
            q in str(t).lower() for t in (r.get("tags") or [])
        )

    def _format_inventory_row(self, row: dict) -> str:
        """One display line for an inventory row (badge + optional links detail)."""
        links = row.get("links") or None
        badge = self._canonical_badge(links)
        filename = str(row.get("s3_key", "")).rsplit("/", 1)[-1]
        desc = f"{row.get('detected_modality', 'unknown')} · {row.get('state', 'unknown')}"
        detail = ""
        if links:
            vectors = ", ".join(self._fmt_vector(v) for v in links.get("vector_rows", []))
            detail = f" — {links.get('document_id')} · {links.get('chunk_nodes', 0)} chunks"
            if vectors:
                detail += f" · {vectors}"
        return f"• {filename} ({desc}) {badge}{detail}"

    @staticmethod
    def _fmt_vector(v: dict) -> str:
        """Compact vector-row summary for the inventory detail suffix."""
        model = str(v.get("model", "")).split("/")[-1]
        return f"{model}d{v.get('dimension', 0)}×{v.get('rows', 0)}"

    # ---- P1b: corpus verbs (folded from `r4 rag`) ----------------------------
    # Thin delegations into `._cli.rag` so the implementation stays single-
    # sourced; `r4 rag <corpus-verb>` prints a fold notice and behaves
    # identically. Durable ingest keeps its own verbs (graph-ingest).

    def _handle_upload(self, args: argparse.Namespace) -> int:
        from .rag import cmd_rag

        if not _resolve_project_arg(args):
            return 1
        args.rag_command = "upload"
        cmd_rag(args)
        return 0

    def _handle_delete(self, args: argparse.Namespace) -> int:
        """Delete one knowledge artifact (ADR-093 MD-16): confirm, then print
        the rows deleted per kind and each object's outcome."""
        from ..client import Client

        if not _resolve_project_arg(args):
            return 1
        if not args.yes and not _confirm(
            f"Delete artifact {args.artifact_id} from project {args.project}, with its "
            "nodes, vectors, edges, IR and findings? This cannot be undone. [y/N] "
        ):
            print("Aborted.")
            return 1
        try:
            result = Client().knowledge.delete_artifact(args.artifact_id, project_id=args.project)
        except Exception as exc:
            print(f"✗ Delete failed: {exc}", file=sys.stderr)
            logger.debug("knowledge delete failed", exc_info=True)
            return 1
        if args.as_json:
            _print_json(asdict(result))
        else:
            print_delete_result(result)
        return 0

    def _handle_bulk_upload(self, args: argparse.Namespace) -> int:
        from .rag import _handle_bulk_upload as _rag_bulk_upload

        if not _resolve_project_arg(args):
            return 1
        return _rag_bulk_upload(args)

    def _handle_docs(self, args: argparse.Namespace) -> int:
        from .rag import _handle_docs as _rag_docs

        return _rag_docs(args)

    def _handle_stats(self, args: argparse.Namespace) -> int:
        from .rag import _handle_stats as _rag_stats

        return _rag_stats(args)

    def _handle_query(self, args: argparse.Namespace) -> int:
        """P2 fold: `r4 knowledge query` = `r4 rag query` (same legacy flow)."""
        from .rag import cmd_rag

        if not _resolve_project_arg(args):
            return 1
        args.rag_command = "query"
        cmd_rag(args)
        return 0

    # ---- graph registry handlers (0069) -------------------------------------

    def _handle_graphs(self, args: argparse.Namespace) -> int:
        """List registered knowledge graphs."""
        from ..client import Client

        try:
            graphs = Client().knowledge.graphs(
                project_id=getattr(args, "project_id", None),
                status=None if args.all else "active",
                query=getattr(args, "query", None),
            )
        except Exception as exc:
            print(f"✗ Failed to list graphs: {exc}", file=sys.stderr)
            logger.debug("knowledge graphs failed", exc_info=True)
            return 1

        if args.as_json:
            _print_json({"graphs": graphs})
            return 0
        if not graphs:
            print("No knowledge graphs found.")
            return 0
        for graph in graphs:
            project = graph.get("project_id") or "-"
            print(
                f"{graph['graph_id']}  {graph.get('name', '')} "
                f"[{graph.get('kind', '')}/{graph.get('status', '')}] project={project}"
            )
            if graph.get("description"):
                print(f"    {graph['description']}")
        return 0

    def _handle_graph(self, args: argparse.Namespace) -> int:
        """Show one knowledge graph."""
        from ..client import Client

        try:
            graph = Client().knowledge.graph_info(args.graph_id)
        except Exception as exc:
            print(f"✗ Graph not found: {exc}", file=sys.stderr)
            logger.debug("knowledge graph show failed", exc_info=True)
            return 1
        if args.as_json:
            _print_json(graph)
        else:
            _print_json({k: v for k, v in graph.items() if v not in (None, "", {})})
        return 0

    def _handle_graph_create(self, args: argparse.Namespace) -> int:
        """Create a named knowledge graph."""
        from ..client import Client

        try:
            graph = Client().knowledge.graph_create(
                args.name,
                description=args.description,
                project_id=args.project_id,
                kind=args.kind,
            )
        except Exception as exc:
            print(f"✗ Graph creation failed: {exc}", file=sys.stderr)
            logger.debug("knowledge graph-create failed", exc_info=True)
            return 1
        if args.as_json:
            _print_json(graph)
        else:
            print(
                f"✓ Graph created: {graph['graph_id']}  {graph.get('name', '')} "
                f"[{graph.get('kind', '')}]"
            )
        return 0

    def _handle_graph_archive(self, args: argparse.Namespace) -> int:
        """Archive a knowledge graph."""
        from ..client import Client

        try:
            graph = Client().knowledge.graph_archive(args.graph_id)
        except Exception as exc:
            print(f"✗ Graph archive failed: {exc}", file=sys.stderr)
            logger.debug("knowledge graph-archive failed", exc_info=True)
            return 1
        print(f"✓ Graph archived: {graph['graph_id']}  {graph.get('name', '')}")
        return 0

    def _handle_graph_access(self, args: argparse.Namespace) -> int:
        """Show or replace a knowledge graph's ADR-080 access list."""
        from ..client import Client

        client = Client()
        try:
            if args.private or args.access_set:
                data = client.set_graph_access(
                    args.graph_id, [] if args.private else args.access_set
                )
            else:
                data = client.get_graph_access(args.graph_id)
        except Exception as exc:
            print(f"✗ Failed to update graph access: {exc}", file=sys.stderr)
            logger.debug("knowledge graph-access failed", exc_info=True)
            return 1
        scopes = data.get("access") or []
        owner = data.get("project_id") or data.get("owner_id") or "?"
        print(f"{args.graph_id}  owning project: {owner}")
        print("  access: " + (", ".join(scopes) if scopes else "owning project only"))
        return 0

    def _handle_graph_ingest(self, args: argparse.Namespace) -> int:
        """Ingest files/URLs into a named knowledge graph."""
        from ..client import Client

        project_id = getattr(args, "project_id", None) or _current_project_id()
        if not project_id:
            print(
                "No project context. Pass --project-id or run: r4 project switch <id>",
                file=sys.stderr,
            )
            return 1
        if not args.files and not args.urls:
            print("Nothing to ingest — pass --file and/or --url", file=sys.stderr)
            return 1
        try:
            result = Client().knowledge.graph_ingest(
                args.graph,
                project_id=project_id,
                files=list(args.files),
                urls=list(args.urls),
                proposal_id=getattr(args, "proposal_id", None),
            )
        except Exception as exc:
            print(f"✗ Graph ingest failed: {exc}", file=sys.stderr)
            logger.debug("knowledge graph-ingest failed", exc_info=True)
            return 1
        if args.as_json:
            _print_json(result)
        else:
            print(
                f"✓ Ingest queued into graph '{args.graph}' — "
                f"job: {result.get('job_id', '?')} ({result.get('status', '?')})"
            )
        return 0

    def _handle_restore(self, args: argparse.Namespace) -> int:
        """Restore a bundle server-side (K4 route)."""
        from ..client import Client

        try:
            payload = Client().knowledge.restore(
                args.bundle_dir,
                project_id=getattr(args, "project_id", None),
                verify=not args.no_verify,
                allow_unsigned=getattr(args, "allow_unsigned", False),
            )
        except Exception as exc:
            print(f"✗ Restore failed: {exc}", file=sys.stderr)
            logger.debug("knowledge restore failed", exc_info=True)
            return 1

        restore = payload.get("restore", payload)
        if args.as_json:
            _print_json(payload)
            return 0

        verified = restore.get("verified", False)
        note = " (verified)" if verified else " (unverified)"
        print(f"✓ Restore complete{note} — project: {restore.get('project_id')}")
        for name, key in (
            ("Artifacts", "artifact_count"),
            ("Documents", "document_count"),
            ("Graph nodes", "node_count"),
            ("Graph edges", "edge_count"),
            ("Embeddings", "embedding_count"),
            ("Catalog rows", "catalog_count"),
            ("A2L links", "a2l_link_count"),
            ("A2L refs", "a2l_ref_count"),
            ("Rows skipped", "skipped_count"),
        ):
            value = restore.get(key)
            if value:
                print(f"  {name:<16}: {value}")
        if restore.get("degraded"):
            print(f"⚠ {restore.get('degraded_reason', 'degraded')}")
        return 0

    def _import_to_platform(self, args: argparse.Namespace, bundle_path: Path) -> int:
        """Upload the bundle; it lands in the caller's own project (D-40)."""
        from ..client import Client

        try:
            options: dict[str, Any] = {"allow_unsigned": getattr(args, "allow_unsigned", False)}
            if getattr(args, "no_raw", False):
                options["accept_raw"] = False
            payload = Client().knowledge.import_bundle(str(bundle_path), **options)
        except Exception as exc:
            print(f"✗ Import failed: {exc}", file=sys.stderr)
            return 1
        if getattr(args, "as_json", False):
            _print_json(payload)
            return 0
        result = payload.get("import", {})
        verified = _verification_label(result)
        print(f"✓ Imported{verified} into project {payload.get('project_id')}")
        for key in ("node_count", "edge_count", "document_count", "embedding_count"):
            if result.get(key):
                print(f"  {key.replace('_', ' '):<16}: {result[key]}")
        _print_canonical_matches(result.get("canonical_matches") or [])
        _print_raw_outcome(result)
        return 0

    def _handle_export_download(self, args: argparse.Namespace) -> int:
        """Fetch a finished server export through the project-scoped files route."""
        from ..client import Client

        key = args.export
        if "/" not in key:  # a job id → the export's deterministic archive key
            project_id = _current_project_id()
            if not project_id:
                print("No active project: pass the archive_key instead", file=sys.stderr)
                return 1
            key = f"{project_id}/knowledge-exports/{key}.kb.tar.gz"
        dest = args.output or key.rsplit("/", 1)[-1]
        try:
            Client().knowledge.download_export(key, dest)
        except Exception as exc:
            print(f"✗ Download failed ({key}): {exc}", file=sys.stderr)
            return 1
        print(f"✓ Downloaded {key} → {dest}")
        return 0

    def _handle_explore(self, args: argparse.Namespace) -> int:
        """Launch the U4 three-pane knowledge explorer TUI."""
        project_id = args.project_id or _current_project_id()
        if not project_id:
            print(
                "No project specified. Pass <project-id> or run: r4 project switch <id>",
                file=sys.stderr,
            )
            return 1

        from ..client import Client
        from ..knowledge_explorer.app import ExploreApp

        knowledge = Client().knowledge
        providers = _ExploreProviders(knowledge, project_id)
        app = ExploreApp(project_id=project_id, **providers.as_kwargs())
        try:
            app.run()
        except Exception as exc:
            print(f"Explorer failed: {exc}", file=sys.stderr)
            logger.debug("knowledge explorer failed", exc_info=True)
            return 1
        return 0

    def _handle_search(self, args: argparse.Namespace) -> int:
        """Unified knowledge search through the K3 facade."""
        project_id = args.project_id or _current_project_id()
        if not project_id:
            print(
                "No project specified. Pass <project-id> or run: r4 project switch <id>",
                file=sys.stderr,
            )
            return 1

        from ..client import Client

        graphs, exclude = _resolve_query_graph_args(args)
        try:
            payload = Client().knowledge.query(
                args.query,
                project_id=project_id,
                scope=args.scope,
                graphs=graphs or None,
                exclude_graphs=exclude or None,
                limit=args.limit,
            )
        except Exception as exc:
            print(f"✗ Query failed: {exc}", file=sys.stderr)
            logger.debug("Knowledge query failed", exc_info=True)
            return 1

        if args.as_json:
            _print_json(payload)
            return 0

        _print_graph_selection_note(payload)
        _print_query_results(payload, args.query)
        return 0

    def _handle_export(self, args: argparse.Namespace) -> int:
        """Export project knowledge to a .kb bundle."""
        project_id = args.project_id or _current_project_id()
        if not project_id:
            print(
                "No project specified. Pass <project-id> or run: r4 project switch <id>",
                file=sys.stderr,
            )
            return 1

        output_path = Path(args.output)
        print(f"Exporting knowledge for project '{project_id}'...")
        print(f"  Output: {output_path}")

        # Try to gather knowledge from the platform
        try:
            manifest = self._export_from_platform(project_id, output_path, args)
            tables = manifest["tables"]
            print(f"\n✓ Client-side bundle exported: {output_path}")
            print(f"  Nodes: {tables['nodes']['count']}")
            print(f"  Edges: {tables['edges']['count']}")
            print(f"  Catalog objects: {tables['catalog']['count']}")
            print(
                "  ⚠ Client projection: documents/embeddings/a2l tables are empty "
                "and the bundle is UNSIGNED."
            )
            print(
                "    Full signed export (server job): r4 project export, then "
                "r4 knowledge export-download <job-id>"
            )
            return 0
        except Exception as exc:
            print(f"✗ Export failed: {exc}", file=sys.stderr)
            logger.debug("Knowledge export failed", exc_info=True)
            return 1

    def _export_from_platform(
        self,
        project_id: str,
        output_path: Path,
        args: argparse.Namespace,
    ) -> dict[str, Any]:
        """Export a client-side projection bundle (r4-kb v1.2, 8-table layout).

        Only tables reachable over REST are populated (graph nodes/edges,
        catalog). Vector- and IR-backed tables (embeddings, documents) and A2L
        rows are intentionally empty here — they live in the server-side
        durable KNOWLEDGE_EXPORT job (ADR-032 amendment 2026-09).
        """
        from ..client import Client

        client = Client()

        entities: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        catalog_objects: list[dict[str, Any]] = []
        if getattr(args, "include_graph", True):
            entities, relations = _fetch_knowledge_graph(client, project_id)
        if getattr(args, "include_catalog", True):
            catalog_objects = _fetch_catalog_objects(client, project_id)

        rows_map: dict[str, list[dict[str, Any]]] = {
            "artifacts": [],
            "documents": [],
            "nodes": entities,
            "edges": relations,
            "embeddings": [],
            "catalog": catalog_objects,
            "a2l_links": [],
            "a2l_refs": [],
        }

        work_dir = output_path
        staged = False
        if output_path.suffix == ".kb":
            import tempfile as _tf

            work_dir = Path(_tf.mkdtemp(prefix="r4-kb-export-"))
            staged = True

        for name, rows in rows_map.items():
            _write_jsonl(work_dir / f"{name}.jsonl", rows)

        manifest = self._build_client_manifest(project_id, rows_map)
        _write_json(work_dir / "manifest.json", manifest)

        if staged:
            import zipfile as _zipfile

            with _zipfile.ZipFile(output_path, "w", _zipfile.ZIP_DEFLATED) as zf:
                for entry in sorted(work_dir.iterdir()):
                    zf.write(entry, entry.name)
            import shutil as _shutil

            _shutil.rmtree(work_dir, ignore_errors=True)

        return manifest

    @staticmethod
    def _build_client_manifest(
        project_id: str, rows_map: dict[str, list[dict[str, Any]]]
    ) -> dict[str, Any]:
        """v1.2 manifest for a client projection (unsigned, pin pending)."""
        from datetime import datetime, timezone
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as pkg_version

        try:
            r4_version = pkg_version("r4")
        except PackageNotFoundError:
            r4_version = "unknown"
        return {
            "format": "r4-kb",
            "version": "1.2",
            "project_id": project_id,
            "generator": "r4",
            "generator_version": r4_version,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "unsigned": True,
            "skills": [],
            "mcp_configs": [],
            "embedding": {
                "model": "",
                "model_path": "",
                "revision": "",
                "dimensions": 0,
                "distance": "cosine",
                "normalization": "unknown",
                "pending": True,
            },
            "tables": {
                name: {"count": len(rows), "file": f"{name}.jsonl"}
                for name, rows in rows_map.items()
            },
        }

    def _handle_import(self, args: argparse.Namespace) -> int:
        """Import a .kb bundle: server-side into your project (archives and
        8-table bundles), or the legacy flat layout into the local store."""
        bundle_path = Path(args.bundle)
        if not bundle_path.exists():
            print(f"✗ Bundle not found: {bundle_path}", file=sys.stderr)
            return 1
        if bundle_path.is_file() or (bundle_path / "tables").is_dir():
            return self._import_to_platform(args, bundle_path)

        print(f"Importing knowledge bundle: {bundle_path}")

        try:
            reader = KnowledgeBundleReader(bundle_path)
            manifest = reader.read_manifest()
            if manifest.version not in BUNDLE_ACCEPTED_VERSIONS:
                print(
                    f"✗ Unsupported bundle version: {manifest.version} (accepted: 1.0, 1.1, 1.2)",
                    file=sys.stderr,
                )
                return 1
            if manifest.tables:
                return self._import_to_platform(args, bundle_path)

            if args.project_id:
                manifest.project_id = args.project_id

            print(f"  Project: {manifest.project_id}")
            print(f"  Documents: {manifest.document_count}")
            print(f"  Chunks: {manifest.chunk_count}")
            print(f"  Entities: {manifest.entity_count}")
            print(f"  Relations: {manifest.relation_count}")
            print(f"  Catalog objects: {manifest.catalog_object_count}")

            # Read bundle contents
            chunks = reader.read_chunks()
            embeddings = reader.read_embeddings()
            entities, relations = reader.read_graph()
            catalog_objects = reader.read_catalog()

            print(
                f"\n  Loaded: {len(chunks)} chunks, {len(embeddings)} embeddings, "
                f"{len(entities)} entities, {len(relations)} relations, "
                f"{len(catalog_objects)} catalog objects"
            )

            local_kb_dir = _persist_bundle_to_local_store(
                manifest,
                chunks=chunks,
                embeddings=embeddings,
                entities=entities,
                relations=relations,
                catalog_objects=catalog_objects,
            )
            reader.cleanup()

            print(f"\n✓ Knowledge imported to: {local_kb_dir}")
            print(f"  Use with: r4 up --knowledge {manifest.project_id}")
            return 0

        except Exception as exc:
            print(f"✗ Import failed: {exc}", file=sys.stderr)
            logger.debug("Knowledge import failed", exc_info=True)
            return 1

    def _handle_list(self, args: argparse.Namespace) -> int:
        """List local knowledge bundles (``bundles``; ``list`` is its alias:
        the project's own knowledge is ``ls``, K1.3)."""
        # List bundles in ~/.config/r4/knowledge/
        kb_root = Path.home() / ".config" / "r4" / "knowledge"
        if not kb_root.exists():
            print("No knowledge bundles found.")
            print(f"  Expected location: {kb_root}")
            return 0

        bundles = []
        for project_dir in kb_root.iterdir():
            if project_dir.is_dir():
                manifest_path = project_dir / "manifest.json"
                if manifest_path.exists():
                    try:
                        manifest = KnowledgeBundleManifest.from_file(manifest_path)
                        bundles.append(manifest)
                    except Exception:
                        pass

        if not bundles:
            print("No knowledge bundles found.")
            return 0

        print(f"Knowledge bundles ({len(bundles)}):")
        print()
        for manifest in bundles:
            print(f"  {manifest.project_id}")
            print(f"    Chunks: {manifest.chunk_count}")
            print(f"    Entities: {manifest.entity_count}")
            print(f"    Relations: {manifest.relation_count}")
            print(f"    Created: {manifest.created_at}")
            print()

        return 0

    def _handle_info(self, args: argparse.Namespace) -> int:
        """Show bundle manifest details."""
        bundle_path = Path(args.bundle)
        if not bundle_path.exists():
            print(f"✗ Bundle not found: {bundle_path}", file=sys.stderr)
            return 1

        try:
            reader = KnowledgeBundleReader(bundle_path)
            manifest = reader.read_manifest()
            reader.cleanup()

            print("Knowledge Bundle:")
            print(f"  Format: {manifest.format}")
            print(f"  Version: {manifest.version}")
            print(f"  Project ID: {manifest.project_id}")
            print(f"  Project Name: {manifest.project_name}")
            print(f"  Created: {manifest.created_at}")
            print(f"  Embedding Model: {manifest.embedding_model}")
            print(f"  Embedding Dimension: {manifest.embedding_dimension}")
            print()
            print("Contents:")
            print(f"  Documents: {manifest.document_count}")
            print(f"  Chunks: {manifest.chunk_count}")
            print(f"  Entities: {manifest.entity_count}")
            print(f"  Relations: {manifest.relation_count}")
            print(f"  Catalog Objects: {manifest.catalog_object_count}")
            return 0

        except Exception as exc:
            print(f"✗ Failed to read bundle: {exc}", file=sys.stderr)
            return 1

    def _handle_validate(self, args: argparse.Namespace) -> int:
        """Validate bundle integrity."""
        bundle_path = Path(args.bundle)
        if not bundle_path.exists():
            print(f"✗ Bundle not found: {bundle_path}", file=sys.stderr)
            return 1

        try:
            manifest = validate_bundle(bundle_path)
            print(f"✓ Bundle is valid: {bundle_path}")
            print(f"  Format: {manifest.format} v{manifest.version}")
            print(f"  Project: {manifest.project_id}")
            print(f"  Chunks: {manifest.chunk_count}")
            return 0
        except Exception as exc:
            print(f"✗ Bundle validation failed: {exc}", file=sys.stderr)
            return 1


# ── Query rendering helpers (module-level) ────────────────────────────────


def _print_query_hit(result: dict) -> None:
    """Render one query hit line (+ optional summary snippet)."""
    score = result.get("score", 0.0)
    print(f"  {result.get('result_type', '?'):<16} {score:>5.2f}  {result.get('title', '')}")
    content = " ".join((result.get("content") or "").split())
    if content:
        print(f"      {content[:160]}")


def _print_unwired_backends(unwired: dict) -> None:
    """List backends the facade could not wire (with reasons)."""
    if not unwired:
        return
    print("Unwired backends:")
    for name, reason in unwired.items():
        print(f"  - {name}: {reason}")


def _print_embeddings_lane(lane: dict | None) -> None:
    """Name the vector lane the facade ran: similarity (model) or why not."""
    if lane:
        detail = "; ".join(str(v) for v in (lane.get("model"), lane.get("reason")) if v)
        print(f"embeddings lane: {lane.get('lane')} ({detail})")


def _print_query_results(payload: dict, query: str) -> None:
    """Render the human-readable query output."""
    # The facade answers {"status": "ok", "query": {...}}; reading the top
    # level printed "0 result(s) [no backend]" for every search (2026-10-09).
    if isinstance(payload.get("query"), dict):
        payload = payload["query"]
    results = payload.get("results", [])
    wired = payload.get("wired_backends") or []
    unwired = payload.get("unavailable_backends") or {}
    duration_ms = payload.get("duration_seconds", 0.0) * 1000
    print(
        f"{len(results)} result(s) for {query!r} "
        f"[{', '.join(wired) or 'no backend'}; {duration_ms:.0f}ms]"
    )
    for result in results:
        _print_query_hit(result)
    _print_embeddings_lane(payload.get("embeddings_lane"))
    _print_unwired_backends(unwired)
