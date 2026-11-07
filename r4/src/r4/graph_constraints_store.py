"""Persisted knowledge-graph selection constraints (multi-graph UX).

One shared source of truth for *which knowledge graphs* the user prefers —
consumed by the TUI graph picker, ``r4 knowledge query`` defaults, and the
Companion ``knowledge_query`` fallback chain. Studio/ACP session constraints
(in-memory on the client) still win over this file; explicit per-call
arguments win over everything.

File: ``~/.config/r4/knowledge_graph_constraints.json``

Shape::

    {"include": ["experiment-history"], "exclude": ["institutional"],
     "discovery": true}
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def default_constraints_path() -> Path:
    """Constraints file location ($R4_STATE_DIR override for tests)."""
    state_dir = os.environ.get("R4_STATE_DIR")
    root = Path(state_dir) if state_dir else Path.home() / ".config" / "r4"
    return root / "knowledge_graph_constraints.json"


def empty_constraints() -> dict[str, Any]:
    """Neutral constraints: nothing pinned, discovery on."""
    return {"include": [], "exclude": [], "discovery": True}


def _sanitize_constraints(raw: Any) -> dict[str, Any]:
    """Coerce loaded JSON into the constraints shape (defaults on bad input)."""
    if not isinstance(raw, dict):
        return empty_constraints()
    return {
        "include": [str(t) for t in (raw.get("include") or []) if str(t).strip()],
        "exclude": [str(t) for t in (raw.get("exclude") or []) if str(t).strip()],
        "discovery": bool(raw.get("discovery", True)),
    }


def load_constraints(path: Optional[Path] = None) -> dict[str, Any]:
    """Load constraints; neutral defaults when file is absent/invalid."""
    path = path or default_constraints_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_constraints()
    return _sanitize_constraints(raw)


def save_constraints(
    constraints: dict[str, Any],
    path: Optional[Path] = None,
) -> Path:
    """Write constraints atomically. Never throws (best-effort persistence)."""
    path = path or default_constraints_path()
    payload = {
        "include": [str(t) for t in (constraints.get("include") or []) if str(t).strip()],
        "exclude": [str(t) for t in (constraints.get("exclude") or []) if str(t).strip()],
        "discovery": bool(constraints.get("discovery", True)),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("failed to persist graph constraints: %s", exc)
    return path


def toggle_graph(
    name: str,
    constraints: dict[str, Any],
) -> dict[str, Any]:
    """Cycle a graph through include → exclude → neutral. Returns new state."""
    name = name.strip()
    include = [t for t in constraints.get("include", []) if t != name]
    exclude = [t for t in constraints.get("exclude", []) if t != name]
    if name in constraints.get("include", []):
        exclude.append(name)
    elif name in constraints.get("exclude", []):
        pass  # back to neutral
    else:
        include.append(name)
    return {
        "include": include,
        "exclude": exclude,
        "discovery": bool(constraints.get("discovery", True)),
    }


def graph_state(name: str, constraints: dict[str, Any]) -> str:
    """Current state of one graph: include | exclude | neutral."""
    if name in (constraints.get("include") or []):
        return "include"
    if name in (constraints.get("exclude") or []):
        return "exclude"
    return "neutral"
