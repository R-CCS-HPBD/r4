"""v3 slash-command ownership, read from the catalog manifest (plan H-5).

The one declaration of which slash rows the v3 TUI owns is
``packages/r4-tui-v3/src/commands/catalog.manifest.json``; v3 reads it
directly. The r4 wheel ships only ``src/r4``, so the bridge reads the
byte-identical copy next to this module (``slash_catalog.manifest.json``),
which ``scripts/regen-slash-fixture.py`` writes and its ``--check`` keeps in
step (``r4/tests/test_tui_audit_bridge.py`` runs it). Never edit the copy.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).with_name("slash_catalog.manifest.json")


@lru_cache(maxsize=1)
def manifest_rows() -> tuple[dict[str, Any], ...]:
    """The manifest's rows (one per v3-owned slash name)."""
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return tuple(data["rows"])


def v3_slash_names() -> frozenset[str]:
    """Every name and alias a v3 row answers to.

    ``interceptAliases`` are left out: v3 serves only their bare form and
    they stay companion aliases (``/endpoints up`` goes to the registry).
    """
    names: set[str] = set()
    for row in manifest_rows():
        names.add(row["name"])
        names.update(row.get("aliases", []))
    return frozenset(names)


def v3_local_rows() -> frozenset[str]:
    """Catalog rows v3 runs itself in every form (``owner: local``).

    A dispatch-only name (``slashRow: false``) has no v3 catalog row, so a
    registry alias on its companion row resolves to that row and forwards.
    """
    return frozenset(
        row["name"]
        for row in manifest_rows()
        if row["owner"] == "local" and row.get("slashRow", True)
    )


def surface_local_commands() -> frozenset[str]:
    """Rows ``list_slash_commands`` leaves out (``companionRow: omit``)."""
    return frozenset(row["name"] for row in manifest_rows() if row.get("companionRow") == "omit")
