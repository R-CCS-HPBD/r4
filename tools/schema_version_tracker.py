"""Track and validate UMRS module schema versions.

Each UMRS module that owns a Postgres schema declares a version constant.
This tool maintains a canonical registry (``tools/schema_versions.yaml``)
and validates that the declared versions match the registry.

Usage:
    python tools/schema_version_tracker.py          # exits 0 on match
    python tools/schema_version_tracker.py --update  # updates registry
    python -m pytest tests/test_schema_version_tracker.py

Enforcement class: PERMANENT
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# Map: module name → (schema file relative to repo, version constant name)
SCHEMA_MODULES: dict[str, tuple[str, str]] = {
    "jobs": ("umrs/src/umrs/jobs/schema.py", "SCHEMA_VERSION"),
    "mcp": ("umrs/src/umrs/mcp/schema.py", "MCP_SCHEMA_VERSION"),
    "model_registry": (
        "umrs/src/umrs/model_registry/schema.py",
        "MODEL_REGISTRY_SCHEMA_VERSION",
    ),
    "context": ("umrs/src/umrs/api/context_schema.py", "CONTEXT_SCHEMA_VERSION"),
}

REGISTRY_PATH = "tools/schema_versions.yaml"


@dataclass(frozen=True)
class SchemaVersionFinding:
    module: str
    expected: int
    actual: int | None
    message: str

    def format(self) -> str:
        return f"{self.module}: {self.message} (registry={self.expected}, code={self.actual})"


def _assign_target_matches(node: ast.Assign, constant_name: str) -> int | None:
    """Return the int value when the assignment targets constant_name."""
    for target in node.targets:
        if (
            isinstance(target, ast.Name)
            and target.id == constant_name
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
        ):
            return node.value.value
    return None


def _extract_version_constant(filepath: Path, constant_name: str) -> int | None:
    """Extract an integer constant from a Python file using AST."""
    if not filepath.exists():
        return None
    try:
        tree = ast.parse(filepath.read_text(), filename=str(filepath))
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        found = _assign_target_matches(node, constant_name)
        if found is not None:
            return found
    return None


def _load_registry(repo_root: Path) -> dict[str, int]:
    """Load schema version registry from YAML."""
    registry_path = repo_root / REGISTRY_PATH
    if not registry_path.exists():
        return {}
    if yaml is None:
        # Fallback: simple YAML parsing for key: value pairs
        versions: dict[str, int] = {}
        for line in registry_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^(\w+):\s*(\d+)", line)
            if match:
                versions[match.group(1)] = int(match.group(2))
        return versions
    return yaml.safe_load(registry_path.read_text()) or {}


def _submodule_available(repo_root: Path) -> bool:
    """Return True if the umrs submodule has been checked out."""
    umrs_src = repo_root / "umrs" / "src"
    return umrs_src.is_dir() and any(umrs_src.iterdir())


def validate_schema_versions(repo_root: Path) -> list[SchemaVersionFinding]:
    """Compare registry versions against code-declared versions."""
    registry = _load_registry(repo_root)
    findings: list[SchemaVersionFinding] = []

    if not _submodule_available(repo_root):
        print("Schema version tracker: umrs submodule not checked out — skipping")
        return findings

    for module_name, (schema_rel, constant_name) in SCHEMA_MODULES.items():
        schema_path = repo_root / schema_rel
        actual = _extract_version_constant(schema_path, constant_name)
        expected = registry.get(module_name)

        if expected is None:
            findings.append(
                SchemaVersionFinding(
                    module=module_name,
                    expected=0,
                    actual=actual,
                    message=f"module not tracked in {REGISTRY_PATH}",
                )
            )
            continue

        if actual is None:
            findings.append(
                SchemaVersionFinding(
                    module=module_name,
                    expected=expected,
                    actual=None,
                    message=f"version constant {constant_name!r} not found in {schema_rel}",
                )
            )
            continue

        if actual != expected:
            findings.append(
                SchemaVersionFinding(
                    module=module_name,
                    expected=expected,
                    actual=actual,
                    message="version mismatch between registry and code",
                )
            )

    return findings


def update_registry(repo_root: Path) -> dict[str, int]:
    """Scan code and write current versions to registry."""
    versions: dict[str, int] = {}
    for module_name, (schema_rel, constant_name) in sorted(SCHEMA_MODULES.items()):
        schema_path = repo_root / schema_rel
        version = _extract_version_constant(schema_path, constant_name)
        if version is not None:
            versions[module_name] = version

    registry_path = repo_root / REGISTRY_PATH
    lines = [
        "# UMRS Module Schema Version Registry",
        "# Auto-generated by tools/schema_version_tracker.py --update",
        "# Do not edit manually — run the tracker to update.",
        "#",
        "# Module schema changes MUST bump the version constant",
        "# in the module's schema.py AND update this registry.",
        "",
    ]
    for module_name, version in sorted(versions.items()):
        lines.append(f"{module_name}: {version}")
    lines.append("")
    registry_path.write_text("\n".join(lines))
    return versions


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]

    if "--update" in sys.argv:
        versions = update_registry(repo_root)
        print(f"Schema version registry updated: {versions}")
        return 0

    findings = validate_schema_versions(repo_root)
    if not findings:
        print("Schema version tracker passed: all versions match registry")
        return 0
    for f in findings:
        print(f.format())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
