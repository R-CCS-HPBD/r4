#!/usr/bin/env python3
"""Data authority inventory validator.

Validates the declarative inventory used to prevent persistence drift.  The
inventory records where each data class lives, which component owns it, and
whether it is authoritative, bootstrap, cache, replica, or ephemeral state.

Usage:
    python tools/data_authority_validator.py
    python tools/data_authority_validator.py --json
    python tools/data_authority_validator.py --inventory path/to/inventory.yaml
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY_PATH = REPO_ROOT / "tools" / "data_authority_inventory.yaml"

ALLOWED_AUTHORITY = {"authoritative", "bootstrap", "cache", "ephemeral", "replica"}
ALLOWED_LOCATIONS = {
    "client",
    "compute-node",
    "login-node-filesystem",
    "local-filesystem",
    "metrics-scrape",
    "object-storage",
    "remote-database",
    "remote-service",
    "shared-filesystem",
    "umrs-process-or-database",
}
ALLOWED_METHODS = {
    "env_file",
    "filesystem_log",
    "in_memory_or_postgres",
    "json",
    "lustre",
    "mysql",
    "postgres",
    "prometheus",
    "s3",
    "sqlite",
    "tmpfs_or_overlay",
    "yaml",
}
ALLOWED_SENSITIVITY = {
    "audit",
    "operational",
    "personal_data",
    "research_data",
    "runtime_only",
    "secret",
}

REQUIRED_ENTRY_FIELDS = {
    "authority",
    "data",
    "id",
    "location",
    "method",
    "owner",
    "retention",
    "sensitivity",
    "source_refs",
    "trust_boundary",
}

FILESYSTEM_METHODS = {
    "env_file",
    "filesystem_log",
    "json",
    "lustre",
    "sqlite",
    "tmpfs_or_overlay",
    "yaml",
}
FILESYSTEM_LOCATIONS = {
    "compute-node",
    "login-node-filesystem",
    "local-filesystem",
    "shared-filesystem",
}


@dataclass(frozen=True)
class DataAuthorityFinding:
    """One inventory validation finding."""

    entry_id: str
    message: str

    def format(self) -> str:
        return f"{self.entry_id}: {self.message}"


@dataclass
class DataAuthorityReport:
    """Complete validation report for the data authority inventory."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    findings: list[DataAuthorityFinding] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, object]:
        by_authority = dict.fromkeys(sorted(ALLOWED_AUTHORITY), 0)
        by_method = dict.fromkeys(sorted(ALLOWED_METHODS), 0)
        for entry in self.entries:
            authority = str(entry.get("authority", ""))
            method = str(entry.get("method", ""))
            by_authority[authority] = by_authority.get(authority, 0) + 1
            by_method[method] = by_method.get(method, 0) + 1
        return {
            "entries": len(self.entries),
            "findings": len(self.findings),
            "by_authority": by_authority,
            "by_method": by_method,
        }


def load_inventory(path: Path = DEFAULT_INVENTORY_PATH) -> dict[str, Any]:
    """Load inventory data from JSON-subset YAML or PyYAML if available."""
    text = path.read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = _load_with_pyyaml(text, path)
    if not isinstance(data, dict):
        raise ValueError(f"inventory root must be an object: {path}")
    return data


def validate_default_inventory(path: Path = DEFAULT_INVENTORY_PATH) -> DataAuthorityReport:
    """Load and validate the repository inventory."""
    return validate_inventory_data(load_inventory(path))


def validate_inventory_data(data: dict[str, Any]) -> DataAuthorityReport:
    """Validate raw inventory data and return a report."""
    report = DataAuthorityReport()

    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        report.findings.append(
            DataAuthorityFinding("inventory", "entries must be a non-empty list")
        )
        return report

    declared_classes = {str(item) for item in data.get("authority_classes", [])}
    if declared_classes != ALLOWED_AUTHORITY:
        report.findings.append(
            DataAuthorityFinding(
                "inventory",
                "authority_classes must exactly match: " + ", ".join(sorted(ALLOWED_AUTHORITY)),
            )
        )

    seen_ids: set[str] = set()
    for index, raw_entry in enumerate(entries, start=1):
        entry_id = _entry_label(raw_entry, index)
        if not isinstance(raw_entry, dict):
            report.findings.append(DataAuthorityFinding(entry_id, "entry must be an object"))
            continue

        report.entries.append(raw_entry)
        if entry_id in seen_ids:
            report.findings.append(DataAuthorityFinding(entry_id, "duplicate entry id"))
        seen_ids.add(entry_id)

        report.findings.extend(_validate_entry(raw_entry, entry_id))

    return report


def format_human(report: DataAuthorityReport) -> str:
    """Format a data authority report for humans."""
    lines = ["Data Authority Inventory", "=" * 66]
    summary = report.summary
    lines.append(f"Entries:  {summary['entries']}")
    lines.append(f"Findings: {summary['findings']}")
    lines.append("")
    lines.append("Authority classes:")
    for authority, count in summary["by_authority"].items():
        if count:
            lines.append(f"  {authority}: {count}")
    lines.append("")
    lines.append("Storage methods:")
    for method, count in summary["by_method"].items():
        if count:
            lines.append(f"  {method}: {count}")
    if report.findings:
        lines.append("")
        lines.append("Findings:")
        for finding in report.findings:
            lines.append(f"  {finding.format()}")
    return "\n".join(lines)


def format_json(report: DataAuthorityReport) -> str:
    """Format a data authority report as JSON."""
    return json.dumps(
        {
            "summary": report.summary,
            "findings": [asdict(finding) for finding in report.findings],
        },
        indent=2,
    )


def _load_with_pyyaml(text: str, path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ValueError(f"inventory is not JSON and PyYAML is unavailable: {path}") from exc
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"inventory root must be an object: {path}")
    return data


def _entry_label(raw_entry: object, index: int) -> str:
    if isinstance(raw_entry, dict) and raw_entry.get("id"):
        return str(raw_entry["id"])
    return f"entry[{index}]"


def _validate_entry(entry: dict[str, Any], entry_id: str) -> list[DataAuthorityFinding]:
    findings: list[DataAuthorityFinding] = []
    missing = REQUIRED_ENTRY_FIELDS - entry.keys()
    if missing:
        findings.append(
            DataAuthorityFinding(entry_id, f"missing required fields: {', '.join(sorted(missing))}")
        )
        return findings

    findings.extend(_validate_scalar(entry, entry_id, "authority", ALLOWED_AUTHORITY))
    findings.extend(_validate_scalar(entry, entry_id, "location", ALLOWED_LOCATIONS))
    findings.extend(_validate_scalar(entry, entry_id, "method", ALLOWED_METHODS))
    findings.extend(_validate_non_empty_string(entry, entry_id, "owner"))
    findings.extend(_validate_non_empty_string(entry, entry_id, "retention"))
    findings.extend(_validate_non_empty_string(entry, entry_id, "trust_boundary"))
    findings.extend(_validate_sensitivity(entry, entry_id))
    findings.extend(_validate_source_refs(entry, entry_id))
    findings.extend(_validate_authority_boundary(entry, entry_id))
    return findings


def _validate_scalar(
    entry: dict[str, Any],
    entry_id: str,
    field_name: str,
    allowed_values: set[str],
) -> list[DataAuthorityFinding]:
    value = entry.get(field_name)
    if not isinstance(value, str) or not value.strip():
        return [DataAuthorityFinding(entry_id, f"{field_name} must be a non-empty string")]
    if value not in allowed_values:
        return [DataAuthorityFinding(entry_id, f"invalid {field_name}: {value}")]
    return []


def _validate_non_empty_string(
    entry: dict[str, Any],
    entry_id: str,
    field_name: str,
) -> list[DataAuthorityFinding]:
    value = entry.get(field_name)
    if not isinstance(value, str) or not value.strip():
        return [DataAuthorityFinding(entry_id, f"{field_name} must be a non-empty string")]
    return []


def _validate_sensitivity(entry: dict[str, Any], entry_id: str) -> list[DataAuthorityFinding]:
    value = entry.get("sensitivity")
    if not isinstance(value, list) or not value:
        return [DataAuthorityFinding(entry_id, "sensitivity must be a non-empty list")]
    invalid = sorted({str(item) for item in value if item not in ALLOWED_SENSITIVITY})
    if invalid:
        return [DataAuthorityFinding(entry_id, f"invalid sensitivity: {', '.join(invalid)}")]
    return []


def _validate_source_refs(entry: dict[str, Any], entry_id: str) -> list[DataAuthorityFinding]:
    value = entry.get("source_refs")
    if not isinstance(value, list) or not value:
        return [DataAuthorityFinding(entry_id, "source_refs must be a non-empty list")]
    bad_refs = [ref for ref in value if not isinstance(ref, str) or not ref.strip()]
    if bad_refs:
        return [DataAuthorityFinding(entry_id, "source_refs must contain only non-empty strings")]
    return []


def _validate_authority_boundary(
    entry: dict[str, Any], entry_id: str
) -> list[DataAuthorityFinding]:
    if entry.get("authority") != "authoritative":
        return []

    findings: list[DataAuthorityFinding] = []

    method = str(entry.get("method", ""))
    location = str(entry.get("location", ""))
    if method in FILESYSTEM_METHODS or location in FILESYSTEM_LOCATIONS:
        exception = entry.get("authority_exception")
        if not isinstance(exception, dict) or not str(exception.get("reason", "")).strip():
            findings.append(
                DataAuthorityFinding(
                    entry_id,
                    "filesystem-backed authoritative state requires authority_exception.reason",
                )
            )
        if isinstance(exception, dict) and not str(exception.get("recovery_semantics", "")).strip():
            findings.append(
                DataAuthorityFinding(
                    entry_id,
                    "filesystem-backed authoritative state requires authority_exception.recovery_semantics",
                )
            )

    # All authoritative entries must have a non-empty owner
    owner = str(entry.get("owner", "")).strip()
    if not owner:
        findings.append(
            DataAuthorityFinding(
                entry_id,
                "authoritative entries require a non-empty owner",
            )
        )

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY_PATH)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    report = validate_default_inventory(args.inventory)
    if args.json:
        print(format_json(report))
    else:
        print(format_human(report))
    return 0 if not report.findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
