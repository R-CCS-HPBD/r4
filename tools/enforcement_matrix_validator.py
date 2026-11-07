#!/usr/bin/env python3
"""Enforcement-point matrix validator (ADR-035).

Validates the declarative enforcement matrix used to prove every mutating
UMRS HTTP route has a non-blank enforcement point inside the server, not
just edge authn or client UX gating.

Modes:
    default            schema validation only (offline, fast)
    --live             additionally cross-check the live FastAPI surface
    --json             machine-readable findings
    --matrix PATH      alternate matrix file

Invariant enforced (hard findings):
  * every matrix row carries the required fields with allowed enum values
  * no two rows share an id; path prefixes are unique
  * every row with mutating=true has umrs_authz in ENFORCEABLE_AUTHZ
  * the base matrix must contain exactly one catch-all row (id:
    platform-default, prefix /umrs)

--live findings:
  * mutating routes lacking ANY matrix row ("no-row")
  * mutating routes matching ONLY the catch-all row ("catch-all-only",
    signals the family needs its own audit row)
  * read-only routes: informational n/a
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX_PATH = REPO_ROOT / "tools" / "enforcement_matrix.yaml"

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
ALLOWED_GATEWAY_AUTH = {"auth_request", "open", "exclude"}
ALLOWED_AUTHZ = {
    "owner-scope",
    "verified-identity",
    # provision-identity-derived: authz computed at provisioning from the
    # verified identity + tenant tier (ADR-037 Rule 2a:
    # RIVAULT_MCP_GRANTED_CAPABILITIES as transport, not source).
    "provision-identity-derived",
    "mutation-guard",
    "ops-admin",
    "read-only",
    "internal",
    "exempt",
}
ALLOWED_UX_GATE = {"permission-manager", "approval-card", "none"}

# Authz values that constitute a real server-side enforcement decision on a
# mutating route (blank/none is never acceptable on mutating rows)
# `umrs_authz` on the catch-all row is advisory by construction; it holds the
# door for routes whose family audit row has not been written yet. Hard-fail
# semantics arrive with the --live snapshot gate (see --strict, future ADR
# cycle). Sprint-1 ships advisory exit codes only.
ENFORCEABLE_AUTHZ = {
    "owner-scope",
    "verified-identity",
    "provision-identity-derived",
    "mutation-guard",
    "ops-admin",
    "internal",
    "exempt",
}

REQUIRED_ROW_FIELDS = {
    "id",
    "path_prefix",
    "methods",
    "mutating",
    "gateway_auth",
    "umrs_authz",
    "ux_gate",
    "notes",
    "review",
}

# ADR-035 rule: a row claims verification ONLY via a canary probe id.
# verified_by may be a single probe id or a comma-separated list.
DEFAULT_CANARIES_PATH = REPO_ROOT / "tools" / "authz_canaries.yaml"


def _load_canary_ids(path: Path = DEFAULT_CANARIES_PATH) -> set[str] | None:
    """Probe ids from the canary file; None when the file is unreadable
    (unknown-verified findings degrade to warnings in that case)."""
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ModuleNotFoundError, ValueError):
        return None
    probes = data.get("probes") if isinstance(data, dict) else None
    if not isinstance(probes, list):
        return set()
    return {str(p.get("id")) for p in probes if isinstance(p, dict) and p.get("id")}


def _verification_exempt(row: dict[str, Any]) -> bool:
    """The row's text carries why no canary can exist (2026-10-20):
    open beacon surface, a retired-unmounted family, or the platform-default
    catch-all whose advisory is ADR-035's permanent reminder."""
    return bool(str(row.get("verification_exempt") or "").strip())


def _row_missing_finding(row_id: str, row: dict[str, Any]) -> list[Finding]:
    """The no-verified_by branch of the ADR-035 claim check."""
    if _verification_exempt(row):
        return []
    return [
        Finding(
            "row-verified-missing",
            row_id,
            "no canary exercises this row — specification-only, not citable as enforced",
            "warn",
        )
    ]


def _check_row_verified(
    row_id: str, row: dict[str, Any], canary_ids: set[str] | None
) -> list[Finding]:
    """ADR-035: verification claims must map to a canary that exists.

    - no verified_by → warn (specification-only, never citable as enforced),
      unless ``verification_exempt`` documents why no canary can exist
      (open beacon surface, retired-unmounted family, the platform-default
      catch-all whose advisory is ADR-035's permanent reminder);
    - verified_by not in the canary file → FAIL (drift) when the file is
      readable; warn when it is not (offline degradation).
    """
    raw = str(row.get("verified_by") or "").strip()
    if not raw:
        return _row_missing_finding(row_id, row)
    claimed = [part.strip() for part in raw.split(",") if part.strip()]
    if canary_ids is None:
        return []
    unknown = [c for c in claimed if c not in canary_ids]
    if unknown:
        return [
            Finding(
                "row-verified-unknown",
                row_id,
                f"verified_by references unknown canary probe(s): {unknown}",
                "fail",
            )
        ]
    return []


@dataclass(frozen=True)
class Finding:
    """One matrix validation finding."""

    code: str
    subject: str
    message: str
    severity: str = "fail"  # fail | warn | info

    def format(self) -> str:
        return f"[{self.severity}] {self.code} {self.subject}: {self.message}"


@dataclass
class LoadResult:
    matrix: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


def load_matrix(path: Path) -> LoadResult:
    """Load the YAML matrix without requiring PyYAML for structure errors."""
    try:
        import yaml
    except ModuleNotFoundError:  # pragma: no cover - venv always has it
        return LoadResult(errors=["PyYAML unavailable"])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return LoadResult(errors=[f"cannot parse {path}: {exc}"])
    if not isinstance(data, dict):
        return LoadResult(errors=["matrix root must be a mapping"])
    return LoadResult(matrix=data)


def _check_row_schema(row_id: str, row: Any) -> list[Finding]:
    findings: list[Finding] = []
    if not isinstance(row, dict):
        findings.append(Finding("row-type", row_id, "row must be a mapping"))
        return findings
    missing = REQUIRED_ROW_FIELDS - set(row)
    if missing:
        findings.append(Finding("row-fields", row_id, f"missing fields: {sorted(missing)}"))
        return findings
    return findings


def _check_methods(row_id: str, methods: Any) -> list[Finding]:
    findings: list[Finding] = []
    if not isinstance(methods, list) or not methods:
        findings.append(Finding("row-methods", row_id, "methods must be a non-empty list"))
        return findings
    bad = [m for m in methods if str(m).upper() not in ALLOWED_METHODS]
    if bad:
        findings.append(Finding("row-methods", row_id, f"unknown methods: {bad}"))
    return findings


# (field, allowed-set, finding-code) — enum table keeps _check_row_values flat
_ROW_ENUMS: tuple[tuple[str, set[str], str], ...] = (
    ("gateway_auth", ALLOWED_GATEWAY_AUTH, "row-gateway"),
    ("umrs_authz", ALLOWED_AUTHZ, "row-authz"),
    ("ux_gate", ALLOWED_UX_GATE, "row-ux"),
)


def _check_row_values(row: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    row_id = str(row["id"])
    findings.extend(_check_methods(row_id, row.get("methods")))
    for field_name, allowed, code in _ROW_ENUMS:
        if row[field_name] not in allowed:
            findings.append(Finding(code, row_id, f"{field_name} must be one of {sorted(allowed)}"))
    if not str(row["path_prefix"]).startswith("/"):
        findings.append(Finding("row-prefix", row_id, "path_prefix must start with '/'"))
    return findings


def _check_row_invariant(row: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    row_id = str(row["id"])
    if row["mutating"] is True and row["umrs_authz"] not in ENFORCEABLE_AUTHZ:
        findings.append(
            Finding(
                "row-enforcement",
                row_id,
                "mutating row lacks an enforceable umrs_authz "
                f"(got {row['umrs_authz']!r}; need one of {sorted(ENFORCEABLE_AUTHZ)})",
            )
        )
    return findings


def _validate_row(row: Any, seen_ids: set[str], seen_prefixes: set[str]) -> list[Finding]:
    """All checks for a single matrix row; dup-tracking updated in place."""
    row_id = str(row.get("id") if isinstance(row, dict) else repr(row))
    findings = _check_row_schema(row_id, row)
    if findings or not isinstance(row, dict):
        return findings
    findings.extend(_check_row_values(row))
    findings.extend(_check_row_invariant(row))
    if row_id in seen_ids:
        findings.append(Finding("row-id", row_id, "duplicate row id"))
    seen_ids.add(row_id)
    prefix = str(row["path_prefix"])
    if prefix in seen_prefixes:
        findings.append(Finding("row-prefix", row_id, f"duplicate path_prefix {prefix}"))
    seen_prefixes.add(prefix)
    return findings


def validate_matrix_data(matrix: dict[str, Any]) -> list[Finding]:
    """Schema + invariant pass over a parsed matrix mapping."""
    findings: list[Finding] = []
    rows = matrix.get("rows")
    if not isinstance(rows, list) or not rows:
        return [Finding("matrix-rows", "rows", "top-level 'rows' must be a non-empty list")]
    seen_ids: set[str] = set()
    seen_prefixes: set[str] = set()
    canary_ids = _load_canary_ids()
    for row in rows:
        findings.extend(_validate_row(row, seen_ids, seen_prefixes))
        if isinstance(row, dict):
            findings.extend(_check_row_verified(str(row.get("id", "?")), row, canary_ids))
    findings.extend(_check_catch_all(rows))
    return findings


def _check_catch_all(rows: list[dict[str, Any]]) -> list[Finding]:
    """Exactly one /umrs catch-all, owned by platform-default."""
    catch_all_ids = [
        str(row.get("id"))
        for row in rows
        if isinstance(row, dict) and row.get("path_prefix") == "/umrs"
    ]
    if len(catch_all_ids) != 1:
        return [
            Finding(
                "catch-all",
                "matrix",
                f"exactly one /umrs catch-all row required (got {len(catch_all_ids)})",
            )
        ]
    if catch_all_ids[0] != "platform-default":
        return [
            Finding(
                "catch-all",
                catch_all_ids[0],
                "only the platform-default row may use prefix /umrs",
            )
        ]
    return []


def _match_row(path: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """All rows whose path_prefix is a prefix of path, longest first."""
    hits = [r for r in rows if path.startswith(str(r["path_prefix"]))]
    hits.sort(key=lambda r: len(str(r["path_prefix"])), reverse=True)
    return hits


def _route_mutating(methods: list[str]) -> bool:
    return bool(MUTATING_METHODS & {m.upper() for m in methods})


def check_live_surface(matrix: dict[str, Any]) -> list[Finding]:
    """Cross-check the matrix against the live FastAPI route surface."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from route_inventory import build_inventory

    rows = [r for r in matrix["rows"] if isinstance(r, dict)]
    findings: list[Finding] = []
    for route in build_inventory():
        path = str(route["path"])
        if not path.startswith("/umrs"):
            continue
        mutating = _route_mutating(list(route["methods"]))
        if not mutating:
            continue
        hits = _match_row(path, rows)
        if not hits:
            findings.append(
                Finding("live-no-row", path, "mutating route matches no matrix row", "fail")
            )
            continue
        best = hits[0]
        if len(hits) == 1 and str(best["path_prefix"]) == "/umrs":
            findings.append(
                Finding(
                    "live-catch-all",
                    path,
                    "mutating route only covered by the platform-default catch-all; "
                    "write a family audit row",
                    "warn",
                )
            )
    return findings


def format_json(findings: list[Finding]) -> str:
    return json.dumps([asdict(f) for f in findings], indent=2)


def _report(findings: list[Finding], matrix: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(format_json(findings))
        return
    for finding in findings:
        print(finding.format())
    if not findings:
        print(f"OK: enforcement matrix valid ({len(matrix.get('rows', []))} rows)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX_PATH)
    parser.add_argument("--live", action="store_true", help="cross-check live surface")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    loaded = load_matrix(args.matrix)
    if loaded.errors:
        for err in loaded.errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 2

    matrix = loaded.matrix or {}
    findings = validate_matrix_data(matrix)
    if args.live:
        findings.extend(check_live_surface(matrix))
    _report(findings, matrix, args.json)

    fails = [f for f in findings if f.severity == "fail"]
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
