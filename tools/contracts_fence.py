"""Validate that r4-contracts declares zero runtime dependencies.

r4-contracts is the shared DTO/schema package consumed by r4, gateway, and
umrs.  Its zero-dependency invariant (INV-10) prevents transitive dependency
contamination across all consumers.

Usage:
    python tools/contracts_fence.py          # exits 0 on success, 1 on failure
    python -m pytest tests/test_contracts_fence.py   # CI gate

Enforcement class: PERMANENT
"""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


def validate_contracts_zero_deps(repo_root: Path) -> list[str]:
    """Return list of violations.  Empty list means success."""
    pyproject = repo_root / "r4-contracts" / "pyproject.toml"
    if not pyproject.exists():
        return [f"{pyproject} not found"]

    with open(pyproject, "rb") as f:
        cfg = tomllib.load(f)

    project = cfg.get("project", {})
    deps = project.get("dependencies", [])
    violations: list[str] = []
    if deps:
        violations.append(
            f"r4-contracts must have zero [project.dependencies], found {len(deps)}: {deps}"
        )
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    violations = validate_contracts_zero_deps(repo_root)
    if not violations:
        print("Contracts fence passed: r4-contracts has zero dependencies")
        return 0
    for v in violations:
        print(f"FAIL: {v}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
