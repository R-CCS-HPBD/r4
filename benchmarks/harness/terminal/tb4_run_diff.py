"""Diff two R4 Terminal-Bench 4.0 runs task by task (markdown on stdout).

    .venv/bin/python benchmarks/harness/terminal/tb4_run_diff.py \
        --base benchmarks/results/terminal-bench/tb40-20261004/baseline_trials.jsonl \
        --new <tb4_trial_diagnostics.py JSON lines of the new run> \
        [--base-label "before W1–W8"] [--new-label "after W1–W8"]

Both inputs are ``tb4_trial_diagnostics.py`` records (one JSON object per task).
A record is *valid* when the trial reached the verifier (a reward) and was not
cancelled. Accuracy is reported on each run's own valid tasks, on the tasks
valid in both runs (the like-for-like diff), and over the 63 runnable tasks
with every missing or invalid trial scored 0, as on the public board.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from benchmarks.harness.terminal.tb4_public_compare import PUBLIC, VALS_CATEGORY, wilson

RUNNABLE_TASKS = 63
INVALID_EXCEPTIONS = {"CancelledError"}
FLAG_NAMES = ("digest", "gave_up", "asks_user", "timeout_seen")


def load(path: Path) -> dict[str, dict[str, Any]]:
    records = {}
    for line in path.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            records[rec["task"]] = rec
    return records


def valid(rec: dict[str, Any] | None) -> bool:
    """Reached the verifier, not cancelled, and not killed from the host side."""
    return (
        bool(rec)
        and rec.get("reward") is not None
        and rec.get("exception") not in INVALID_EXCEPTIONS
        and not rec.get("infra_kill")
    )


def solved(rec: dict[str, Any] | None) -> bool:
    return valid(rec) and (rec.get("reward") or 0) >= 1.0


def _pct(n: int, d: int) -> str:
    if not d:
        return "–"
    lo, hi = wilson(n, d)
    return f"{100 * n / d:.1f}% ({n}/{d}; {lo:.1f}–{hi:.1f})"


def _median(values: list[float]) -> str:
    return f"{statistics.median(values):g}" if values else "–"


def _tests(rec: dict[str, Any] | None) -> str:
    if not valid(rec):
        return (rec or {}).get("exception") or "not run"
    passed, total = rec.get("tests_passed"), rec.get("tests_total")
    mark = "✓" if solved(rec) else "✗"
    return f"{mark} {passed}/{total}" if total else mark


def _count(run: dict, tasks: list[str] | None = None) -> tuple[int, int]:
    """(solved, valid) over ``tasks`` (default: every record of the run)."""
    recs = [run.get(t) for t in tasks] if tasks is not None else list(run.values())
    return sum(map(solved, recs)), sum(map(valid, recs))


def _common(base: dict, new: dict) -> list[str]:
    return sorted(t for t in base if valid(base[t]) and valid(new.get(t)))


def _scopes(base: dict, new: dict, common: list[str]) -> dict[str, list[tuple[int, int]]]:
    runs = (base, new)
    return {
        "own valid tasks": [_count(r) for r in runs],
        "tasks valid in both": [_count(r, common) for r in runs],
        f"all {RUNNABLE_TASKS} runnable (missing = 0)": [
            (_count(r)[0], RUNNABLE_TASKS) for r in runs
        ],
    }


def _transitions(base: dict, new: dict, common: list[str]) -> tuple[int, int]:
    flips = [solved(new[t]) - solved(base[t]) for t in common]
    return flips.count(1), flips.count(-1)


def overall(base: dict, new: dict, labels: tuple[str, str]) -> list[str]:
    common = _common(base, new)
    lines = ["## Overall", "", "| Scope | " + " | ".join(labels) + " |", "|---|---|---|"]
    for name, runs in _scopes(base, new, common).items():
        lines.append(f"| {name} | " + " | ".join(_pct(n, d) for n, d in runs) + " |")
    gained, lost = _transitions(base, new, common)
    public = "; ".join(f"{name} {score:.2f}% {spread}" for name, score, spread in PUBLIC)
    return [
        *lines,
        "",
        f"Like-for-like transitions on {len(common)} tasks: **{gained} gained**, "
        f"**{lost} lost** (exact sign test over discordant pairs: {sign_test(gained, lost)}).",
        "",
        f"Public glm-5.3 rows (all 66 tasks): {public}.",
    ]


def sign_test(wins: int, losses: int) -> str:
    """Two-sided exact binomial p over discordant pairs."""
    n = wins + losses
    if not n:
        return "no discordant pairs"
    from math import comb

    k = min(wins, losses)
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2**n)
    return f"p = {p:.3f}"


def _tests_delta(b: dict[str, Any], n: dict[str, Any]) -> str:
    if not (b.get("tests_total") and n.get("tests_total")):
        return ""
    delta = (n.get("tests_passed") or 0) - (b.get("tests_passed") or 0)
    return f"tests {delta:+d}" if delta else ""


def _change(b: dict[str, Any] | None, n: dict[str, Any] | None) -> str:
    if not (valid(b) and valid(n)):
        return "new only" if valid(n) else ("base only" if valid(b) else "")
    flip = {1: "**gained**", -1: "**lost**"}.get(solved(n) - solved(b), "")
    return flip or _tests_delta(b, n)


def per_task(base: dict, new: dict, labels: tuple[str, str]) -> list[str]:
    def category(task: str) -> str:
        return (new.get(task) or base.get(task) or {}).get("category") or ""

    def order(task: str) -> tuple:
        b, n = base.get(task), new.get(task)
        return (solved(b) - solved(n), not valid(n), category(task), task)

    lines = [
        "## Per task",
        "",
        f"| Task | Category | {labels[0]} | {labels[1]} | Change |",
        "|---|---|---|---|---|",
    ]
    for task in sorted(set(base) | set(new), key=order):
        b, n = base.get(task), new.get(task)
        lines.append(f"| {task} | {category(task)} | {_tests(b)} | {_tests(n)} | {_change(b, n)} |")
    return lines


TRANSCRIPT_SUMS = {
    "length_nudges": "continue nudges after an output-limit cut (transcript)",
    "empty_nudges": "continue nudges after an empty reply (transcript)",
    "platform_tool_calls": "platform tool calls (transcript)",
    "result_fetches": "tool_result_fetch calls (transcript)",
}


def _transcript_stats(recs: list[dict[str, Any]]) -> dict[str, str]:
    """Only runs with W6 transcripts have these; older runs show '–'."""
    with_tx = [r for r in recs if "tool_calls" in r]
    if not with_tx:
        return dict.fromkeys(("tasks with a transcript", *TRANSCRIPT_SUMS.values()), "–")
    out = {"tasks with a transcript": str(len(with_tx))}
    for key, label in TRANSCRIPT_SUMS.items():
        out[label] = str(sum(int(r.get(key) or 0) for r in with_tx))
    return out


def mechanism(base: dict, new: dict, labels: tuple[str, str]) -> list[str]:
    """Harness symptoms the waves target, over each run's valid tasks."""

    def stats(run: dict) -> dict[str, str]:
        recs = [r for r in run.values() if valid(r)]
        flags = Counter(f for r in recs for f in r.get("flags") or [])
        num = lambda key: [r[key] for r in recs if isinstance(r.get(key), (int, float))]  # noqa: E731
        return {
            "valid tasks": str(len(recs)),
            "median model calls (driver)": _median(num("llm_calls")),
            "median agent minutes": _median(num("agent_min")),
            "median peak prompt tokens": _median(num("peak_prompt")),
            "max peak prompt tokens": f"{max(num('peak_prompt'), default=0):,}",
            "tasks with a reply cut at the output limit": str(
                sum(1 for r in recs if r.get("cut_at_limit"))
            ),
            "replies cut at the output limit": str(sum(num("cut_at_limit"))),
            "completion tokens (sum)": f"{sum(num('completion_tokens')):,}",
            **{f"final reply flag: {f}": str(flags.get(f, 0)) for f in FLAG_NAMES},
            **_transcript_stats(recs),
        }

    rows = [stats(base), stats(new)]
    lines = ["## Harness symptoms", "", "| Metric | " + " | ".join(labels) + " |", "|---|---|---|"]
    for key in rows[0]:
        lines.append(f"| {key} | {rows[0][key]} | {rows[1][key]} |")
    return lines


def by_category(base: dict, new: dict, labels: tuple[str, str]) -> list[str]:
    cats = sorted({(r or {}).get("category") or "" for r in [*base.values(), *new.values()]} - {""})
    lines = [
        "## By category (solved / valid)",
        "",
        f"| Category | {labels[0]} | {labels[1]} | Vals glm-5.3 % |",
        "|---|---|---|---|",
    ]
    for cat in cats:
        cells = []
        for run in (base, new):
            recs = [r for r in run.values() if r.get("category") == cat]
            cells.append(f"{sum(map(solved, recs))}/{sum(map(valid, recs))}")
        lines.append(f"| {cat} | {cells[0]} | {cells[1]} | {VALS_CATEGORY.get(cat, '–')} |")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--new", type=Path, required=True)
    ap.add_argument("--base-label", default="baseline")
    ap.add_argument("--new-label", default="new")
    a = ap.parse_args()
    base, new = load(a.base), load(a.new)
    labels = (a.base_label, a.new_label)
    sections = [overall, by_category, mechanism, per_task]
    print("\n\n".join("\n".join(section(base, new, labels)) for section in sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
