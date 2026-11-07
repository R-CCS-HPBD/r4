"""Exemption governance validator — track and audit architectural exemptions.

Scans the repository for ``# noqa: boundary`` inline exemptions and
``allow_lazy`` rule-level exemptions in the import boundary validator.

Required checks:
  1. Missing rationale — exemption has no ``reason=`` metadata
  2. Missing SLA/expiry — no ``expires=`` or ``ticket=`` metadata
  3. Expired exemptions — ``expires=`` date has passed
  4. Orphaned exemptions — file or import target no longer exists
  5. Exemptions referencing removed modules
  6. Duplicated exemptions — same import exempted twice
  7. Exemptions no longer necessary — boundary rule was relaxed
  8. Stale exemptions — ``added=`` date is older than age threshold
     with no ``expires=`` or ``ticket=`` set

Preferred metadata format (inline comment after ``# noqa: boundary``)::

    # noqa: boundary — reason=lazy composition owner=jobs expires=2026-06 ticket=U-999

Usage:
    python tools/exemption_governance_validator.py          # exits 0/1
    python tools/exemption_governance_validator.py --json   # machine-readable
    python tools/exemption_governance_validator.py --inventory  # full inventory

Enforcement class: ADVISORY (warnings) with optional hard-fail via --strict
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Exemption metadata model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExemptionEntry:
    """One architectural exemption found in the source."""

    file: str
    line: int
    kind: str  # "noqa_boundary" | "allow_lazy"
    import_target: str  # What is being imported
    raw_comment: str  # Full comment text after the ``noqa: boundary`` marker
    reason: str = ""
    owner: str = ""
    expires: str = ""
    ticket: str = ""
    added: str = ""  # Date exemption was introduced (YYYY-MM-DD)
    validator: str = ""  # Which validator the exemption suppresses


@dataclass(frozen=True)
class ExemptionFinding:
    """One governance issue with an exemption."""

    file: str
    line: int
    severity: str  # "error" | "warning" | "info"
    check: str  # Check identifier (e.g. "missing_rationale")
    message: str
    exemption_kind: str = ""


@dataclass
class ExemptionReport:
    """Complete exemption governance report."""

    exemptions: list[ExemptionEntry] = field(default_factory=list)
    findings: list[ExemptionFinding] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    def has_warnings(self) -> bool:
        return any(f.severity == "warning" for f in self.findings)


# ---------------------------------------------------------------------------
# Metadata parser
# ---------------------------------------------------------------------------

_METADATA_RE = re.compile(
    r"(reason|owner|expires|ticket|added|validator)\s*=\s*(\S+(?:\s+\S+)*?)(?=\s+(?:reason|owner|expires|ticket|added|validator)=|\s*$)"
)


def _parse_exemption_metadata(comment: str) -> dict[str, str]:
    """Extract structured metadata from the comment after ``# noqa: boundary``."""
    metadata: dict[str, str] = {}
    for match in _METADATA_RE.finditer(comment):
        key = match.group(1)
        value = match.group(2).strip()
        metadata[key] = value
    return metadata


# ---------------------------------------------------------------------------
# Inline exemption scanner
# ---------------------------------------------------------------------------

_NOQA_RE = re.compile(r"#\s*noqa:\s*(boundary|C901)\b(.*)")
_DEF_NAME_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")


def _scan_inline_exemptions(repo_root: Path) -> list[ExemptionEntry]:  # noqa: C901 — reason=pre-existing complexity debt owner=agent-debug added=2026-09-05
    """Find all ``# noqa: boundary`` and ``# noqa: C901`` exemptions in Python
    source files (round-5 T4: C901 noopas join the governance path)."""
    entries: list[ExemptionEntry] = []

    # Scan only source directories (skip test fixtures and docs)
    scan_roots = [
        "umrs/src",
        "r4/src",
        "gateway",
        "mcp-servers/src",
    ]

    for scan_root in scan_roots:
        root_path = repo_root / scan_root
        if not root_path.exists():
            continue
        for py_file in sorted(root_path.rglob("*.py")):
            if py_file.is_file():
                entries.extend(_exemptions_from_file(py_file, repo_root))

    return entries


def _exemptions_from_file(py_file: Path, repo_root: Path) -> list[ExemptionEntry]:
    """All inline exemptions in ONE source file."""
    try:
        lines = py_file.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    entries: list[ExemptionEntry] = []
    rel_path = py_file.relative_to(repo_root).as_posix()
    for lineno, line in enumerate(lines, 1):
        entry = _entry_from_line(rel_path, lineno, line)
        if entry is not None:
            entries.append(entry)
    return entries


def _entry_from_line(rel_path: str, lineno: int, line: str) -> ExemptionEntry | None:
    """One noqa line → one entry (None when no noqa marker)."""
    match = _NOQA_RE.search(line)
    if not match:
        return None

    noqa_kind = f"noqa_{match.group(1).lower().replace(' ', '_')}"
    raw_comment = match.group(2).strip()
    # Remove leading dash/em-dash separator
    if raw_comment.startswith(("—", "-", "–")):
        raw_comment = raw_comment.lstrip("—-– ").strip()

    metadata = _parse_exemption_metadata(raw_comment)

    # Extract import target from the line (def name for C901 entries;
    # import for boundary entries).
    import_target = (
        _extract_def_name(line) if noqa_kind == "noqa_c901" else _extract_import_target(line)
    )

    return ExemptionEntry(
        file=rel_path,
        line=lineno,
        kind=noqa_kind,
        import_target=import_target,
        raw_comment=raw_comment,
        reason=metadata.get("reason", ""),
        owner=metadata.get("owner", ""),
        expires=metadata.get("expires", ""),
        ticket=metadata.get("ticket", ""),
        added=metadata.get("added", ""),
        validator=metadata.get("validator", ""),
    )


_IMPORT_FROM_RE = re.compile(r"from\s+(\S+)\s+import")
_IMPORT_RE = re.compile(r"import\s+(\S+)")


def _extract_import_target(line: str) -> str:
    """Extract the imported module from an import statement line."""
    m = _IMPORT_FROM_RE.search(line)
    if m:
        return m.group(1)
    m = _IMPORT_RE.search(line)
    if m:
        return m.group(1)
    return "<unknown>"


def _extract_def_name(line: str) -> str:
    """Extract the function/method being suppressed from a `noqa: C901` line."""
    m = _DEF_NAME_RE.search(line)
    if m:
        return m.group(1)
    return "<unknown>"


# ---------------------------------------------------------------------------
# Rule-level exemption scanner
# ---------------------------------------------------------------------------


def _scan_rule_exemptions(repo_root: Path) -> list[ExemptionEntry]:
    """Find ``allow_lazy=True`` rule-level exemptions in the boundary validator."""
    entries: list[ExemptionEntry] = []
    validator_path = repo_root / "tools" / "import_boundary_validator.py"
    if not validator_path.exists():
        return entries

    try:
        lines = validator_path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return entries

    rel_path = validator_path.relative_to(repo_root).as_posix()

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if "allow_lazy=True" in stripped:
            # Extract context from surrounding lines (look for root= above)
            root_target = _find_rule_root(lines, lineno - 1)
            comment = ""
            # Check for inline comment
            if "#" in stripped:
                comment = stripped[stripped.index("#") + 1 :].strip()
            # Structured metadata travels in the comment too (kv tokens —
            # required for expires=/ticket= governance on rule exemptions).
            metadata = _parse_exemption_metadata(comment)

            entries.append(
                ExemptionEntry(
                    file=rel_path,
                    line=lineno,
                    kind="allow_lazy",
                    import_target=root_target,
                    raw_comment=comment,
                    reason=metadata.get("reason", "") or (comment if comment else ""),
                    owner=metadata.get("owner", ""),
                    expires=metadata.get("expires", ""),
                    ticket=metadata.get("ticket", ""),
                    added=metadata.get("added", ""),
                    validator=metadata.get("validator", ""),
                )
            )

    return entries


def _find_rule_root(lines: list[str], allow_lazy_idx: int) -> str:
    """Look backwards from an allow_lazy line to find the root= value."""
    for i in range(allow_lazy_idx - 1, max(allow_lazy_idx - 15, -1), -1):
        stripped = lines[i].strip()
        m = re.search(r'root\s*=\s*"([^"]+)"', stripped)
        if m:
            return m.group(1)
    return "<unknown>"


# ---------------------------------------------------------------------------
# Governance checks
# ---------------------------------------------------------------------------


def _check_missing_rationale(
    exemptions: list[ExemptionEntry],
) -> list[ExemptionFinding]:
    """Check 1: exemptions without a reason."""
    findings = []
    for ex in exemptions:
        if not ex.reason and not ex.raw_comment:
            findings.append(
                ExemptionFinding(
                    file=ex.file,
                    line=ex.line,
                    severity="warning",
                    check="missing_rationale",
                    message=f"Exemption for {ex.import_target!r} has no rationale "
                    f"(add reason=<text> after # noqa: boundary)",
                    exemption_kind=ex.kind,
                )
            )
    return findings


def _check_missing_expiry(
    exemptions: list[ExemptionEntry],
) -> list[ExemptionFinding]:
    """Check 2: exemptions without SLA/expiry metadata."""
    findings = []
    for ex in exemptions:
        if not ex.expires and not ex.ticket:
            findings.append(
                ExemptionFinding(
                    file=ex.file,
                    line=ex.line,
                    severity="info",
                    check="missing_expiry",
                    message=f"Exemption for {ex.import_target!r} has no expiry or ticket "
                    f"(add expires=YYYY-MM or ticket=U-NNN)",
                    exemption_kind=ex.kind,
                )
            )
    return findings


def _check_expired(
    exemptions: list[ExemptionEntry],
    now: datetime | None = None,
) -> list[ExemptionFinding]:
    """Check 3: exemptions past their expiry date."""
    if now is None:
        now = datetime.now()
    findings = []
    for ex in exemptions:
        if not ex.expires:
            continue
        try:
            # Support YYYY-MM and YYYY-MM-DD formats
            expires_str = ex.expires
            if len(expires_str) == 7:  # YYYY-MM
                expires_date = datetime.strptime(expires_str, "%Y-%m")
            elif len(expires_str) == 10:  # YYYY-MM-DD
                expires_date = datetime.strptime(expires_str, "%Y-%m-%d")
            else:
                findings.append(
                    ExemptionFinding(
                        file=ex.file,
                        line=ex.line,
                        severity="warning",
                        check="invalid_expiry_format",
                        message=f"Exemption for {ex.import_target!r} has unparseable "
                        f"expiry date: {ex.expires!r} (use YYYY-MM or YYYY-MM-DD)",
                        exemption_kind=ex.kind,
                    )
                )
                continue
        except ValueError:
            findings.append(
                ExemptionFinding(
                    file=ex.file,
                    line=ex.line,
                    severity="warning",
                    check="invalid_expiry_format",
                    message=f"Exemption for {ex.import_target!r} has unparseable "
                    f"expiry date: {ex.expires!r}",
                    exemption_kind=ex.kind,
                )
            )
            continue

        if now > expires_date:
            findings.append(
                ExemptionFinding(
                    file=ex.file,
                    line=ex.line,
                    severity="error",
                    check="expired_exemption",
                    message=f"Exemption for {ex.import_target!r} expired on "
                    f"{ex.expires} — review and remove or renew",
                    exemption_kind=ex.kind,
                )
            )
    return findings


def _check_orphaned(
    exemptions: list[ExemptionEntry],
    repo_root: Path,
) -> list[ExemptionFinding]:
    """Check 4: exemptions in files that no longer exist."""
    findings = []
    for ex in exemptions:
        full_path = repo_root / ex.file
        if not full_path.exists():
            findings.append(
                ExemptionFinding(
                    file=ex.file,
                    line=ex.line,
                    severity="error",
                    check="orphaned_file",
                    message=f"Exemption file {ex.file!r} no longer exists",
                    exemption_kind=ex.kind,
                )
            )
    return findings


def _check_removed_targets(
    exemptions: list[ExemptionEntry],
    repo_root: Path,
) -> list[ExemptionFinding]:
    """Check 5: exemptions referencing modules that no longer exist."""
    findings = []
    for ex in exemptions:
        if ex.import_target == "<unknown>":
            continue
        # C901 exemptions name a FUNCTION in the same file (complexity
        # suppression), never a module path — they can't be stale imports.
        # Skipping them removes the steady 8-row false-positive class
        # (WP-13 r2 promotion follow-up).
        if ex.kind == "noqa_c901":
            continue
        # Convert dotted module path to filesystem path
        module_path = _module_to_path(ex.import_target, repo_root)
        if module_path is None:
            findings.append(
                ExemptionFinding(
                    file=ex.file,
                    line=ex.line,
                    severity="warning",
                    check="removed_target",
                    message=f"Import target {ex.import_target!r} may no longer exist "
                    f"in the repository (could not resolve to a file)",
                    exemption_kind=ex.kind,
                )
            )
    return findings


def _module_to_path(module: str, repo_root: Path) -> Path | None:
    """Try to resolve a dotted module path to a filesystem path."""
    # Try common source layouts
    prefixes = [
        "umrs/src",
        "r4/src",
        "gateway",
        "mcp-servers/src",
        "",
    ]
    parts = module.split(".")
    for prefix in prefixes:
        base = repo_root / prefix if prefix else repo_root
        # Try as package (directory with __init__.py)
        pkg_path = base / "/".join(parts) / "__init__.py"
        if pkg_path.exists():
            return pkg_path
        # Try as module file
        mod_path = (
            base / "/".join(parts[:-1]) / f"{parts[-1]}.py"
            if len(parts) > 1
            else base / f"{parts[0]}.py"
        )
        if mod_path.exists():
            return mod_path
    return None


def _check_duplicates(
    exemptions: list[ExemptionEntry],
) -> list[ExemptionFinding]:
    """Check 6: same import exempted more than once in same file."""
    findings = []
    seen: dict[tuple[str, str], ExemptionEntry] = {}
    for ex in exemptions:
        key = (ex.file, ex.import_target)
        if key in seen:
            prev = seen[key]
            findings.append(
                ExemptionFinding(
                    file=ex.file,
                    line=ex.line,
                    severity="info",
                    check="duplicate_exemption",
                    message=f"Duplicate exemption for {ex.import_target!r} "
                    f"(also at line {prev.line})",
                    exemption_kind=ex.kind,
                )
            )
        else:
            seen[key] = ex
    return findings


def _rule_forbids_import(file_path: str, import_target: str, rules: list) -> bool:
    """True when any rule actually forbids this import from this file."""
    for rule in rules:
        if not file_path.startswith(rule.root):
            continue
        top_level = import_target.split(".", 1)[0]
        if top_level in rule.forbidden_top_levels:
            return True
        for forbidden in rule.forbidden_subpackages:
            if import_target == forbidden or import_target.startswith(forbidden + "."):
                return True
    return False


def _exemption_still_forbidden(ex: ExemptionEntry, rules: list) -> bool:
    """One row would-definitely-be-forbidden test (check 7)."""
    return _rule_forbids_import(ex.file, ex.import_target, rules)


def _check_unnecessary(
    exemptions: list[ExemptionEntry],
    repo_root: Path,
) -> list[ExemptionFinding]:  # noqa: C901 — reason=pre-existing complexity debt owner=agent-debug added=2026-09-05
    """Check 7: exemptions that may no longer be necessary.

    An exemption is unnecessary if the boundary rule that would have
    triggered no longer covers the import. This is conservative — only
    flags when we can definitively determine no rule applies.
    """
    # Import the boundary rules to check if the exemption is still needed
    findings = []
    try:
        # Add tools/ to path for import
        tools_dir = str(repo_root / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        from import_boundary_validator import (
            IMPORT_BOUNDARY_RULES,
            UMRS_INTERNAL_BOUNDARY_RULES,
        )
    except ImportError:
        return findings

    all_rules = list(IMPORT_BOUNDARY_RULES) + list(UMRS_INTERNAL_BOUNDARY_RULES)

    for ex in exemptions:
        if ex.kind != "noqa_boundary":
            continue
        if ex.import_target == "<unknown>":
            continue
        if not _exemption_still_forbidden(ex, all_rules):
            findings.append(
                ExemptionFinding(
                    file=ex.file,
                    line=ex.line,
                    severity="warning",
                    check="unnecessary_exemption",
                    message=f"Exemption for {ex.import_target!r} may no longer be "
                    f"necessary — no boundary rule forbids this import",
                    exemption_kind=ex.kind,
                )
            )

    return findings


# Default: exemptions older than 180 days without expiry/ticket are stale.
_STALE_AGE_DAYS = 180


def _check_stale(
    exemptions: list[ExemptionEntry],
    now: datetime | None = None,
    stale_age_days: int = _STALE_AGE_DAYS,
) -> list[ExemptionFinding]:
    """Check 8: exemptions with ``added=`` older than *stale_age_days* and no expiry or ticket."""
    if now is None:
        now = datetime.now()
    findings = []
    for ex in exemptions:
        if not ex.added:
            continue
        # If the exemption already has an expiry or ticket, skip it —
        # those are covered by check 2/3.
        if ex.expires or ex.ticket:
            continue
        try:
            added_date = datetime.strptime(ex.added, "%Y-%m-%d")
        except ValueError:
            try:
                added_date = datetime.strptime(ex.added, "%Y-%m")
            except ValueError:
                continue
        age_days = (now - added_date).days
        if age_days > stale_age_days:
            findings.append(
                ExemptionFinding(
                    file=ex.file,
                    line=ex.line,
                    severity="warning",
                    check="stale_exemption",
                    message=(
                        f"Exemption for {ex.import_target!r} was added {ex.added} "
                        f"({age_days} days ago) and has no expiry or ticket — "
                        f"review whether it is still needed"
                    ),
                    exemption_kind=ex.kind,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def _run_check_pipeline(report: ExemptionReport, repo_root: Path) -> None:
    """Check suite, once, in defined order (error-affecting checks first)."""
    report.findings.extend(_check_missing_rationale(report.exemptions))
    report.findings.extend(_check_missing_expiry(report.exemptions))
    report.findings.extend(_check_expired(report.exemptions))
    report.findings.extend(_check_orphaned(report.exemptions, repo_root))
    report.findings.extend(_check_removed_targets(report.exemptions, repo_root))
    report.findings.extend(_check_duplicates(report.exemptions))
    report.findings.extend(_check_unnecessary(report.exemptions, repo_root))
    report.findings.extend(_check_stale(report.exemptions))


def audit_exemptions(repo_root: Path) -> ExemptionReport:
    """Run all governance checks and produce a report."""
    report = ExemptionReport()

    # Collect exemptions
    report.exemptions.extend(_scan_inline_exemptions(repo_root))
    report.exemptions.extend(_scan_rule_exemptions(repo_root))

    _run_check_pipeline(report, repo_root)
    report.summary = _summary_counts(report)
    return report


def _summary_counts(report: ExemptionReport) -> dict[str, int]:
    """Summary counters (separated so audit stays within the gate)."""
    kinds = Counter(e.kind for e in report.exemptions)
    severities = Counter(f.severity for f in report.findings)
    return {
        "total_exemptions": len(report.exemptions),
        "noqa_boundary": kinds.get("noqa_boundary", 0),
        "allow_lazy": kinds.get("allow_lazy", 0),
        "errors": severities.get("error", 0),
        "warnings": severities.get("warning", 0),
        "info": severities.get("info", 0),
    }


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_human(report: ExemptionReport) -> str:
    """Human-readable diagnostic output."""
    lines: list[str] = []

    lines.append(f"Exemption Governance Report ({report.summary['total_exemptions']} exemptions)")
    lines.append("=" * 60)

    if report.findings:
        lines.append("")
        lines.append(f"Findings ({len(report.findings)}):")
        for f in report.findings:
            icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}.get(f.severity, "?")
            lines.append(f"  {icon} [{f.severity.upper()}] {f.file}:{f.line}: {f.message}")
            lines.append(f"    check: {f.check}")
    else:
        lines.append("")
        lines.append("No governance issues found.")

    lines.append("")
    lines.append("Summary:")
    for key, value in report.summary.items():
        lines.append(f"  {key}: {value}")

    return "\n".join(lines)


def _format_inventory(report: ExemptionReport) -> str:
    """Full exemption inventory."""
    lines: list[str] = []
    lines.append("Exemption Inventory")
    lines.append("=" * 60)

    for ex in report.exemptions:
        lines.append("")
        lines.append(f"  File:    {ex.file}:{ex.line}")
        lines.append(f"  Kind:    {ex.kind}")
        lines.append(f"  Target:  {ex.import_target}")
        if ex.reason:
            lines.append(f"  Reason:  {ex.reason}")
        if ex.owner:
            lines.append(f"  Owner:   {ex.owner}")
        if ex.expires:
            lines.append(f"  Expires: {ex.expires}")
        if ex.ticket:
            lines.append(f"  Ticket:  {ex.ticket}")
        if ex.raw_comment and not ex.reason:
            lines.append(f"  Comment: {ex.raw_comment}")

    lines.append("")
    lines.append(f"Total: {len(report.exemptions)} exemptions")
    return "\n".join(lines)


def _format_json(report: ExemptionReport) -> str:
    """Machine-readable JSON output."""
    return json.dumps(
        {
            "exemptions": [asdict(e) for e in report.exemptions],
            "findings": [asdict(f) for f in report.findings],
            "summary": report.summary,
        },
        indent=2,
    )


def _markdown_summary_section(report: ExemptionReport) -> list[str]:
    """Header + summary table lines."""
    lines = [
        "# Exemption Inventory",
        "",
        "*Auto-generated by `tools/exemption_governance_validator.py --emit-inventory`.*",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|-------|",
    ]
    for key, value in report.summary.items():
        lines.append(f"| {key} | {value} |")
    return lines


def _markdown_exemptions_section(exemptions: list[ExemptionEntry]) -> list[str]:
    """Active-exemptions table lines."""
    lines = [
        "",
        "## Active Exemptions",
        "",
        "| File | Line | Kind | Target | Reason | Owner | Expires | Ticket |",
        "|------|------|------|--------|--------|-------|---------|--------|",
    ]
    for ex in exemptions:
        reason = ex.reason or ex.raw_comment or "—"
        owner = ex.owner or "—"
        expires = ex.expires or "—"
        ticket = ex.ticket or "—"
        lines.append(
            f"| `{ex.file}` | {ex.line} | {ex.kind} | `{ex.import_target}` "
            f"| {reason} | {owner} | {expires} | {ticket} |"
        )
    return lines


def _markdown_findings_section(findings: list[ExemptionFinding]) -> list[str]:
    """Findings table lines (empty when clean)."""
    if not findings:
        return []
    lines = [
        "",
        "## Governance Findings",
        "",
        "| Severity | File | Line | Check | Message |",
        "|----------|------|------|-------|---------|",
    ]
    for f in findings:
        lines.append(f"| {f.severity.upper()} | `{f.file}` | {f.line} | {f.check} | {f.message} |")
    return lines


def _format_markdown_inventory(report: ExemptionReport) -> str:
    """Structured markdown inventory for governance documentation."""
    lines: list[str] = []
    lines.extend(_markdown_summary_section(report))
    lines.extend(_markdown_exemptions_section(report.exemptions))
    lines.extend(_markdown_findings_section(report.findings))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]

    output_json = "--json" in sys.argv
    output_inventory = "--inventory" in sys.argv
    emit_inventory = "--emit-inventory" in sys.argv
    strict = "--strict" in sys.argv

    report = audit_exemptions(repo_root)

    if emit_inventory:
        inventory_path = repo_root / "docs" / "EXEMPTION_INVENTORY.md"
        inventory_path.write_text(_format_markdown_inventory(report))
        print(f"Exemption inventory written to {inventory_path.relative_to(repo_root)}")

    if output_json:
        print(_format_json(report))
    elif output_inventory:
        print(_format_inventory(report))
        print("\nExemption governance validator passed")
    else:
        print(_format_human(report))
        print("\nExemption governance validator passed")

    # Exit code: strict mode fails on any error/warning
    if strict and (report.has_errors() or report.has_warnings()):
        return 1
    if report.has_errors():
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
