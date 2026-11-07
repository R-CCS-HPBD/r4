#!/usr/bin/env python3
"""Metrics instrumentation audit — compare defined metric methods vs call sites.

Scans umrs/src/umrs/metrics.py for public methods and checks which are
actually called in production code. Reports uninstrumented methods.

Usage:
    python tools/metrics_instrumentation_audit.py        # Human summary
    python tools/metrics_instrumentation_audit.py --json # JSON output
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
METRICS_FILE = REPO_ROOT / "umrs" / "src" / "umrs" / "metrics.py"
SEARCH_DIR = REPO_ROOT / "umrs" / "src" / "umrs"

# Methods that are internal helpers, not instrumentation points
EXCLUDED_METHODS = {"__init__", "inc", "set", "observe", "labels"}


def extract_public_methods(metrics_path: Path) -> list[str]:
    """Extract public method names from UMRSMetrics class."""
    source = metrics_path.read_text()
    tree = ast.parse(source)
    methods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "UMRSMetrics":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = item.name
                    if not name.startswith("_") and name not in EXCLUDED_METHODS:
                        methods.append(name)
    return sorted(methods)


def find_call_sites(search_dir: Path, method_name: str) -> list[str]:
    """Find files that call METRICS.<method_name>."""
    sites = []
    for py_file in search_dir.rglob("*.py"):
        if py_file == METRICS_FILE:
            continue
        if "__pycache__" in str(py_file):
            continue
        try:
            content = py_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if f"METRICS.{method_name}" in content:
            sites.append(str(py_file.relative_to(REPO_ROOT)))
    return sorted(sites)


def run_audit() -> dict:
    """Run the full instrumentation audit."""
    methods = extract_public_methods(METRICS_FILE)
    results = {}
    for method in methods:
        sites = find_call_sites(SEARCH_DIR, method)
        results[method] = sites

    instrumented = [m for m, s in results.items() if s]
    uninstrumented = [m for m, s in results.items() if not s]

    return {
        "total_methods": len(methods),
        "instrumented": len(instrumented),
        "uninstrumented_count": len(uninstrumented),
        "uninstrumented": uninstrumented,
        "coverage_pct": round(len(instrumented) / len(methods) * 100, 1) if methods else 0,
        "details": results,
    }


def main() -> int:
    use_json = "--json" in sys.argv

    audit = run_audit()

    if use_json:
        print(json.dumps(audit, indent=2))
    else:
        print("Metrics Instrumentation Audit")
        print("=" * 60)
        print(
            f"Defined: {audit['total_methods']} | "
            f"Instrumented: {audit['instrumented']} | "
            f"Coverage: {audit['coverage_pct']}%"
        )
        if audit["uninstrumented"]:
            print(f"\nUninstrumented methods ({audit['uninstrumented_count']}):")
            for m in audit["uninstrumented"]:
                print(f"  - {m}")
        else:
            print("\nAll metric methods have call sites.")
        print()

        # Show call site summary
        print(f"{'Method':<30} {'Call Sites':>10}")
        print("-" * 42)
        for method, sites in sorted(audit["details"].items()):
            print(f"{method:<30} {len(sites):>10}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
