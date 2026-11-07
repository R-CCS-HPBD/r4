"""Harness benchmark trend comparison (harness-benchmarking plan Phase 3).

Scans a results root (<root>/<stamp>/<suite>/*.json) and compares the
latest run's per-cell pass rate against the best of the recent history.
Exits 1 when any (harness, ablation) cell regressed beyond --threshold —
that is the weekly lane's regression alert.

Usage:
    python -m benchmarks.harness.trend \
        --root benchmarks/results/harness --suite sample_coding \
        [--threshold 0.05] [--history 8]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def _load_runs(root: Path, suite: str, history: int) -> list[list[dict]]:
    """Outcome payloads per run dir, oldest first, capped at history+1."""
    runs: list[tuple[str, list[dict]]] = []
    for stamp_dir in sorted(root.iterdir()):
        suite_dir = stamp_dir / suite
        if not suite_dir.is_dir():
            continue
        outcomes = [json.loads(p.read_text()) for p in sorted(suite_dir.glob("*.json"))]
        if outcomes:
            runs.append((stamp_dir.name, outcomes))
    return [outcomes for _, outcomes in runs[-(history + 1) :]]


def _pass_rates(outcomes: list[dict]) -> dict[tuple[str, str], tuple[int, int]]:
    """(harness, ablation) -> (passed, total)."""
    rates: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for outcome in outcomes:
        cell = (str(outcome.get("harness")), str(outcome.get("ablation", "full")))
        rates[cell][1] += 1
        if outcome.get("passed"):
            rates[cell][0] += 1
    return {cell: (v[0], v[1]) for cell, v in rates.items()}


def _cell_trend(
    cell: tuple[str, str],
    passed: int,
    total: int,
    history_rates: list[dict[tuple[str, str], tuple[int, int]]],
    threshold: float,
) -> tuple[str, str | None]:
    """One cell's trend line and regression note (None when clean)."""
    rate = passed / total if total else 0.0
    past = [h[cell] for h in history_rates if cell in h]
    best = max((p / t for p, t in past if t), default=None)
    if best is not None and rate < best - threshold:
        marker = f"  REGRESSION (best history {best:.0%})"
        note = f"{cell[0]}/{cell[1]}: {rate:.0%} vs best {best:.0%}"
    else:
        marker, note = "", None
    return f"{cell[0]}/{cell[1]}: {passed}/{total} ({rate:.0%}){marker}", note


def compare(root: Path, suite: str, threshold: float, history: int) -> tuple[list[str], list[str]]:
    """Return (trend lines, regression lines). No history -> all-clear."""
    runs = _load_runs(root, suite, history)
    if not runs:
        return ["no runs found"], []
    latest = _pass_rates(runs[-1])
    history_rates = [_pass_rates(run) for run in runs[:-1]]
    trend: list[str] = []
    regressions: list[str] = []
    for cell, (passed, total) in sorted(latest.items()):
        line, note = _cell_trend(cell, passed, total, history_rates, threshold)
        trend.append(line)
        if note is not None:
            regressions.append(note)
    return trend, regressions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="r4-harness-trend")
    parser.add_argument("--root", default="benchmarks/results/harness")
    parser.add_argument("--suite", required=True)
    parser.add_argument("--threshold", type=float, default=0.05)
    parser.add_argument("--history", type=int, default=8)
    args = parser.parse_args(argv)

    trend, regressions = compare(Path(args.root), args.suite, args.threshold, args.history)
    print("trend:")
    for line in trend:
        print(f"  {line}")
    if regressions:
        print("REGRESSION ALERT:")
        for line in regressions:
            print(f"  {line}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
