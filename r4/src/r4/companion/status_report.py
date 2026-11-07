"""Aggregate, read-only project/platform status snapshot.

One view of everything a researcher needs about the active source: health,
endpoints, models, workflows (registered + running jobs), MCP servers/tools,
knowledge graphs, research loops/sessions, RAG documents, the scientific
catalog, and companion skills.

Best-effort per section: a surface that errors is shown as a note; it never
masks the rest. Consumed by the TUI ``/status`` command, the webview bridge
(same renderer), and the companion ``project_status`` tool.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, Optional

_ROW_LIMIT = 8

#: H19 (2026-10-24): set while an agent turn runs whose system prompt already
#: carries the skill catalog. The project-state block (and a project_status
#: call in that turn) then leaves out "Companion Skills": the model read the
#: same list twice, about 2.9k chars per request. Outside a turn (the TUI's
#: /status) the section stays.
_SKILLS_IN_PROMPT: ContextVar[bool] = ContextVar("r4_skills_in_prompt", default=False)


@contextmanager
def skills_listed_in_prompt() -> Iterator[None]:
    """Mark the running agent turn's prompt as carrying the skill catalog."""
    token = _SKILLS_IN_PROMPT.set(True)
    try:
        yield
    finally:
        _SKILLS_IN_PROMPT.reset(token)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _val(value: Any) -> str:
    """Render enums and nested values as their scalar value."""
    return str(getattr(value, "value", value))


def _as_list(payload: Any) -> list:
    """Normalize list payloads, unwrapping single-key dicts ({'loops': [...]})."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                return value
    return []


def _first_str(obj: Any, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _get(obj, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "?"


def _rows(items: list[str], limit: int = _ROW_LIMIT) -> list[str]:
    if len(items) <= limit:
        return items
    return items[:limit] + [f"- ... (+{len(items) - limit} more)"]


def _section(title: str, lines: list[str], note: Optional[str] = None) -> str:
    body = _rows(lines) if lines else ["(none)"]
    if note:
        body = [f"unavailable: {note}"] + body
    return f"## {title}\n" + "\n".join(body)


def _health_section(client: Any) -> str:
    try:
        healthy = client.health()
        readiness = client.readiness() or {}
        checks = readiness.get("checks") or {}
        failed = [k for k, v in checks.items() if str(v).lower() not in ("ok", "ready")]
        lines = [f"- healthy: {healthy}", f"- ready: {readiness.get('ready')}"]
        if failed:
            lines.append(f"- checks failing: {', '.join(sorted(failed))}")
        return _section("Source & Health", lines)
    except Exception as e:
        return _section("Source & Health", [], note=str(e))


def _endpoints_section(client: Any) -> str:
    try:
        result = client.list_endpoints()
        eps = result if isinstance(result, list) else (result or {}).get("endpoints", [])
        rows = [
            f"- {_get(ep, 'instance_id', '?')}  model={_get(ep, 'model', '?')}"
            f"  tier={_get(ep, 'tier', '?')}  status={_get(ep, 'status', '?')}"
            for ep in eps
        ]
        return _section(f"Endpoints ({len(rows)})", rows)
    except Exception as e:
        return _section("Endpoints", [], note=str(e))


def _model_client(client: Any) -> Any:
    from ..models import ModelClient

    return ModelClient(config=client.config)


def _models_section(client: Any) -> str:
    try:
        models = _model_client(client).list_models(limit=50)

        def _row(m: Any) -> str:
            parts = (
                f"{_first_str(m, ('name',))}  scope={_val(_get(m, 'scope', '?'))}"
                f"  type={_val(_get(m, 'model_type', '?'))}"
                f"  status={_val(_get(m, 'status', '?'))}"
            )
            return f"- {parts}"

        rows = [_row(m) for m in models]
        ready_states = {"available", "ready"}
        ready = sum(1 for m in models if _val(_get(m, "status", "")).lower() in ready_states)
        rows.insert(0, f"- ready: {ready}/{len(models)}")
        return _section(f"Model Registry ({len(models)})", rows)
    except Exception as e:
        return _section("Model Registry", [], note=str(e))


def _workflows_section(client: Any) -> str:
    try:
        definitions = client.workflows.list_workflows()
        names = [
            str(_get(wf, "workflow_name", None) or _get(wf, "name", "?")) for wf in definitions
        ]
        rows = [f"- {n}" for n in sorted(names)]
        return _section(f"Registered Workflows ({len(rows)})", rows)
    except Exception as e:
        return _section("Registered Workflows", [], note=str(e))


def _jobs_section(client: Any) -> str:
    try:
        jobs = client.jobs.list(status="running", limit=20)
        rows = [
            f"- {_get(j, 'job_id', '?')}  type={_get(j, 'job_type', '?')}"
            f"  created={_get(j, 'created_at', '?')}"
            for j in jobs
        ]
        return _section(f"Running Jobs ({len(rows)})", rows)
    except Exception as e:
        return _section("Running Jobs", [], note=str(e))


def _mcp_section(client: Any) -> str:
    try:
        servers = client.mcp.list_servers()
        rows = [
            f"- {_get(s, 'name', '?')}: {_get(s, 'url', '?')}"
            + (f" [{_get(s, 'status')}]" if _get(s, "status") else "")
            for s in servers
        ]
        try:
            tools = client.mcp.list_tools()
            rows.append(f"- tools available: {len(tools)}")
        except Exception as e:
            rows.append(f"- tools unavailable: {e}")
        return _section(f"MCP Servers ({len(servers)})", rows)
    except Exception as e:
        return _section("MCP Servers", [], note=str(e))


def _knowledge_section(client: Any) -> str:
    try:
        graphs = client.knowledge.graphs(status="active")
        rows = [
            f"- {_get(g, 'name', _get(g, 'graph_id', '?'))}"
            f"  kind={_get(g, 'kind', '?')}  status={_get(g, 'status', '?')}"
            for g in graphs
        ]
        return _section(f"Knowledge Graphs ({len(rows)})", rows)
    except Exception as e:
        return _section("Knowledge Graphs", [], note=str(e))


def _research_section(client: Any, project_id: Optional[str]) -> str:
    rows: list[str] = []
    note = None
    try:
        loops = _as_list(client.knowledge.list_loops(project_id))
        for loop in loops:
            name = _first_str(loop, ("loop_name", "name", "id", "loop_id"))
            status = _val(_get(loop, "status", "?"))
            rows.append(f"- loop {name}  status={status}")
    except Exception as e:
        note = str(e)
    try:
        # No 'latest' sentinel: the server reads it as a project id (403 for
        # non-admins). None lets scope_filter bind the caller's project (#6).
        target = project_id or getattr(getattr(client, "config", None), "project_id", None)
        sessions = _as_list(client.list_research_sessions(target, status="active"))
        for s in sessions:
            name = _first_str(s, ("session_name", "name", "id", "session_id"))
            status = _val(_get(s, "status", _get(s, "state", "?")))
            rows.append(f"- session {name}  status={status}")
    except Exception as e:
        note = f"{note or ''} sessions: {e}".strip()
    return _section("Research Loops & Sessions", rows, note=note)


def _rag_section(client: Any, project_id: Optional[str]) -> str:
    try:
        docs = client.rag.list_documents(project_id=project_id)
        rows = [
            f"- {_get(d, 'filename', '?')}  id={_get(d, 'document_id', '?')}"
            f"  chunks={_get(d, 'chunks', 0)}"
            for d in docs
        ]
        return _section(f"RAG Documents ({len(rows)})", rows)
    except Exception as e:
        return _section("RAG Documents", [], note=str(e))


def _catalog_section(client: Any) -> str:
    try:
        data = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/catalog/search",
            params={"q": "", "limit": _ROW_LIMIT},
        )
        results = data.get("results", []) if isinstance(data, dict) else []
        rows = [
            f"- {_get(r, 's3_key', _get(r, 'key', '?'))}"
            + (f"  [{', '.join(_get(r, 'tags', [])[:3])}]" if _get(r, "tags") else "")
            for r in results
        ]
        # Data catalog (S3 objects) — NOT the design's "Scientific Catalog"
        # object (gap row D-39); the label clash was removed 2026-09-22 (V-12).
        return _section(f"Data Catalog (latest {len(rows)})", rows)
    except Exception as e:
        return _section("Data Catalog", [], note=str(e))


def _skills_section() -> str:
    try:
        from .skills import SkillRegistry

        registry = SkillRegistry()
        builtins = registry.builtin_skills
        active = sorted(registry.active_names()) if hasattr(registry, "active_names") else []
        rows = [f"- {s.name}: {s.description}" for s in builtins]
        if active:
            rows.insert(0, f"- active: {', '.join(active)}")
        return _section(f"Companion Skills ({len(builtins)})", rows)
    except Exception as e:
        return _section("Companion Skills", [], note=str(e))


def build_project_status(
    client: Any,
    project_id: Optional[str] = None,
    include_local: bool = False,
) -> str:
    """Plain-text status snapshot over every connected surface."""
    sections: list[Callable[[], str]] = [
        lambda: _health_section(client),
        lambda: _endpoints_section(client),
        lambda: _models_section(client),
        lambda: _workflows_section(client),
        lambda: _jobs_section(client),
        lambda: _mcp_section(client),
        lambda: _knowledge_section(client),
        lambda: _research_section(client, project_id),
        lambda: _rag_section(client, project_id),
        lambda: _catalog_section(client),
    ]
    if include_local and not _SKILLS_IN_PROMPT.get():
        sections.append(_skills_section)
    return "\n\n".join(fn() for fn in sections)
