"""Validate the contracts dependency fence (INV-10, ADR-005 as amended by ADR-117).

r4-contracts is the shared DTO/schema package consumed by r4, gateway, and
umrs.  INV-10 keeps transitive dependencies out of every consumer.

umrs-protocol (``umrs/protocol``, ADR-117 D-2) carries the wire protocol and
the HPC gate; from U2.0b on the 16 moved r4-contracts modules are aliases of
it. Two rules:

- fence (a), changed in U2.0b (2026-11-15; until then: zero dependencies):
  r4-contracts declares exactly one dependency, ``umrs-protocol`` with a
  ``>=`` floor, and nothing else: no extras, no marker, no URL;
- fence (b), added in U2.0a (2026-11-13): umrs-protocol declares
  ``dependencies = []``. Its pyproject must exist: a missing file is a
  violation, never a skip.

Usage:
    python tools/contracts_fence.py          # exits 0 on success, 1 on failure
    python -m pytest tests/test_contracts_fence.py   # CI gate

Enforcement class: PERMANENT
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

PROTOCOL_PYPROJECT = Path("umrs") / "protocol" / "pyproject.toml"
PROTOCOL_NAME = "umrs-protocol"
CONTRACTS_PYPROJECT = Path("r4-contracts") / "pyproject.toml"
# PEP 508 name, then exactly one ``>=`` floor (cut.sh seds ``umrs-protocol>=…``).
_FLOOR_REQUIREMENT = re.compile(
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*>=\s*[0-9][A-Za-z0-9.+!-]*"
)


def _normalized(name: str) -> str:
    """PEP 503 normalisation: ``UMRS_Protocol`` and ``umrs.protocol`` are ``umrs-protocol``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_violations(requirement: object) -> list[str]:
    """Fence (a) on one requirement string: ``umrs-protocol>=<version>``, nothing more."""
    text = requirement.strip() if isinstance(requirement, str) else ""
    match = _FLOOR_REQUIREMENT.fullmatch(text)
    if match is None:
        return [
            f"{CONTRACTS_PYPROJECT}: {requirement!r} must be a plain '{PROTOCOL_NAME}>=<version>' "
            "floor (no extras, marker, URL or other specifier)"
        ]
    if _normalized(match["name"]) != PROTOCOL_NAME:
        return [
            f"{CONTRACTS_PYPROJECT}: the one dependency must be {PROTOCOL_NAME!r}, not {text!r}"
        ]
    return []


def validate_contracts_dependency(repo_root: Path) -> list[str]:
    """Fence (a): r4-contracts depends on umrs-protocol (a ``>=`` floor) and on nothing else."""
    pyproject = repo_root / CONTRACTS_PYPROJECT
    if not pyproject.exists():
        return [f"{pyproject} not found"]
    with open(pyproject, "rb") as f:
        project = tomllib.load(f).get("project", {})
    if "dependencies" in project.get("dynamic", []):
        return [f"{CONTRACTS_PYPROJECT}: dependencies must be static, not dynamic"]
    deps = project.get("dependencies", [])
    if not isinstance(deps, list) or len(deps) != 1:
        return [
            f"{CONTRACTS_PYPROJECT}: r4-contracts must declare exactly one dependency, "
            f"{PROTOCOL_NAME!r}, found {deps!r}"
        ]
    return requirement_violations(deps[0])


def validate_umrs_protocol_zero_deps(repo_root: Path) -> list[str]:
    """Fence (b): umrs/protocol/pyproject.toml exists, is ``umrs-protocol`` and
    declares ``dependencies = []`` explicitly (absent or dynamic fails closed)."""
    pyproject = repo_root / PROTOCOL_PYPROJECT
    if not pyproject.is_file():
        return [f"{pyproject} not found (umrs-protocol fence fails closed)"]
    with open(pyproject, "rb") as f:
        project = tomllib.load(f).get("project", {})
    violations: list[str] = []
    name = project.get("name")
    if name != PROTOCOL_NAME:
        violations.append(
            f"{PROTOCOL_PYPROJECT}: project.name must be {PROTOCOL_NAME!r}, found {name!r}"
        )
    if "dependencies" in project.get("dynamic", []):
        violations.append(f"{PROTOCOL_PYPROJECT}: dependencies must be static, not dynamic")
    deps = project.get("dependencies")
    if deps != []:
        violations.append(
            f"{PROTOCOL_PYPROJECT}: umrs-protocol must declare dependencies = [], found {deps!r}"
        )
    return violations


def validate_contracts_fence(repo_root: Path) -> list[str]:
    """Every contracts fence rule; an empty list means success."""
    return validate_contracts_dependency(repo_root) + validate_umrs_protocol_zero_deps(repo_root)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    violations = validate_contracts_fence(repo_root)
    if not violations:
        print(
            "Contracts fence passed: r4-contracts depends only on umrs-protocol, "
            "which has zero dependencies"
        )
        return 0
    for v in violations:
        print(f"FAIL: {v}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
