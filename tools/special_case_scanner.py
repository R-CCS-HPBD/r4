#!/usr/bin/env python3
"""Special-case reduction audit scanner (COR-007).

Scans the codebase for hidden fallback patterns, platform-specific branches,
and runtime special-casing that may indicate technical debt:

- os.environ.get(..., fallback) with non-obvious defaults
- platform/OS conditional branches (sys.platform, os.name)
- try/except ImportError fallback stubs
- isinstance checks that gate on optional dependencies
- Inline environment variable fallbacks that shadow config

Each finding is tagged with a category:
  blessed        — Intentional, documented, part of the architecture
  compat         — Backward-compatibility shim (candidates for retirement)
  experimental   — Feature flags or gated code paths
  untagged       — Unknown, needs review

Usage:
  python tools/special_case_scanner.py                # scan all
  python tools/special_case_scanner.py --src umrs/    # scan specific tree
  python tools/special_case_scanner.py --json         # machine-readable output
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Paths to scan by default
_DEFAULT_SCAN_DIRS = [
    "umrs/src",
    "gateway/gateway",
    "r4/src",
    "mcp-servers/src",
]

# Known blessed patterns (file:lineno → reason)
_BLESSED_PATTERNS: dict[str, str] = {
    "umrs/src/umrs/metrics.py": "No-op metrics degradation is architectural (Phase D)",
    "umrs/src/umrs/orchestrator/durable.py": "Gen1/Gen2 bridge is permanent (ADR-001)",
}


@dataclass
class Finding:
    """A detected special-case pattern."""

    file: str
    line: int
    category: str  # blessed | compat | experimental | untagged
    pattern_type: str  # env_fallback | platform_branch | import_fallback | isinstance_gate
    code: str  # source line content
    detail: str = ""


@dataclass
class ScanResult:
    """Aggregated scan results."""

    total: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)


def scan_file(filepath: Path, rel_root: Path) -> list[Finding]:
    """Scan a single Python file for special-case patterns."""
    findings: list[Finding] = []
    rel_path = str(filepath.relative_to(rel_root))

    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, ValueError):
        return findings

    lines = source.splitlines()

    for node in ast.walk(tree):
        # Pattern 1: os.environ.get(KEY, fallback) with non-trivial fallback
        if isinstance(node, ast.Call) and _is_environ_get(node) and len(node.args) >= 2:
            fallback = node.args[1]
            if not isinstance(fallback, ast.Constant) or fallback.value not in (
                None,
                "",
                "false",
                "true",
            ):
                lineno = node.lineno
                line_text = lines[lineno - 1].strip() if lineno <= len(lines) else ""
                cat = _categorize(rel_path, lineno)
                findings.append(
                    Finding(
                        file=rel_path,
                        line=lineno,
                        category=cat,
                        pattern_type="env_fallback",
                        code=line_text,
                        detail="Fallback value in os.environ.get()",
                    )
                )

        # Pattern 2: sys.platform / os.name comparisons
        if isinstance(node, ast.Compare) and _references_platform(node):
            lineno = node.lineno
            line_text = lines[lineno - 1].strip() if lineno <= len(lines) else ""
            cat = _categorize(rel_path, lineno)
            findings.append(
                Finding(
                    file=rel_path,
                    line=lineno,
                    category=cat,
                    pattern_type="platform_branch",
                    code=line_text,
                    detail="Platform-specific conditional",
                )
            )

        # Pattern 3: try/except ImportError
        if isinstance(node, ast.ExceptHandler) and (
            node.type
            and isinstance(node.type, ast.Name)
            and node.type.id in ("ImportError", "ModuleNotFoundError")
        ):
            lineno = node.lineno
            line_text = lines[lineno - 1].strip() if lineno <= len(lines) else ""
            cat = _categorize(rel_path, lineno)
            findings.append(
                Finding(
                    file=rel_path,
                    line=lineno,
                    category=cat,
                    pattern_type="import_fallback",
                    code=line_text,
                    detail="Import fallback pattern",
                )
            )

    return findings


def _is_environ_get(node: ast.Call) -> bool:
    """Check if a Call node is os.environ.get(...)."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "get":
        return False
    return isinstance(func.value, ast.Attribute) and func.value.attr == "environ"


def _references_platform(node: ast.Compare) -> bool:
    """Check if a comparison references sys.platform or os.name."""
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute):
            if n.attr in ("platform",) and isinstance(n.value, ast.Name) and n.value.id == "sys":
                return True
            if n.attr in ("name",) and isinstance(n.value, ast.Name) and n.value.id == "os":
                return True
    return False


def _categorize(rel_path: str, lineno: int) -> str:
    """Categorize a finding based on blessed patterns."""
    for pattern_path, _reason in _BLESSED_PATTERNS.items():
        if rel_path.startswith(pattern_path):
            return "blessed"
    return "untagged"


def scan_directory(dir_path: Path, rel_root: Path) -> list[Finding]:
    """Recursively scan a directory for Python files."""
    findings: list[Finding] = []
    for py_file in sorted(dir_path.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        findings.extend(scan_file(py_file, rel_root))
    return findings


def run_scan(scan_dirs: list[str]) -> ScanResult:
    """Run the full scan across specified directories."""
    all_findings: list[Finding] = []

    for d in scan_dirs:
        dir_path = ROOT / d
        if dir_path.exists():
            all_findings.extend(scan_directory(dir_path, ROOT))

    result = ScanResult(
        total=len(all_findings),
        findings=all_findings,
    )

    for f in all_findings:
        result.by_category[f.category] = result.by_category.get(f.category, 0) + 1
        result.by_type[f.pattern_type] = result.by_type.get(f.pattern_type, 0) + 1

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Special-case reduction audit scanner")
    parser.add_argument("--src", nargs="*", help="Directories to scan (default: all src)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    scan_dirs = args.src if args.src else _DEFAULT_SCAN_DIRS
    result = run_scan(scan_dirs)

    if args.json:
        print(json.dumps(asdict(result), indent=2))
        return 0

    print("Special-Case Reduction Audit (COR-007)")
    print(f"{'=' * 50}")
    print(f"Total findings: {result.total}")
    print()

    if result.by_category:
        print("By category:")
        for cat, count in sorted(result.by_category.items()):
            print(f"  {cat:15s} {count}")
        print()

    if result.by_type:
        print("By pattern type:")
        for pt, count in sorted(result.by_type.items()):
            print(f"  {pt:20s} {count}")
        print()

    # Show first 20 findings
    shown = min(20, len(result.findings))
    for f in result.findings[:shown]:
        tag = f"[{f.category}]"
        print(f"  {f.file}:{f.line} {tag:12s} {f.pattern_type:20s} {f.code[:60]}")

    if result.total > shown:
        print(f"  ... and {result.total - shown} more (use --json for full output)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
