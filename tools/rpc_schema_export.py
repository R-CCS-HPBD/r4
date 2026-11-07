#!/usr/bin/env python3
"""Companion bridge RPC result-schema exporter (plan H-4.3).

Walks ``RPC_RESULT_TYPES`` in ``r4/src/r4/companion/ui/rpc_schema.py`` and
emits one JSON Schema document per covered RPC verb into
``contracts/bridge/v1/<verb>.schema.json``. These files are the published
result contract of the JSONL bridge; the v3 TUI's
``src/bridge/rpc-types.gen.ts`` is generated from them
(``tools/rpc_types_gen.py``), never from the Python source.

Modes (mirrors ``tools/control_schema_export.py``):
  --write    regenerate the artifacts in place (drift fixer)
  --check    exit 1 when the artifacts drift from the code (default; CI)

A stale file (a verb no longer covered) counts as drift too.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "contracts" / "bridge" / "v1"
_SUFFIX = ".schema.json"


def _schema_module() -> Any:
    src = str(REPO_ROOT / "r4" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    return importlib.import_module("r4.companion.ui.rpc_schema")


def export_all() -> dict[str, str]:
    """Compute {filename: content} for every covered RPC verb."""
    module = _schema_module()
    return {
        f"{method}{_SUFFIX}": json.dumps(module.result_schema(method), indent=2) + "\n"
        for method in sorted(module.RPC_RESULT_TYPES)
    }


def drifted(out_dir: Path) -> list[str]:
    """Artifact names that are missing, stale or no longer produced."""
    artifacts = export_all()
    stale = [
        path.name for path in sorted(out_dir.glob(f"*{_SUFFIX}")) if path.name not in artifacts
    ]
    changed = [
        name
        for name, content in sorted(artifacts.items())
        if not (out_dir / name).exists() or (out_dir / name).read_text(encoding="utf-8") != content
    ]
    return changed + stale


def cmd_write(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = export_all()
    for path in out_dir.glob(f"*{_SUFFIX}"):
        if path.name not in artifacts:
            path.unlink()
    for name, content in artifacts.items():
        (out_dir / name).write_text(content, encoding="utf-8")
    print(f"Wrote {len(artifacts)} bridge RPC schemas to {out_dir}")
    return 0


def cmd_check(out_dir: Path) -> int:
    stale = drifted(out_dir)
    if stale:
        print(f"DRIFT: {len(stale)} bridge RPC schema(s) stale: {stale}")
        print("Run: python tools/rpc_schema_export.py --write")
        return 1
    print(f"OK: {len(export_all())} bridge RPC schemas in sync with code")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)
    if args.write:
        return cmd_write(args.out)
    return cmd_check(args.out)


if __name__ == "__main__":
    sys.exit(main())
