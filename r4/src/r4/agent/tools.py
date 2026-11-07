"""Built-in tools for the R4 agent.

These tools are always available to the agent regardless of MCP
server configuration. Inspired by built-in agent tool sets
and goose's final_output_tool.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Tool interface ──────────────────────────────────────────────────────────


@dataclass
class ToolDefinition:
    """Declarative tool definition (OpenAI function-calling schema)."""

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    is_final: bool = False  # True means this tool's output ends the turn


@dataclass
class ToolResult:
    """Result of a tool invocation."""

    output: Any = None
    error: Optional[str] = None
    is_final: bool = False  # True if this is the final turn output


ToolHandler = Callable[[Dict[str, Any]], ToolResult]
"""Signature: (args) → ToolResult"""


class BuiltinToolRegistry:
    """Registry of built-in agent tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._handlers: Dict[str, ToolHandler] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        self._tools[definition.name] = definition
        self._handlers[definition.name] = handler

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def handle(self, name: str, args: Dict[str, Any]) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(error=f"Unknown tool: {name}")
        try:
            return handler(args)
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return ToolResult(error=str(exc))

    def definitions(self) -> List[ToolDefinition]:
        return list(self._tools.values())

    def as_openai_tools(self) -> List[Dict[str, Any]]:
        """Return definitions in OpenAI function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools


# ── Default built-in tools ──────────────────────────────────────────────────

_registry = BuiltinToolRegistry()


def get_builtin_registry() -> BuiltinToolRegistry:
    """Return the global built-in tool registry."""
    return _registry


# 1. AskUserQuestion ─────────────────────────────────────────────────────────

_ASK_USER = ToolDefinition(
    name="ask_user",
    description=(
        "Ask the user a clarifying question when you need more information "
        "to proceed. Provide a clear, specific question."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user.",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of suggested answers.",
            },
        },
        "required": ["question"],
    },
)


# The actual handler is set externally (TUI/CLI injects the real I/O callback)
_ask_user_callback: Optional[Callable[[str, List[str]], str]] = None


def set_ask_user_callback(fn: Callable[[str, List[str]], str]) -> None:
    """Set the real ask-user callback (injected by the UI layer)."""
    global _ask_user_callback
    _ask_user_callback = fn


ASK_USER_NOT_WIRED = "[user input not available — ask_user callback not wired]"


def _unwired_ask_user_answer() -> str:
    """No UI callback: the user's ``ask_user_mode=auto`` (``R4_ASK_USER_MODE``)
    still answers, with the text the bridge and the venue worker use (TB 4.0
    harness-efficiency plan, item H12); otherwise the question is unanswerable."""
    try:
        from r4.companion.agent_settings import ask_user_auto_answer, configured_ask_user_mode
    except ImportError:  # pragma: no cover - the companion package ships with r4
        return ASK_USER_NOT_WIRED
    if configured_ask_user_mode() == "auto":
        return ask_user_auto_answer()
    return ASK_USER_NOT_WIRED


def _handle_ask_user(args: Dict[str, Any]) -> ToolResult:
    question = args.get("question", "")
    options = args.get("options", [])
    if _ask_user_callback is not None:
        answer = _ask_user_callback(question, options)
        return ToolResult(output=answer)
    return ToolResult(output=_unwired_ask_user_answer())


_registry.register(_ASK_USER, _handle_ask_user)


# 2. TodoWrite ───────────────────────────────────────────────────────────────

_TODO_WRITE = ToolDefinition(
    name="todo_write",
    description=(
        "Create or update a structured todo list to track multi-step work. "
        "Provide the full list each time."
    ),
    parameters={
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "title": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["not-started", "in-progress", "completed"],
                        },
                    },
                    "required": ["id", "title", "status"],
                },
            }
        },
        "required": ["items"],
    },
)

_current_todos: List[Dict[str, Any]] = []

# Optional session-plan mirror (injected by the UI layer; e.g. the bridge
# server mirrors todos into the session TaskGraph so a structured plan
# graph is available to clients — discoverable via get_session_plan).
_plan_mirror: Optional[Callable[[List[Dict[str, Any]]], None]] = None


def set_plan_mirror(fn: Optional[Callable[[List[Dict[str, Any]]], None]]) -> None:
    """Set the session-plan mirror callback (UI layer injects it)."""
    global _plan_mirror
    _plan_mirror = fn


def _handle_todo_write(args: Dict[str, Any]) -> ToolResult:
    global _current_todos
    _current_todos = args.get("items", [])
    if _plan_mirror is not None:
        try:
            _plan_mirror(list(_current_todos))
        except Exception:
            # The mirror is presentation plumbing — it must never fail
            # the agent's tool call.
            pass

    def _marker(status: str) -> str:
        if status == "completed":
            return "✓"
        if status == "in-progress":
            return "→"
        return " "

    summary = ", ".join(f"[{_marker(t['status'])}] {t['title']}" for t in _current_todos)
    return ToolResult(output=f"Todo list updated ({len(_current_todos)} items):\n{summary}")


def get_todos() -> List[Dict[str, Any]]:
    """Return the current todo list."""
    return list(_current_todos)


_registry.register(_TODO_WRITE, _handle_todo_write)


# 3. ToolSearch ──────────────────────────────────────────────────────────────

_TOOL_SEARCH = ToolDefinition(
    name="tool_search",
    description=(
        "Search for available tools by description. Use this when you need "
        "a capability but aren't sure which tool provides it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language description of the needed capability.",
            }
        },
        "required": ["query"],
    },
)

# The real implementation is injected by the MCP layer
_tool_search_fn: Optional[Callable[[str], List[Dict[str, Any]]]] = None


def set_tool_search_callback(fn: Callable[[str], List[Dict[str, Any]]]) -> None:
    """Set the real tool-search callback (injected by the MCP layer)."""
    global _tool_search_fn
    _tool_search_fn = fn


def _handle_tool_search(args: Dict[str, Any]) -> ToolResult:
    query = args.get("query", "")
    if _tool_search_fn is not None:
        results = _tool_search_fn(query)
        return ToolResult(output=json.dumps(results, indent=2))
    return ToolResult(output="[tool search not available — no MCP servers connected]")


_registry.register(_TOOL_SEARCH, _handle_tool_search)


# 4. WebFetch ────────────────────────────────────────────────────────────────

_WEB_FETCH = ToolDefinition(
    name="web_fetch",
    description=(
        "Fetch the main content from a web URL. Returns the page text "
        "(stripped of HTML). Subject to EgressGuard policy."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default: 50000).",
            },
        },
        "required": ["url"],
    },
)


def _handle_web_fetch(args: Dict[str, Any]) -> ToolResult:
    import urllib.error
    import urllib.request

    url = args.get("url", "")
    max_chars = args.get("max_chars", 50000)

    if not url.startswith(("http://", "https://")):
        return ToolResult(error="URL must start with http:// or https://")

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "r4-agent/1.0"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        # Rudimentary HTML stripping
        import re

        text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... [truncated at {max_chars} chars]"
        return ToolResult(output=text)
    except Exception as exc:
        return ToolResult(error=f"Fetch failed: {exc}")


_registry.register(_WEB_FETCH, _handle_web_fetch)


# 5. WebSearch ───────────────────────────────────────────────────────────────

_WEB_SEARCH = ToolDefinition(
    name="web_search",
    description=(
        "Search the web for information. Use this when you need "
        "up-to-date facts, recent events, or information beyond your "
        "training data. Returns search result snippets."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query.",
            },
            "num_results": {
                "type": "integer",
                "description": "Number of results to return (default: 5, max: 10).",
            },
        },
        "required": ["query"],
    },
)

# An injected backend replaces the built-in SearXNG client
# (``r4.agent.web_search``, 2026-10-30: before it, no surface injected one
# and the tool was dead).
_web_search_fn: Optional[Callable[[str, int], List[Dict[str, str]]]] = None


def set_web_search_callback(fn: Callable[[str, int], List[Dict[str, str]]]) -> None:
    """Replace the built-in web-search backend."""
    global _web_search_fn
    _web_search_fn = fn


def _handle_web_search(args: Dict[str, Any]) -> ToolResult:
    query = args.get("query", "")
    num_results = min(args.get("num_results", 5), 10)
    search = _web_search_fn
    if search is None:
        from r4.agent.web_search import search as builtin_search

        search = builtin_search
    results = search(query, num_results)
    formatted = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        snippet = r.get("snippet", "")
        url = r.get("url", "")
        formatted.append(f"{i}. {title}\n   {snippet}\n   {url}")
    return ToolResult(output="\n\n".join(formatted) or f"No results for: {query}")


_registry.register(_WEB_SEARCH, _handle_web_search)


# 6. FinalOutput ─────────────────────────────────────────────────────────────

_FINAL_OUTPUT = ToolDefinition(
    name="final_output",
    description=(
        "Signal that the task is complete and provide the final answer or "
        "summary. Use this when you have finished all work."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Brief summary of what was accomplished.",
            },
            "result": {
                "type": "string",
                "description": "The final output or answer for the user.",
            },
        },
        "required": ["summary"],
    },
    is_final=True,
)


def _handle_final_output(args: Dict[str, Any]) -> ToolResult:
    summary = args.get("summary", "")
    result = args.get("result", "")
    output = summary
    if result:
        output += f"\n\n{result}"
    return ToolResult(output=output, is_final=True)


_registry.register(_FINAL_OUTPUT, _handle_final_output)
