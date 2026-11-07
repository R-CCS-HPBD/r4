"""Unified skill scanner — SkillSpector with graceful fallback.

This module provides a single ``scan_skill_directory`` entry point that
uses NVIDIA's SkillSpector (https://github.com/NVIDIA/SkillSpector)
when available, and falls back to the built-in ``SkillScanner`` from
``r4_contracts.security`` otherwise.

SkillSpector detects 64 vulnerability patterns across 16 categories
(prompt injection, data exfiltration, privilege escalation, supply
chain, excessive agency, output handling, system prompt leakage, memory
poisoning, tool misuse, rogue agent, trigger abuse, behavioral AST,
taint tracking, YARA signatures, MCP least privilege, and MCP tool
poisoning). It is the recommended scanner for any skill installed from
an external source.

When SkillSpector is not installed, the fallback ``SkillScanner``
provides basic shell-injection, path-traversal, and prompt-injection
checks — sufficient for trusted internal skills but not for
third-party ones.

Installation::

    pip install skillspector
    # or
    uv tool install git+https://github.com/NVIDIA/skillspector.git
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# -- Severity constants -----------------------------------------------------

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"

BLOCKING_SEVERITIES = {SEVERITY_CRITICAL, SEVERITY_HIGH}


# -- Unified result types ---------------------------------------------------


@dataclass
class ScanFinding:
    """A single finding from a skill scan."""

    severity: str
    rule_id: str
    message: str
    location: str = ""
    confidence: float = 0.0


@dataclass
class ScanReport:
    """Unified scan report from either SkillSpector or SkillScanner."""

    scanner: str  # "skillspector" | "skillscanner" | "none"
    skill_path: str
    risk_score: int = 0
    risk_severity: str = "LOW"
    risk_recommendation: str = "SAFE"
    findings: list[ScanFinding] = field(default_factory=list)
    error: str | None = None

    @property
    def has_blocking_findings(self) -> bool:
        """True if any finding has CRITICAL or HIGH severity."""
        return any(f.severity in BLOCKING_SEVERITIES for f in self.findings)

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def is_safe(self) -> bool:
        """True if no blocking findings and risk score is acceptable."""
        return not self.has_blocking_findings and self.risk_score <= 50


# -- SkillSpector detection -------------------------------------------------


def is_skillspector_available() -> bool:
    """Check if SkillSpector is installed and importable."""
    try:
        import skillspector  # noqa: F401

        return True
    except ImportError:
        return False


def _scan_with_skillspector(skill_dir: Path) -> ScanReport:
    """Run SkillSpector scan on a skill directory.

    Uses static-only analysis (no LLM) by default for speed and
    offline operation. Set ``SKILLSPECTOR_USE_LLM=1`` to enable
    LLM semantic analysis.
    """
    report = ScanReport(
        scanner="skillspector",
        skill_path=str(skill_dir),
    )

    try:
        from skillspector import graph
    except ImportError as exc:
        report.scanner = "skillscanner"
        report.error = str(exc)
        return report

    use_llm = (
        json.loads("true")
        if False
        else __import__("os").environ.get("SKILLSPECTOR_USE_LLM", "0") == "1"
    )

    try:
        result = graph.invoke(
            {
                "input_path": str(skill_dir),
                "output_format": "json",
                "use_llm": use_llm,
            }
        )
    except Exception as exc:  # noqa: BLE001 - scanner should not crash CLI
        report.error = f"SkillSpector failed: {exc}"
        logger.warning("SkillSpector scan failed: %s", exc)
        return report

    # Parse SkillSpector JSON result
    if isinstance(result, str):
        result = json.loads(result)

    report.risk_score = result.get("risk_score", 0)
    report.risk_severity = result.get("risk_severity", "LOW")
    report.risk_recommendation = result.get("risk_recommendation", "SAFE")

    for finding in result.get("filtered_findings", result.get("findings", [])):
        report.findings.append(
            ScanFinding(
                severity=finding.get("severity", "LOW").upper(),
                rule_id=finding.get("rule_id", finding.get("id", "unknown")),
                message=finding.get("message", finding.get("description", "")),
                location=finding.get("location", ""),
                confidence=finding.get("confidence", 0.0),
            )
        )

    return report


def _scan_with_skillscanner(skill_dir: Path) -> ScanReport:
    """Fallback scan using the built-in SkillScanner."""
    report = ScanReport(
        scanner="skillscanner",
        skill_path=str(skill_dir),
    )

    try:
        from r4_contracts.security import SkillScanner
    except ImportError as exc:
        report.scanner = "none"
        report.error = str(exc)
        return report

    skill_md_path = skill_dir / "SKILL.md"
    config_path = skill_dir / "config.json"

    md_text = ""
    if skill_md_path.exists():
        md_text = skill_md_path.read_text(encoding="utf-8", errors="replace")

    cfg_data: dict[str, Any] = {}
    if config_path.exists():
        try:
            cfg_data = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    result = SkillScanner().scan_skill(cfg_data, md_text)

    for finding in result.findings:
        report.findings.append(
            ScanFinding(
                severity=finding.severity.value.upper(),
                rule_id=finding.category,
                message=finding.message,
                location=finding.location,
            )
        )

    # Calculate a simple risk score
    score = 0
    for f in report.findings:
        if f.severity == SEVERITY_CRITICAL:
            score += 50
        elif f.severity == SEVERITY_HIGH:
            score += 25
        elif f.severity == SEVERITY_MEDIUM:
            score += 10
        elif f.severity == SEVERITY_LOW:
            score += 5
    report.risk_score = min(score, 100)

    if report.risk_score >= 81:
        report.risk_severity = SEVERITY_CRITICAL
        report.risk_recommendation = "DO NOT INSTALL"
    elif report.risk_score >= 51:
        report.risk_severity = SEVERITY_HIGH
        report.risk_recommendation = "DO NOT INSTALL"
    elif report.risk_score >= 21:
        report.risk_severity = SEVERITY_MEDIUM
        report.risk_recommendation = "CAUTION"
    else:
        report.risk_severity = SEVERITY_LOW
        report.risk_recommendation = "SAFE"

    return report


# -- Public API --------------------------------------------------------------


def scan_skill_directory(skill_dir: Path) -> ScanReport:
    """Scan a skill directory for security issues.

    Uses SkillSpector if available, otherwise falls back to the
    built-in SkillScanner.

    Args:
        skill_dir: Path to the skill directory to scan

    Returns:
        ScanReport with findings and risk assessment
    """
    if not skill_dir.is_dir():
        return ScanReport(
            scanner="none",
            skill_path=str(skill_dir),
            error=f"Not a directory: {skill_dir}",
        )

    if is_skillspector_available():
        report = _scan_with_skillspector(skill_dir)
        if report.error is None:
            return report
        # SkillSpector failed, fall through to fallback
        logger.warning(
            "SkillSpector unavailable (%s), falling back to SkillScanner",
            report.error,
        )

    return _scan_with_skillscanner(skill_dir)


def format_scan_report(report: ScanReport) -> str:
    """Format a ScanReport as a human-readable string."""
    lines = [
        f"SkillSpector Security Report  (scanner: {report.scanner})",
        "",
        f"  Skill:          {report.skill_path}",
        f"  Risk Score:     {report.risk_score}/100",
        f"  Severity:       {report.risk_severity}",
        f"  Recommendation: {report.risk_recommendation}",
    ]

    if report.error:
        lines.append(f"  Error:          {report.error}")

    if report.findings:
        lines.append("")
        lines.append(f"  Issues ({report.finding_count})")
        lines.append("")
        for f in report.findings:
            lines.append(f"  {f.severity}: {f.rule_id}")
            if f.location:
                lines.append(f"    Location: {f.location}")
            lines.append(f"    {f.message}")
            if f.confidence > 0:
                lines.append(f"    Confidence: {f.confidence}%")
            lines.append("")
    else:
        lines.append("")
        lines.append("  No issues found.")

    return "\n".join(lines)


def scan_skill_directory_cli(skill_dir: Path, json_output: bool = False) -> int:
    """CLI entry point for scanning a skill directory.

    Returns exit code: 0 if safe, 1 if blocking findings, 2 on error.
    """
    report = scan_skill_directory(skill_dir)

    if json_output:
        output = {
            "scanner": report.scanner,
            "skill_path": report.skill_path,
            "risk_score": report.risk_score,
            "risk_severity": report.risk_severity,
            "risk_recommendation": report.risk_recommendation,
            "findings": [
                {
                    "severity": f.severity,
                    "rule_id": f.rule_id,
                    "message": f.message,
                    "location": f.location,
                    "confidence": f.confidence,
                }
                for f in report.findings
            ],
        }
        if report.error:
            output["error"] = report.error
        print(json.dumps(output, indent=2))
    else:
        print(format_scan_report(report))

    if report.error and report.scanner == "none":
        return 2
    if report.has_blocking_findings:
        return 1
    return 0
