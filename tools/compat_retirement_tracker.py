#!/usr/bin/env python3
"""Backward-compatibility retirement tracker — scan for compat markers and
assess retirement readiness.

Finds comments/code patterns like:
  - ``# backward compat``
  - ``# legacy``
  - ``# deprecated``
  - ``DEPRECATED``

Classifies each by risk and retirement readiness.

Usage:
    python tools/compat_retirement_tracker.py          # Human summary
    python tools/compat_retirement_tracker.py --json   # JSON output
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UMRS_SRC = REPO_ROOT / "umrs" / "src" / "umrs"

# Patterns that mark backward-compatibility code
COMPAT_PATTERNS = [
    (re.compile(r"#\s*backward[\s-]*compat", re.IGNORECASE), "backward_compat"),
    (re.compile(r"#\s*legacy\b", re.IGNORECASE), "legacy"),
    (re.compile(r"#\s*DEPRECATED\b|DEPRECATED\s*[-—]", re.IGNORECASE), "deprecated"),
    (re.compile(r"#\s*kept\s+for\s+compat", re.IGNORECASE), "kept_for_compat"),
    (re.compile(r"#\s*fallback\s+.*compat", re.IGNORECASE), "fallback_compat"),
]

# Security-sensitive patterns that elevate risk
SECURITY_PATTERNS = [
    re.compile(r"sha[-_]?256|md5|unsalted|plaintext", re.IGNORECASE),
    re.compile(r"password|secret|credential|token", re.IGNORECASE),
]


@dataclass(frozen=True)
class CompatMarker:
    """One backward-compat marker found in source."""

    file: str
    line: int
    kind: str  # backward_compat | legacy | deprecated | kept_for_compat
    content: str  # The line content
    risk: str  # low | medium | high
    security_relevant: bool
    detail: str


@dataclass
class CompatReport:
    """Complete backward-compat retirement report."""

    markers: list[CompatMarker] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        by_kind: dict[str, int] = {}
        by_risk: dict[str, int] = {}
        for m in self.markers:
            by_kind[m.kind] = by_kind.get(m.kind, 0) + 1
            by_risk[m.risk] = by_risk.get(m.risk, 0) + 1
        return {
            "total_markers": len(self.markers),
            "by_kind": by_kind,
            "by_risk": by_risk,
            "security_relevant": sum(1 for m in self.markers if m.security_relevant),
            "unique_files": len({m.file for m in self.markers}),
        }


def _rel_path(path: Path) -> str:
    """Return path relative to repo root."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _assess_risk(line_content: str, kind: str) -> tuple[str, bool]:
    """Assess risk level and security relevance of a compat marker."""
    security_relevant = any(p.search(line_content) for p in SECURITY_PATTERNS)

    if security_relevant:
        return "high", True
    if kind == "deprecated":
        return "medium", False
    return "low", False


def scan_file(filepath: Path) -> list[CompatMarker]:
    """Scan a single file for backward-compat markers."""
    results = []
    rel = _rel_path(filepath)

    try:
        content = filepath.read_text()
    except (OSError, UnicodeDecodeError):
        return results

    for lineno, line in enumerate(content.splitlines(), 1):
        for pattern, kind in COMPAT_PATTERNS:
            if pattern.search(line):
                risk, sec = _assess_risk(line, kind)
                results.append(
                    CompatMarker(
                        file=rel,
                        line=lineno,
                        kind=kind,
                        content=line.strip()[:120],
                        risk=risk,
                        security_relevant=sec,
                        detail=kind,
                    )
                )
                break  # Only match first pattern per line

    return results


def run_scan() -> CompatReport:
    """Scan entire UMRS source tree for compat markers."""
    report = CompatReport()

    for filepath in sorted(UMRS_SRC.rglob("*")):
        if "__pycache__" in str(filepath):
            continue
        if filepath.suffix not in (".py", ".sh", ".slurm", ".yaml", ".yml"):
            continue
        report.markers.extend(scan_file(filepath))

    return report


def format_human(report: CompatReport) -> str:
    """Format report as human-readable text."""
    lines = []
    lines.append("Backward-Compatibility Retirement Tracker")
    lines.append("=" * 66)

    s = report.summary
    lines.append(f"\nTotal markers:       {s['total_markers']}")
    lines.append(f"Unique files:        {s['unique_files']}")
    lines.append(f"Security-relevant:   {s['security_relevant']}")
    lines.append(f"By kind:             {s['by_kind']}")
    lines.append(f"By risk:             {s['by_risk']}")

    # Security-relevant items first
    sec_markers = [m for m in report.markers if m.security_relevant]
    if sec_markers:
        lines.append("\nSECURITY-RELEVANT (review priority):")
        lines.append("-" * 66)
        for m in sec_markers:
            lines.append(f"  [{m.risk}] {m.file}:{m.line}")
            lines.append(f"        {m.content}")

    # Then by risk
    for risk_level in ("medium", "low"):
        risk_markers = [
            m for m in report.markers if m.risk == risk_level and not m.security_relevant
        ]
        if risk_markers:
            lines.append(f"\n{risk_level.upper()} risk:")
            lines.append("-" * 66)
            for m in risk_markers:
                lines.append(f"  [{m.kind}] {m.file}:{m.line}")
                lines.append(f"        {m.content}")

    return "\n".join(lines)


def format_json(report: CompatReport) -> str:
    """Format report as JSON."""
    data = {
        "summary": report.summary,
        "markers": [asdict(m) for m in report.markers],
    }
    return json.dumps(data, indent=2)


def main() -> int:
    use_json = "--json" in sys.argv

    report = run_scan()

    if use_json:
        print(format_json(report))
    else:
        print(format_human(report))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
