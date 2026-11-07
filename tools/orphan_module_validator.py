"""Drift detector: orphan module detector.

Scans r4/, gateway/, mcp-servers/, umrs/ for Python packages (dirs with __init__.py)
that are NOT imported by any other package in the repo. Reports orphans with
last seen date (git log).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OrphanPackage:
    """A Python package that appears to be unused."""

    path: str
    module: str
    last_seen: str | None = None


def _is_valid_py_file(path: Path) -> bool:
    parts = str(path).split(os.sep)
    for p in parts:
        if p.startswith(".") or p.startswith("#"):
            return False
    return path.suffix == ".py"


def find_all_python_packages(root: Path) -> dict[str, Path]:
    packages = {}
    scan_dirs = ["r4/src", "gateway/gateway", "mcp-servers/src", "umrs/src"]
    for scan_dir in scan_dirs:
        base = root / scan_dir
        if not base.exists():
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            if "__init__.py" in filenames:
                rel = os.path.relpath(dirpath, root)
                module = rel.replace(os.sep, ".").replace("/", ".")
                if module.endswith(".__init__"):
                    module = module[:-9]
                elif module.endswith("__init__"):
                    module = module[:-8]
                packages[module] = Path(dirpath)
    return packages


def find_all_imports(root: Path) -> set[str]:
    imports = set()
    for py_file in root.rglob("*.py"):
        if not _is_valid_py_file(py_file):
            continue
        try:
            source = py_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
        except SyntaxError:
            continue
    return imports


def get_last_seen(path: Path, root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci,%H", "--", str(path)],
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


def check_orphans(root: Path = Path(__file__).parent.parent.parent) -> list[OrphanPackage]:
    all_packages = find_all_python_packages(root)
    all_imports = find_all_imports(root)
    orphans = []
    for module, path in all_packages.items():
        is_used = False
        pkg_parts = module.split(".")
        for imp in all_imports:
            if imp in pkg_parts or pkg_parts[0] == imp:
                is_used = True
                break
        if not is_used:
            for py_file in root.rglob("*.py"):
                if not _is_valid_py_file(py_file):
                    continue
                try:
                    source = py_file.read_text(encoding="utf-8", errors="ignore")
                    if f"from .{path.name}" in source or f"from ..{path.name}" in source:
                        is_used = True
                        break
                except Exception:
                    continue
        if not is_used:
            init_file = Path(str(path) + "/__init__.py")
            last_seen = get_last_seen(init_file, root)
            orphans.append(OrphanPackage(path=str(path), module=module, last_seen=last_seen))
    return orphans


def main() -> int:
    orphans = check_orphans()
    if orphans:
        print(f"Found {len(orphans)} orphaned packages:")
        for o in orphans:
            print(f"  - {o.module} ({o.path})")
            if o.last_seen:
                print(f"    last seen: {o.last_seen}")
        print("\nConsider removing these unused packages.")
        return 1
    else:
        print("No orphaned packages found.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
