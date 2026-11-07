"""ADR index + numbering uniqueness validator (D-1).

Invariants enforced:

  R1 file-not-indexed     every numbered base ADR file is linked from the
                          index (own row or an amendment link anywhere in
                          README.md — amendments may ride the parent row)
  R2 row-target-missing   every ``(NNN-slug.md)`` link in README.md targets
                          an existing file
  R3 number-collision     >1 base file sharing an ADR number, unless the
                          exact member set is registered in
                          ``KNOWN_NUMBER_SHARING`` (with a reason)
  R4 amendment-orphan     a ``NNN-amendment-*.md`` file with no base ADR
                          file for its number
  R5 index-duplicate-row  the same file linked from more than one index row
  R6 reserved-allocated   a file allocated a number in ``RESERVED_NUMBERS``
  R7 row-missing-status   an index row with an empty Status cell

Enforcement class: HARD-FAIL (WP-13, ratified package row: "Includes
hard-fail ADR-index uniqueness validator (D-1)").

Usage:
    python tools/adr_index_uniqueness_validator.py          # exits 0/1
    python tools/adr_index_uniqueness_validator.py --json   # machine-readable
    python tools/adr_index_uniqueness_validator.py --adr-dir DIR --readme FILE
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADR_DIR = REPO_ROOT / "docs" / "adr"
DEFAULT_README = DEFAULT_ADR_DIR / "README.md"

FILE_NUMBER_RE = re.compile(r"^(\d{3})-(.+)\.md$")
AMENDMENT_RE = re.compile(r"^\d{3}-amendment-")
ROW_LINK_RE = re.compile(r"^\|\s*\[\d{3}\]\(([^)]+)\)", re.MULTILINE)
ANY_LINK_RE = re.compile(r"\((\d{3}-[^)]+\.md)\)")

# Explicitly registered multi-file numbers. Member lists must match exactly
# what exists on disk — a change to the members requires updating the
# registry (and the reason), which is the mechanical review point.
KNOWN_NUMBER_SHARING: dict[str, dict[str, object]] = {
    "012": {
        "kind": "annex",
        "members": [
            "012-assistant-api-v1-contract.md",
            "012-assistant-service-and-conversational-surfaces.md",
        ],
        "reason": "assistant-api v1 contract annex travels with ADR-012",
    },
    "027": {
        "kind": "renumber-pending",
        "members": [
            "027-unified-knowledge-spine.md",
            "027-workflow-upload-authorization.md",
        ],
        "reason": "acknowledged collision; 027-workflow-upload-authorization "
        "renumbers to 038 when the renumber lands (see ADR-039 row note)",
    },
}

# Numbers that must stay unallocated until a named event assigns them.
RESERVED_NUMBERS: dict[str, str] = {
    "038": "reserved for the ADR-027 workflow-upload-authorization renumber",
}


@dataclass
class Finding:
    rule: str
    severity: str  # "fail" only — hard-fail validator
    message: str


@dataclass
class Report:
    adr_dir: str
    findings: list[Finding] = field(default_factory=list)
    base_files: int = 0
    amendment_files: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings


def _numbered_files(adr_dir: Path) -> dict[str, list[str]]:
    """Map ADR number → sorted member file names (base + amendments)."""
    out: dict[str, list[str]] = {}
    for path in sorted(adr_dir.glob("*.md")):
        m = FILE_NUMBER_RE.match(path.name)
        if m:
            out.setdefault(m.group(1), []).append(path.name)
    return out


def _fail(report: Report, rule: str, message: str) -> None:
    report.findings.append(Finding(rule=rule, severity="fail", message=message))


def _check_links(report: Report, adr_dir: Path, any_links: set[str]) -> None:
    """R2 — every README ADR link must resolve to a real file."""
    for link in sorted(any_links):
        if not (adr_dir / link).is_file():
            _fail(
                report, "row-target-missing", f"README links ({link}) but the file does not exist"
            )


def _check_reserved(report: Report, by_number: dict[str, list[str]]) -> None:
    """R6 — reserved numbers stay unallocated."""
    for number, why in RESERVED_NUMBERS.items():
        if number in by_number:
            _fail(
                report,
                "reserved-allocated",
                f"{sorted(by_number[number])} allocated reserved {number} ({why})",
            )


def _check_number(
    report: Report,
    number: str,
    base_files: list[str],
    all_files: list[str],
    any_links: set[str],
) -> None:
    """R1 + R3 + R4 for one ADR number."""
    if not base_files:
        _fail(
            report,
            "amendment-orphan",
            f"{sorted(all_files)} exists but no base ADR file for {number}",
        )
        return
    # R1 — every base file must be linked somewhere in README.
    for name in base_files:
        if name not in any_links:
            _fail(report, "file-not-indexed", f"{name} is not linked from README.md")
    if len(base_files) <= 1:
        return
    # R3 — one base file per number unless registered.
    known = KNOWN_NUMBER_SHARING.get(number)
    if known is None:
        _fail(
            report,
            "number-collision",
            f"{sorted(base_files)} share ADR number {number}; register in "
            "KNOWN_NUMBER_SHARING with a reason, or renumber",
        )
    elif sorted(known["members"]) != sorted(base_files):  # type: ignore[index]
        _fail(
            report,
            "number-collision",
            f"{sorted(base_files)} no longer match the KNOWN_NUMBER_SHARING "
            f"registry for {number} ({known['members']}) — update the registry "
            "with the new member set and reason",
        )


def _check_duplicate_rows(report: Report, row_links: list[str]) -> None:
    """R5 — a file owned by more than one index row."""
    seen: dict[str, int] = {}
    for link in row_links:
        seen[link] = seen.get(link, 0) + 1
    for link, count in sorted(seen.items()):
        if count > 1:
            _fail(report, "index-duplicate-row", f"{link} is linked from {count} index rows")


def _check_status_cells(report: Report, readme_text: str) -> None:
    """R7 — index rows must carry a status cell."""
    for row in re.findall(r"^\|\s*\[\d{3}\]\([^)]+\)\s*\|([^|]*)\|", readme_text, re.MULTILINE):
        if not row.strip():
            _fail(report, "row-missing-status", "index row with an empty Status cell")


def validate(adr_dir: Path, readme: Path) -> Report:
    report = Report(adr_dir=str(adr_dir))
    by_number = _numbered_files(adr_dir)
    base_by_number = {
        n: [f for f in files if not AMENDMENT_RE.match(f)] for n, files in by_number.items()
    }
    report.base_files = sum(len(v) for v in base_by_number.values())
    report.amendment_files = report.base_files and (
        sum(len(v) for v in by_number.values()) - report.base_files
    )

    readme_text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
    any_links = set(ANY_LINK_RE.findall(readme_text))

    _check_links(report, adr_dir, any_links)
    _check_reserved(report, by_number)
    for number, base_files in sorted(base_by_number.items()):
        _check_number(report, number, base_files, by_number[number], any_links)
    _check_duplicate_rows(report, ROW_LINK_RE.findall(readme_text))
    _check_status_cells(report, readme_text)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adr-dir", type=Path, default=DEFAULT_ADR_DIR)
    parser.add_argument("--readme", type=Path, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    readme = args.readme or (args.adr_dir / "README.md")

    if not args.adr_dir.is_dir():
        print(f"FAIL: ADR dir not found: {args.adr_dir}", file=sys.stderr)
        return 1

    report = validate(args.adr_dir, readme)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2))
    else:
        if report.ok:
            print(
                f"adr-index: OK ({report.base_files} base files, "
                f"{report.amendment_files} amendments, 0 findings)"
            )
        else:
            print(f"adr-index: {len(report.findings)} problem(s)")
            for f in report.findings:
                print(f"  FAIL [{f.rule}] {f.message}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
