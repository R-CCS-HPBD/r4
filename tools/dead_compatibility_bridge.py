"""Drift detector: dead compatibility bridge detector.

Scans for @deprecated decorators or compatibility shims (e.g. _legacy_, _compat_)
that have zero importers. Reports bridges with last importer date,
suggesting removal candidates.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DeadBridge:
    name: str
    path: str
    kind: str
    reason: str
    last_importer: str | None = None
    last_imported: str | None = None


def _is_valid_py_file(path: Path) -> bool:
    parts = str(path).split(os.sep)
    for p in parts:
        if p.startswith(".") or p.startswith("#"):
            return False
    return path.suffix == ".py"


def find_deprecated_items(root: Path) -> list[dict[str, Any]]:
    deprecated = []
    for py_file in root.rglob("*.py"):
        if not _is_valid_py_file(py_file):
            continue
        try:
            source = py_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source, filename=str(py_file))
            rel_path = str(py_file.relative_to(root))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for decorator in node.decorator_list:
                        name = ""
                        if isinstance(decorator, ast.Name) and decorator.id in (
                            "deprecated",
                            "Deprecated",
                        ):
                            name = decorator.id
                        elif isinstance(decorator, ast.Attribute) and decorator.attr in (
                            "deprecated",
                            "Deprecated",
                        ):
                            name = decorator.attr
                        if name:
                            deprecated.append(
                                {
                                    "name": node.name,
                                    "path": rel_path,
                                    "kind": "function",
                                    "reason": f"@{name} decorator",
                                }
                            )
                elif isinstance(node, ast.ClassDef):
                    if "_legacy" in node.name or "_compat" in node.name:
                        deprecated.append(
                            {
                                "name": node.name,
                                "path": rel_path,
                                "kind": "class",
                                "reason": "contains '_legacy' or '_compat' in name",
                            }
                        )
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if "_legacy" in node.name or "_compat" in node.name:
                        deprecated.append(
                            {
                                "name": node.name,
                                "path": rel_path,
                                "kind": "function",
                                "reason": "contains '_legacy' or '_compat' in name",
                            }
                        )
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and (
                            "_legacy" in target.id or "_compat" in target.id
                        ):
                            deprecated.append(
                                {
                                    "name": target.id,
                                    "path": rel_path,
                                    "kind": "variable",
                                    "reason": "contains '_legacy' or '_compat' in name",
                                }
                            )
        except SyntaxError:
            continue
    return deprecated


def find_importers(root: Path, item_name: str, item_path: str) -> list[str]:
    importers = []
    for py_file in root.rglob("*.py"):
        if not _is_valid_py_file(py_file):
            continue
        try:
            if str(py_file.relative_to(root)) == item_path:
                continue
        except ValueError:
            pass
        try:
            source = py_file.read_text(encoding="utf-8", errors="ignore")
            if f"import {item_name}" in source or f"from ... {item_name}" in source:
                importers.append(str(py_file.relative_to(root)))
        except Exception:
            continue
    return importers


def get_last_imported_date(root: Path, file_path: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci,%H", "--", file_path],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(",")
            return f"{parts[0][:10]} (commit {parts[1][:8]})"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def check_dead_bridges(root: Path = Path(__file__).parent.parent.parent) -> list[DeadBridge]:
    deprecated_items = find_deprecated_items(root)
    dead_bridges = []
    for item in deprecated_items:
        importers = find_importers(root, item["name"], item["path"])
        if not importers:
            last_imported = get_last_imported_date(root, item["path"])
            dead_bridges.append(
                DeadBridge(
                    name=item["name"],
                    path=item["path"],
                    kind=item["kind"],
                    reason=item["reason"],
                    last_imported=last_imported,
                )
            )
    return dead_bridges


def main() -> int:
    dead = check_dead_bridges()
    if dead:
        print(f"Found {len(dead)} dead compatibility bridges:")
        for b in dead:
            print(f"  - {b.kind} '{b.name}' in {b.path}")
            print(f"    reason: {b.reason}")
            if b.last_imported:
                print(f"    last modified: {b.last_imported}")
            print("    safe to remove")
        print("\nReview and remove these dead bridges.")
        return 1
    else:
        print("No dead compatibility bridges found.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
