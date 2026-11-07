#!/usr/bin/env python3
"""Security compat scanner — detect deprecated or weak security patterns.

Scans for:
  - Unsalted hash usage (SHA-256, MD5 without salt)
  - Hardcoded secrets or tokens
  - Deprecated auth patterns
  - Weak cryptographic operations

Usage:
    python tools/security_compat_scanner.py          # Human summary
    python tools/security_compat_scanner.py --json   # JSON output

Enforcement class: ADVISORY (reports only, no hard failures)
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UMRS_SRC = REPO_ROOT / "umrs" / "src" / "umrs"

# Files excluded from scanning (test fixtures, docs)
EXCLUDED_PATTERNS = {"__pycache__", ".pyc", "test_", "conftest", "tests/"}


@dataclass(frozen=True)
class SecurityFinding:
    """One security-relevant pattern found in source."""

    file: str
    line: int
    severity: str  # "info" | "warning" | "critical"
    category: str  # "weak_hash" | "hardcoded_secret" | "deprecated_auth" | "weak_crypto"
    detail: str
    remediation: str


@dataclass
class SecurityReport:
    """Complete security compat scan report."""

    findings: list[SecurityFinding] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        by_severity: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for f in self.findings:
            by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
            by_category[f.category] = by_category.get(f.category, 0) + 1
        return {
            "total_findings": len(self.findings),
            "by_severity": by_severity,
            "by_category": by_category,
            "unique_files": len({f.file for f in self.findings}),
        }


# Detection rules: (pattern, category, severity, detail_template, remediation)
DETECTION_RULES: list[tuple[re.Pattern, str, str, str, str]] = [
    (
        re.compile(
            r"hashlib\.(sha256|md5)\("
            r"(?:.*(?:password|token|secret|credential|key).*)"
            r"\.encode\(\)\)\.hexdigest\(\)"
        ),
        "weak_hash",
        "warning",
        "Unsalted hash of auth-sensitive value — vulnerable to rainbow tables",
        "Use PBKDF2, scrypt, or bcrypt with per-entry salt",
    ),
    (
        re.compile(
            r"(?:password|secret|api_key)\s*=\s*\"[a-zA-Z0-9_-]{8,}\"",
            re.IGNORECASE,
        ),
        "hardcoded_secret",
        "critical",
        "Potential hardcoded secret: {match}",
        "Move to environment variable or secret store",
    ),
    (
        re.compile(r"hashlib\.md5\("),
        "weak_crypto",
        "info",
        "MD5 used (acceptable for checksums, not for auth)",
        "Verify this is not used for authentication or integrity verification",
    ),
]

# Patterns that indicate a line is example/docstring/template, not real code
FALSE_POSITIVE_PATTERNS = [
    re.compile(r"\{[a-z_]+\}"),  # template variables like {project_id}
    re.compile(r"\"my-secret\""),  # obvious placeholder
    re.compile(r"example|docstring|demo", re.IGNORECASE),
]


def _rel_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _is_excluded(filepath: Path) -> bool:
    name = filepath.name
    path_str = str(filepath)
    return (
        any(ex in name for ex in EXCLUDED_PATTERNS) or "tests/" in path_str or "/tests/" in path_str
    )


def scan_file(filepath: Path) -> list[SecurityFinding]:
    """Scan a single file for security patterns."""
    results = []
    rel = _rel_path(filepath)

    try:
        content = filepath.read_text()
    except (OSError, UnicodeDecodeError):
        return results

    for lineno, line in enumerate(content.splitlines(), 1):
        # Skip comments that document the issue (not code)
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue

        for pattern, category, severity, detail_tmpl, remediation in DETECTION_RULES:
            match = pattern.search(line)
            if match:
                # Skip false positives (templates, examples)
                if any(fp.search(line) for fp in FALSE_POSITIVE_PATTERNS):
                    continue
                matched_text = match.group(0)[:60]
                detail = detail_tmpl.format(match=matched_text)
                results.append(
                    SecurityFinding(
                        file=rel,
                        line=lineno,
                        severity=severity,
                        category=category,
                        detail=detail,
                        remediation=remediation,
                    )
                )

    return results


def run_scan() -> SecurityReport:
    """Scan UMRS source tree for security compat issues."""
    report = SecurityReport()

    for filepath in sorted(UMRS_SRC.rglob("*.py")):
        if _is_excluded(filepath):
            continue
        if "__pycache__" in str(filepath):
            continue
        report.findings.extend(scan_file(filepath))

    return report


def format_human(report: SecurityReport) -> str:
    lines = []
    lines.append("Security Compatibility Scanner")
    lines.append("=" * 66)

    s = report.summary
    lines.append(f"\nTotal findings:    {s['total_findings']}")
    lines.append(f"Unique files:      {s['unique_files']}")
    lines.append(f"By severity:       {s['by_severity']}")
    lines.append(f"By category:       {s['by_category']}")

    for severity in ("critical", "warning", "info"):
        items = [f for f in report.findings if f.severity == severity]
        if items:
            lines.append(f"\n{severity.upper()}:")
            lines.append("-" * 66)
            for f in items:
                lines.append(f"  {f.file}:{f.line}")
                lines.append(f"    {f.detail}")
                lines.append(f"    Remediation: {f.remediation}")

    if not report.findings:
        lines.append("\nNo security-relevant compat patterns found.")

    return "\n".join(lines)


def format_json(report: SecurityReport) -> str:
    data = {
        "summary": report.summary,
        "findings": [asdict(f) for f in report.findings],
    }
    return json.dumps(data, indent=2)


def main() -> int:
    use_json = "--json" in sys.argv

    report = run_scan()

    if use_json:
        print(format_json(report))
    else:
        print(format_human(report))

    # Fail only on critical findings
    critical = sum(1 for f in report.findings if f.severity == "critical")
    return 1 if critical else 0


if __name__ == "__main__":
    raise SystemExit(main())
