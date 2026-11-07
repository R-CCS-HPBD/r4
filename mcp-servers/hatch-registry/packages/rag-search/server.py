"""rag-search — default Hatch! MCP server for platform RAG retrieval.

Exposes the Gateway RAG proxy (``/gw/rag/*``) as MCP tools. Stdlib-only;
credentials resolve from R4_GATEWAY_URL/R4_GATEWAY_TOKEN env vars or the
active source in ~/.config/r4/sources.json.
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
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return {"error": f"HTTP {exc.code}", "detail": detail}


class RagSearchServer:
    """RAG retrieval tools backed by the Gateway RAG proxy."""

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "rag_search",
                "description": "Search indexed documents with a natural-language query",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
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
                "name": "rag_list_documents",
                "description": "List documents in the RAG index",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "rag_status",
                "description": "RAG index health and statistics",
                "parameters": {"type": "object", "properties": {}},
            },
        ]

    def invoke(self, tool_name: str, args: dict[str, Any]) -> Any:
        if tool_name == "rag_search":
            return _gateway_request(
                "POST",
                "/gw/rag/query",
                body={
                    "query": args["query"],
                    "top_k": int(args.get("top_k", 5)),
                },
            )
        if tool_name == "rag_list_documents":
            return _gateway_request("GET", "/gw/rag/documents")
        if tool_name == "rag_status":
            return _gateway_request("GET", "/gw/rag/status")
        raise ValueError(f"Unknown tool: {tool_name}")


def create_server() -> RagSearchServer:
    """Entry point required by the Hatch runtime."""
    return RagSearchServer()
