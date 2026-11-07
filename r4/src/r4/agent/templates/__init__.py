"""Starter agent workflow templates (Phase 2k, A-1106).

These templates are surfaced via ``r4 agent run --template <name>`` and
``r4 workflow list --scope system`` once registered with UMRS. They are
intentionally minimal — domain-specific tweaks happen by editing the JSON
copies that ``r4 agent run`` writes into the user's workspace.

Each template is a ``WorkflowDefinition``-shaped ``dict`` consisting of:

- ``name``: short snake_case identifier
- ``description``: one-line summary shown in ``r4 workflow list``
- ``parameters``: declared inputs (kind/required/default)
- ``steps``: ordered tool invocations with ``id`` / ``depends_on``

Templates are loaded by :func:`load_template` below.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

_TEMPLATES_DIR = Path(__file__).parent


def list_templates() -> List[str]:
    """Return the list of bundled template names (no ``.json`` suffix)."""
    return sorted(p.stem for p in _TEMPLATES_DIR.glob("*.json"))


def load_template(name: str) -> Dict[str, Any]:
    """Load a bundled template by name."""
    path = _TEMPLATES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Unknown template: {name!r}")
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["list_templates", "load_template"]
