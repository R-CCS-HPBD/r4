"""Gateway-backed MCP search sources and bounded local code fallback."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from r4.client import Client
    from r4.mcp import MCPTool

    from .core import Source

SearchKind = Literal["web", "code"]

SEARCH_TOOL_NAMES: dict[SearchKind, tuple[str, ...]] = {
    "web": ("web_search", "brave_search"),
    "code": ("code_search", "grep_repo"),
}
_COUNT_ARGUMENTS = ("max_results", "num_results", "limit", "count")
_PATH_ARGUMENTS = ("paths", "path")
_MAX_CANDIDATE_FILES = 1_000
_MAX_FILE_SIZE = 1_048_576
_MAX_MATCHES_PER_FILE = 20


def resolve_search_tool(client: Client, kind: SearchKind) -> MCPTool | None:
    """Resolve the highest-priority exact MCP tool name for a search kind."""
    priorities = SEARCH_TOOL_NAMES.get(kind)
    if priorities is None:
        raise ValueError(f"unknown search kind: {kind}")
    tools = {tool.name.casefold(): tool for tool in client.mcp.list_tools()}
    return next((tools[name] for name in priorities if name in tools), None)


def invoke_search_tool(
    client: Client,
    tool: MCPTool,
    *,
    kind: SearchKind,
    query: str,
    limit: int,
    paths: list[str] | None = None,
) -> list[Source]:
    """Invoke one MCP search tool and normalize its sources."""
    arguments = _invocation_arguments(tool, query=query, limit=limit, paths=paths)
    result = client.mcp.invoke(tool.name, **arguments)
    if not result.success:
        raise RuntimeError(result.error or f"{tool.name} invocation failed")
    items = _normalize_items(result.result)
    mapper = _map_web_source if kind == "web" else _map_code_source
    return [source for item in items if (source := mapper(item)) is not None]


def _invocation_arguments(
    tool: MCPTool,
    *,
    query: str,
    limit: int,
    paths: list[str] | None,
) -> dict[str, Any]:
    properties = tool.parameters.get("properties", {})
    supported = properties if isinstance(properties, dict) else {}
    arguments: dict[str, Any] = {"query": query}
    count_name = _first_supported(supported, _COUNT_ARGUMENTS)
    if count_name:
        arguments[count_name] = limit
    if paths:
        path_name = _first_supported(supported, _PATH_ARGUMENTS)
        if path_name:
            arguments[path_name] = paths if path_name == "paths" else paths[0]
    return arguments


def _first_supported(
    properties: dict[str, Any],
    candidates: tuple[str, ...],
) -> str | None:
    return next((name for name in candidates if name in properties), None)


def _normalize_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("search tool returned invalid JSON") from exc
    if isinstance(payload, dict):
        payload = payload.get("results", payload.get("items"))
    if not isinstance(payload, list):
        raise ValueError("search tool returned an unsupported payload")
    items = [item for item in payload if isinstance(item, dict)]
    if payload and not items:
        raise ValueError("search tool returned no valid result objects")
    return items


def _map_web_source(item: dict[str, Any]) -> Source | None:
    from .core import Source, SourceType

    url = _first_value(item, ("url", "link"))
    title = _first_value(item, ("title",)) or url
    if not title:
        return None
    return Source(
        type=SourceType.WEB_PAGE,
        title=str(title),
        url=str(url) if url else None,
        content=str(_first_value(item, ("snippet", "content", "text", "description")) or ""),
        relevance_score=_score(item),
    )


def _map_code_source(item: dict[str, Any]) -> Source | None:
    from .core import Source, SourceType

    path = _first_value(item, ("file", "path", "name"))
    if not path:
        return None
    metadata = _code_metadata(item)
    return Source(
        type=SourceType.CODE_FILE,
        title=str(path),
        url=str(item["url"]) if item.get("url") else None,
        content=str(_first_value(item, ("content", "snippet", "text")) or ""),
        metadata=metadata,
        relevance_score=_score(item),
    )


def _first_value(item: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if item.get(name):
            return item[name]
    return None


def _code_metadata(item: dict[str, Any]) -> dict[str, Any]:
    keys = ("language", "line", "line_number", "line_start", "line_end")
    metadata = {key: item[key] for key in keys if item.get(key) is not None}
    metadata["search_backend"] = "mcp"
    return metadata


def _score(item: dict[str, Any]) -> float:
    value = item.get("score", item.get("relevance_score", 0.5))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.5


def search_local_code(
    query: str,
    paths: list[str],
    *,
    max_files: int,
) -> list[Source]:
    """Search explicitly supplied paths with deterministic, bounded regex IO."""
    try:
        pattern = re.compile(query)
    except re.error as exc:
        raise ValueError(f"invalid code-search regex: {exc}") from exc

    candidates = _candidate_files(paths)
    sources: list[Source] = []
    for path in candidates[:_MAX_CANDIDATE_FILES]:
        source = _search_local_file(path, pattern)
        if source is not None:
            sources.append(source)
            if len(sources) >= max_files:
                break
    return sources


def _candidate_files(paths: list[str]) -> list[Path]:
    candidates: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            raise ValueError(f"code-search path does not exist: {raw_path}")
        if path.is_file():
            candidates.add(path)
            continue
        candidates.update(
            candidate
            for candidate in path.rglob("*")
            if candidate.is_file() and ".git" not in candidate.parts
        )
    return sorted(candidates, key=lambda item: str(item))


def _search_local_file(path: Path, pattern: re.Pattern[str]) -> Source | None:
    from .core import Source, SourceType

    text = _read_searchable_text(path)
    if text is None:
        return None
    matches = _matching_lines(text, pattern)
    if not matches:
        return None
    return Source(
        type=SourceType.CODE_FILE,
        title=str(path),
        content="\n".join(f"{line_number}: {line}" for line_number, line in matches),
        metadata={
            "path": str(path),
            "line_numbers": [line_number for line_number, _ in matches],
            "search_backend": "local_regex",
        },
        relevance_score=1.0,
    )


def _read_searchable_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_FILE_SIZE:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    return raw.decode("utf-8", errors="replace")


def _matching_lines(
    text: str,
    pattern: re.Pattern[str],
) -> list[tuple[int, str]]:
    matches: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
            matches.append((line_number, line))
            if len(matches) >= _MAX_MATCHES_PER_FILE:
                break
    return matches
