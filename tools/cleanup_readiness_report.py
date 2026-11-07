"""Cleanup readiness report — aggregate validator evidence into actionable candidates.

Collects outputs from:
  * orphan_module_detector (dead modules)
  * bridge_detector (removable bridges)
  * exemption_governance_validator (stale/expired exemptions)
  * adr_linkage_validator (stale references)

Classifies each candidate with:
  * evidence_source — which validator detected it
  * justification — why it's a cleanup candidate
  * rollback_simplicity — trivial/simple/complex
  * risk — low/medium/high
  * dependency_visibility — how easy to assess impact

Usage:
    python tools/cleanup_readiness_report.py          # human-readable
    python tools/cleanup_readiness_report.py --json   # machine-readable
    python tools/cleanup_readiness_report.py --emit   # write docs/CLEANUP_READINESS.md

Enforcement class: ADVISORY (never hard-fail)
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CleanupCandidate:
    """One potential cleanup target with evidence."""

    target: str  # Module, file, or exemption identifier
    category: str  # "orphan_module" | "stale_exemption" | "dead_bridge" | "stale_reference"
    evidence_source: str  # Validator that detected it
    justification: str  # Why this is a candidate
    rollback_simplicity: str  # "trivial" | "simple" | "complex"
    risk: str  # "low" | "medium" | "high"
    dependency_visibility: str  # "clear" | "uncertain" | "opaque"
    confidence: str  # "high" | "medium" | "low"


@dataclass
class CleanupReport:
    """Aggregated cleanup readiness report."""

    candidates: list[CleanupCandidate] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


def _collect_orphan_candidates(repo_root: Path) -> list[CleanupCandidate]:
    """Collect cleanup candidates from orphan module detector."""
    candidates: list[CleanupCandidate] = []
    try:
        sys.path.insert(0, str(repo_root / "tools"))
        from orphan_module_detector import detect_orphans

        report = detect_orphans(repo_root)
    except ImportError:
        return candidates
    finally:
        if str(repo_root / "tools") in sys.path:
            sys.path.remove(str(repo_root / "tools"))

    for orphan in report.candidates:
        risk = "low" if orphan.confidence == "high" else "medium"
        candidates.append(
            CleanupCandidate(
                target=orphan.file,
                category="orphan_module",
                evidence_source="orphan_module_detector",
                justification=orphan.reason,
                rollback_simplicity="trivial",
                risk=risk,
                dependency_visibility="clear" if orphan.confidence == "high" else "uncertain",
                confidence=orphan.confidence,
            )
        )
    return candidates


def _collect_exemption_candidates(repo_root: Path) -> list[CleanupCandidate]:
    """Collect cleanup candidates from exemption governance validator."""
    candidates: list[CleanupCandidate] = []
    try:
        sys.path.insert(0, str(repo_root / "tools"))
        from exemption_governance_validator import audit_exemptions

        report = audit_exemptions(repo_root)
    except ImportError:
        return candidates
    finally:
        if str(repo_root / "tools") in sys.path:
            sys.path.remove(str(repo_root / "tools"))

    for finding in report.findings:
        if finding.check in ("expired_exemption", "unnecessary_exemption", "orphaned_file"):
            candidates.append(
                CleanupCandidate(
                    target=f"{finding.file}:{finding.line}",
                    category="stale_exemption",
                    evidence_source="exemption_governance_validator",
                    justification=finding.message,
                    rollback_simplicity="trivial",
                    risk="low",
                    dependency_visibility="clear",
                    confidence="high" if finding.check == "expired_exemption" else "medium",
                )
            )
    return candidates


def _collect_bridge_candidates(repo_root: Path) -> list[CleanupCandidate]:
    """Collect cleanup candidates from bridge detector."""
    candidates: list[CleanupCandidate] = []
    try:
        sys.path.insert(0, str(repo_root / "tools"))
        from bridge_detector import detect_bridges

        report = detect_bridges(repo_root)
    except ImportError:
        return candidates
    finally:
        if str(repo_root / "tools") in sys.path:
            sys.path.remove(str(repo_root / "tools"))

    for bridge in report.bridges:
        if bridge.classification in ("likely_dead", "removable"):
            candidates.append(
                CleanupCandidate(
                    target=f"{bridge.file}:{bridge.line}",
                    category="dead_bridge",
                    evidence_source="bridge_detector",
                    justification=bridge.evidence,
                    rollback_simplicity="simple"
                    if bridge.classification == "removable"
                    else "complex",
                    risk="medium",
                    dependency_visibility="uncertain",
                    confidence="high" if bridge.classification == "removable" else "medium",
                )
            )
    return candidates


def _collect_stale_references(repo_root: Path) -> list[CleanupCandidate]:
    """Collect cleanup candidates from ADR linkage validator."""
    candidates: list[CleanupCandidate] = []
    try:
        sys.path.insert(0, str(repo_root / "tools"))
        from adr_linkage_validator import validate_adr_linkage

        report = validate_adr_linkage(repo_root)
    except ImportError:
        return candidates
    finally:
        if str(repo_root / "tools") in sys.path:
            sys.path.remove(str(repo_root / "tools"))

    for finding in report.findings:
        # A configured scan path that does not exist is a validator setup
        # error, not a stale reference to clean up (2026-10-18).
        if finding.check == "missing_scan_path":
            continue
        if finding.severity in ("error", "warning"):
            candidates.append(
                CleanupCandidate(
                    target=f"{finding.file}:{finding.line}",
                    category="stale_reference",
                    evidence_source="adr_linkage_validator",
                    justification=finding.message,
                    rollback_simplicity="trivial",
                    risk="low",
                    dependency_visibility="clear",
                    confidence="medium",
                )
            )
    return candidates


def generate_report(repo_root: Path) -> CleanupReport:
    """Run all collectors and produce aggregated report."""
    report = CleanupReport()

    report.candidates.extend(_collect_orphan_candidates(repo_root))
    report.candidates.extend(_collect_exemption_candidates(repo_root))
    report.candidates.extend(_collect_bridge_candidates(repo_root))
    report.candidates.extend(_collect_stale_references(repo_root))

    # Build summary
    report.summary = {
        "total_candidates": len(report.candidates),
        "orphan_modules": sum(1 for c in report.candidates if c.category == "orphan_module"),
        "stale_exemptions": sum(1 for c in report.candidates if c.category == "stale_exemption"),
        "dead_bridges": sum(1 for c in report.candidates if c.category == "dead_bridge"),
        "stale_references": sum(1 for c in report.candidates if c.category == "stale_reference"),
        "high_confidence": sum(1 for c in report.candidates if c.confidence == "high"),
        "low_risk": sum(1 for c in report.candidates if c.risk == "low"),
        "trivial_rollback": sum(1 for c in report.candidates if c.rollback_simplicity == "trivial"),
    }

    return report


def _format_human(report: CleanupReport) -> str:
    """Human-readable output."""
    lines: list[str] = []
    lines.append("Cleanup Readiness Report")
    lines.append("=" * 60)
    lines.append("")
    lines.append("Summary:")
    for key, value in report.summary.items():
        lines.append(f"  {key}: {value}")

    if report.candidates:
        lines.append("")
        lines.append("Candidates:")
        for c in report.candidates:
            icon = {"high": "●", "medium": "◐", "low": "○"}.get(c.confidence, "?")
            lines.append(f"  {icon} [{c.category}] {c.target}")
            lines.append(
                f"    Risk: {c.risk} | Rollback: {c.rollback_simplicity} | Confidence: {c.confidence}"
            )
            lines.append(f"    Justification: {c.justification}")
    else:
        lines.append("")
        lines.append("No cleanup candidates found.")

    return "\n".join(lines)


def _format_json(report: CleanupReport) -> str:
    """Machine-readable JSON output."""
    return json.dumps(
        {
            "candidates": [asdict(c) for c in report.candidates],
            "summary": report.summary,
        },
        indent=2,
    )


def _format_markdown(report: CleanupReport) -> str:
    """Markdown report for documentation."""
    lines: list[str] = []
    lines.append("# Cleanup Readiness Report")
    lines.append("")
    lines.append("*Auto-generated by `tools/cleanup_readiness_report.py --emit`.*")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    for key, value in report.summary.items():
        lines.append(f"| {key} | {value} |")
    lines.append("")

    # Group by category
    categories = {}
    for c in report.candidates:
        categories.setdefault(c.category, []).append(c)

    for category, items in sorted(categories.items()):
        title = category.replace("_", " ").title()
        lines.append(f"## {title} ({len(items)})")
        lines.append("")
        lines.append("| Target | Risk | Rollback | Confidence | Justification |")
        lines.append("|--------|------|----------|------------|---------------|")
        for c in items:
            target = f"`{c.target}`"
            lines.append(
                f"| {target} | {c.risk} | {c.rollback_simplicity} | {c.confidence} | {c.justification} |"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]

    output_json = "--json" in sys.argv
    emit = "--emit" in sys.argv

    report = generate_report(repo_root)

    if emit:
        md_path = repo_root / "docs" / "CLEANUP_READINESS.md"
        md_path.write_text(_format_markdown(report))
        print(f"Cleanup readiness report written to {md_path.relative_to(repo_root)}")

    if output_json:
        print(_format_json(report))
    else:
        print(_format_human(report))

    # Always advisory — never hard-fail
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
