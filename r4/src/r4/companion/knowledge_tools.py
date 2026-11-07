"""Companion tools for project knowledge — status, ingest, describe.

Chat-first verbs for the knowledge pipeline. Keep them thin over the shared
``client.knowledge`` facade so all r4-tool surfaces (Companion chat, slash
handlers, console TUI slash route, vscode webview Studio) reach the same
server behavior without duplicating state.

One research spine, three layers (ADR-036; the RC1 ``project_research_*``
family retired 2026-09-07, WP-07):
- ``knowledge_*`` — the graph itself (ingest, status, show, query); loops read
  it and write their findings back into it with lineage.
- ``research_loop_*`` — the server-hosted, durable loop (start/list/status/
  stop, approvals/decide).
- ``workflow_*`` — registered UMRS pipelines, the compute layer beneath a loop.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .knowledge_write_gate import resolve_ingest_target

if TYPE_CHECKING:
    from r4.client import Client


def _tool(name: str, description: str, parameters: dict[str, Any], execute) -> dict[str, Any]:
    return {"name": name, "description": description, "parameters": parameters, "execute": execute}


def _structured_error(code: str, message: str, **details: Any) -> str:
    return json.dumps(
        {"type": "knowledge_error", "code": code, "message": message, "details": details},
        indent=2,
    )


def _project_id_for(client: "Client", override: str | None) -> str | None:
    if override and override.strip():
        return override.strip()
    config = getattr(client, "config", None)
    project_id = getattr(config, "project_id", None)
    if isinstance(project_id, str) and project_id.strip():
        return project_id
    # Fallback: the client config is snapshot at construction; a project
    # created/switched mid-session (chat tool, Studio, CLI) lives in the
    # shared local registry and must be visible to knowledge calls too.
    try:
        from r4.project import get_project_manager

        current = get_project_manager().get_current()
    except Exception:  # noqa: BLE001
        return None
    return current if isinstance(current, str) and current.strip() else None


def _normalize_string_list(value: Any) -> list[str]:
    """Tools callers often pass a single string instead of a list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    raise ValueError("expected a string or list of strings")


def _knowledge_type_counts(nodes: list[dict]) -> dict[str, int]:
    """Group nodes by type field for numeric summaries."""
    counts: dict[str, int] = {}
    for node in nodes:
        node_type = str(node.get("type", "knowledge")).lower()
        counts[node_type] = counts.get(node_type, 0) + 1
    return counts


def _knowledge_active_loop_count(client: "Client", project_id: str) -> tuple[int, int]:
    """(active_loop_count, total_loops) — swallows older-gateway failures."""
    try:
        loops = client.knowledge.list_loops(project_id) or []
    except Exception:  # noqa: BLE001 - older gateway may lack loop list
        return 0, 0
    active = sum(
        1
        for loop in loops
        if str(loop.get("status", "")).lower() in {"running", "paused", "pending"}
    )
    return active, len(loops)


def _build_knowledge_status(client: "Client") -> dict[str, Any]:
    def _status(project_id: str | None = None) -> str:
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected — pass project_id or set one in the client config.",
            )
        try:
            graph = client.knowledge.get_graph(target)
            nodes = graph.get("nodes") or []
            edges = graph.get("edges") or []
            active_loops, total_loops = _knowledge_active_loop_count(client, target)
            return json.dumps(
                {
                    "type": "knowledge_status",
                    "project_id": target,
                    "nodes_total": len(nodes),
                    "edges_total": len(edges),
                    "nodes_by_type": _knowledge_type_counts(nodes),
                    "active_loops": active_loops,
                    "loop_total": total_loops,
                },
                indent=2,
                default=str,
            )
        except Exception as exc:  # noqa: BLE001
            return _structured_error("knowledge_status_failed", str(exc), project_id=target)

    return _tool(
        "knowledge_status",
        "Show a compact snapshot of the project knowledge graph "
        "(node/edge counts by type + active research loops).",
        {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project (default: current)"},
            },
        },
        _status,
    )


def _queue_durable_ingest(
    client: "Client",
    target: str,
    paths: list[str],
    urls: list[str],
    proposal_id: str | None = None,
    graph_id: str | None = None,
) -> dict[str, Any]:
    """Queue durable ingest for workstation paths and/or remote URLs.

    File contents reach UMRS inline or as uploaded bytes (``ingest_paths``)
    — paths never travel; the job runs server-side.

    Per-call actor marking (0069): the actor header is set HERE, for this
    call only — not at tool-build time, and reset when the call ends (K1.5)
    — so the shared client never leaks agent identity into human-initiated
    paths (e.g. Studio ingest buttons through the same ACP client).
    """
    from .knowledge_graph_tools import agent_mutation

    with agent_mutation(client):
        return client.knowledge.ingest_paths(
            target,
            paths,
            urls=urls,
            proposal_id=proposal_id,
            graph_id=graph_id,
        )


#: The typed refusals of ``POST /knowledge/external/search`` (K1.1, K-13).
_EXTERNAL_SEARCH_REFUSALS = ("external_search_disabled", "external_search_restricted")


def external_search_skipped(queries: list[str], exc: Exception) -> dict[str, Any]:
    """What a refused or failed external search reports instead of raising.

    K1.1 turned external search off by default (403), and it ran after the
    ingest job was queued: the whole call reported failure, the job id was
    lost and the agent re-uploaded (review of K1 part A, 2026-10-24).
    """
    text = str(exc)
    code = next((c for c in _EXTERNAL_SEARCH_REFUSALS if c in text), "external_search_failed")
    return {"queries": queries, "skipped": code, "message": text[:300]}


def _run_external_probes(client: "Client", target: str, queries: list[str]) -> dict[str, Any]:
    """External-search probes capped at five terms; a refusal is reported,
    never raised (the ingest job beside it stays queued and named)."""
    limited = queries[:5]
    try:
        results = [client.knowledge.external_search(target, q) for q in limited]
    except Exception as exc:  # noqa: BLE001 — reported in the payload, by name
        return external_search_skipped(limited, exc)
    return {"queries": limited, "results": results}


def _execute_ingest(
    client: "Client",
    target: str,
    *,
    paths: list[str],
    queries: list[str],
    urls: list[str],
    proposal_id: str | None,
    graph_id: str | None,
) -> dict[str, Any]:
    """Queue the durable ingest job and/or external probes; compose payload."""
    result: dict[str, Any] = {"type": "knowledge_ingested", "project_id": target}
    if graph_id:
        result["target_graph"] = graph_id
    if paths or urls:
        result["ingest_job"] = _queue_durable_ingest(
            client,
            target,
            paths,
            urls,
            proposal_id=proposal_id,
            graph_id=graph_id,
        )
    if queries:
        result["external_search"] = _run_external_probes(client, target, queries)
    result["next"] = (
        "The durable ingest job extracts knowledge and grows the graph. "
        "knowledge_status for counts; research_loop_start to begin iterations; "
        "the webview Studio Knowledge & Loops tab visualizes the graph."
    )
    return result


def _build_knowledge_ingest(client: "Client") -> dict[str, Any]:
    def _ingest(
        files: str | list[str] | None = None,
        sources: str | list[str] | None = None,
        urls: str | list[str] | None = None,
        project_id: str | None = None,
        proposal_id: str | None = None,
        graph_id: str | None = None,
    ) -> str:
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected. Create one with project_ensure "
                "(project_id, e.g. from the research subject), then retry.",
            )
        # Session acquisition mode (write gate): the user pins or disables
        # knowledge growth for this chat — checked BEFORE any server call.
        resolved_graph, gate_error = resolve_ingest_target(client, graph_id)
        if gate_error:
            return _structured_error("knowledge_acquisition_blocked", gate_error)
        graph_id = resolved_graph
        paths = _normalize_string_list(files)
        queries = _normalize_string_list(sources)
        urls_l = _normalize_string_list(urls)
        if not paths and not queries and not urls_l:
            return _structured_error(
                "empty_ingest",
                "Nothing to ingest — pass files, sources (search terms), and/or urls.",
            )
        try:
            result = _execute_ingest(
                client,
                target,
                paths=paths,
                queries=queries,
                urls=urls_l,
                proposal_id=proposal_id,
                graph_id=graph_id,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as exc:  # noqa: BLE001
            return _structured_error("knowledge_ingest_failed", str(exc), project_id=target)

    return _tool(
        "knowledge_ingest",
        "Add data into the project knowledge graph — local file paths, web "
        "search subjects (sources), and/or specific web page URLs. URLs are "
        "fetched inside the egress-controlled durable ingest job, then "
        "extracted and linked into the graph. If the user gave no project, "
        "call project_ensure first. Confirm what was queued and suggest they "
        "can inspect the Studio graph or start a research loop next.",
        {
            "type": "object",
            "properties": {
                "files": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Local file/dir paths to ingest (single string or list)",
                },
                "sources": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": (
                        "Search terms for external academic search (may be off on "
                        "this site; files/urls still ingest)"
                    ),
                },
                "urls": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": (
                        "Specific web page URLs to fetch + ingest "
                        "(e.g. a project homepage or paper page)"
                    ),
                },
                "project_id": {"type": "string", "description": "Project (default: current)"},
                "proposal_id": {
                    "type": "string",
                    "description": (
                        "Approved graph-extension proposal id executing this "
                        "ingest. REQUIRED when the ingest executes a proposal "
                        "(agent-originated): the server rejects without it."
                    ),
                },
                "graph_id": {
                    "type": "string",
                    "description": (
                        "Target named knowledge graph (id or name). Default: "
                        "the project's default graph. Use the graph named in "
                        "an approved proposal."
                    ),
                },
            },
        },
        _ingest,
    )


def _knowledge_node_line(node: dict) -> str:
    """Line for one graph node in textual summaries."""
    label = node.get("name") or node.get("label") or node.get("id")
    node_type = str(node.get("type", "knowledge")).lower()
    confidence = node.get("confidence")
    cf_str = f" ({confidence * 100:.0f}%)" if isinstance(confidence, (int, float)) else ""
    return f"- [{node_type}] {label}{cf_str}"


def _build_knowledge_show(client: "Client") -> dict[str, Any]:
    def _show(project_id: str | None = None, type_filter: str | None = None) -> str:
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected — pass project_id or set one in the client config.",
            )
        try:
            graph = client.knowledge.get_graph(target)
            nodes = graph.get("nodes") or []
            if type_filter:
                key = type_filter.lower()
                nodes = [n for n in nodes if str(n.get("type", "")).lower() == key]
            lines = [_knowledge_node_line(node) for node in nodes[:50]]
            return json.dumps(
                {
                    "type": "knowledge_show",
                    "project_id": target,
                    "count": len(nodes),
                    "nodes": lines,
                    "hint": ("Subset with type_filter (hypothesis|task|knowledge|paper|dataset)."),
                },
                indent=2,
                default=str,
            )
        except Exception as exc:  # noqa: BLE001
            return _structured_error("knowledge_show_failed", str(exc), project_id=target)

    return _tool(
        "knowledge_show",
        "List knowledge graph nodes by label (optionally filtered by node type).",
        {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "type_filter": {
                    "type": "string",
                    "description": "Node type filter (hypothesis, task, paper, …)",
                },
            },
        },
        _show,
    )


def _context_excerpt_gate(excerpt: str) -> str | None:
    """Empty/oversize guards for the build-from-context verb."""
    if not excerpt:
        return "empty_context"
    if len(excerpt) > 4 * 1024 * 1024:
        return "context_too_large"
    return None


_TOOL_MESSAGE_ROLES: frozenset[str] = frozenset({"tool", "tool_result", "function"})


def _parse_role_jsonl(text: str) -> list[list[str]] | None:
    """Parse role-marked JSONL excerpt lines into [role, text] rows.

    Returns None when the excerpt is NOT uniform role-marked JSONL (the
    raw-text path then applies untouched).
    """
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = _role_row(line)
        if row is None:
            return None
        rows.append(row)
    return rows or None


def _role_row(line: str) -> list[str] | None:
    """One role-marked JSONL line → [role, text]; None when not role-marked."""
    import json

    try:
        item = json.loads(line)
    except ValueError:
        return None
    if not isinstance(item, dict) or "role" not in item:
        return None
    body = item.get("text") or item.get("content") or ""
    return [str(item.get("role") or ""), str(body)]


def _verbatim_slice(
    content: str, *, include_tool_results: bool
) -> tuple[list[list[str]], str | None]:
    """Split the excerpt into verbatim rows, excluding tool-result lines
    by default (they carry fetched content, not what the user said —
    ADR-050 review 2026-09-13).
    """
    text = (content or "").strip()
    if not text:
        return [], "empty_context"
    rows = _parse_role_jsonl(text)
    if rows is None:
        return [["session", text]], None
    kept = _kept_rows(rows, include_tool_results=include_tool_results)
    if not kept:
        return [], "nothing_after_tool_exclusion"
    return kept, None


def _kept_rows(rows: list[list[str]], *, include_tool_results: bool) -> list[list[str]]:
    """Non-empty rows, minus tool-result roles unless asked to keep them."""
    if not include_tool_results:
        rows = [row for row in rows if row[0].lower() not in _TOOL_MESSAGE_ROLES]
    return [row for row in rows if row[1].strip()]


def _session_history_package(
    client: "Client",
    *,
    title: str,
    excerpt: str,
    target: str,
    stamp: str,
    include_tool_results: bool = False,
) -> tuple[str, bytes, str | None]:
    """The verbatim session-history JSONL package (filename + bytes).

    Only what the agent was told to keep — never a synthesized claim.
    """
    import json as _json

    safe_title = " ".join((title or "session context").strip().split())[:120]
    lines, error = _verbatim_slice(excerpt, include_tool_results=include_tool_results)
    if error:
        return "", b"", error
    payload = {
        "lane": "session-history",
        "session_id": getattr(client, "session_id", None) or "",
        # The agent typed this excerpt (review of K1 part B, 2026-10-24): it
        # is agent-written, proposed until a person establishes it. A
        # verbatim keep read from the session store is the user's /save.
        "built_by": "agent",
        "title": safe_title,
        "stamp_utc": stamp,
        "lines": [{"role": role, "text": text, "verbatim": True} for role, text in lines],
    }
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in safe_title.lower()[:60])
    filename = f"session-context/{target}/{stamp}-{safe}.jsonl"
    return filename, (_json.dumps(payload) + "\n").encode("utf-8"), None


def _build_knowledge_build_from_context(client: "Client") -> dict[str, Any]:
    def _build(
        content: str,
        title: str,
        project_id: str | None = None,
        graph_id: str | None = None,
        include_tool_results: bool = False,
        proposal_id: str | None = None,
    ) -> str:
        """Materialize the current chat's history/context into knowledge."""
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected. Create or switch with project_use, then retry.",
            )
        resolved_graph, gate_error = resolve_ingest_target(client, graph_id)
        if gate_error:
            return _structured_error("knowledge_acquisition_blocked", gate_error)
        graph_id = resolved_graph

        excerpt = (content or "").strip()
        guard = _context_excerpt_gate(excerpt)
        if guard:
            return _structured_error(
                guard,
                "Nothing to build from — pass the verbatim excerpt (turns/slices)."
                if guard == "empty_context"
                else "Excerpt exceeds 4 MiB — build from smaller slices per call.",
            )

        import datetime
        import json as _json

        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename, bytes_body, package_error = _session_history_package(
            client,
            title=title,
            excerpt=excerpt,
            target=target,
            stamp=stamp,
            include_tool_results=include_tool_results,
        )
        if package_error:
            return _structured_error(
                package_error,
                (
                    "No lines remain after excluding tool results — "
                    "mark `include_tool_results: true` to keep them."
                ),
            )
        try:
            body = _upload_session_history(
                client,
                filename=filename,
                bytes_body=bytes_body,
                target=target,
                graph_id=graph_id,
                proposal_id=proposal_id,
            )
            return _json.dumps(body, indent=2, default=str)
        except Exception as exc:  # noqa: BLE001
            return _structured_error("context_build_failed", str(exc), project_id=target)

    return _tool(
        "knowledge_build_from_context",
        (
            "Package chat-history excerpts into a durable session-history "
            "knowledge artifact (same acquisition write gate as every "
            "knowledge verb). It is an agent write like knowledge_ingest: it "
            "needs an approved graph-extension proposal (proposal_id) and "
            "lands proposed until a person establishes it. To keep a turn "
            "verbatim the user runs /save themselves. Only with what the user "
            "asks to keep; never summarize away their words. Tool-result "
            "lines (role=tool/tool_result/function) are excluded by default."
        ),
        {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "Verbatim excerpt — raw text, or JSONL lines "
                        '{"role": "user|assistant|tool", "text"|"content": "..."} '
                        "(tool-role lines are excluded by default)"
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Short artifact title (e.g. 'ucx shmget failure notes')",
                },
                "project_id": {"type": "string"},
                "graph_id": {"type": "string"},
                "include_tool_results": {
                    "type": "boolean",
                    "description": "Keep tool/tool_result/function-role lines (default false)",
                    "default": False,
                },
                "proposal_id": {
                    "type": "string",
                    "description": "Approved graph-extension proposal (knowledge_graph_propose)",
                },
            },
            "required": ["content", "title"],
        },
        _build,
    )


def _upload_session_history(
    client: "Client",
    *,
    filename: str,
    bytes_body: bytes,
    target: str,
    graph_id: str | None,
    proposal_id: str | None = None,
) -> dict[str, Any]:
    """Upload leg over the same central durable ingest every write takes,
    marked as the agent's (``agent_mutation``): the model typed the excerpt,
    so the server's agent rule applies (an approved proposal, rows proposed).
    Unmarked, it landed established as the user's, unscanned (review of K1
    part B, 2026-10-24)."""
    from .knowledge_graph_tools import agent_mutation

    knowledge = client.knowledge
    with agent_mutation(client):
        headers = dict(knowledge._actor_headers() or {})
    if proposal_id:
        headers["x-proposal-id"] = proposal_id
    body = knowledge._client.post_multipart(
        f"{knowledge._client._api_prefix}/knowledge/upload",
        filename=filename.rsplit("/", 1)[-1],
        content=bytes_body,
        content_type="application/x-ndjson",
        params={"project_id": target, **({"graph_id": graph_id} if graph_id else {})},
        headers=headers,
    )
    body["lane"] = "session-history"
    return body


def build_knowledge_tools(
    client: "Client", reader: Any = None, evidence: Any = None
) -> list[dict[str, Any]]:
    """Return knowledge-pipeline tools for Companion.

    Includes the multi-graph tools (0069) — lazily imported so this module
    stays the single registration point (knowledge_graph_tools imports the
    helpers above, so a module-level import here would cycle). ``reader``
    (the session's model, a name or its accessor) sizes ``knowledge_query``
    (ADR-093 D-14); ``evidence`` (the session's ToolEvidence) labels its hits
    in the session's evidence ledger (K1.4).
    """
    from .knowledge_graph_tools import build_knowledge_graph_tools

    return [
        _build_knowledge_status(client),
        _build_knowledge_ingest(client),
        _build_knowledge_show(client),
        _build_knowledge_build_from_context(client),
        *build_knowledge_graph_tools(client, reader=reader, evidence=evidence),
    ]
