#!/usr/bin/env python3
"""Runtime support matrix validator.

Validates the declarative support matrix for Phase 5 operational convergence.
The matrix distinguishes supported, site-dependent, development, CI,
compatibility, experimental, and retired runtime compositions.

Usage:
    python tools/runtime_support_matrix.py
    python tools/runtime_support_matrix.py --json
    python tools/runtime_support_matrix.py --check profile=hpc-full \
        --check runtime_wrapper=slurm-podman
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX_PATH = REPO_ROOT / "tools" / "runtime_support_matrix.json"

REQUIRED_ENTRY_FIELDS = {
    "id",
    "envelope",
    "profile",
    "gateway",
    "transport",
    "execution_authority",
    "runtime_wrapper",
    "storage",
    "egress",
    "support_level",
    "validation",
    "smoke_command",
    "rollback",
}

CHECK_FIELDS = {
    "envelope",
    "profile",
    "gateway",
    "transport",
    "execution_authority",
    "runtime_wrapper",
    "storage",
    "egress",
    "support_level",
}

ACCEPTED_SUPPORT_LEVELS = {"supported", "site_dependent", "development", "ci"}

ALLOWED_VALUES = {
    "envelope": {"hpc", "k8s", "dev-server"},
    "profile": {
        "hpc-full",
        "hpc-minimal",
        "k8s-cluster",
        "server",
        "development",
        "ci-testing",
    },
    "gateway": {"nginx-sidecar", "none", "included", "python-gateway"},
    "transport": {"http+zmq-tunnel", "http+zmq", "zmq-only"},
    "execution_authority": {"slurm", "k8s", "compose"},
    "runtime_wrapper": {
        "slurm-podman",
        "singularity",
        "k8s-job",
        "podman-compose",
        "docker-compose",
        "hybrid-wrapper",
    },
    "storage": {
        "postgres+minio+shared-posix",
        "postgres+shared-posix",
        "postgres+minio+pvc",
        "postgres+minio-local",
        "ephemeral-compose",
    },
    "egress": {
        "egressguard+site-firewall",
        "egressguard-networkpolicy",
        "egressguard",
        "offline",
        "dev-only",
        "ci-only",
    },
    "support_level": {
        "supported",
        "site_dependent",
        "development",
        "ci",
        "compatibility",
        "experimental",
        "retired",
    },
}


@dataclass(frozen=True)
class MatrixEntry:
    """One runtime topology composition in the support matrix."""

    id: str
    envelope: str
    profile: str
    gateway: str
    transport: str
    execution_authority: str
    runtime_wrapper: str
    storage: str
    egress: str
    support_level: str
    validation: str
    smoke_command: str
    rollback: str
    required_services: list[str] = field(default_factory=list)
    auth_model: str = ""
    ha: str = ""
    readiness_gate: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> MatrixEntry:
        """Build an entry from raw JSON data after required-field validation."""
        values = {field_name: str(data[field_name]) for field_name in REQUIRED_ENTRY_FIELDS}
        # Optional operational fields
        raw_services = data.get("required_services", [])
        values["required_services"] = (
            [str(s) for s in raw_services] if isinstance(raw_services, list) else []
        )
        values["auth_model"] = str(data.get("auth_model", ""))
        values["ha"] = str(data.get("ha", ""))
        values["readiness_gate"] = str(data.get("readiness_gate", ""))
        return cls(**values)


@dataclass(frozen=True)
class RuntimeSupportFinding:
    """One support matrix validation finding."""

    entry_id: str
    message: str

    def format(self) -> str:
        return f"{self.entry_id}: {self.message}"


@dataclass(frozen=True)
class SupportDecision:
    """Decision for a requested runtime composition."""

    accepted: bool
    support_level: str
    entry_ids: list[str]
    reason: str


@dataclass
class RuntimeSupportReport:
    """Complete validation report for the support matrix."""

    entries: list[MatrixEntry] = field(default_factory=list)
    findings: list[RuntimeSupportFinding] = field(default_factory=list)
    blessed_envelopes: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, object]:
        by_level = dict.fromkeys(sorted(ALLOWED_VALUES["support_level"]), 0)
        for entry in self.entries:
            by_level[entry.support_level] = by_level.get(entry.support_level, 0) + 1
        return {
            "entries": len(self.entries),
            "findings": len(self.findings),
            "blessed_envelopes": self.blessed_envelopes,
            "by_support_level": by_level,
        }


def load_matrix(path: Path = DEFAULT_MATRIX_PATH) -> dict[str, Any]:
    """Load raw matrix JSON."""
    return json.loads(path.read_text())


def validate_matrix_data(data: dict[str, Any]) -> RuntimeSupportReport:
    """Validate raw matrix data and return a report."""
    blessed_envelopes = [str(envelope) for envelope in data.get("blessed_envelopes", [])]
    report = RuntimeSupportReport(blessed_envelopes=blessed_envelopes)

    _validate_blessed_envelopes(blessed_envelopes, report.findings)
    _validate_slo_definitions(data.get("slo_definitions", {}), report.findings)

    seen_ids: set[str] = set()
    for index, raw_entry in enumerate(data.get("entries", []), start=1):
        entry_label = (
            str(raw_entry.get("id", f"entry[{index}]"))
            if isinstance(raw_entry, dict)
            else f"entry[{index}]"
        )
        if not isinstance(raw_entry, dict):
            report.findings.append(RuntimeSupportFinding(entry_label, "entry must be an object"))
            continue

        missing = REQUIRED_ENTRY_FIELDS - raw_entry.keys()
        if missing:
            message = f"missing required fields: {', '.join(sorted(missing))}"
            report.findings.append(RuntimeSupportFinding(entry_label, message))
            continue

        entry = MatrixEntry.from_mapping(raw_entry)
        report.entries.append(entry)

        if entry.id in seen_ids:
            report.findings.append(RuntimeSupportFinding(entry.id, "duplicate entry id"))
        seen_ids.add(entry.id)

        report.findings.extend(_validate_entry(entry, blessed_envelopes))

    _validate_envelope_coverage(report.entries, blessed_envelopes, report.findings)
    return report


def validate_default_matrix(path: Path = DEFAULT_MATRIX_PATH) -> RuntimeSupportReport:
    """Load and validate the repository runtime support matrix."""
    return validate_matrix_data(load_matrix(path))


def evaluate_composition(criteria: dict[str, str], entries: list[MatrixEntry]) -> SupportDecision:
    """Evaluate whether a runtime composition is accepted by the matrix."""
    unknown_keys = sorted(set(criteria) - CHECK_FIELDS)
    if unknown_keys:
        reason = f"unknown check field(s): {', '.join(unknown_keys)}"
        return SupportDecision(False, "invalid", [], reason)

    matches = [
        entry
        for entry in entries
        if all(getattr(entry, key) == value for key, value in criteria.items())
    ]
    if not matches:
        reason = "no matching runtime support matrix entry"
        return SupportDecision(False, "unsupported", [], reason)

    best_match = min(matches, key=lambda entry: _support_rank(entry.support_level))
    accepted = best_match.support_level in ACCEPTED_SUPPORT_LEVELS
    reason = (
        "accepted runtime composition"
        if accepted
        else f"runtime composition is {best_match.support_level}"
    )
    return SupportDecision(
        accepted=accepted,
        support_level=best_match.support_level,
        entry_ids=[entry.id for entry in matches],
        reason=reason,
    )


def format_human(report: RuntimeSupportReport, decision: SupportDecision | None = None) -> str:
    """Format a runtime support report for humans."""
    lines = ["Runtime Support Matrix", "=" * 66]
    summary = report.summary
    lines.append(f"Entries:            {summary['entries']}")
    lines.append(f"Findings:           {summary['findings']}")
    lines.append(f"Blessed envelopes:  {', '.join(report.blessed_envelopes)}")
    lines.append("")
    lines.append("Support levels:")
    for level, count in summary["by_support_level"].items():
        if count:
            lines.append(f"  {level}: {count}")

    if report.findings:
        lines.append("")
        lines.append("Findings:")
        for finding in report.findings:
            lines.append(f"  {finding.format()}")

    if decision is not None:
        lines.append("")
        lines.append("Composition check:")
        lines.append(f"  accepted: {decision.accepted}")
        lines.append(f"  support_level: {decision.support_level}")
        entry_ids = ", ".join(decision.entry_ids) if decision.entry_ids else "(none)"
        lines.append(f"  entries: {entry_ids}")
        lines.append(f"  reason: {decision.reason}")

    return "\n".join(lines)


def format_json(report: RuntimeSupportReport, decision: SupportDecision | None = None) -> str:
    """Format a runtime support report as JSON."""
    payload: dict[str, object] = {
        "summary": report.summary,
        "entries": [asdict(entry) for entry in report.entries],
        "findings": [asdict(finding) for finding in report.findings],
    }
    if decision is not None:
        payload["decision"] = asdict(decision)
    return json.dumps(payload, indent=2)


REQUIRED_SLO_FIELDS = {"description", "target", "unit", "measurement"}


def _validate_slo_definitions(
    slo_defs: dict[str, Any],
    findings: list[RuntimeSupportFinding],
) -> None:
    """Validate SLO definition block in the matrix."""
    if not slo_defs:
        findings.append(
            RuntimeSupportFinding("matrix", "slo_definitions block is missing or empty")
        )
        return
    for name, spec in slo_defs.items():
        if not isinstance(spec, dict):
            findings.append(RuntimeSupportFinding(f"slo:{name}", "SLO spec must be an object"))
            continue
        missing = REQUIRED_SLO_FIELDS - spec.keys()
        if missing:
            findings.append(
                RuntimeSupportFinding(
                    f"slo:{name}", f"missing fields: {', '.join(sorted(missing))}"
                )
            )
        target = spec.get("target")
        if target is not None and not isinstance(target, (int, float)):
            findings.append(RuntimeSupportFinding(f"slo:{name}", "target must be numeric"))


def _validate_blessed_envelopes(
    blessed_envelopes: list[str],
    findings: list[RuntimeSupportFinding],
) -> None:
    if not blessed_envelopes:
        findings.append(RuntimeSupportFinding("matrix", "blessed_envelopes must not be empty"))
        return

    invalid = sorted(set(blessed_envelopes) - ALLOWED_VALUES["envelope"])
    if invalid:
        message = f"invalid blessed envelope(s): {', '.join(invalid)}"
        findings.append(RuntimeSupportFinding("matrix", message))


def _validate_entry(
    entry: MatrixEntry, blessed_envelopes: list[str]
) -> list[RuntimeSupportFinding]:
    findings: list[RuntimeSupportFinding] = []
    for field_name, allowed in ALLOWED_VALUES.items():
        value = getattr(entry, field_name)
        if value not in allowed:
            findings.append(RuntimeSupportFinding(entry.id, f"invalid {field_name}: {value}"))

    if entry.support_level == "supported" and entry.envelope not in blessed_envelopes:
        findings.append(
            RuntimeSupportFinding(entry.id, "supported entries must use a blessed envelope")
        )
    if entry.support_level in ACCEPTED_SUPPORT_LEVELS and (
        not entry.smoke_command or not entry.rollback
    ):
        findings.append(
            RuntimeSupportFinding(entry.id, "accepted entries require smoke_command and rollback")
        )
    if entry.gateway == "python-gateway" and entry.support_level in {"supported", "site_dependent"}:
        findings.append(
            RuntimeSupportFinding(
                entry.id, "python-gateway cannot be a production-supported Gateway"
            )
        )
    if entry.runtime_wrapper == "hybrid-wrapper" and entry.support_level != "experimental":
        findings.append(
            RuntimeSupportFinding(entry.id, "hybrid-wrapper entries must remain experimental")
        )
    if entry.transport == "zmq-only" and entry.gateway != "none":
        findings.append(
            RuntimeSupportFinding(entry.id, "zmq-only transport cannot expose a Gateway")
        )
    if entry.execution_authority == "k8s" and entry.runtime_wrapper != "k8s-job":
        findings.append(
            RuntimeSupportFinding(entry.id, "k8s authority requires k8s-job runtime wrapper")
        )
    if entry.execution_authority == "compose" and entry.envelope == "hpc":
        findings.append(
            RuntimeSupportFinding(entry.id, "compose authority is not valid for the hpc envelope")
        )
    if entry.egress == "dev-only" and entry.support_level not in {"development", "compatibility"}:
        message = "dev-only egress must stay in development or compatibility entries"
        findings.append(RuntimeSupportFinding(entry.id, message))
    if entry.egress == "ci-only" and entry.support_level != "ci":
        findings.append(RuntimeSupportFinding(entry.id, "ci-only egress must stay in ci entries"))
    if entry.support_level in {"supported", "site_dependent"} and not entry.readiness_gate:
        findings.append(
            RuntimeSupportFinding(entry.id, "production entries require a readiness_gate reference")
        )
    if entry.support_level in {"supported", "site_dependent"} and not entry.required_services:
        findings.append(
            RuntimeSupportFinding(entry.id, "production entries require required_services")
        )
    return findings


def _validate_envelope_coverage(
    entries: list[MatrixEntry],
    blessed_envelopes: list[str],
    findings: list[RuntimeSupportFinding],
) -> None:
    accepted_envelopes = {
        entry.envelope for entry in entries if entry.support_level in ACCEPTED_SUPPORT_LEVELS
    }
    for envelope in blessed_envelopes:
        if envelope not in accepted_envelopes:
            findings.append(
                RuntimeSupportFinding(
                    "matrix", f"blessed envelope has no accepted entry: {envelope}"
                )
            )


def _support_rank(level: str) -> int:
    ranks = {
        "supported": 0,
        "site_dependent": 1,
        "development": 2,
        "ci": 3,
        "compatibility": 4,
        "experimental": 5,
        "retired": 6,
    }
    return ranks.get(level, 99)


def _parse_checks(raw_checks: list[str]) -> dict[str, str]:
    criteria: dict[str, str] = {}
    for raw_check in raw_checks:
        if "=" not in raw_check:
            raise ValueError(f"check must be KEY=VALUE: {raw_check}")
        key, value = raw_check.split("=", 1)
        criteria[key.strip()] = value.strip()
    return criteria


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate runtime support matrix")
    parser.add_argument("--json", action="store_true", help="emit JSON output")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX_PATH, help="matrix JSON path")
    parser.add_argument(
        "--check",
        action="append",
        default=[],
        help="check a composition field as KEY=VALUE",
    )
    args = parser.parse_args(argv)

    try:
        report = validate_default_matrix(args.matrix)
        criteria = _parse_checks(args.check)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Runtime support matrix error: {exc}", file=sys.stderr)
        return 1

    decision = evaluate_composition(criteria, report.entries) if criteria else None

    if args.json:
        print(format_json(report, decision))
    else:
        print(format_human(report, decision))

    if report.findings:
        return 1
    if decision is not None and not decision.accepted:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
