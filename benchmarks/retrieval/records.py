"""Aggregate records of eval runs, safe to commit (ADR-093 MD-10, 2026-10-11).

A result file under ``results/`` carries per-claim rows derived from SciFact
(CC BY-NC 2.0) and is never committed. The maintainer decided (MD-10) that
aggregate metrics may be: this module keeps only what a run measured in
aggregate — the arm, the claim set, the label, the model, the preset, the
code commits and digests, and every NUMBER of the summary — and drops every
row and every string a claim or a passage could hide in.

    .venv/bin/python benchmarks/retrieval/records.py results/*.json \\
        --out records/adr093-2026-10-11.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

RECORD_VERSION = 1
#: Top-level fields kept as they are: short identifiers, never claim text.
META_KEYS = (
    "schema_version",
    "arm",
    "kind",
    "claims_set",
    "label",
    "at",
    "model",
    "reader_model",
    "prompt",
    "order",
    "threshold",
    "scorer_gate",
    "verdict_max_tokens",
)
#: Provenance fields kept: commits, dirty flags and content digests (no paths).
PROVENANCE_KEYS = ("parent_commit", "parent_dirty", "umrs_commit", "umrs_dirty")
_DIGEST_KEYS = frozenset({"commit", "dirty", "sha256", "status_sha256", "diff_sha256", "files"})
_MAX_ID_CHARS = 64


def numbers_only(value: Any) -> Any:
    """``value`` with every leaf that is not a number, a bool or None dropped
    (dicts and lists keep their shape; a list left empty stays empty)."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return _numeric_dict(value)
    if isinstance(value, (list, tuple)):
        return _kept([numbers_only(x) for x in value])
    return _DROP


def _numeric_dict(value: dict) -> dict[str, Any]:
    short = {str(k): v for k, v in value.items() if _short_key(k)}
    return _kept_items({k: numbers_only(v) for k, v in short.items()})


def _kept(values: list[Any]) -> list[Any]:
    return [v for v in values if v is not _DROP]


def _kept_items(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not _DROP}


def _short_key(key: Any) -> bool:
    """A metric or lane name, never a sentence (a key could carry text too)."""
    text = str(key)
    return len(text) <= _MAX_ID_CHARS and not any(ch.isspace() for ch in text)


class _Dropped:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<dropped>"


_DROP: Any = _Dropped()


def _identifier(value: Any) -> Any:
    """A short identifier string, a number, a bool or None; else dropped."""
    if isinstance(value, str):
        return value if len(value) <= _MAX_ID_CHARS and "\n" not in value else _DROP
    return numbers_only(value)


def _digests(code: Any) -> dict[str, Any]:
    """Per code tree: its commit, dirty flag and content digests, no paths."""
    if not isinstance(code, dict):
        return {}
    out: dict[str, Any] = {}
    for tree, facts in code.items():
        if isinstance(facts, dict):
            kept = {k: _identifier(v) for k, v in facts.items() if k in _DIGEST_KEYS}
            out[str(tree)] = {k: v for k, v in kept.items() if v is not _DROP}
    return out


def aggregate(record: dict[str, Any], *, source: str = "") -> dict[str, Any]:
    """The committable aggregate of one result file."""
    provenance = record.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    return {
        "source": Path(source).name if source else "",
        **_identifiers(record, META_KEYS),
        "preset": numbers_only(record.get("preset") or {}) or {},
        "provenance": {
            **_identifiers(provenance, PROVENANCE_KEYS),
            "code": _digests(provenance.get("code")),
        },
        "summary": numbers_only(record.get("summary") or {}) or {},
    }


def _identifiers(source: dict[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    present = {k: _identifier(source[k]) for k in keys if k in source}
    return _kept_items(present)


def build(paths: Sequence[Path]) -> dict[str, Any]:
    """One record over several result files, ordered by file name."""
    runs = [aggregate(json.loads(p.read_text()), source=str(p)) for p in sorted(paths)]
    return {"record_version": RECORD_VERSION, "runs": runs}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    record = build(args.results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(f"{len(record['runs'])} runs -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
