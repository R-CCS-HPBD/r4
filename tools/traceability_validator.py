"""Traceability validator — verify the design-verification traceability registry.

Advisory validator supporting ``docs/verification/traceability.yaml``:
fails when a principle has no evidence rows, a row cites a path that does
not exist in the repo, or the registry structure is malformed. Also warns
when an ``Implemented`` row cites no test file.

Usage:
    python tools/traceability_validator.py [--registry docs/verification/traceability.yaml]

Enforcement class: ADVISORY (register with enforcement_maturity_scorer as advisory).
Do not promote to hard-fail without a policy branch (see ADR-035 governance).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

ALLOWED_STATUSES = {
    "Implemented",
    "Partial",
    "Missing",
    "Contradicted",
    "Unverifiable",
    "Closed",
}
ALLOWED_SEVERITIES = {"S1", "S2", "S3", "S4"}
SKIP_VALUES = {"none", "-", ""}
PATH_KEYS = ("code", "tests", "docs")
REQUIRED_PRINCIPLES = tuple(f"P{i}" for i in range(1, 13))
REQUIRED_ROW_KEYS = ("capability", "status")
REQUIRED_GAP_KEYS = ("id", "severity", "status", "claim", "evidence", "remedy", "package")
DEFAULT_REGISTRY = "docs/verification/traceability.yaml"


def strip_line_suffix(entry: str) -> str:
    """Drop a trailing ``:LINE`` numeric suffix from a path entry."""
    if ":" in entry:
        head, _, tail = entry.rpartition(":")
        if tail.isdigit():
            return head
    return entry


def iter_rows(doc: dict[str, Any]) -> Any:
    """Yield (principle_id, claim_id, row) tuples from the registry document."""
    principles = doc.get("principles", {})
    for pid, pdata in principles.items():
        for claim in pdata.get("claims", []) or []:
            cid = claim.get("id", "<no-id>")
            for row in claim.get("rows", []) or []:
                yield pid, cid, row


def gap_ids_of(row: Any) -> list[str]:
    """Normalize a row's gap reference(s) (``gap: X`` or ``gaps: [X, Y]``)."""
    refs = []
    single = row.get("gap")
    if isinstance(single, str) and single.strip() not in SKIP_VALUES:
        refs.append(single.strip())
    for gap in row.get("gaps", []) or []:
        gap = str(gap).strip()
        if gap not in SKIP_VALUES:
            refs.append(gap)
    return refs


def check_structure(doc: Any, problems: list[str]) -> None:
    """Validate registry shape: principles, claims, rows, statuses."""
    if not isinstance(doc, dict) or not isinstance(doc.get("principles"), dict):
        problems.append("registry must define a top-level 'principles' mapping")
        return
    principles = doc["principles"]
    for required in REQUIRED_PRINCIPLES:
        if required not in principles:
            problems.append(f"principle {required} missing from registry")
    for pid, pdata in principles.items():
        check_principle(pid, pdata, problems)


def check_principle(pid: str, pdata: dict[str, Any], problems: list[str]) -> None:
    """Validate one principle's claims and evidence rows."""
    claims = pdata.get("claims") or []
    if not claims:
        problems.append(f"{pid}: no claims with evidence rows")
        return
    for claim in claims:
        cid = claim.get("id", "<no-id>")
        rows = claim.get("rows") or []
        if not rows:
            problems.append(f"{pid}: claim {cid} has no evidence rows")
            continue
        for row in rows:
            check_row_shape(pid, cid, row, problems)


def check_row_shape(pid: str, cid: str, row: Any, problems: list[str]) -> None:
    """Validate a single evidence row's required keys and status value."""
    if not isinstance(row, dict):
        problems.append(f"{pid}/{cid}: evidence row is not a mapping")
        return
    for key in REQUIRED_ROW_KEYS:
        if not row.get(key):
            problems.append(f"{pid}/{cid}: row missing required key '{key}'")
    status = row.get("status")
    if status and status not in ALLOWED_STATUSES:
        problems.append(f"{pid}/{cid}: invalid status '{status}'")


def check_paths(root: Path, doc: dict[str, Any], problems: list[str], warnings: list[str]) -> None:
    """Check every cited path exists; warn on Implemented rows without tests."""
    for pid, cid, row in iter_rows(doc):
        label = f"{pid}/{cid} ({row.get('capability', '<unnamed>')})"
        if _first_missing(root, row, problems, label):
            continue
        if _implemented_without_tests(row):
            warnings.append(f"{label}: Implemented but cites no test file")


def _implemented_without_tests(row: Any) -> bool:
    """True when a row claims Implemented but cites no non-placeholder test file."""
    test_entries = row.get("tests", []) or []
    has_tests = any(str(t).strip() not in SKIP_VALUES for t in test_entries)
    return row.get("status") == "Implemented" and not has_tests


def _first_missing(root: Path, row: Any, problems: list[str], label: str) -> bool:
    """Append problems for missing paths or ADR files. Returns True if any missing."""
    missing = _append_missing_paths(root, row, problems, label)
    return _append_missing_adrs(root, row, problems, label) or missing


def _append_missing_paths(root: Path, row: Any, problems: list[str], label: str) -> bool:
    """Append a problem for each cited code/tests/docs path that does not exist."""
    missing = False
    for key in PATH_KEYS:
        for entry in row.get(key, []) or []:
            entry = strip_line_suffix(str(entry).strip())
            if entry in SKIP_VALUES:
                continue
            if not (root / entry).exists():
                problems.append(f"{label}: cited {key} path does not exist: {entry}")
                missing = True
    return missing


def _append_missing_adrs(root: Path, row: Any, problems: list[str], label: str) -> bool:
    """Append a problem for each cited ADR number with no docs/adr file."""
    missing = False
    for adr in row.get("adrs", []) or []:
        adr = str(adr).strip()
        if adr in SKIP_VALUES:
            continue
        if not list((root / "docs/adr").glob(f"{adr.lstrip('0').zfill(3)}-*.md")):
            problems.append(f"{label}: cited ADR file does not exist: {adr}")
            missing = True
    return missing


def check_gaps_section(doc: Any, problems: list[str]) -> None:
    """Validate the top-level ``gaps:`` register section shape and uniqueness."""
    gaps = doc.get("gaps")
    if not isinstance(gaps, list) or not gaps:
        problems.append("registry must define a non-empty 'gaps' section")
        return
    seen: set[str] = set()
    for entry in gaps:
        _check_gap_entry(entry, seen, problems)


def _check_gap_entry(entry: Any, seen: set[str], problems: list[str]) -> None:
    """Validate one gaps-section entry's keys, uniqueness, severity, status."""
    if not isinstance(entry, dict):
        problems.append("gap entry is not a mapping")
        return
    gid = entry.get("id")
    if not gid:
        problems.append("gap entry missing 'id'")
        return
    if gid in seen:
        problems.append(f"duplicate gap id: {gid}")
    seen.add(gid)
    for key in REQUIRED_GAP_KEYS:
        if key not in entry:
            problems.append(f"{gid}: missing required key '{key}'")
    if entry.get("severity") not in ALLOWED_SEVERITIES:
        problems.append(f"{gid}: invalid severity {entry.get('severity')!r}")
    if entry.get("status") not in ALLOWED_STATUSES:
        problems.append(f"{gid}: invalid status {entry.get('status')!r}")


def check_gap_crossrefs(doc: Any, problems: list[str]) -> None:
    """Cross-check row gap references against the gaps section (both ways)."""
    defs = {g["id"]: g for g in doc.get("gaps", []) or [] if isinstance(g, dict) and g.get("id")}
    referenced: set[str] = set()
    for pid, cid, row in iter_rows(doc):
        referenced |= _check_row_gaps(defs, f"{pid}/{cid}", row, problems)
    for gid, gdef in defs.items():
        if gid not in referenced and gdef.get("status") != "Closed":
            problems.append(f"gaps-section gap {gid} is not referenced by any evidence row")


def _check_row_gaps(defs: dict[str, Any], claim: str, row: Any, problems: list[str]) -> set[str]:
    """Validate one row's gap references; return the set it referenced."""
    label = f"{claim} ({row.get('capability', '<unnamed>')})"
    referenced: set[str] = set()
    for gid in gap_ids_of(row):
        referenced.add(gid)
        if gid not in defs:
            problems.append(f"{label}: gap '{gid}' has no gaps-section entry")
        else:
            _check_row_gap_agreement(label, row, gid, defs[gid], problems)
    return referenced


def check_package_crossrefs(doc: Any, problems: list[str]) -> None:
    """Bidirectional gap <-> package consistency (gaps' homes match lists)."""
    packages = {
        p["id"]: p for p in doc.get("packages", []) or [] if isinstance(p, dict) and p.get("id")
    }
    gaps = doc.get("gaps", []) or []
    if not packages and gaps:
        problems.append("registry has gaps but no 'packages' section")
        return
    _check_gap_package_fields(packages, gaps, problems)
    for pid, pkg in packages.items():
        _check_package_lists(pkg, pid, gaps, problems)


def _check_gap_package_fields(
    packages: dict[str, Any], gaps: list[Any], problems: list[str]
) -> None:
    """Each gap's package must exist and list the gap in gaps_closed."""
    for gap in gaps:
        gid = gap.get("id", "<no-id>")
        pkg = gap.get("package")
        if pkg not in packages:
            problems.append(f"gap {gid}: package {pkg!r} not in packages section")
            continue
        if gid not in (packages[pkg].get("gaps_closed") or []):
            problems.append(
                f"gap {gid}: names package {pkg} but {pkg}.gaps_closed does not list it"
            )


def _check_package_lists(
    pkg: dict[str, Any], pid: str, gaps: list[Any], problems: list[str]
) -> None:
    """Every gap a package claims to close must exist and point back."""
    gaps_by_id = {g.get("id"): g for g in gaps if isinstance(g, dict)}
    for gid in pkg.get("gaps_closed") or []:
        match = gaps_by_id.get(gid)
        if match is None:
            problems.append(f"package {pid}: gaps_closed lists unknown gap {gid}")
        elif match.get("package") != pid:
            problems.append(
                f"package {pid}: gaps_closed lists {gid} "
                f"whose package field is {match.get('package')!r}"
            )


def check_report_link(root: Path, doc: Any, problems: list[str]) -> None:
    """The companion report named in the registry must exist."""
    report = doc.get("report_file")
    if not report:
        problems.append("registry must name its companion report in 'report_file'")
        return
    if not (root / "docs/verification" / str(report)).exists():
        problems.append(f"report_file does not exist: {report}")


def _check_row_gap_agreement(
    label: str, row: Any, gid: str, gdef: dict[str, Any], problems: list[str]
) -> None:
    """A row may not be Implemented with a gap, and row/ gap statuses must match."""
    if gdef.get("status") == "Closed":
        # Closed gaps keep no semantic bond with rows; rows drop refs on close.
        return
    if row.get("status") == "Implemented":
        problems.append(f"{label}: Implemented row must not carry gap '{gid}'")
        return
    if gdef.get("status") and row.get("status") != gdef.get("status"):
        problems.append(
            f"{label}: row status '{row.get('status')}' does not match "
            f"gap {gid} status '{gdef.get('status')}'"
        )


def validate(registry_path: Path, root: Path) -> tuple[list[str], list[str]]:
    """Run all checks. Returns (problems, warnings)."""
    problems: list[str] = []
    warnings: list[str] = []
    try:
        doc = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"cannot load registry {registry_path}: {exc}"], []
    check_structure(doc, problems)
    if not problems:
        check_paths(root, doc, problems, warnings)
        check_gaps_section(doc, problems)
        check_gap_crossrefs(doc, problems)
        check_package_crossrefs(doc, problems)
        check_report_link(root, doc, problems)
    return problems, warnings


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    problems, warnings = validate(root / args.registry, root)
    for warning in warnings:
        print(f"WARN: {warning}")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        print(f"traceability: {len(problems)} problem(s), {len(warnings)} warning(s)")
        return 1
    print(f"traceability: OK ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
