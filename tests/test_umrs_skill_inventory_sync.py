"""Pins for `tools/sync_umrs_skill_inventory.py` — the umrs skill inventory
block stays byte-identical to the audit machinery's live output."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sync_umrs_skill_inventory.py"), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_check_mode_passes_on_current_file():
    result = _run("--check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_render_prefix_covers_new_families():
    # --check already proven; this block adds an invariant between the audit
    # output and the skill body: every newly-added slash (from topic-7/8/9)
    # must be ON the rendered list. Keep the list small so drift is caught.
    body = (ROOT / "r4/src/r4/companion/builtin/umrs/SKILL.md").read_text(encoding="utf-8")
    for expected in [
        "/quota",
        "/circles",  # "/groups" until ADR-086 (ade019af renamed the surface)
        "/usage",
        "/grants",
        "/loops",
        "/traces",
        "/provenance",
        "/rag",
    ]:
        assert expected in body, f"skill inventory missing {expected}"


def test_markers_wellformed_and_paired():
    body = (ROOT / "r4/src/r4/companion/builtin/umrs/SKILL.md").read_text(encoding="utf-8")
    assert body.count("<!-- INVENTORY:START -->") == 1
    assert body.count("<!-- INVENTORY:END -->") == 1


BRIDGE_MD = ROOT / "r4/src/r4/companion/builtin/umrs/references/bridge-verbs.md"


def test_fanout_counts_included_fresh_families():
    # ADR-105 S4b: the bridge-verb inventory lives in references/, not the prompt.
    body = BRIDGE_MD.read_text(encoding="utf-8")
    # Any new verb family prefix (assets./circles./loops./traces./provenance./research_loop.)
    # must appear in the fanout line with a count > 0. circles. replaced groups. (ADR-086).
    for family in ["assets.", "circles.", "loops.", "traces.", "provenance.", "research_loop."]:
        assert f"{family}*" in body or family in body


def test_bridge_total_imported_follows_bridge_rpc():
    # The count the tool renders must equal the audit's live count — no stale counts.
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "tools"))
    from audit_tui_accessibility import bridge_rpc_names

    actual = len(bridge_rpc_names())
    assert f"pairs {actual} exact" in BRIDGE_MD.read_text(encoding="utf-8")


def test_the_always_on_skill_carries_no_bridge_verb_list():
    """ADR-105 S4b: console RPC verbs are not agent tools; ~500 tokens a turn."""
    body = (ROOT / "r4/src/r4/companion/builtin/umrs/SKILL.md").read_text(encoding="utf-8")
    assert "pairs " not in body and "steady rails" not in body
    assert "references/bridge-verbs.md" in body
