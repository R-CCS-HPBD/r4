"""Tests for tools/gen_gap_register.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

from tools.gen_gap_register import _table, main, render  # noqa: E402


@pytest.fixture()
def doc() -> dict:
    return yaml.safe_load((ROOT / "docs/verification/traceability.yaml").read_text())


def test_repo_register_is_up_to_date() -> None:
    """Drift check: the committed register matches the committed yaml."""
    assert main(["--check"]) == 0


def test_render_contains_all_sections(doc: dict) -> None:
    text = render(doc)
    for marker in ("GENERATED FILE", "## S1", "## S2", "## S3", "## S4", "Work-package plan"):
        assert marker in text


def test_every_gap_rendered(doc: dict) -> None:
    text = render(doc)
    for gap in doc["gaps"]:
        assert gap["id"] in text


def test_companion_report_link_matches_report_file(doc: dict) -> None:
    """The companion link must point at an existing file, not a derived name."""
    text = render(doc)
    report = doc["report_file"]
    assert (ROOT / "docs/verification" / report).exists()
    assert f"]({report})" in text


def test_every_package_rendered_in_sequence(doc: dict) -> None:
    text = render(doc)
    section = text.split("Work-package plan", 1)[1]
    sequence = doc["ratifications"]["wp_sequence"]
    # Match the id as a table cell so cross-references inside notes
    # (e.g. "deferred to WP-06") do not count as the row itself.
    positions = [section.index(f"| {pkg} |") for pkg in sequence]
    assert positions == sorted(positions)


def test_table_separator_has_one_cell_per_column() -> None:
    out = _table(("A", "B", "C"), [["1", "2", "3"]])
    separator = out.splitlines()[1]
    assert separator.count("|") - 1 == 3
