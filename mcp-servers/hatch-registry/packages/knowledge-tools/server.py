"""knowledge-tools — default Hatch! MCP server for project knowledge.

Exposes the Gateway knowledge routes (``/gw/knowledge/*``) as MCP tools:
project status, readiness gate, semantic search, and claim verification.
Stdlib-only; credentials resolve from R4_GATEWAY_URL/R4_GATEWAY_TOKEN env
vars or the active source in ~/.config/r4/sources.json.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _r4_config_dir() -> Path:
    """r4's config dir, resolved the way r4 resolves it (r4/config.py)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(xdg) if xdg else Path.home() / ".config") / "r4"


def _keyed_sources(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Sources that carry both a URL and a key (list or name->record mapping)."""
    sources = data.get("sources", [])
    if isinstance(sources, dict):
        sources = [{"name": k, **v} for k, v in sources.items() if isinstance(v, dict)]
    return [s for s in sources if s.get("url") and s.get("api_key")]


def _gateway_from_sources() -> tuple[str, str]:
    """Read (url, api_key) for r4's ACTIVE source in sources.json.

    Same selection r4 makes (r4/source.py): R4_SOURCE, then the stored
    ``"active"`` key. This used to read ``"current"`` — a key r4 never writes —
    and so silently used the FIRST keyed source, sending a scientist's tool
    calls to a different deployment than the one they selected. It also
    ignored XDG_CONFIG_HOME. With no active source, a single keyed source is
    used; with several, none is guessed.
    """
    sources_path = _r4_config_dir() / "sources.json"
    if not sources_path.is_file():
        return "", ""
    data = json.loads(sources_path.read_text(encoding="utf-8"))
    active = os.environ.get("R4_SOURCE") or data.get("active") or data.get("current")
    keyed = _keyed_sources(data)
    if active:
        keyed = [s for s in keyed if s.get("name") == active]
    if len(keyed) != 1:
        return "", ""
    return keyed[0]["url"].rstrip("/"), keyed[0]["api_key"]


def _resolve_gateway() -> tuple[str, str]:
    """Resolve (gateway_url, token) from env or r4 sources.json."""
    url = os.environ.get("R4_GATEWAY_URL", "").rstrip("/")
    token = os.environ.get("R4_GATEWAY_TOKEN", "")
    if not (url and token):
        src_url, src_key = _gateway_from_sources()
        url = url or src_url
        token = token or src_key
    if not url:
        raise RuntimeError(
            "No Gateway configured: set R4_GATEWAY_URL/R4_GATEWAY_TOKEN or run 'r4 auth login'"
        )
    return url, token


def _default_project_id() -> str:
    """Read the current project from env or r4 projects.json."""
    project = os.environ.get("R4_PROJECT_ID", "")
    if project:
        return project
    projects_path = Path.home() / ".config" / "r4" / "projects.json"
    if projects_path.is_file():
        try:
            data = json.loads(projects_path.read_text(encoding="utf-8"))
            return data.get("current", "") or ""
        except (OSError, json.JSONDecodeError):
            return ""
    return ""


def _gateway_request(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    """Perform a Gateway request and return parsed JSON."""
    base, token = _resolve_gateway()
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}{query}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return {"error": f"HTTP {exc.code}", "detail": detail}


class KnowledgeToolsServer:
    """Project knowledge tools backed by the Gateway knowledge routes."""

    def list_tools(self) -> list[dict[str, Any]]:
        project_param = {
            "type": "string",
            "description": "Project ID (defaults to the current r4 project)",
        }
        return [
            {
                "name": "knowledge_status",
                "description": (
                    "Knowledge build status for a project (artifacts, graph, verifications)"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"project_id": project_param},
                },
            },
            {
                "name": "knowledge_readiness",
                "description": (
                    "Readiness gate report: is project knowledge ready for research iteration?"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"project_id": project_param},
                },
            },
            {
                "name": "knowledge_search",
                "description": "Semantic search over project knowledge objects",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "project_id": project_param,
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "knowledge_verify",
                "description": "Verify a claim/question against project knowledge with citations",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Claim or question to verify",
                        },
                        "project_id": project_param,
                    },
                    "required": ["question"],
                },
            },
        ]

    def invoke(self, tool_name: str, args: dict[str, Any]) -> Any:
        project_id = args.get("project_id") or _default_project_id()
        if tool_name == "knowledge_status":
            return _gateway_request("GET", "/gw/knowledge/status", {"project_id": project_id})
        if tool_name == "knowledge_readiness":
            return _gateway_request("GET", "/gw/knowledge/readiness", {"project_id": project_id})
        if tool_name == "knowledge_search":
            # Re-pointed 2026-10-19 (cleanup C-3a): the retired GET /search
            # engine was never injected (always 503); the tool now rides the
            # same unified facade the r4 client uses (GET /knowledge/query).
            # ADR-085 KGC-16's "always 503" premise has a dated note.
            return _gateway_request(
                "GET",
                "/gw/knowledge/query",
                {
                    "q": args["query"],
                    "project_id": project_id,
                    "scope": "all",
                    "limit": int(args.get("top_k", 5)),
                },
            )
        if tool_name == "knowledge_verify":
            return _gateway_request(
                "POST",
                "/gw/knowledge/verify",
                params={"project_id": project_id},
                body={"question": args["question"]},
            )
        raise ValueError(f"Unknown tool: {tool_name}")


def create_server() -> KnowledgeToolsServer:
    """Entry point required by the Hatch runtime."""
    return KnowledgeToolsServer()
