#!/usr/bin/env python3
"""Accessibility matrix audit — which r4 / umrs surfaces the current TUI
(companion `ui/bridge_server.py` + slash catalog) can reach, mechanically
derived from source.

Usage: `.venv/bin/python tools/audit_tui_accessibility.py`
Reads-only. Emits tables + writes `docs/verification/tui-accessibility-matrix-*.md`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def bridge_rpc_names() -> list[str]:
    src = read(ROOT / "r4/src/r4/companion/ui/bridge_server.py")
    m = re.search(
        r"BRIDGE_RPC_NAMES: frozenset\[str\] = frozenset\(\s*\{(.*?)\}\s*\)", src, re.DOTALL
    )
    if not m:
        raise SystemExit("BRIDGE_RPC_NAMES block not found")
    return sorted(re.findall(r'"([a-z0-9_./]+)"', m.group(1)))


def bridge_dispatch_verbs() -> list[str]:
    src = read(ROOT / "r4/src/r4/companion/ui/bridge_server.py")
    m = re.search(r"_dispatch_table_compiled\(\)\s*.*?^\s*\}\s*$", src, re.DOTALL | re.MULTILINE)
    block = m.group(0) if m else src
    return sorted(set(re.findall(r'"([a-z0-9_./]+)":\s*(?:server\.\w+|lambda)', block)))


def client_methods(path: Path) -> list[str]:
    tree = ast.parse(read(path))
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and not node.name.startswith("_")
            and node.name
            not in {
                "from_env",
                "close",
            }
        ):
            out.append(node.name)
    return sorted(set(out))


def umrs_router_routes() -> dict[str, list[str]]:
    """Return router-file -> list of (METHOD path) pairs."""
    api_dir = ROOT / "umrs/src/umrs/api"
    out: dict[str, list[str]] = {}
    for f in sorted(api_dir.glob("*.py")):
        if f.name.startswith("_") or f.name.endswith(".pyc"):
            continue
        src = read(f)
        routes: list[str] = []
        for m in re.finditer(r"@router\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)[\"']", src):
            routes.append(f"{m.group(1).upper()} {m.group(2)}")
        if routes:
            out[f.name] = routes
    return out


def slash_top_level() -> list[str]:
    src = read(ROOT / "packages/r4-tui-v3/src/commands/slash.ts")
    # top-level `name:` rows sit at two-space indentation; subs are deeper.
    names = re.findall(r"^      name: \"([a-zA-Z][a-zA-Z0-9_-]+)\",", src, re.MULTILINE)
    return sorted(set(names))


def main() -> None:
    bridge = bridge_rpc_names()
    dispatch = bridge_dispatch_verbs()
    umrs = umrs_router_routes()
    slash = slash_top_level()
    client_top = client_methods(ROOT / "r4/src/r4/client.py")

    # flatten umrs routes
    umrs_flat: list[str] = []
    for fname, paths in umrs.items():
        for p in paths:
            umrs_flat.append(f"{fname[:18]:<19} {p}")

    print("## TUI ACCESSIBILITY AUDIT — 2026-09-19 (mechanically derived)\n")
    print(f"TUI bridge verbs (BRIDGE_RPC_NAMES):          {len(bridge)}")
    print(f"dispatch-table registrations:                 {len(dispatch)}")
    print(f"r4 client.py public top-level methods:        {len(client_top)}")
    print(f"umrs routers with routes:                     {len(umrs)}")
    print(f"umrs total routes (router.@ decorators):      {len(umrs_flat)}")
    print(f"TUI top-level slash names:                    {len(slash)}\n")

    print("### TUI top-level slash names")
    for n in slash:
        print(f"  /{n}")

    print("\n### Bridge verbs (BRIDGE_RPC_NAMES, sorted)")
    for v in bridge:
        print(f"  {v}")

    print("\n### r4 client.py public methods (sweep candidates)")
    for m in client_top:
        print(f"  {m}")

    print("\n### umrs routes by router-file (sweep candidates)")
    for fname, paths in umrs.items():
        print(f"  # {fname} ({len(paths)} routes)")
        for p in paths:
            print(f"    {p}")


if __name__ == "__main__":
    main()
