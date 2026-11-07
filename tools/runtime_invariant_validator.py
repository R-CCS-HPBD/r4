#!/usr/bin/env python3
"""Runtime invariant validator.

Validates that the codebase enforces runtime zero-trust invariants:
container launch specifications must not pass durable credentials to
runtime containers, and must set required isolation variables.

This validator checks source files that construct container environment
variables or launch specifications — it does NOT inspect live containers.

Usage:
    python tools/runtime_invariant_validator.py
    python tools/runtime_invariant_validator.py --json

Enforcement class: advisory (promote to hard-fail after validation)
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Files that construct container environments or launch specs
LAUNCH_SPEC_GLOBS = [
    "umrs/src/umrs/orchestrator/*.py",
    "umrs/src/umrs/mini-rivault-llm/*.slurm",
    "umrs/src/umrs/mini-rivault-llm/*.sh",
    "deploy/singularity/*.def",
    "deploy/singularity/*.sh",
]

# Patterns that must NOT appear in container env construction
FORBIDDEN_CREDENTIAL_PATTERNS = [
    (r"UMRS_DB_DSN", "Postgres DSN must not be passed to runtime containers"),
    (r"MINIO_SECRET_KEY", "MinIO secret key must not be passed to runtime containers"),
    (r"MINIO_ACCESS_KEY", "MinIO access key must not be passed to runtime containers"),
    (r"JWT_SECRET", "JWT signing key must not be passed to runtime containers"),
    (r"ADMIN_TOKEN", "Admin tokens must not be passed to runtime containers"),
]

# Patterns that MUST appear in container env construction
REQUIRED_ENV_PATTERNS = [
    (r"TRANSFORMERS_OFFLINE", "Container launch must set TRANSFORMERS_OFFLINE=1"),
]

# Files where credential references are legitimate (not container launch)
EXEMPT_PATHS = {
    "umrs/src/umrs/orchestrator/site_config.py",  # reads site config, not container env
    "umrs/src/umrs/shared/auth.py",  # admin token validation
    "umrs/src/umrs/shared/storage/s3.py",  # server-side S3 client
    "umrs/src/umrs/api/",  # server-side API handlers
    "umrs/src/umrs/server/",  # server-side runtime
    "gateway/",  # gateway reads its own secrets
    "r4/",  # client-side config
    "tools/",  # validators and tools
    "tests/",  # test fixtures
}


@dataclass(frozen=True)
class RuntimeInvariantFinding:
    """One runtime invariant validation finding."""

    file: str
    line: int
    pattern: str
    message: str

    def format(self) -> str:
        return f"{self.file}:{self.line}: {self.message} (matched: {self.pattern})"


@dataclass
class RuntimeInvariantReport:
    """Complete runtime invariant validation report."""

    files_scanned: int = 0
    forbidden_findings: list[RuntimeInvariantFinding] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)

    @property
    def finding_count(self) -> int:
        return len(self.forbidden_findings) + len(self.missing_requirements)


def _is_exempt(path: Path, repo_root: Path) -> bool:
    """Check if a file is exempt from credential scanning."""
    rel = str(path.relative_to(repo_root))
    return any(rel.startswith(exempt) for exempt in EXEMPT_PATHS)


def _scan_for_forbidden_creds(
    path: Path,
    repo_root: Path,
) -> list[RuntimeInvariantFinding]:
    """Scan a file for forbidden credential patterns in container env construction."""
    findings: list[RuntimeInvariantFinding] = []
    rel_path = str(path.relative_to(repo_root))

    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return findings

    for line_num, line in enumerate(lines, start=1):
        # Skip comments
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        for pattern, message in FORBIDDEN_CREDENTIAL_PATTERNS:
            if re.search(pattern, line):
                findings.append(
                    RuntimeInvariantFinding(
                        file=rel_path,
                        line=line_num,
                        pattern=pattern,
                        message=message,
                    )
                )

    return findings


def _check_required_env(
    launch_files: list[Path],
    repo_root: Path,
) -> list[str]:
    """Check that required env vars appear somewhere in launch specifications."""
    missing: list[str] = []

    for pattern, message in REQUIRED_ENV_PATTERNS:
        found = False
        for path in launch_files:
            try:
                content = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            if re.search(pattern, content):
                found = True
                break
        if not found:
            missing.append(message)

    return missing


def validate_runtime_invariants(repo_root: Path = REPO_ROOT) -> RuntimeInvariantReport:
    """Run runtime invariant validation against the repository."""
    report = RuntimeInvariantReport()

    # Collect launch specification files
    launch_files: list[Path] = []
    for glob_pattern in LAUNCH_SPEC_GLOBS:
        launch_files.extend(repo_root.glob(glob_pattern))

    # Filter to only files that construct container environments
    container_env_files = [f for f in launch_files if not _is_exempt(f, repo_root)]
    report.files_scanned = len(container_env_files)

    # Check for forbidden credentials in container launch specs
    for path in container_env_files:
        report.forbidden_findings.extend(_scan_for_forbidden_creds(path, repo_root))

    # Check for required env vars across all launch files
    report.missing_requirements = _check_required_env(launch_files, repo_root)

    return report


def format_human(report: RuntimeInvariantReport) -> str:
    """Format report for humans."""
    lines = ["Runtime Invariant Validator", "=" * 66]
    lines.append(f"Files scanned:        {report.files_scanned}")
    lines.append(f"Forbidden findings:   {len(report.forbidden_findings)}")
    lines.append(f"Missing requirements: {len(report.missing_requirements)}")

    if report.forbidden_findings:
        lines.append("")
        lines.append("Forbidden credential patterns in container launch specs:")
        for finding in report.forbidden_findings:
            lines.append(f"  {finding.format()}")

    if report.missing_requirements:
        lines.append("")
        lines.append("Missing required environment variables:")
        for msg in report.missing_requirements:
            lines.append(f"  {msg}")

    if report.finding_count == 0:
        lines.append("")
        lines.append("Runtime invariant validator passed")

    return "\n".join(lines)


def format_json(report: RuntimeInvariantReport) -> str:
    """Format report as JSON."""
    return json.dumps(
        {
            "files_scanned": report.files_scanned,
            "forbidden_findings": [asdict(f) for f in report.forbidden_findings],
            "missing_requirements": report.missing_requirements,
            "passed": report.finding_count == 0,
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    report = validate_runtime_invariants()

    if args.json:
        print(format_json(report))
    else:
        print(format_human(report))

    return 0 if report.finding_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
