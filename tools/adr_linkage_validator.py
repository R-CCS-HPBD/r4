"""ADR linkage validator — ensure governance docs and validators stay connected.

Validates:
  1. ADR references in governance docs resolve to actual ADR files
  2. Active validators are referenced by at least one governance doc
  3. ADR status fields are consistent
  4. Governance docs don't reference superseded/deprecated ADRs as authoritative
  5. Validator tools referenced in instruction files exist
  6. Every configured scan path exists (root entry files included)

Usage:
    python tools/adr_linkage_validator.py          # exits 0/1
    python tools/adr_linkage_validator.py --json   # machine-readable

Enforcement class: ADVISORY
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Collection
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ADR_DIR = "docs/adr"

# Governance doc directories to scan for ADR references. The root entry files
# joined on 2026-10-18 (hardening plan H-11.8): agents and humans start there.
# A configured path that does not exist is an error (missing_scan_path), not
# a silent skip.
GOVERNANCE_SCAN_PATHS: tuple[str, ...] = (
    "docs",
    ".github/instructions",
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "CONSTITUTION.md",
    "README.md",
    "R4.md",
    "ARCHITECTURE.md",
    "DEVELOPMENT_OBJECTIVES.md",
    "PLATFORM_CONTRACT.md",
    "CONTRIBUTING.md",
    "TESTING.md",
)

# Active validators that should be referenced in governance docs
VALIDATOR_TOOLS: tuple[str, ...] = (
    "tools/import_boundary_validator.py",
    "tools/contracts_fence.py",
    "tools/dto_equivalence_check.py",
    "tools/schema_version_tracker.py",
    "tools/stale_name_validator.py",
    "tools/config_drift_validator.py",
    "tools/exemption_governance_validator.py",
)

# Instruction files that reference tools
INSTRUCTION_FILES: tuple[str, ...] = (
    ".github/instructions/mutation-safety.instructions.md",
    ".github/instructions/development.instructions.md",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ADREntry:
    """An ADR file and its metadata."""

    file: str
    number: str
    title: str
    status: str  # "Accepted" | "Proposed" | "Superseded" | "Deprecated"


@dataclass(frozen=True)
class LinkageFinding:
    """One governance linkage issue."""

    file: str
    line: int
    severity: str  # "error" | "warning" | "info"
    check: str
    message: str


@dataclass
class LinkageReport:
    """Complete ADR linkage report."""

    adrs: list[ADREntry] = field(default_factory=list)
    findings: list[LinkageFinding] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "total_adrs": len(self.adrs),
            "active_adrs": sum(1 for a in self.adrs if a.status == "Accepted"),
            "errors": sum(1 for f in self.findings if f.severity == "error"),
            "warnings": sum(1 for f in self.findings if f.severity == "warning"),
            "info": sum(1 for f in self.findings if f.severity == "info"),
        }

    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)


# ---------------------------------------------------------------------------
# ADR inventory
# ---------------------------------------------------------------------------

_STATUS_RE = re.compile(r"\*?\*?Status:?\*?\*?:?\s*(\w+)", re.MULTILINE)


def _scan_adrs(repo_root: Path) -> list[ADREntry]:
    """Scan the ADR directory for decision records."""
    entries = []
    adr_dir = repo_root / ADR_DIR
    if not adr_dir.exists():
        return entries

    for adr_file in sorted(adr_dir.glob("*.md")):
        if adr_file.name in ("README.md", "_template.md"):
            continue
        if not adr_file.is_file():
            continue

        # Parse number and title from filename
        m = re.match(r"(\d+)-(.+)\.md$", adr_file.name)
        if not m:
            continue

        number = m.group(1)
        title = m.group(2).replace("-", " ")

        # Parse status from content
        try:
            content = adr_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        status_match = _STATUS_RE.search(content)
        status = status_match.group(1) if status_match else "Unknown"

        rel_path = adr_file.relative_to(repo_root).as_posix()
        entries.append(
            ADREntry(
                file=rel_path,
                number=number,
                title=title,
                status=status,
            )
        )

    return entries


# ---------------------------------------------------------------------------
# Reference scanning
# ---------------------------------------------------------------------------

_ADR_REF_RE = re.compile(r"(?:ADR|adr)[\s-]*(\d{3})")
_ADR_FILE_RE = re.compile(r"(?:^|/)(\d{3})-[\w-]+\.md")


def _scan_adr_references(repo_root: Path) -> list[tuple[str, int, str]]:
    """Find ADR references in governance docs. Returns (file, line, adr_number)."""
    refs: list[tuple[str, int, str]] = []

    for scan_path in GOVERNANCE_SCAN_PATHS:
        path = repo_root / scan_path
        if path.is_file():
            refs.extend(_scan_file_for_adr_refs(path, repo_root))
        elif path.is_dir():
            for md_file in path.rglob("*.md"):
                if md_file.is_file():
                    refs.extend(_scan_file_for_adr_refs(md_file, repo_root))

    return refs


def _scan_file_for_adr_refs(filepath: Path, repo_root: Path) -> list[tuple[str, int, str]]:
    """Scan a single file for ADR references."""
    refs = []
    try:
        lines = filepath.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return refs

    rel_path = filepath.relative_to(repo_root).as_posix()
    for lineno, line in enumerate(lines, 1):
        # Match "ADR-001" or "ADR 001" patterns
        for m in _ADR_REF_RE.finditer(line):
            refs.append((rel_path, lineno, m.group(1)))
        # Match "001-short-title.md" file references
        for m in _ADR_FILE_RE.finditer(line):
            refs.append((rel_path, lineno, m.group(1)))

    return refs


# ---------------------------------------------------------------------------
# Validator reference scanning
# ---------------------------------------------------------------------------


def _scan_validator_references(repo_root: Path) -> set[str]:
    """Find which validator tools are referenced in governance/instruction docs."""
    referenced: set[str] = set()

    for scan_path in GOVERNANCE_SCAN_PATHS + INSTRUCTION_FILES:
        path = repo_root / scan_path
        if path.is_file():
            _check_file_for_validators(path, referenced)
        elif path.is_dir():
            for md_file in path.rglob("*.md"):
                if md_file.is_file():
                    _check_file_for_validators(md_file, referenced)

    return referenced


def _check_file_for_validators(filepath: Path, referenced: set[str]) -> None:
    try:
        content = filepath.read_text()
    except (OSError, UnicodeDecodeError):
        return

    for validator in VALIDATOR_TOOLS:
        # Match the tool name (filename or full path)
        tool_name = Path(validator).stem
        if tool_name in content or validator in content:
            referenced.add(validator)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


_RESERVED_HEADING = "### Numbers not allocated"
_RESERVED_ENTRY_RE = re.compile(r"^- \*\*(\d{3})\*\*:", re.MULTILINE)


def _reserved_adr_numbers(repo_root: Path) -> set[str]:
    """Numbers the ADR index registers as named but not yet written.

    A plan may name a future ADR (e.g. ADR-091, stand-alone WS-09). The index
    lists such numbers under "Numbers not allocated" with what they are for;
    references to them are not broken. Any other missing number still is.
    """
    index = repo_root / ADR_DIR / "README.md"
    try:
        text = index.read_text()
    except (OSError, UnicodeDecodeError):
        return set()
    _, found, rest = text.partition(_RESERVED_HEADING)
    if not found:
        return set()
    section = rest.split("\n## ", 1)[0].split("\n### ", 1)[0]
    return set(_RESERVED_ENTRY_RE.findall(section))


def _check_broken_adr_refs(
    refs: list[tuple[str, int, str]],
    adrs: list[ADREntry],
    reserved: Collection[str] = (),
) -> list[LinkageFinding]:
    """Check 1: ADR references that don't resolve to actual ADR files.

    ``reserved`` numbers (registered in the ADR index, not yet written) are
    not findings.
    """
    known_numbers = {a.number for a in adrs} | set(reserved)
    findings = []
    for file, line, adr_num in refs:
        if adr_num not in known_numbers:
            findings.append(
                LinkageFinding(
                    file=file,
                    line=line,
                    severity="warning",
                    check="broken_adr_ref",
                    message=f"References ADR-{adr_num} but no matching ADR file exists",
                )
            )
    return findings


def _check_undocumented_validators(
    referenced: set[str],
    repo_root: Path,
) -> list[LinkageFinding]:
    """Check 2: Validators not referenced in any governance doc."""
    findings = []
    for validator in VALIDATOR_TOOLS:
        if not (repo_root / validator).exists():
            continue
        if validator not in referenced:
            findings.append(
                LinkageFinding(
                    file=validator,
                    line=0,
                    severity="info",
                    check="undocumented_validator",
                    message=f"Validator {validator} exists but is not referenced "
                    f"in any governance or instruction doc",
                )
            )
    return findings


def _check_superseded_as_authoritative(
    refs: list[tuple[str, int, str]],
    adrs: list[ADREntry],
) -> list[LinkageFinding]:
    """Check 4: Governance docs referencing superseded/deprecated ADRs."""
    non_active = {a.number: a for a in adrs if a.status in ("Superseded", "Deprecated")}
    findings = []

    for file, line, adr_num in refs:
        if adr_num in non_active:
            adr = non_active[adr_num]
            # Skip if the reference is in the ADR directory itself
            if file.startswith(ADR_DIR):
                continue
            findings.append(
                LinkageFinding(
                    file=file,
                    line=line,
                    severity="warning",
                    check="superseded_adr_ref",
                    message=f"References ADR-{adr_num} ({adr.title}) which has "
                    f"status '{adr.status}' — may be outdated guidance",
                )
            )

    return findings


def _check_tool_references_in_instructions(
    repo_root: Path,
) -> list[LinkageFinding]:
    """Check 5: Tools referenced in instruction files that don't exist."""
    findings = []
    tool_ref_re = re.compile(r"python\s+tools/(\w+\.py)")

    for instr_file in INSTRUCTION_FILES:
        path = repo_root / instr_file
        if not path.exists():
            continue
        try:
            lines = path.read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        rel_path = path.relative_to(repo_root).as_posix()
        for lineno, line in enumerate(lines, 1):
            for m in tool_ref_re.finditer(line):
                tool_path = repo_root / "tools" / m.group(1)
                if not tool_path.exists():
                    findings.append(
                        LinkageFinding(
                            file=rel_path,
                            line=lineno,
                            severity="error",
                            check="missing_tool_reference",
                            message=f"References tools/{m.group(1)} which does not exist",
                        )
                    )

    return findings


def _check_missing_scan_paths(repo_root: Path) -> list[LinkageFinding]:
    """Check 6: configured scan paths that do not exist.

    A renamed or deleted entry file used to drop out of the scan silently,
    so its ADR references went unchecked.
    """
    return [
        LinkageFinding(
            file=configured,
            line=0,
            severity="error",
            check="missing_scan_path",
            message=f"Configured scan path {configured} does not exist; "
            "fix or remove it in tools/adr_linkage_validator.py",
        )
        for configured in GOVERNANCE_SCAN_PATHS + INSTRUCTION_FILES
        if not (repo_root / configured).exists()
    ]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def validate_adr_linkage(repo_root: Path) -> LinkageReport:
    """Run all ADR linkage checks."""
    report = LinkageReport()
    report.adrs = _scan_adrs(repo_root)

    refs = _scan_adr_references(repo_root)
    referenced_validators = _scan_validator_references(repo_root)

    reserved = _reserved_adr_numbers(repo_root)
    report.findings.extend(_check_broken_adr_refs(refs, report.adrs, reserved))
    report.findings.extend(_check_undocumented_validators(referenced_validators, repo_root))
    report.findings.extend(_check_superseded_as_authoritative(refs, report.adrs))
    report.findings.extend(_check_tool_references_in_instructions(repo_root))
    report.findings.extend(_check_missing_scan_paths(repo_root))

    return report


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _format_human(report: LinkageReport) -> str:
    lines: list[str] = []
    lines.append("ADR Linkage Validation Report")
    lines.append("=" * 60)
    lines.append(f"ADRs found: {len(report.adrs)}")
    for adr in report.adrs:
        lines.append(f"  [{adr.status}] ADR-{adr.number}: {adr.title}")

    if report.findings:
        lines.append(f"\nFindings ({len(report.findings)}):")
        for f in report.findings:
            icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}.get(f.severity, "?")
            lines.append(f"  {icon} [{f.severity.upper()}] {f.file}:{f.line}: {f.message}")
    else:
        lines.append("\nNo linkage issues found.")

    lines.append(f"\nSummary: {report.summary}")
    return "\n".join(lines)


def _format_json(report: LinkageReport) -> str:
    return json.dumps(
        {
            "adrs": [asdict(a) for a in report.adrs],
            "findings": [asdict(f) for f in report.findings],
            "summary": report.summary,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    output_json = "--json" in sys.argv

    report = validate_adr_linkage(repo_root)

    if output_json:
        print(_format_json(report))
    else:
        print(_format_human(report))
        print("\nADR linkage validator completed")

    return 1 if report.has_errors() else 0


if __name__ == "__main__":
    raise SystemExit(main())
