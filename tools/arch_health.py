#!/usr/bin/env python3
"""Architecture health diagnostic — single-command contributor health check.

Runs all enforcement validators and advisory detectors, then prints a
compact summary. Designed for quick local runs and CI artifact generation.

Usage:
    python tools/arch_health.py              # Human-readable summary
    python tools/arch_health.py --json       # JSON for CI artifacts
    python tools/arch_health.py --verbose    # Show individual findings
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Validators ordered by severity: hard-fail first, then advisory
VALIDATORS = [
    # (script_path, label, level)
    ("tools/import_boundary_validator.py", "Import Boundaries", "hard_fail"),
    ("tools/contracts_fence.py", "Contracts Fence", "hard_fail"),
    ("tools/config_drift_validator.py", "Config Drift", "hard_fail"),
    ("tools/data_authority_validator.py", "Data Authority", "hard_fail"),
    ("tools/stale_name_validator.py", "Stale Names", "hard_fail"),
    ("tools/schema_version_tracker.py", "Schema Versions", "hard_fail"),
    ("tools/dto_equivalence_check.py", "DTO Equivalence", "hard_fail"),
    ("tools/runtime_support_matrix.py", "Runtime Support Matrix", "hard_fail"),
    ("tools/adr_linkage_validator.py", "ADR Linkage", "advisory"),
    ("tools/orphan_module_detector.py", "Orphan Modules", "advisory"),
    ("tools/bridge_detector.py", "Bridge Detector", "advisory"),
    ("tools/check_metrics_coverage.py", "Metrics Coverage", "advisory"),
    ("tools/exemption_governance_validator.py", "Exemption Governance", "advisory"),
    ("tools/cleanup_readiness_report.py", "Cleanup Readiness", "informational"),
    ("tools/transport_boundary_audit.py", "Transport Boundary", "advisory"),
    ("tools/compat_retirement_tracker.py", "Compat Tracker", "informational"),
    ("tools/security_compat_scanner.py", "Security Scanner", "advisory"),
]


@dataclass
class ValidatorResult:
    label: str
    level: str
    passed: bool
    duration_ms: float
    output: str = ""
    error: str = ""


@dataclass
class HealthReport:
    results: list[ValidatorResult] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def hard_fail_passed(self) -> bool:
        return all(r.passed for r in self.results if r.level == "hard_fail")

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def summary_status(self) -> str:
        if self.hard_fail_passed and self.fail_count == 0:
            return "HEALTHY"
        if self.hard_fail_passed:
            return "WARNINGS"
        return "FAILING"


def run_validator(script_rel: str, label: str, level: str) -> ValidatorResult:
    """Run a single validator and capture result."""
    script_path = REPO_ROOT / script_rel
    if not script_path.exists():
        return ValidatorResult(
            label=label,
            level=level,
            passed=False,
            duration_ms=0.0,
            error=f"Script not found: {script_rel}",
        )

    start = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=60,
        )
        duration_ms = (time.monotonic() - start) * 1000
        return ValidatorResult(
            label=label,
            level=level,
            passed=result.returncode == 0,
            duration_ms=duration_ms,
            output=result.stdout.strip(),
            error=result.stderr.strip() if result.returncode != 0 else "",
        )
    except subprocess.TimeoutExpired:
        duration_ms = (time.monotonic() - start) * 1000
        return ValidatorResult(
            label=label,
            level=level,
            passed=False,
            duration_ms=duration_ms,
            error="Timeout (>60s)",
        )
    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        return ValidatorResult(
            label=label,
            level=level,
            passed=False,
            duration_ms=duration_ms,
            error=str(e),
        )


def run_health_check() -> HealthReport:
    """Run all validators and produce a health report."""
    report = HealthReport()
    overall_start = time.monotonic()

    for script_rel, label, level in VALIDATORS:
        result = run_validator(script_rel, label, level)
        report.results.append(result)

    report.total_duration_ms = (time.monotonic() - overall_start) * 1000
    return report


def format_human(report: HealthReport, verbose: bool = False) -> str:
    """Format report as human-readable text."""
    lines = []
    lines.append("Architecture Health Diagnostic")
    lines.append("=" * 66)
    lines.append("")

    # Status table
    lines.append(f"{'Validator':<28} {'Level':<12} {'Status':<8} {'Time':>8}")
    lines.append("-" * 66)

    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        time_str = f"{r.duration_ms:.0f}ms"
        lines.append(f"{r.label:<28} {r.level:<12} {status:<8} {time_str:>8}")

    lines.append("-" * 66)
    lines.append(
        f"Total: {report.pass_count} passed, {report.fail_count} failed "
        f"| {report.total_duration_ms:.0f}ms | Status: {report.summary_status}"
    )

    if verbose:
        failures = [r for r in report.results if not r.passed]
        if failures:
            lines.append("")
            lines.append("Failure Details:")
            lines.append("-" * 66)
            for r in failures:
                lines.append(f"\n[{r.label}]")
                if r.output:
                    lines.append(r.output)
                if r.error:
                    lines.append(f"  stderr: {r.error}")

    return "\n".join(lines)


def format_json(report: HealthReport) -> str:
    """Format report as JSON for CI artifacts."""
    data = {
        "status": report.summary_status,
        "passed": report.pass_count,
        "failed": report.fail_count,
        "total_duration_ms": round(report.total_duration_ms, 1),
        "hard_fail_ok": report.hard_fail_passed,
        "results": [
            {
                "label": r.label,
                "level": r.level,
                "passed": r.passed,
                "duration_ms": round(r.duration_ms, 1),
                "output": r.output if not r.passed else "",
                "error": r.error,
            }
            for r in report.results
        ],
    }
    return json.dumps(data, indent=2)


def main() -> int:
    args = sys.argv[1:]
    use_json = "--json" in args
    verbose = "--verbose" in args or "-v" in args

    report = run_health_check()

    if use_json:
        print(format_json(report))
    else:
        print(format_human(report, verbose=verbose))

    return 0 if report.hard_fail_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
