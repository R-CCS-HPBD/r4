"""ADR-105 D-1/D-2 (slice S1): the agent-tool allowlist agrees with r4.

``contracts/umrs-http/v1/agent-tools.allowlist.json`` names the UMRS
operations agents may call and classes each one. This pins that every row
names a real native tool and that r4 enforces the class it declares:

- risk ``safe`` ⇒ the tool runs unasked in chat mode (``SAFE_TOOLS``);
  ``elevated``/``privileged`` ⇒ ``RISKY_TOOLS`` (the ADR-104 ladder input);
- taint ⇒ ``TOOL_TAINT_CLASSES``;
- site ⇒ the multi-site schema offers ``site``;
- workflow rows ⇒ ``WORKFLOW_CAPABILITY_RISK``;
- ADR-030 companion-only families are never ``mcp-eligible``.

The UMRS side (operation ids, typing, drift of the generated files) is
``tests/test_umrs_agent_manifest.py`` at the repo root.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from r4_contracts.taint import TOOL_TAINT_CLASSES
from r4_contracts.workflows import WORKFLOW_CAPABILITY_RISK

from r4.companion import permissions
from r4.companion import tools as tools_mod
from r4.companion.tools import get_builtin_tools

ALLOWLIST = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "umrs-http"
    / "v1"
    / "agent-tools.allowlist.json"
)
COMPANION_ONLY_PREFIXES = ("lab_catalog_", "research_loop_", "research_session_")
# ADR-105 D-4: MCP has no ADR-104 ladder, so it could claim a human approved a commit.
COMPANION_ONLY_TOOLS = frozenset({"job_commit"})
ROWS = json.loads(ALLOWLIST.read_text(encoding="utf-8"))["operations"]


@pytest.fixture(scope="module")
def catalog():
    from unittest.mock import MagicMock

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tools_mod, "_one_site_session", lambda client: False)
        return {t["name"]: t for t in get_builtin_tools(client=MagicMock())}


def _ids(row: dict) -> str:
    return row["operation_id"]


def test_rows_are_unique():
    ids = [row["operation_id"] for row in ROWS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("row", ROWS, ids=_ids)
def test_row_names_a_native_tool(row, catalog):
    assert row["tool"] in catalog


@pytest.mark.parametrize("row", ROWS, ids=_ids)
def test_risk_matches_the_permission_class(row):
    tool = row["tool"]
    if row["risk"] == "safe":
        assert tool in permissions.SAFE_TOOLS and tool not in permissions.RISKY_TOOLS
    else:
        assert row["risk"] in {"elevated", "privileged"}
        assert tool in permissions.RISKY_TOOLS | permissions.NEVER_AUTO_APPROVE


@pytest.mark.parametrize("row", ROWS, ids=_ids)
def test_taint_matches_the_contract(row):
    assert row["taint"] == (TOOL_TAINT_CLASSES.get(row["tool"]) or "none")


@pytest.mark.parametrize("row", ROWS, ids=_ids)
def test_site_flag_matches_the_schema(row, catalog):
    props = catalog[row["tool"]]["parameters"].get("properties", {})
    assert ("site" in props) is row["site"]


_CONTRACT_ROWS = [
    r for r in ROWS if r["tool"].removeprefix("workflow_") in WORKFLOW_CAPABILITY_RISK
]


@pytest.mark.parametrize("row", _CONTRACT_ROWS, ids=_ids)
def test_workflow_rows_match_the_workflow_contract(row):
    """Rows for the workflow contract's own operations (plan/commit, ADR-105
    D-4, are not contract operations: their class is pinned above)."""
    operation = row["tool"].removeprefix("workflow_")
    assert WORKFLOW_CAPABILITY_RISK[operation] == row["risk"]


@pytest.mark.parametrize("row", ROWS, ids=_ids)
def test_companion_only_families_stay_off_mcp(row):
    assert row["scope"] in {"mcp-eligible", "companion-only"}
    if row["tool"].startswith(COMPANION_ONLY_PREFIXES) or row["tool"] in COMPANION_ONLY_TOOLS:
        assert row["scope"] == "companion-only"
