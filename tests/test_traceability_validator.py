"""Tests for tools/traceability_validator.py."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from tools.traceability_validator import validate  # noqa: E402


def _write(tmp_path: Path, body: str) -> Path:
    companion = tmp_path / "docs/verification"
    companion.mkdir(parents=True, exist_ok=True)
    (companion / "companion.md").write_text("companion\n", encoding="utf-8")
    path = tmp_path / "traceability.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _rows(rows: str) -> str:
    return f"principles:\n  P1:\n    claims:\n      - id: P1-C1\n{rows}\n"


def _pad_grouped_principles(body: str) -> str:
    """Append P2..P12 stubs with empty claims so structure checks pass."""
    stubs = "".join(f"  P{i}:\n    claims: []\n" for i in range(2, 13))
    # P2..P12 empty claims would fail structure checks; give each one row.
    stubs = ""
    for i in range(2, 13):
        stubs += (
            f"  P{i}:\n    claims:\n      - id: P{i}-C1\n"
            "        text: t\n"
            "        rows:\n"
            "          - capability: c\n"
            "            status: Missing\n"
            "            code: ['none']\n"
            "            tests: ['none']\n"
            "            adrs: ['none']\n"
            "            docs: ['none']\n"
        )
    return body + stubs


def _with_gaps(body: str, gap_ids: list[str] | None = None, report: bool = True) -> str:
    """Append minimal valid gaps + packages (+ report_file) sections."""
    ids = gap_ids or ["G-P9-9"]
    entries = "".join(
        f"  - id: {gid}\n    severity: S2\n    status: Partial\n    package: WP-01\n"
        f"    claim: c\n    evidence: e\n    remedy: r\n"
        for gid in ids
    )
    joined = ", ".join(ids)
    packages = (
        "packages:\n  - id: WP-01\n    title: t\n    principle: P1\n"
        f"    gaps_closed: [{joined}]\n    risk: Low\n    adr: 'No'\n    notes: ''\n"
    )
    prefix = "report_file: companion.md\n" if report else ""
    return prefix + body + "gaps:\n" + entries + packages


def test_real_registry_passes() -> None:
    """The committed registry validates cleanly against the repo root."""
    problems, _warnings = validate(ROOT / "docs/verification/traceability.yaml", ROOT)
    assert problems == []


def test_empty_principle_claims_fails(tmp_path: Path) -> None:
    body = _pad_grouped_principles("principles:\n  P1:\n    claims: []\n")
    problems, _ = validate(_write(tmp_path, body), tmp_path)
    assert any("P1" in p and "no claims" in p for p in problems)


def test_missing_principle_fails(tmp_path: Path) -> None:
    body = "principles:\n  P1:\n    claims: []\n"
    problems, _ = validate(_write(tmp_path, body), tmp_path)
    assert any("P12" in p for p in problems)


def test_claim_without_rows_fails(tmp_path: Path) -> None:
    body = _pad_grouped_principles("principles:\n  P1:\n    claims:\n      - id: P1-C1\n")
    problems, _ = validate(_write(tmp_path, body), tmp_path)
    assert any("P1-C1" in p for p in problems)


def test_nonexistent_code_path_fails(tmp_path: Path) -> None:
    rows = (
        "        rows:\n          - capability: c\n            status: Partial\n"
        "            code: ['tools/does_not_exist.py']\n            tests: ['none']\n"
        "            adrs: ['none']\n            docs: ['none']\n"
    )
    problems, _ = validate(_write(tmp_path, _pad_grouped_principles(_rows(rows))), tmp_path)
    assert any("does_not_exist.py" in p for p in problems)


def test_line_suffix_is_stripped(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/exists.py").write_text("x = 1\n", encoding="utf-8")
    rows = (
        "        rows:\n          - capability: c\n            status: Partial\n"
        "            code: ['tools/exists.py:12']\n            tests: ['none']\n"
        "            adrs: ['none']\n            docs: ['none']\n            gap: 'G-P9-9'\n"
    )
    doc = _with_gaps(_pad_grouped_principles(_rows(rows)))
    problems, _ = validate(_write(tmp_path, doc), tmp_path)
    assert problems == []


def test_invalid_status_fails(tmp_path: Path) -> None:
    rows = (
        "        rows:\n          - capability: c\n            status: Bogus\n"
        "            code: ['none']\n            tests: ['none']\n"
        "            adrs: ['none']\n            docs: ['none']\n"
    )
    problems, _ = validate(_write(tmp_path, _pad_grouped_principles(_rows(rows))), tmp_path)
    assert any("invalid status" in p for p in problems)


def test_implemented_without_tests_warns_not_fails(tmp_path: Path) -> None:
    rows = (
        "        rows:\n          - capability: c\n            status: Implemented\n"
        "            code: ['none']\n            tests: ['none']\n"
        "            adrs: ['none']\n            docs: ['none']\n"
        "          - capability: c2\n            status: Partial\n"
        "            code: ['none']\n            tests: ['none']\n"
        "            adrs: ['none']\n            docs: ['none']\n            gap: 'G-P9-9'\n"
    )
    doc = _with_gaps(_pad_grouped_principles(_rows(rows)))
    problems, warnings = validate(_write(tmp_path, doc), tmp_path)
    assert problems == []
    assert any("no test" in w for w in warnings)


def test_missing_adr_fails(tmp_path: Path) -> None:
    (tmp_path / "docs/adr").mkdir(parents=True)
    rows = (
        "        rows:\n          - capability: c\n            status: Partial\n"
        "            code: ['none']\n            tests: ['none']\n"
        "            adrs: ['042']\n            docs: ['none']\n"
    )
    problems, _ = validate(_write(tmp_path, _pad_grouped_principles(_rows(rows))), tmp_path)
    assert any("ADR" in p and "042" in p for p in problems)


def _gap_row(gap_ref: str, status: str = "Partial") -> str:
    return (
        "        rows:\n          - capability: c\n"
        f"            status: {status}\n"
        "            code: ['none']\n            tests: ['none']\n"
        "            adrs: ['none']\n            docs: ['none']\n"
        f"            {gap_ref}\n"
    )


def test_gap_reference_without_entry_fails(tmp_path: Path) -> None:
    doc = _with_gaps(_pad_grouped_principles(_rows(_gap_row("gap: 'G-XX-42'"))), ["G-P9-9"])
    problems, _ = validate(_write(tmp_path, doc), tmp_path)
    assert any("G-XX-42" in p and "no gaps-section entry" in p for p in problems)


def test_unreferenced_register_gap_fails(tmp_path: Path) -> None:
    doc = _with_gaps(
        _pad_grouped_principles(_rows(_gap_row("gap: 'G-A1-1'"))), ["G-A1-1", "G-A1-2"]
    )
    problems, _ = validate(_write(tmp_path, doc), tmp_path)
    assert any("G-A1-2" in p and "not referenced" in p for p in problems)


def test_implemented_row_with_gap_fails(tmp_path: Path) -> None:
    doc = _with_gaps(_pad_grouped_principles(_rows(_gap_row("gap: 'G-P9-9'", "Implemented"))))
    problems, _ = validate(_write(tmp_path, doc), tmp_path)
    assert any("Implemented" in p and "G-P9-9" in p for p in problems)


def test_status_mismatch_row_gap_fails(tmp_path: Path) -> None:
    doc = _with_gaps(_pad_grouped_principles(_rows(_gap_row("gap: 'G-P9-9'", "Missing"))))
    problems, _ = validate(_write(tmp_path, doc), tmp_path)
    assert any("does not match" in p for p in problems)


def test_gaps_section_required(tmp_path: Path) -> None:
    problems, _ = validate(_write(tmp_path, _pad_grouped_principles(_rows(_gap_row("")))), tmp_path)
    assert any("'gaps' section" in p for p in problems)


def test_gap_package_not_in_packages_fails(tmp_path: Path) -> None:
    doc = _with_gaps(_pad_grouped_principles(_rows(_gap_row("gap: 'G-P9-9'"))))
    doc = doc.replace("package: WP-01\n", "package: WP-99\n", 1)
    problems, _ = validate(_write(tmp_path, doc), tmp_path)
    assert any("WP-99" in p and "not in packages" in p for p in problems)


def test_gap_package_not_listed_in_gaps_closed_fails(tmp_path: Path) -> None:
    doc = _with_gaps(_pad_grouped_principles(_rows(_gap_row("gap: 'G-P9-9'"))))
    doc = doc.replace("gaps_closed: [G-P9-9]", "gaps_closed: []")
    problems, _ = validate(_write(tmp_path, doc), tmp_path)
    assert any("does not list it" in p for p in problems)


def test_package_lists_foreign_gap_fails(tmp_path: Path) -> None:
    doc = _with_gaps(_pad_grouped_principles(_rows(_gap_row("gap: 'G-P9-9'"))))
    doc = doc.replace(
        "gaps_closed: [G-P9-9]",
        "gaps_closed: []\n  - id: WP-99\n    title: t\n    principle: P1\n"
        "    gaps_closed: [G-P9-9]\n    risk: Low\n    adr: 'No'\n    notes: ''",
    )
    problems, _ = validate(_write(tmp_path, doc), tmp_path)
    assert any("WP-99" in p and "whose package field" in p for p in problems)


def test_report_file_missing_fails(tmp_path: Path) -> None:
    doc = _with_gaps(_pad_grouped_principles(_rows(_gap_row("gap: 'G-P9-9'"))), report=False)
    doc = "report_file: nope.md\n" + doc
    problems, _ = validate(_write(tmp_path, doc), tmp_path)
    assert any("report_file" in p and "nope.md" in p for p in problems)
