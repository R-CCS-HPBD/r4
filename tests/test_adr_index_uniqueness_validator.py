"""Tests for tools/adr_index_uniqueness_validator.py (D-1, WP-13).

Each rule gets a pin built from a synthetic ADR dir + README in tmp_path.
The live-repo invariants (current tree passes; 010/012 index rows stay; the
027 collision and 038 reservation stay registered) are pinned separately.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

from tools import adr_index_uniqueness_validator as v  # noqa: E402


def _mk_adr(tmp_path: Path, files: list[str], readme_rows: list[str]) -> tuple[Path, Path]:
    adr = tmp_path / "adr"
    adr.mkdir()
    for name in files:
        (adr / name).write_text(f"# {name}\n\n**Status:** Accepted\n", encoding="utf-8")
    rows = "\n".join(readme_rows)
    (adr / "README.md").write_text(
        "# ADRs\n\n## Index\n\n| ADR | Status | Decision |\n|-----|--------|----------|\n"
        + rows
        + "\n",
        encoding="utf-8",
    )
    return adr, adr / "README.md"


def _rules(report: v.Report) -> list[str]:
    return sorted(f.rule for f in report.findings)


# --- happy path ---------------------------------------------------------------


def test_clean_tree_passes(tmp_path):
    adr, readme = _mk_adr(
        tmp_path,
        ["001-base.md", "002-other.md", "002-amendment-2026-01-01-x.md"],
        [
            "| [001](001-base.md) | Accepted | A |",
            "| [002](002-other.md) | Accepted | B — [amendment](002-amendment-2026-01-01-x.md) |",
        ],
    )
    report = v.validate(adr, readme)
    assert report.ok, [asdict_f.message for asdict_f in report.findings]
    assert report.base_files == 2
    assert report.amendment_files == 1


# --- R1 -----------------------------------------------------------------------


def test_unindexed_file_fails_r1(tmp_path):
    adr, readme = _mk_adr(
        tmp_path, ["001-base.md", "002-lonely.md"], ["| [001](001-base.md) | Accepted | A |"]
    )
    report = v.validate(adr, readme)
    assert not report.ok
    assert _rules(report) == ["file-not-indexed"]


def test_amendment_linked_from_parent_row_is_indexed(tmp_path):
    adr, readme = _mk_adr(
        tmp_path,
        ["001-base.md", "001-amendment-2026-06-01-fix.md"],
        ["| [001](001-base.md) | Accepted | A — [fix](001-amendment-2026-06-01-fix.md) |"],
    )
    assert v.validate(adr, readme).ok


# --- R2 -----------------------------------------------------------------------


def test_link_to_missing_file_fails_r2(tmp_path):
    adr, readme = _mk_adr(tmp_path, ["001-base.md"], ["| [001](001-base.md) | Accepted | A |"])
    (adr / "README.md").write_text(
        "# ADRs\n\n| ADR | Status | Decision |\n|-----|--------|----------|\n"
        "| [001](001-base.md) | Accepted | A — see [ghost](009-ghost.md) |\n",
        encoding="utf-8",
    )
    report = v.validate(adr, readme)
    assert _rules(report) == ["row-target-missing"]


# --- R3 -----------------------------------------------------------------------


def test_number_collision_fails_r3(tmp_path):
    adr, readme = _mk_adr(
        tmp_path,
        ["001-alpha.md", "001-beta.md"],
        ["| [001](001-alpha.md) | Accepted | A |", "| [001](001-beta.md) | Accepted | B |"],
    )
    report = v.validate(adr, readme)
    assert _rules(report) == ["number-collision"]


def test_registered_annex_set_passes_r3(tmp_path, monkeypatch):
    monkeypatch.setitem(
        v.KNOWN_NUMBER_SHARING,
        "009",
        {"kind": "annex", "members": ["009-a.md", "009-b.md"], "reason": "test"},
    )
    adr, readme = _mk_adr(
        tmp_path,
        ["009-a.md", "009-b.md"],
        ["| [009](009-a.md) | Accepted | A — [annex](009-b.md) |"],
    )
    assert v.validate(adr, readme).ok


def test_registered_members_drift_fails_r3(tmp_path, monkeypatch):
    monkeypatch.setitem(
        v.KNOWN_NUMBER_SHARING,
        "009",
        {"kind": "annex", "members": ["009-a.md", "009-b.md"], "reason": "test"},
    )
    adr, readme = _mk_adr(
        tmp_path,
        ["009-a.md", "009-b.md", "009-c.md"],
        [
            "| [009](009-a.md) | Accepted | A |",
            "| [009](009-b.md) | Accepted | B |",
            "| [009](009-c.md) | Accepted | C |",
        ],
    )
    assert _rules(v.validate(adr, readme)) == ["number-collision"]


# --- R4 -----------------------------------------------------------------------


def test_amendment_without_base_fails_r4(tmp_path):
    adr, readme = _mk_adr(
        tmp_path,
        ["001-base.md", "003-amendment-2026-01-01-x.md"],
        [
            "| [001](001-base.md) | Accepted | A — [ghost amend](003-amendment-2026-01-01-x.md) |",
        ],
    )
    report = v.validate(adr, readme)
    assert _rules(report) == ["amendment-orphan"]


# --- R5 -----------------------------------------------------------------------


def test_same_file_two_rows_fails_r5(tmp_path):
    adr, readme = _mk_adr(
        tmp_path,
        ["001-base.md"],
        [
            "| [001](001-base.md) | Accepted | A |",
            "| [001](001-base.md) | Accepted | duplicate |",
        ],
    )
    report = v.validate(adr, readme)
    assert _rules(report) == ["index-duplicate-row"]


# --- R6 -----------------------------------------------------------------------


def test_reserved_number_allocated_fails_r6(tmp_path, monkeypatch):
    monkeypatch.setitem(v.RESERVED_NUMBERS, "077", "reserved for test renumber")
    adr, readme = _mk_adr(tmp_path, ["077-new.md"], ["| [077](077-new.md) | Accepted | A |"])
    report = v.validate(adr, readme)
    assert _rules(report) == ["reserved-allocated"]


# --- R7 -----------------------------------------------------------------------


def test_blank_status_cell_fails_r7(tmp_path):
    adr, readme = _mk_adr(tmp_path, ["001-base.md"], ["| [001](001-base.md) |  | A |"])
    report = v.validate(adr, readme)
    assert _rules(report) == ["row-missing-status"]


# --- CLI ----------------------------------------------------------------------


def test_cli_exit_codes(tmp_path):
    adr, readme = _mk_adr(tmp_path, ["001-base.md"], ["| [001](001-base.md) | Accepted | A |"])
    ok = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools/adr_index_uniqueness_validator.py"),
            "--adr-dir",
            str(adr),
        ],
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0
    (adr / "002-lonely.md").write_text("# x\n", encoding="utf-8")
    bad = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools/adr_index_uniqueness_validator.py"),
            "--adr-dir",
            str(adr),
        ],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1
    assert "file-not-indexed" in bad.stdout


# --- live-repo invariants -----------------------------------------------------


def test_live_tree_passes():
    report = v.validate(v.DEFAULT_ADR_DIR, v.DEFAULT_README)
    assert report.ok, [f.message for f in report.findings]


def test_known_sharing_registry_matches_live_tree():
    files = v._numbered_files(v.DEFAULT_ADR_DIR)
    base = {n: sorted(f for f in fs if not v.AMENDMENT_RE.match(f)) for n, fs in files.items()}
    for number, entry in v.KNOWN_NUMBER_SHARING.items():
        assert sorted(entry["members"]) == base[number], (
            f"registry members for {number} drifted from disk"
        )
    for number in v.RESERVED_NUMBERS:
        assert number not in files


def test_index_counts_expected_shape():
    report = v.validate(v.DEFAULT_ADR_DIR, v.DEFAULT_README)
    assert report.base_files >= 44
    assert report.amendment_files >= 4
