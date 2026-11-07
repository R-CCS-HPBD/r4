#!/usr/bin/env python3
"""Control-event schema exporter (ADR-035 plan track 3, Review F5).

Walks the dataclasses in ``r4/src/r4/companion/control_schema.py`` and emits
one JSON Schema artifact per event type into ``contracts/control/v1/``.
These artifacts are the published contract for the JSONL bridge: clients
(TypeScript TUI, ACP, Jupyter) generate their types from THESE files, not
from the Python source — "wire only" becomes a tested guarantee.

Modes:
  --write    regenerate artifacts in place (drift fixer)
  --check    exit 1 when artifacts drift from code (CI gate)

Field typing derives from dataclass annotations; literal constraints come
from each event's ``_require(...)`` allow-sets (encoded via ``Choices``
markers in docstrings is NOT used — sets live in code constants).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import typing
from pathlib import Path
from typing import Any, Union, get_args, get_origin

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "contracts" / "control" / "v1"

_PY_TO_JSON = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "dict": {"type": "object"},
    "Dict": {"type": "object"},
    "list": {"type": "array"},
    "List": {"type": "array"},
}

# Enum allow-sets from control_schema.py constants → JSON enum values.
# Keeping them here (not re-importing private names) makes the artifact
# content reviewable in one diff.
_ENUM_MAP: dict[str, list[str]] = {
    "risk_tier": ["risky", "never_auto"],
    "mode": ["ask_always", "auto_safe", "deny", "trust"],
    "source": ["user", "autopilot", "acp", "policy"],
    "decision": ["once", "always", "no"],
    "status": ["done", "error", "cancelled"],
}


def _union_schema(args: tuple) -> dict[str, Any]:
    """Union annotation → anyOf; Optional[X] → X|null."""
    non_none = [a for a in args if a is not type(None)]
    if len(non_none) == 1 and len(args) == 2:
        return {"anyOf": [_annotation_to_schema(non_none[0]), {"type": "null"}]}
    return {"anyOf": [_annotation_to_schema(a) for a in non_none]}


def _sequence_schema(annotation: Any, args: tuple) -> dict[str, Any]:
    item_schema = _annotation_to_schema(args[0]) if args else {}
    return {"type": "array", **({"items": item_schema} if item_schema else {})}


def _annotation_to_schema(annotation: Any) -> dict[str, Any]:
    """Map a dataclass annotation to a JSON-schema fragment."""
    origin = get_origin(annotation)
    if origin is Union:
        return _union_schema(get_args(annotation))
    if origin in (list, tuple) or annotation is list:
        return _sequence_schema(annotation, get_args(annotation))
    if origin is dict or annotation is dict:
        return {"type": "object"}
    name = getattr(annotation, "__name__", str(annotation))
    return dict(_PY_TO_JSON.get(name, {}))


def _field_schema(name: str, resolved_type: Any, f: dataclasses.Field) -> dict[str, Any]:
    schema = _annotation_to_schema(resolved_type)
    enum = _ENUM_MAP.get(name)
    if enum is not None:
        schema["enum"] = enum
    if f.default is not dataclasses.MISSING:
        schema["default"] = f.default
    return schema


def event_schema(cls: type) -> dict[str, Any]:
    """Emit the JSON Schema artifact for one event's WIRE ENVELOPE.

    Envelope shape on the JSONL rail: ``{"type": <EVENT>, "payload": {...}}``.
    Payload fields describe the dataclass; unknown payload keys tolerated.
    """
    hints = typing.get_type_hints(cls)
    payload_props: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name == "EVENT":
            continue
        payload_props[f.name] = _field_schema(f.name, hints.get(f.name, f.type), f)
    doc_lines = (cls.__doc__ or "").strip().splitlines()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://r4.dev/contracts/control/v1/{cls.EVENT}.schema.json",  # type: ignore[attr-defined]
        "title": cls.EVENT,  # type: ignore[attr-defined]
        "description": doc_lines[0] if doc_lines else "",
        "type": "object",
        "additionalProperties": True,  # forward-compat: unknown envelope keys ignored
        "required": ["type", "payload"],
        "properties": {
            "type": {"const": cls.EVENT},  # type: ignore[attr-defined]
            "payload": {
                "type": "object",
                "additionalProperties": True,
                "properties": payload_props,
            },
        },
    }


def _load_events() -> dict[str, type]:
    sys.path.insert(0, str(REPO_ROOT / "r4" / "src"))
    import importlib

    module = importlib.import_module("r4.companion.control_schema")
    return dict(module._EVENT_TYPES)


def export_all(out_dir: Path) -> dict[str, str]:
    """Compute {filename: content} for every known event type."""
    artifacts: dict[str, str] = {}
    for name, cls in sorted(_load_events().items()):
        artifacts[f"{name}.schema.json"] = json.dumps(event_schema(cls), indent=2) + "\n"
    return artifacts


def cmd_write(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = export_all(out_dir)
    for name, content in artifacts.items():
        (out_dir / name).write_text(content, encoding="utf-8")
    print(f"Wrote {len(artifacts)} schema artifacts to {out_dir}")
    return 0


def cmd_check(out_dir: Path) -> int:
    artifacts = export_all(out_dir)
    drifted = []
    for name, content in sorted(artifacts.items()):
        target = out_dir / name
        actual = target.read_text(encoding="utf-8") if target.exists() else None
        if actual != content:
            drifted.append(name)
    if drifted:
        print(f"DRIFT: {len(drifted)} artifact(s) stale: {drifted}")
        print("Run: tools/control_schema_export.py --write")
        return 1
    print(f"OK: {len(artifacts)} artifacts in sync with code")
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
