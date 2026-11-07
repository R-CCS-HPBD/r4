"""``r4 rag`` command tree.

Reorganized 2026-04-27 (Phase 2l): the previous top-level
``r4 pipeline`` (X-001) and ``r4 profile`` (X-002) commands have been
folded into ``r4 rag ingest`` and ``r4 rag retriever`` so all RAG
sub-resources live under one noun. Hard rename — no aliases.

Retired 2026-10-18 (hardening plan H-10.1 follow-up): the ``r4 rag ingest``
CRUD. UMRS kept pipeline definitions in process memory and nothing at
ingest or search time read them; the route family and its ``/gw/pipelines``
edge location are gone. ``r4 rag ingest ...`` now prints the replacement
(site settings + ``r4 rag retriever components``) and calls nothing.

Retired 2026-10-18 (hardening plan H-10.1 rag-profiles wave): the
``r4 rag retriever`` profile CRUD (X-002). Profiles were ephemeral — never
consulted at ingest or search time (site config tunes retrieval); the
routes answered 410 since 2026-08-22 (owner decision) and are deleted with
their ``/gw/rag-profiles`` edge locations. The CRUD prints the reason and
calls nothing; ``components`` stays.

Subtree:

    r4 rag
    ├── upload FILE         one-shot document upload
    ├── query Q             retrieval-augmented chat
    ├── docs                managed documents in a project
    │   ├── list
    │   └── delete ID
    ├── ingest              retired 2026-10-18 (prints the replacement)
    └── retriever           component catalog; profile CRUD retired 2026-10-18
        └── components KIND  (chunkers|embedders|rerankers)
"""

from __future__ import annotations

import argparse
import json
import sys
from argparse import ArgumentParser, Namespace
from typing import Any

from ._helpers import _current_project_id
from .base import CommandHandler
from .registry import command


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


_INGEST_RETIRED_NOTE = (
    "r4 rag ingest: retired (2026-10-18). Ingestion pipeline definitions were "
    "kept in server memory, lost at every restart, and never read by ingest or "
    "search. Ingestion chunks and embeds with the site settings "
    "UMRS_RAG_CHUNK_MAX_TOKENS and UMRS_RAG_EMBEDDING_MODEL; list the available "
    "components with `r4 rag retriever components {chunkers,embedders,rerankers}` "
    "and upload documents with `r4 knowledge upload`."
)


@command
class RagCommand(CommandHandler):
    """``r4 rag`` — RAG ingestion + retrieval surface."""

    name = "rag"
    help = "RAG: documents, retrieval, component catalogs"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Work with documents, retrieval, and the chunker/embedder/reranker "
                "catalogs under a single RAG surface."
            ),
            epilog=(
                "Examples:\n"
                "  r4 rag upload paper.pdf --project demo\n"
                '  r4 rag query "What changed in the latest run?" --project demo\n'
                "  r4 rag retriever components embedders"
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        sub = parser.add_subparsers(
            dest="rag_command",
            title="subcommands",
            metavar="<rag-command>",
        )
        sub.required = True

        # ---- upload (one-shot) ---------------------------------------------
        p_up = sub.add_parser("upload", help="Upload a document")
        p_up.add_argument("file", help="File to upload")
        p_up.add_argument("--project", "-p", required=True, help="Project ID")

        # ---- bulk-upload (directory) ----------------------------------------
        p_bulk = sub.add_parser(
            "bulk-upload",
            help="Upload all supported files from a directory",
        )
        p_bulk.add_argument("directory", help="Directory to scan")
        p_bulk.add_argument("--project", "-p", required=True, help="Project ID")
        p_bulk.add_argument("--recursive", "-r", action="store_true", help="Scan subdirectories")
        p_bulk.add_argument(
            "--dry-run",
            action="store_true",
            help="List files that would be uploaded without uploading",
        )
        p_bulk.add_argument("--json", "-j", action="store_true", dest="as_json", help="JSON output")

        # ---- query ----------------------------------------------------------
        p_q = sub.add_parser("query", help="Query documents (RAG)")
        p_q.add_argument("query", help="Query text")
        p_q.add_argument("--project", "-p", required=True, help="Project ID")
        p_q.add_argument("--top-k", "-k", type=int, default=5)
        p_q.add_argument(
            "--threshold",
            type=float,
            default=None,
            help="Legacy absolute similarity cut (0-1). Omitted by default: the server's "
            "adaptive cut and sufficiency verdict decide (ADR-093 D-10)",
        )
        p_q.add_argument("--no-sources", action="store_true")

        # ---- stats ----------------------------------------------------------
        p_stats = sub.add_parser("stats", help="Show RAG corpus statistics")
        p_stats.add_argument("--project", "-p", help="Project ID (uses current if not set)")
        p_stats.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        # ---- corpus-access (ADR-080 read-sharing list) ---------------------
        p_access = sub.add_parser(
            "corpus-access",
            help="Show or set who may read a project's corpus (user/project/group/system)",
        )
        p_access.add_argument("project", help="Project ID whose corpus list to show/set")
        p_access.add_argument(
            "--set",
            dest="access_set",
            action="append",
            metavar="SCOPE",
            help="Replace list (repeatable): user:<id>, project:<id>, circle:<proj>/<id>, system",
        )
        p_access.add_argument(
            "--private",
            action="store_true",
            help="Clear the list: only the owning project may read the corpus",
        )

        # ---- docs ----------------------------------------------------------
        p_docs = sub.add_parser(
            "docs",
            help="Manage documents in a project",
            description="List or delete stored RAG documents within a project.",
            epilog=(
                "Examples:\n"
                "  r4 rag docs list --project demo\n"
                "  r4 rag docs delete <document-id> --project demo --yes"
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        d_sub = p_docs.add_subparsers(
            dest="docs_command",
            title="subcommands",
            metavar="<docs-command>",
        )
        d_sub.required = True

        p_docs_list = d_sub.add_parser("list", help="List documents")
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

        p_docs_del = d_sub.add_parser("delete", help="Delete a document")
        p_docs_del.add_argument("document_id", help="Document ID to delete")
        p_docs_del.add_argument("--project", "-p", help="Project ID")
        p_docs_del.add_argument("--yes", "-y", action="store_true")

        # ---- ingest: retired 2026-10-18 (H-10.1 follow-up) ----------------
        # Kept as a name so a user who still types it gets the replacement;
        # every argument is accepted and ignored.
        p_ing = sub.add_parser(
            "ingest",
            help="Retired: pipeline definitions were never used (prints the replacement)",
            description=_INGEST_RETIRED_NOTE,
        )
        p_ing.add_argument("ingest_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

        # ---- retriever (was: r4 profile) -----------------------------------
        p_ret = sub.add_parser(
            "retriever",
            help="Retrieval component catalog (profile CRUD retired 2026-10-18)",
            description=(
                "List the retrieval component catalogs. The profile CRUD "
                "(list/show/create/update/delete) is retired — see below."
            ),
            epilog=(
                "Retired 2026-10-18 (H-10.1 rag-profiles wave): profiles were\n"
                "ephemeral and never read at ingest or search time; retrieval\n"
                "tuning lives in site config. The CRUD prints the reason and\n"
                "exits 2 without a request.\n"
                "Examples:\n"
                "  r4 rag retriever components rerankers"
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        r_sub = p_ret.add_subparsers(
            dest="retriever_command",
            title="subcommands",
            metavar="<retriever-command>",
        )
        r_sub.required = True

        r_list = r_sub.add_parser("list", help="Retired: prints the reason (was: list profiles)")
        r_list.add_argument("--project")
        r_list.add_argument("--owner")
        r_list.add_argument("--json", "-j", action="store_true", dest="as_json")

        r_show = r_sub.add_parser("show", help="Retired: prints the reason (was: show a profile)")
        r_show.add_argument("profile_id")
        r_show.add_argument("--json", "-j", action="store_true", dest="as_json")

        r_create = r_sub.add_parser(
            "create", help="Retired: prints the reason (was: create a profile)"
        )
        r_create.add_argument("--file", "-f", required=False)
        r_create.add_argument("--json", "-j", action="store_true", dest="as_json")

        r_update = r_sub.add_parser(
            "update", help="Retired: prints the reason (was: patch a profile)"
        )
        r_update.add_argument("profile_id")
        r_update.add_argument(
            "--set",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help=argparse.SUPPRESS,
        )
        r_update.add_argument("--json", "-j", action="store_true", dest="as_json")

        r_delete = r_sub.add_parser(
            "delete", help="Retired: prints the reason (was: delete a profile)"
        )
        r_delete.add_argument("profile_id")
        r_delete.add_argument("--yes", "-y", action="store_true")

        r_comp = r_sub.add_parser(
            "components",
            help="List available chunkers/embedders/rerankers",
        )
        r_comp.add_argument("kind", choices=["chunkers", "embedders", "rerankers"])
        r_comp.add_argument("--json", "-j", action="store_true", dest="as_json")

        return parser

    # Corpus verbs fold into ``r4 knowledge`` under ADR-027 (P1b CLI
    # consolidation). Behavior is unchanged — the fold is an alias + move
    # of the canonical surface, not a semantic change. ``retriever`` stays
    # under ``r4 rag``; ``ingest`` is retired (2026-10-18).
    _FOLD_NOTICE = (
        "note: `r4 rag {sub}` folds into `r4 knowledge {sub}` (ADR-027 P1b); "
        "behavior is identical — the knowledge tree is the canonical corpus surface. "
        "`r4 rag retriever` stays here."
    )
    _FOLDED_SUBCOMMANDS = ("upload", "bulk-upload", "query", "docs", "stats")

    def handle(self, args: Namespace) -> int:
        sub = getattr(args, "rag_command", None)
        if sub is None:
            print(
                "Usage: r4 rag <upload|query|docs|corpus-access|retriever>",
                file=sys.stderr,
            )
            return 1
        if sub == "ingest":
            return _handle_ingest(args)
        # Retired retriever CRUD prints before preflight (like ingest): the
        # note must not wait on a gateway call (rag-profiles wave 2026-10-18).
        if sub == "retriever" and getattr(args, "retriever_command", None) in _RETRIEVER_CRUD:
            return _handle_retriever(args)

        if sub in self._FOLDED_SUBCOMMANDS:
            print(self._FOLD_NOTICE.format(sub=sub), file=sys.stderr)

        from ._helpers import _preflight_check

        _preflight_check(action="rag")

        # ``upload``/``query`` keep the legacy cmd_rag flow so we don't
        # double-implement the client logic.
        if sub in ("upload", "query"):
            cmd_rag(args)
            return 0

        handler = _rag_subcommand_handlers().get(sub)
        if handler is not None:
            return handler(args)

        print(f"Unknown rag subcommand: {sub}", file=sys.stderr)
        return 1


def _rag_subcommand_handlers():
    """Dispatch table (built lazily so forward defs resolve at call time)."""
    return {
        "bulk-upload": _handle_bulk_upload,
        "stats": _handle_stats,
        "corpus-access": _handle_corpus_access,
        "docs": _handle_docs,
        "retriever": _handle_retriever,
    }


# -----------------------------------------------------------------------------
# bulk-upload (directory)
# -----------------------------------------------------------------------------


# File extensions supported by the RAG pipeline.
_SUPPORTED_EXTENSIONS: set[str] = {
    # Text
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".pdf",
    ".html",
    ".htm",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    # Image
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".webp",
    # Audio
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
    ".m4a",
    ".aac",
    # Video
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".webm",
    ".flv",
    # Notebook
    ".ipynb",
    # Code
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".rs",
    ".go",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".sh",
    ".bash",
    ".r",
    ".R",
    ".m",
    ".jl",
    ".rb",
    ".sql",
    ".scala",
    ".swift",
    ".kt",
    ".kts",
    ".dart",
    ".lua",
    ".pl",
    ".pm",
    ".t",
    ".ps1",
    ".bat",
    ".cmd",
    ".com",
    ".fish",
    ".zsh",
    ".php",
    ".phtml",
    ".php3",
    ".php4",
    ".php5",
    ".php7",
    ".phps",
    ".pht",
    ".thtml",
    ".pyp",
    ".pyc",
    ".pyo",
    ".pyd",
    ".pyw",
    ".pyx",
    ".pxi",
    ".pxd",
    ".pyz",
    ".pywz",
    ".egg",
    ".whl",
    ".jar",
    ".war",
    ".ear",
    ".class",
}


def _collect_bulk_upload_files(directory, recursive: bool) -> list:
    """Collect supported files from a directory (sorted, extension-filtered)."""
    if recursive:
        files = sorted(directory.rglob("*"))
    else:
        files = sorted(directory.glob("*"))

    # Filter to supported extensions
    return [f for f in files if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS]


def _bulk_upload_file(client, file_path, args, results: list, counts: dict) -> None:
    """Upload one file, printing/appending the outcome like the CLI loop did."""
    try:
        result = client.rag.upload(str(file_path), project_id=args.project)
        counts["uploaded"] += 1
        if args.as_json:
            results.append(
                {
                    "file": str(file_path),
                    "document_id": result.document_id,
                    "filename": result.filename,
                    "chunks": result.chunks,
                }
            )
        else:
            print(f"  ✓ {file_path} → {result.document_id}")
    except Exception as exc:
        counts["failed"] += 1
        if args.as_json:
            results.append({"file": str(file_path), "error": str(exc)})
        else:
            print(f"  ✗ {file_path}: {exc}", file=sys.stderr)


def _handle_bulk_upload(args: Namespace) -> int:
    """Bulk upload all supported files from a directory."""
    from pathlib import Path

    from ..client import Client

    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"Error: {directory} is not a directory", file=sys.stderr)
        return 1

    supported_files = _collect_bulk_upload_files(directory, args.recursive)

    if not supported_files:
        print(f"No supported files found in {directory}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"Found {len(supported_files)} file(s) to upload:")
        for f in supported_files:
            print(f"  {f}")
        return 0

    # Upload
    client = Client.from_env()
    counts = {"uploaded": 0, "failed": 0}
    results: list[dict[str, Any]] = []

    for file_path in supported_files:
        _bulk_upload_file(client, file_path, args, results, counts)

    if args.as_json:
        _print_json(
            {
                "uploaded": counts["uploaded"],
                "failed": counts["failed"],
                "results": results,
            }
        )
    else:
        print(f"\nDone: {counts['uploaded']} uploaded, {counts['failed']} failed")

    return 0 if counts["failed"] == 0 else 1


# -----------------------------------------------------------------------------
# stats
# -----------------------------------------------------------------------------


def _handle_stats(args: Namespace) -> int:
    from ..client import Client

    client = Client.from_env()
    project_id = getattr(args, "project", None) or _current_project_id()

    try:
        data = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/rag/stats", params={"project_id": project_id}
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        _print_json(data)
        return 0

    # Pretty-print table
    print(f"RAG Corpus Statistics (project: {project_id})")
    print("-" * 40)
    print(f"  Documents:          {data.get('documents', '?')}")
    print(f"  Chunks:             {data.get('chunks', '?')}")
    print(f"  Avg chunks/doc:     {data.get('avg_chunks_per_doc', '?')}")
    print(f"  Projects indexed:   {data.get('projects', '?')}")
    print(f"  Embedding model:    {data.get('embedding_model', '?')}")
    print(f"  Embedding dims:     {data.get('embedding_dimensions', '?')}")
    if "embedded" in data:
        # K1.3: the spine counts which documents retrieval can find yet.
        print(f"  Indexed documents:  {data.get('embedded')} (pending {data.get('pending', '?')})")
    print(f"  Status:             {data.get('status', '?')}")
    return 0


# -----------------------------------------------------------------------------
# corpus-access (ADR-080)
# -----------------------------------------------------------------------------


def _handle_corpus_access(args: Namespace) -> int:
    """Show or replace a project's ADR-080 corpus read-sharing list."""
    from ..client import Client

    client = Client()
    try:
        if args.private or args.access_set:
            data = client.set_rag_corpus_access(
                args.project, [] if args.private else args.access_set
            )
        else:
            data = client.get_rag_corpus_access(args.project)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    scopes = data.get("access") or []
    print(f"{args.project}  corpus")
    print("  access: " + (", ".join(scopes) if scopes else "owning project only"))
    return 0


# -----------------------------------------------------------------------------
# docs
# -----------------------------------------------------------------------------


def _handle_docs(args: Namespace) -> int:
    from ..client import Client

    sub = getattr(args, "docs_command", None)
    if sub is None:
        print("Usage: r4 rag docs <list|delete>", file=sys.stderr)
        return 1

    if sub == "list":
        # Reshape: cmd_rag's "list" branch reads args.rag_command == "list".
        args.rag_command = "list"

        cmd_rag(args)
        return 0

    if sub == "delete":
        return _docs_delete(args, Client)

    print(f"Unknown rag docs subcommand: {sub}", file=sys.stderr)
    return 1


def _docs_delete(args: Namespace, client_cls: Any) -> int:
    """Delete a corpus document (ADR-093 MD-16): the server resolves it to its
    knowledge artifact and deletes it; print what it deleted, never a bare
    "Deleted"."""
    from .knowledge import print_delete_result

    if not args.yes:
        resp = input(f"Delete document {args.document_id}? [y/N] ")
        if resp.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1
    try:
        result = client_cls().rag.delete_document(
            args.document_id, project_id=getattr(args, "project", None)
        )
    except Exception as exc:
        print(f"✗ Delete failed: {exc}", file=sys.stderr)
        return 1
    print_delete_result(result)
    return 0


# -----------------------------------------------------------------------------
# ingest (was: r4 pipeline) — retired 2026-10-18
# -----------------------------------------------------------------------------


def _handle_ingest(args: Namespace) -> int:
    """``r4 rag ingest ...``: print the replacement; call nothing."""
    del args
    print(_INGEST_RETIRED_NOTE, file=sys.stderr)
    return 2


# -----------------------------------------------------------------------------
# retriever (was: r4 profile)
# -----------------------------------------------------------------------------


_RETRIEVER_CRUD = frozenset({"list", "show", "create", "update", "delete"})

_RETRIEVER_RETIRED_NOTE = (
    "r4 rag retriever <list|show|create|update|delete>: retired — rag-profiles "
    "CRUD was ephemeral (in-memory) and never read at ingest or search time "
    "(retired R3 2026-08-22; the routes and the /gw/rag-profiles edge are "
    "deleted). Retrieval tuning lives in site config; the component catalogs "
    "stay: `r4 rag retriever components {chunkers,embedders,rerankers}`."
)


def _handle_retriever(args: Namespace) -> int:
    """Profile CRUD prints the retirement and calls nothing; components stays."""
    sub = getattr(args, "retriever_command", None)
    if sub in _RETRIEVER_CRUD:
        print(_RETRIEVER_RETIRED_NOTE, file=sys.stderr)
        return 2
    if sub == "components":
        from ..client import Client

        return _retriever_components(args, Client())
    if sub is None:
        print("Usage: r4 rag retriever <components>", file=sys.stderr)
    else:
        print(f"Unknown rag retriever subcommand: {sub}", file=sys.stderr)
    return 1


def _retriever_components(args: Namespace, client) -> int:
    data = client.get(f"{getattr(client, '_api_prefix', '/gw')}/components/{args.kind}")
    if args.as_json:
        _print_json(data)
        return 0
    _print_components(args.kind, _response_items(data))
    return 0


def _response_items(data) -> list[Any]:
    return data if isinstance(data, list) else data.get("items", [])


def _print_components(kind: str, items: list[Any]) -> None:
    if not items:
        print(f"No {kind} registered.")
        return
    for component in items:
        print(_component_line(component))


def _component_line(component: Any) -> str:
    if not isinstance(component, dict):
        return f"  {component}"
    name = (
        component.get("name") or component.get("display_name") or component.get("model_name") or "?"
    )
    return f"  {name:<24} {component.get('description', '')}"


def cmd_rag(args):
    """Handle RAG commands."""
    from ..client import Client

    client = Client()
    handler = _legacy_rag_handlers().get(args.rag_command)
    if handler is None:
        print("Unknown RAG command", file=sys.stderr)
        sys.exit(1)
    handler(args, client)


def _legacy_rag_handlers():
    return {
        "query": _legacy_rag_query,
        "upload": _legacy_rag_upload,
        "list": _legacy_rag_list,
    }


def _legacy_rag_query(args: Namespace, client) -> None:
    response = client.rag.query(
        args.query,
        project_id=args.project,
        top_k=args.top_k,
        threshold=args.threshold,
        include_sources=not args.no_sources,
    )
    print(response.answer)
    if not args.no_sources and response.sources:
        _print_rag_sources(response.sources)


def _print_rag_sources(sources) -> None:
    print("\n--- Sources ---")
    for index, source in enumerate(sources, 1):
        # Spine answers name the source document; endpoint answers carry an id.
        name = (source.metadata or {}).get("source_name") or source.document_id
        print(f"\n[{index}] {name} (score: {source.score:.2f})")
        print(f"    {' '.join(source.content.split())[:200]}...")


def _legacy_rag_upload(args: Namespace, client) -> None:
    result = client.rag.upload(args.file, project_id=args.project)
    print(f"Uploaded: {result.filename}")
    if result.status.startswith("queued"):
        # Central facade: document_id is the KNOWLEDGE_INGEST job id; the
        # graph and vectors are built by the job chain, not by this call.
        print(f"Queued for knowledge ingest: {result.document_id}")
        print("  The graph and vectors build in the background (ingest → understand → embed).")
        print(f"  Follow it: r4 job list · r4 job describe {result.document_id}")
        return
    print(f"Document ID: {result.document_id}")
    print(f"Chunks: {result.chunks}")


def _legacy_rag_list_project_id(args: Namespace) -> str:
    project_id = args.project or _current_project_id()
    if not project_id:
        print(
            "Error: --project is required (or set a current project with `r4 project switch <id>`)",
            file=sys.stderr,
        )
        sys.exit(2)
    return project_id


def _report_rag_list_error(exc: Exception) -> None:
    """Surface FastAPI/UMRS "detail" verbatim when available; re-raise otherwise."""
    from r4.errors import APIError

    if not (isinstance(exc, APIError) and exc.details.get("response_body")):
        raise exc
    print(f"Error: {exc}", file=sys.stderr)
    try:
        import json as _json

        body = _json.loads(exc.details["response_body"])
        detail = body.get("detail") if isinstance(body, dict) else None
        if detail:
            print(f"  detail: {detail}", file=sys.stderr)
    except Exception:
        print(f"  body: {exc.details['response_body'][:500]}", file=sys.stderr)


def _legacy_rag_list_documents(client, project_id: str):
    try:
        return client.rag.list_documents(project_id=project_id)
    except Exception as exc:
        _report_rag_list_error(exc)
        sys.exit(1)


def _legacy_rag_list(args: Namespace, client) -> None:
    project_id = _legacy_rag_list_project_id(args)
    documents = _legacy_rag_list_documents(client, project_id)
    if getattr(args, "as_json", False):
        _print_json(documents)
        return
    if not documents:
        print(f"No documents indexed for project '{project_id}'.")
        return
    for document in documents:
        print(_document_line(document))


def _document_line(document: dict[str, Any]) -> str:
    """``- id: name``, plus who added it when the server names them (the
    spine listing does, K1.3; the endpoint corpus never did)."""
    line = f"- {document.get('document_id')}: {document.get('filename', 'unknown')}"
    raw = document.get("metadata")
    meta: dict[str, Any] = raw if isinstance(raw, dict) else {}
    who = ", ".join(str(u) for u in meta.get("contributors") or [])
    return f"{line}  (added by {who})" if who else line
