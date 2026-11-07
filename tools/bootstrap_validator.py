"""Bootstrap validator for airgap and pre-deployment checks (COR-003).

Validates that a deployment target has all prerequisites met before
launching UMRS and related services. Checks:
- Required environment variables
- Model path existence and permissions
- Storage directories and disk space
- Runtime availability (docker/podman/singularity)
- Port availability
- Database connectivity prerequisites

Usage:
    python tools/bootstrap_validator.py --profile hpc-full
    python tools/bootstrap_validator.py --profile dev-compose --check-ports
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CheckResult:
    """Result of a single validation check."""

    name: str
    status: str  # "pass", "fail", "warn", "skip"
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class ValidationReport:
    """Aggregated validation results."""

    profile: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.status == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c.status == "fail")

    @property
    def warnings(self) -> int:
        return sum(1 for c in self.checks if c.status == "warn")

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "ok": self.ok,
            "passed": self.passed,
            "failed": self.failed,
            "warnings": self.warnings,
            "checks": [c.to_dict() for c in self.checks],
        }


# ---- Profile definitions ----

PROFILE_REQUIRED_ENV: dict[str, list[str]] = {
    "hpc-full": [
        "UMRS_DB_URL",
        "UMRS_S3_ENDPOINT",
        "UMRS_S3_ACCESS_KEY",
        "UMRS_S3_SECRET_KEY",
        "LITELLM_MASTER_KEY",
        "VLLM_MODEL_PATH",
        "SLURM_PARTITION",
    ],
    "hpc-minimal": [
        "UMRS_DB_URL",
        "VLLM_MODEL_PATH",
        "SLURM_PARTITION",
    ],
    "k8s-cluster": [
        "UMRS_DB_URL",
        "UMRS_S3_ENDPOINT",
        "UMRS_S3_ACCESS_KEY",
        "UMRS_S3_SECRET_KEY",
        "LITELLM_MASTER_KEY",
    ],
    "dev-compose": [
        "UMRS_DB_URL",
    ],
}

PROFILE_RUNTIMES: dict[str, list[str]] = {
    "hpc-full": ["singularity", "podman"],
    "hpc-minimal": ["singularity"],
    "k8s-cluster": ["kubectl"],
    "dev-compose": ["docker"],
}

PROFILE_PORTS: dict[str, list[int]] = {
    "hpc-full": [8000, 8001, 11434],
    "hpc-minimal": [8000, 11434],
    "k8s-cluster": [8000, 8001],
    "dev-compose": [8000, 8001, 5432, 9000, 11434],
}

PROFILE_MODEL_PATHS: dict[str, str] = {
    "hpc-full": "VLLM_MODEL_PATH",
    "hpc-minimal": "VLLM_MODEL_PATH",
    "k8s-cluster": "",
    "dev-compose": "",
}


def check_env_vars(profile: str) -> list[CheckResult]:
    """Check required environment variables are set."""
    results = []
    required = PROFILE_REQUIRED_ENV.get(profile, [])
    for var in required:
        val = os.environ.get(var, "").strip()
        if val:
            results.append(
                CheckResult(
                    name=f"env:{var}",
                    status="pass",
                    detail=f"Set ({len(val)} chars)",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"env:{var}",
                    status="fail",
                    detail="Not set or empty",
                )
            )
    return results


def check_runtimes(profile: str) -> list[CheckResult]:
    """Check that required container runtimes are available."""
    results = []
    runtimes = PROFILE_RUNTIMES.get(profile, [])
    for rt in runtimes:
        path = shutil.which(rt)
        if path:
            results.append(
                CheckResult(
                    name=f"runtime:{rt}",
                    status="pass",
                    detail=path,
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"runtime:{rt}",
                    status="fail",
                    detail=f"{rt} not found in PATH",
                )
            )
    return results


def check_model_path(profile: str) -> list[CheckResult]:
    """Check model path exists and is readable."""
    env_var = PROFILE_MODEL_PATHS.get(profile, "")
    if not env_var:
        return [CheckResult(name="model_path", status="skip", detail="No model path for profile")]

    model_path = os.environ.get(env_var, "").strip()
    if not model_path:
        return [
            CheckResult(
                name="model_path",
                status="fail",
                detail=f"{env_var} not set",
            )
        ]

    p = Path(model_path)
    if not p.exists():
        return [
            CheckResult(
                name="model_path",
                status="fail",
                detail=f"Path does not exist: {model_path}",
            )
        ]

    if not os.access(model_path, os.R_OK):
        return [
            CheckResult(
                name="model_path",
                status="fail",
                detail=f"Path not readable: {model_path}",
            )
        ]

    return [
        CheckResult(
            name="model_path",
            status="pass",
            detail=f"Exists and readable: {model_path}",
        )
    ]


def check_disk_space(min_gb: float = 10.0) -> list[CheckResult]:
    """Check available disk space on /tmp."""
    try:
        usage = shutil.disk_usage("/tmp")
        free_gb = usage.free / (1024**3)
        if free_gb >= min_gb:
            return [
                CheckResult(
                    name="disk_space:/tmp",
                    status="pass",
                    detail=f"{free_gb:.1f}GB free (>= {min_gb}GB required)",
                )
            ]
        return [
            CheckResult(
                name="disk_space:/tmp",
                status="warn",
                detail=f"{free_gb:.1f}GB free (< {min_gb}GB recommended)",
            )
        ]
    except OSError as e:
        return [
            CheckResult(
                name="disk_space:/tmp",
                status="warn",
                detail=str(e),
            )
        ]


def check_ports(profile: str) -> list[CheckResult]:
    """Check if required ports are available."""
    import socket

    results = []
    ports = PROFILE_PORTS.get(profile, [])
    for port in ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex(("127.0.0.1", port))
                if result == 0:
                    results.append(
                        CheckResult(
                            name=f"port:{port}",
                            status="warn",
                            detail=f"Port {port} already in use",
                        )
                    )
                else:
                    results.append(
                        CheckResult(
                            name=f"port:{port}",
                            status="pass",
                            detail=f"Port {port} available",
                        )
                    )
        except OSError:
            results.append(
                CheckResult(
                    name=f"port:{port}",
                    status="pass",
                    detail=f"Port {port} available",
                )
            )
    return results


# The supported interpreter floor. This is NOT a free choice: CONSTITUTION.md
# §4.1 says "Python 3.10+ only", every pyproject in the workspace declares
# requires-python >= 3.10, and .python-version pins 3.10. This check used to
# demand >= 3.11, so it failed a deployment running the very version the
# project documents as supported (found 2026-09-14). Keep it equal to the
# declared floor — tests/test_provenance_diff_and_bootstrap.py pins the two
# together so they cannot drift apart again.
MIN_PYTHON: tuple[int, int] = (3, 10)


def check_python_version() -> list[CheckResult]:
    """Check the interpreter meets the workspace's declared Python floor."""
    ver = sys.version_info
    floor = ".".join(str(part) for part in MIN_PYTHON)
    if ver >= MIN_PYTHON:
        return [
            CheckResult(
                name="python_version",
                status="pass",
                detail=f"{ver.major}.{ver.minor}.{ver.micro}",
            )
        ]
    return [
        CheckResult(
            name="python_version",
            status="fail",
            detail=f"{ver.major}.{ver.minor}.{ver.micro} (requires >= {floor})",
        )
    ]


def validate(profile: str, check_port_availability: bool = False) -> ValidationReport:
    """Run all validation checks for a deployment profile."""
    report = ValidationReport(profile=profile)

    report.checks.append(
        CheckResult(
            name="profile",
            status="pass" if profile in PROFILE_REQUIRED_ENV else "fail",
            detail=f"Profile: {profile}",
        )
    )

    report.checks.extend(check_python_version())
    report.checks.extend(check_env_vars(profile))
    report.checks.extend(check_runtimes(profile))
    report.checks.extend(check_model_path(profile))
    report.checks.extend(check_disk_space())

    if check_port_availability:
        report.checks.extend(check_ports(profile))

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap validator for airgap deployment (COR-003)",
    )
    parser.add_argument(
        "--profile",
        "-p",
        required=True,
        choices=sorted(PROFILE_REQUIRED_ENV.keys()),
        help="Deployment profile to validate",
    )
    parser.add_argument(
        "--check-ports",
        action="store_true",
        help="Also check port availability",
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Output as JSON",
    )
    args = parser.parse_args()

    report = validate(args.profile, check_port_availability=args.check_ports)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        icons = {"pass": "✓", "fail": "✗", "warn": "⚠", "skip": "○"}
        print(f"Bootstrap Validation: {report.profile}")
        print("=" * 60)

        for check in report.checks:
            icon = icons.get(check.status, "?")
            print(f"  [{icon}] {check.name:<30} {check.detail}")

        print()
        print(f"Result: {report.passed} passed, {report.failed} failed, {report.warnings} warnings")

        if not report.ok:
            print("\nFailed checks must be resolved before deployment.")

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
