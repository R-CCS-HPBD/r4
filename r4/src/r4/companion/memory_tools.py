"""The agent's saved-memory tools (ADR-111 D-3): read, write, forget.

Attached by the lead :class:`CompanionSession` only, never through
``get_builtin_tools``: team roles, plan agents, recipes and sub-agents read
the memory the lead's turn carries and never write it.

``memory_read`` is read-only (``SAFE_TOOLS``). ``memory_write`` and
``memory_forget`` ask in chat and plan modes like any change, and in every
mode once the session has seen untrusted content (``_rung_memory_taint``).
Replacing or deleting a fact needs the sha ``memory_read`` returned, so two
sessions never overwrite each other blindly.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .memory_store import MEMORY_TYPES, SCOPES, MemoryFact, MemoryStoreError

READ_DESCRIPTION = (
    "Saved memory: with no name, list facts (name, scope, type, description, sha); "
    "with a name, the fact's body and sha."
)
WRITE_DESCRIPTION = (
    "Save one fact for later sessions: user = about this researcher (all projects), "
    "project = this project's working notes. Save preferences, corrections and "
    "decisions that are not in the code, the docs or the project's knowledge; "
    "never secrets, never what you can re-derive. Domain facts belong in the "
    "project's knowledge. To change a fact, pass the sha memory_read gave you."
)
FORGET_DESCRIPTION = "Delete one saved fact; pass the sha memory_read gave you."

_NAME = {"type": "string", "description": "Fact name: a-z 0-9 . _ - (max 64)"}
_SCOPE = {"type": "string", "enum": list(SCOPES), "description": "Default: project"}
_SHA = {"type": "string", "description": "The sha memory_read returned"}


def _error(exc: Exception) -> str:
    return f"Error: {exc}"


def _listing(store: Any) -> str:
    rows = [{k: v for k, v in fact.as_dict().items() if k != "body"} for fact in store.facts()]
    return json.dumps({"project_scope": store.project, "facts": rows}, indent=2)


def _store_of(session: Any) -> Callable[[], Any]:
    """The session's store, read per call; refused after ``/memory off``."""

    def store() -> Any:
        enabled = getattr(session, "memory_enabled", None)
        if callable(enabled) and not enabled():
            raise MemoryStoreError("memory is off for this session (the user ran /memory off)")
        return session.context.memory_store()

    return store


def _read_tool(store: Callable[[], Any]) -> Callable[..., str]:
    def memory_read(name: str = "", scope: str = "") -> str:
        try:
            if not name:
                return _listing(store())
            current = store()
            fact = current.get(name, scope) if scope else current.find(name)
        except (MemoryStoreError, OSError) as exc:
            return _error(exc)
        if fact is None:
            return f"Error: no saved fact named {name!r}"
        return json.dumps(fact.as_dict(), indent=2)

    return memory_read


def _write_tool(store: Callable[[], Any], session_id: str) -> Callable[..., str]:
    def memory_write(
        name: str,
        description: str,
        body: str = "",
        type: str = "project",  # noqa: A002 - the tool's parameter name
        scope: str = "project",
        expires: str = "",
        expected_sha: str = "",
    ) -> str:
        fact = MemoryFact(
            name=name,
            type=type,
            description=description,
            body=body,
            scope=scope,
            source="agent",
            source_session=session_id,
            expires=expires,
        )
        try:
            saved = store().write(fact, expected_sha=expected_sha or None)
        except (MemoryStoreError, OSError) as exc:
            return _error(exc)
        return json.dumps({"saved": f"{saved.scope}/{saved.name}", "sha": saved.sha})

    return memory_write


def _forget_tool(store: Callable[[], Any]) -> Callable[..., str]:
    def memory_forget(name: str, expected_sha: str, scope: str = "project") -> str:
        if not expected_sha:
            return "Error: pass the sha memory_read gave you (expected_sha)"
        try:
            gone = store().forget(name, scope, expected_sha=expected_sha)
        except (MemoryStoreError, OSError) as exc:
            return _error(exc)
        return json.dumps({"forgotten": f"{scope}/{name}" if gone else None})

    return memory_forget


def build_memory_tools(session: Any) -> list[dict[str, Any]]:
    """The three memory tools bound to ``session`` (its context and id)."""
    store = _store_of(session)
    memory_read = _read_tool(store)
    memory_write = _write_tool(store, str(getattr(session, "session_id", "") or ""))
    memory_forget = _forget_tool(store)
    return [
        {
            "name": "memory_read",
            "description": READ_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {"name": _NAME, "scope": _SCOPE},
            },
            "execute": memory_read,
            "concurrency_safe": True,
        },
        {
            "name": "memory_write",
            "description": WRITE_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _NAME,
                    "description": {"type": "string", "description": "One line, max 200"},
                    "body": {"type": "string", "description": "The fact (max 4000)"},
                    "type": {"type": "string", "enum": list(MEMORY_TYPES)},
                    "scope": _SCOPE,
                    "expires": {"type": "string", "description": "YYYY-MM-DD, optional"},
                    "expected_sha": _SHA,
                },
                "required": ["name", "description"],
            },
            "execute": memory_write,
            "concurrency_safe": False,
        },
        {
            "name": "memory_forget",
            "description": FORGET_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {"name": _NAME, "scope": _SCOPE, "expected_sha": _SHA},
                "required": ["name", "expected_sha"],
            },
            "execute": memory_forget,
            "concurrency_safe": False,
        },
    ]
