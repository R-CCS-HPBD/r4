"""First-class MCP tool registration for the companion (catch-up W1/G2).

Previously the companion exposed MCP only through discovery wrappers
(``mcp_list_servers``, ``mcp_list_tools``, ``mcp_invoke(tool_name,
arguments_json)``) — an opaque two-hop that costs the model ≥1 discovery
turn and accepts an unvalidated JSON-blob argument string.

This module converts each MCP descriptor into an individually-named,
fully-schemad tool entry (``mcp.<server>.<tool>``), matching how Copilot
and Claude Code register MCP tools at session start:

- name: ``mcp.<server>.<name>`` (dotted names pass through the planner)
- parameters: the server's ``inputSchema`` passed through verbatim
- handler: thin ``client.mcp.invoke`` passthrough with a) friendly
  error strings and b) a cheap local pre-validation of ``required``
  fields and primitive types so malformed arguments never reach the
  upstream (acceptance: malformed-arg error rate -> 0)
- cache: TTL 300s keyed by a fingerprint of (server, name, schema-hash)
  tuple across tools; ``mcp_refresh`` tool invalidates and forces
  ``client.mcp.list_tools(refresh=True)`` so newly-added servers appear
  next session
- failure: gateway down / no MCP configured -> return [] silently; the
  legacy discovery wrappers remain the fallback
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from r4.client import Client

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

_PRIMITIVE_TYPES = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _fingerprint(name: str, server: str, parameters: dict[str, Any]) -> str:
    blob = json.dumps({"n": name, "s": server, "p": parameters}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _validate_required(schema: dict[str, Any], args: dict[str, Any]) -> str | None:
    required = schema.get("required") or []
    missing = [name for name in required if name not in args]
    if missing:
        return f"missing required field(s): {', '.join(missing)}"
    return None


def _validate_field_types(props: dict[str, Any], args: dict[str, Any]) -> str | None:
    for key, value in args.items():
        spec = props.get(key)
        if not (isinstance(spec, dict) and isinstance((expected := spec.get("type")), str)):
            continue
        if expected in _PRIMITIVE_TYPES and not isinstance(value, _PRIMITIVE_TYPES[expected]):
            return f"field '{key}' must be {expected}, got {type(value).__name__}"
    return None


def _validate_args(schema: dict[str, Any], args: dict[str, Any]) -> str | None:
    """Cheap pre-validation against JSON-Schema required + primitive types."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    return _validate_required(schema, args) or _validate_field_types(
        schema.get("properties") or {}, args
    )


def mcp_result_text(result: Any) -> str:
    """The model-facing text of ``client.mcp.invoke``'s ``MCPToolResult``.

    A success goes through ``MCPToolResult.to_message`` — the T-214
    injection scan and the T-213 boundary wrap ``MCPClient.chat`` applies to
    the same output; a failure names the tool's error. (Until 2026-10-30
    this read ``is_error`` / ``content``, fields of the ``/gw/mcp/call``
    shape the result does not have, so every call errored.)
    """
    if not result.success:
        return f"Tool error: {result.error or 'the MCP tool reported a failure'}"
    return str(result.to_message()["content"])


def _make_handler(
    client: "Client", mcpserver_tool_name: str, schema: dict[str, Any]
) -> Callable[..., str]:
    def _call(**kwargs: Any) -> str:
        err = _validate_args(schema, kwargs)
        if err:
            return f"Error: {err}"
        try:
            return mcp_result_text(client.mcp.invoke(mcpserver_tool_name, **kwargs))
        except Exception as e:  # noqa: BLE001 — model gets a readable error
            return f"Error invoking {mcpserver_tool_name}: {e}"

    return _call


def _registry_key(tools: list[Any]) -> str:
    fp = sorted(_fingerprint(t.name, t.server, t.parameters) for t in tools)
    return hashlib.sha256(",".join(fp).encode()).hexdigest()[:24]


def invalidate_cache(key: str | None = None) -> None:
    """Clear cached first-class registrations (all or by key)."""
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)


def build_first_class_mcp_tools(client: "Client") -> list[dict[str, Any]]:
    """Inspect ``client.mcp.list_tools()`` and register each descriptor as
    ``mcp.<server>.<tool>``. Failure (no gateway / no MCP) -> []."""
    try:
        descriptors = client.mcp.list_tools()
    except Exception as e:  # noqa: BLE001 — no MCP available is fine
        logger.debug("first-class MCP registration skipped: %s", e)
        return _refresh_tool_only(client)
    if not descriptors:
        return _refresh_tool_only(client)
    key = _registry_key(descriptors)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        cached = hit[1]
        return [*cached, *_refresh_tool_only(client, key)]
    # rebuild handers bound to *this* client (handlers close over client)
    rebuilt: list[dict[str, Any]] = []
    for t in descriptors:
        server = t.server or "mcp"
        schema = t.parameters or {"type": "object", "properties": {}}
        description = t.description or f"MCP tool {t.name} on server {server}"
        rebuilt.append(
            {
                "name": f"mcp.{server}.{t.name}",
                "description": f"[first-class MCP] {description}",
                "parameters": schema,
                "execute": _make_handler(client, t.name, schema),
            }
        )
    _cache[key] = (now, rebuilt)
    return [*rebuilt, *_refresh_tool_only(client, key)]


def mcp_tool_capabilities(client: Any) -> dict[str, tuple[str, ...]]:
    """``{"mcp.<server>.<tool>": declared capabilities}`` (ADR-092 D-6).

    Only tools whose descriptor declares capabilities are listed; a team
    plan reads every other MCP tool as a writer (fail closed). No MCP, an
    unreachable gateway or a descriptor without the field gives ``{}``.
    """
    try:
        descriptors = client.mcp.list_tools()
    except Exception as e:  # noqa: BLE001 — no MCP declares nothing
        logger.debug("MCP capability lookup skipped: %s", e)
        return {}
    declared: dict[str, tuple[str, ...]] = {}
    for tool in descriptors or ():
        flags = tuple(getattr(tool, "capabilities", ()) or ())
        if flags:
            declared[f"mcp.{getattr(tool, 'server', '') or 'mcp'}.{tool.name}"] = flags
    return declared


def _refresh_tool_only(client: "Client", key: str | None = None) -> list[dict[str, Any]]:
    """The ``mcp_refresh`` escape tool — always available, always last."""

    def _mcp_refresh() -> str:
        invalidate_cache()
        try:
            tools = client.mcp.list_tools(refresh=True)
            names = ", ".join(t.name for t in tools) or "(none)"
            return (
                f"MCP cache invalidated; client list refreshed: {len(tools)} tool(s): "
                f"{names}. New servers appear in the next companion session."
            )
        except Exception as e:  # noqa: BLE001
            return f"MCP cache invalidated locally; refresh failed: {e}"

    return [
        {
            "name": "mcp_refresh",
            "description": (
                "Force-refresh the MCP tool registry (clears companion + client "
                "caches; newly-added servers appear in the next session)"
            ),
            "parameters": {"type": "object", "properties": {}},
            "execute": _mcp_refresh,
        }
    ]
