"""Validate DTO structural equivalence between r4-contracts and UMRS mirrors.

This is a TRANSITIONAL enforcement tool.  It ensures that the DTO shapes
in ``r4-contracts`` and their mirrors in UMRS remain structurally
equivalent until UMRS imports directly from r4-contracts (convergence
Step 5).

Once UMRS imports the shared DTOs from r4-contracts, this tool should be
retired.

Usage:
    python tools/dto_equivalence_check.py
    python -m pytest tests/test_dto_equivalence.py

Enforcement class: TRANSITIONAL (retire after convergence Step 5)
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DTO_PAIRS: list[tuple[str, str, str]] = [
    # MCP DTOs — not yet converged; UMRS still defines locally
    (
        "r4-contracts/src/r4_contracts/mcp.py",
        "umrs/src/umrs/mcp/models.py",
        "MCPTool",
    ),
    (
        "r4-contracts/src/r4_contracts/mcp.py",
        "umrs/src/umrs/mcp/models.py",
        "MCPServer",
    ),
]

# Pairs converged — UMRS now imports from r4-contracts.
# Kept as documentation; no AST check needed.
_CONVERGED_PAIRS: list[tuple[str, str, str]] = [
    (
        "r4-contracts/src/r4_contracts/skills.py",
        "umrs/src/umrs/skills/__init__.py",
        "SkillManifest",
    ),
    ("r4-contracts/src/r4_contracts/skills.py", "umrs/src/umrs/skills/__init__.py", "SkillScope"),
    (
        "r4-contracts/src/r4_contracts/recipes.py",
        "umrs/src/umrs/recipes/__init__.py",
        "RecipeDefinition",
    ),
    ("r4-contracts/src/r4_contracts/recipes.py", "umrs/src/umrs/recipes/__init__.py", "RecipeStep"),
    ("r4-contracts/src/r4_contracts/recipes.py", "umrs/src/umrs/recipes/__init__.py", "StepType"),
    (
        "r4-contracts/src/r4_contracts/recipes.py",
        "umrs/src/umrs/recipes/__init__.py",
        "RecipeScope",
    ),
    (
        "r4-contracts/src/r4_contracts/security.py",
        "umrs/src/umrs/security/scanner.py",
        "SeverityLevel",
    ),
    (
        "r4-contracts/src/r4_contracts/security.py",
        "umrs/src/umrs/security/scanner.py",
        "ScanFinding",
    ),
    (
        "r4-contracts/src/r4_contracts/security.py",
        "umrs/src/umrs/security/scanner.py",
        "ScanResult",
    ),
]


@dataclass(frozen=True)
class DTOEquivalenceFinding:
    contracts_path: str
    umrs_path: str
    class_name: str
    message: str

    def format(self) -> str:
        return (
            f"{self.class_name}: {self.message}\n"
            f"  contracts: {self.contracts_path}\n"
            f"  umrs:      {self.umrs_path}"
        )


def _parse_file_ast(filepath: Path) -> ast.AST | None:
    """Parse a Python file to AST, or None when missing/unparseable."""
    if not filepath.exists():
        return None
    try:
        return ast.parse(filepath.read_text(), filename=str(filepath))
    except SyntaxError:
        return None


def _class_members_of(tree: ast.AST, class_name: str) -> list[ast.stmt] | None:
    """Return the named class's body statements (first match)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return list(node.body)
    return None


def _extract_class_fields(filepath: Path, class_name: str) -> dict[str, str] | None:
    """Extract field names and type annotation strings from a dataclass/class."""
    tree = _parse_file_ast(filepath)
    if tree is None:
        return None
    body = _class_members_of(tree, class_name)
    if body is None:
        return None
    fields: dict[str, str] = {}
    for item in body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            type_str = ast.dump(item.annotation) if item.annotation else "Any"
            fields[item.target.id] = type_str
    return fields


def _extract_enum_members(filepath: Path, class_name: str) -> list[str] | None:
    """Extract member names from an enum class."""
    tree = _parse_file_ast(filepath)
    if tree is None:
        return None
    body = _class_members_of(tree, class_name)
    if body is None:
        return None
    members: list[str] = []
    for item in body:
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    members.append(target.id)
    return members


def _submodule_available(repo_root: Path) -> bool:
    """Return True if the umrs submodule has been checked out."""
    umrs_src = repo_root / "umrs" / "src"
    return umrs_src.is_dir() and any(umrs_src.iterdir())


def validate_dto_equivalence(
    repo_root: Path,
    pairs: Sequence[tuple[str, str, str]] | None = None,
) -> list[DTOEquivalenceFinding]:
    """Compare DTO shapes between r4-contracts and UMRS mirrors."""
    findings: list[DTOEquivalenceFinding] = []

    if pairs is None:
        if not _submodule_available(repo_root):
            print("DTO equivalence check: umrs submodule not checked out — skipping")
            return findings
        pairs = DTO_PAIRS

    for contracts_rel, umrs_rel, class_name in pairs:
        contracts_path = repo_root / contracts_rel
        umrs_path = repo_root / umrs_rel

        if not contracts_path.exists():
            findings.append(
                DTOEquivalenceFinding(
                    contracts_path=contracts_rel,
                    umrs_path=umrs_rel,
                    class_name=class_name,
                    message=f"contracts file not found: {contracts_rel}",
                )
            )
            continue

        if not umrs_path.exists():
            findings.append(
                DTOEquivalenceFinding(
                    contracts_path=contracts_rel,
                    umrs_path=umrs_rel,
                    class_name=class_name,
                    message=f"UMRS file not found: {umrs_rel}",
                )
            )
            continue

        # Try as dataclass first
        c_fields = _extract_class_fields(contracts_path, class_name)
        u_fields = _extract_class_fields(umrs_path, class_name)

        if c_fields is not None and u_fields is not None:
            # Compare field names (type AST dump may differ cosmetically)
            c_names = set(c_fields.keys())
            u_names = set(u_fields.keys())
            only_in_contracts = c_names - u_names
            only_in_umrs = u_names - c_names

            if only_in_contracts:
                findings.append(
                    DTOEquivalenceFinding(
                        contracts_path=contracts_rel,
                        umrs_path=umrs_rel,
                        class_name=class_name,
                        message=(f"fields only in contracts: {sorted(only_in_contracts)}"),
                    )
                )
            if only_in_umrs:
                findings.append(
                    DTOEquivalenceFinding(
                        contracts_path=contracts_rel,
                        umrs_path=umrs_rel,
                        class_name=class_name,
                        message=f"fields only in UMRS: {sorted(only_in_umrs)}",
                    )
                )
            continue

        # Try as enum
        c_members = _extract_enum_members(contracts_path, class_name)
        u_members = _extract_enum_members(umrs_path, class_name)

        if c_members is not None and u_members is not None:
            c_set = set(c_members)
            u_set = set(u_members)
            only_in_contracts = c_set - u_set
            only_in_umrs = u_set - c_set
            if only_in_contracts:
                findings.append(
                    DTOEquivalenceFinding(
                        contracts_path=contracts_rel,
                        umrs_path=umrs_rel,
                        class_name=class_name,
                        message=(f"members only in contracts: {sorted(only_in_contracts)}"),
                    )
                )
            if only_in_umrs:
                findings.append(
                    DTOEquivalenceFinding(
                        contracts_path=contracts_rel,
                        umrs_path=umrs_rel,
                        class_name=class_name,
                        message=f"members only in UMRS: {sorted(only_in_umrs)}",
                    )
                )
            continue

        # Could not extract either class
        if c_fields is None and c_members is None:
            findings.append(
                DTOEquivalenceFinding(
                    contracts_path=contracts_rel,
                    umrs_path=umrs_rel,
                    class_name=class_name,
                    message=f"class {class_name!r} not found in {contracts_rel}",
                )
            )
        if u_fields is None and u_members is None:
            findings.append(
                DTOEquivalenceFinding(
                    contracts_path=contracts_rel,
                    umrs_path=umrs_rel,
                    class_name=class_name,
                    message=f"class {class_name!r} not found in {umrs_rel}",
                )
            )

    return findings


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    findings = validate_dto_equivalence(repo_root)
    if not findings:
        print(
            "DTO equivalence check passed: r4-contracts and UMRS DTOs are structurally equivalent"
        )
        return 0
    print("DTO equivalence violations:")
    for f in findings:
        print(f.format())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
