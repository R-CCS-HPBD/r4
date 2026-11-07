"""Compare one R4 Terminal-Bench 4.0 run with the public glm-5.3 results (markdown on stdout).

    .venv/bin/python benchmarks/harness/terminal/tb4_public_compare.py \
        --run benchmarks/results/terminal-bench/<run> \
        --tasks <terminal-bench 4.0 dataset dir with */task.toml> \
        [--proxy-log benchmarks/results/terminal-bench/<run>/model-proxy.jsonl]

Public reference rows (recorded 2026-10-03, re-verified 2026-10-24; URLs in
``PUBLIC_SOURCES``): the board as mirrored by Snorkel (Claude Code, max effort,
8 h agent timeout; the official sources are tbench.ai and the Harbor Hub), Vals.ai (mini-swe-agent, max effort, 3 runs), Artificial Analysis
(mini-swe-agent) and AA Coding Agents (OpenCode). Values marked † were recorded
2026-10-03 and are no longer shown on the source page (2026-10-24); per-trial
efficiency values marked "derived" are computed here, not published. A trial
with no verifier reward scores 0, as on the board.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from benchmarks.harness.terminal.compare import _exception, _reward, _seconds, load_arm

VERIFIED_ON = "2026-10-24"
RECORDED_ON = "2026-10-03"
# (name, glm-5.3 accuracy %, spread). † = as recorded 2026-10-03; not shown 2026-10-24.
_BOARD = "Board (Snorkel mirror) — Claude Code, max effort, 5 × 66 trials†"
PUBLIC = [
    (_BOARD, 41.82, "±3.23 (95% CI†)"),
    ("Vals.ai — mini-swe-agent, max effort, 3 runs", 38.89, "±1.82 (SE)†"),
    ("Artificial Analysis — mini-swe-agent, max†", 41.92, "–"),
    ("AA Coding Agents — OpenCode", 40.0, "–"),
]
# (tasks, date the page shows) per row; n/s = not stated on the page.
PUBLIC_DETAILS = {
    _BOARD: ("66", "page date 2026-08-14"),
    "Vals.ai — mini-swe-agent, max effort, 3 runs": ("66", "updated 2026-10-22"),
    "Artificial Analysis — mini-swe-agent, max†": ("66", "n/s"),
    "AA Coding Agents — OpenCode": ("n/s", "n/s"),
}
PUBLIC_SOURCES = {
    "Board (Snorkel mirror)": "https://snorkel.ai/leaderboard/terminal-bench/",
    "Official sources (tbench.ai, the Harbor Hub)": "https://www.tbench.ai/",
    "Vals.ai": "https://www.vals.ai/benchmarks/terminal-bench-4",
    "Artificial Analysis": "https://artificialanalysis.ai/methodology/intelligence-benchmarking",
    "AA Coding Agents": (
        "https://artificialanalysis.ai/agents/coding-agents/comparisons/claude-code-vs-opencode"
    ),
}
PUBLIC_NOTES = [
    f"Public rows re-verified {VERIFIED_ON}. † = as recorded {RECORDED_ON}; not shown on the "
    f"source page {VERIFIED_ON} (board 5 × 66 trials and mean trial time, Vals ±1.82 SE, "
    "AA 41.92).",
    "Vals' sampling settings (temperature, top_p, output limit) are not stated on its page.",
    "The board row comes from Snorkel's mirror, which names tbench.ai and the Harbor Hub as "
    f"the official sources; on {VERIFIED_ON} it shows ±3.2 without naming a confidence "
    "level (95 % is as recorded).",
    "The board's derived per-trial values (tokens, USD) divide the page totals by the 330 "
    "trials (5 × 66), which are themselves † (recorded, not shown now).",
    f"Vals per-category scores: as recorded {RECORDED_ON}; the page no longer shows them "
    f"({VERIFIED_ON}), so they cannot be re-verified.",
]
# Per-trial efficiency references. "published" = shown on the page; "derived" = computed
# here from published totals; "recorded" = as recorded 2026-10-03, not shown 2026-10-24.
PUBLIC_EFFICIENCY = [
    # (row, tokens per trial, USD per trial, minutes per trial, provenance)
    (
        "Board (Snorkel mirror) — Claude Code",
        26.4e6,
        8.2,
        97.0,
        "derived: 8.7B tok and $2.7k over 330 trials; minutes recorded 2026-10-03†",
    ),
    ("AA Coding Agents — OpenCode", 15.9e6, 5.23, 56.2, "published per task"),
    ("Vals.ai — mini-swe-agent", None, 9.37, 83.0, "published per test"),
]
# Not re-verifiable since 2026-10-24 (the page shows categories without scores).
VALS_CATEGORY = {
    "Software": 35.19,
    "Science": 38.09,
    "ML": 54.55,
    "Operations": 22.22,
    "Hardware": 20.0,
    "Security": 60.0,
    "Media": 50.0,
}
BOARD_TRIAL_SECONDS = 5831  # board glm-5.3 mean trial duration, recorded 2026-10-03†
BOARD_TOKENS_PER_TRIAL = 8.7e9 / 330  # derived: the page shows the 8.7B total only


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (100 * (centre - half), 100 * (centre + half))


def _fmt(value: float | None, scale: float = 1.0, fmt: str = "{:.1f}") -> str:
    return "–" if value is None else fmt.format(value / scale)


def public_section() -> list[str]:
    """The public reference rows, dated notes and per-trial efficiency (markdown lines)."""
    lines = [
        "| Public row (glm-5.3) | Accuracy % | Spread | Tasks | Page date |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {name} | {score:.2f} | {ci} | {' | '.join(PUBLIC_DETAILS[name])} |"
        for name, score, ci in PUBLIC
    ]
    lines += ["", "| Public row | Tokens / trial | USD / trial | Min / trial | Provenance |"]
    lines.append("|---|---|---|---|---|")
    for name, tok, usd, minutes, how in PUBLIC_EFFICIENCY:
        lines.append(
            f"| {name} | {_fmt(tok, 1e6, '{:.1f}M')} | {_fmt(usd, 1, '${:.2f}')} "
            f"| {_fmt(minutes, 1, '{:.0f}')} | {how} |"
        )
    lines.append("")
    lines += [f"- {note}" for note in PUBLIC_NOTES]
    lines += [f"- {name}: {url}" for name, url in PUBLIC_SOURCES.items()]
    return lines


def task_categories(tasks_dir: Path) -> dict[str, str]:
    try:
        import tomllib
    except ImportError:  # python < 3.11
        import tomli as tomllib
    out = {}
    for path in tasks_dir.glob("*/task.toml"):
        data = tomllib.loads(path.read_text())
        name = (data.get("task") or {}).get("name") or f"terminal-bench/{path.parent.name}"
        out[name] = (data.get("metadata") or {}).get("category") or "?"
    return out


def _row(label: str, solved: int, n: int, finished: int) -> str:
    lo, hi = wilson(solved, n)
    return (
        f"| **{label}** | **{100 * solved / n:.1f}%** ({solved}/{n}) | {lo:.1f}–{hi:.1f}% (Wilson 95%) "
        f"| {finished} finished of {n} |"
    )


def overall(
    trials: dict[str, dict[str, Any]], cats: dict[str, str], not_run: set[str]
) -> list[str]:
    solved = sum(1 for t in trials.values() if _reward(t) >= 1.0)
    runnable = len(cats) - len(not_run & set(cats))
    lines = ["| Run | Accuracy (pass@1) | Interval | Tasks |", "|---|---|---|---|"]
    lines.append(
        _row("R4, 1 trial — all 66 tasks (not-run tasks scored 0)", solved, len(cats), len(trials))
    )
    if not_run:
        lines.append(
            _row(f"R4, 1 trial — {runnable} runnable tasks", solved, runnable, len(trials))
        )
    lines += [
        f"| {name} | {score:.2f}% | {ci} | {PUBLIC_DETAILS[name][0]} |"
        for name, score, ci in PUBLIC
    ]
    return lines


def by_category(
    trials: dict[str, dict[str, Any]], cats: dict[str, str], not_run: set[str]
) -> list[str]:
    groups: dict[str, list[float]] = defaultdict(list)
    skipped: Counter = Counter()
    for name, cat in cats.items():
        trial = trials.get(name)
        groups[cat].append(_reward(trial) if trial else 0.0)
        skipped[cat] += name in not_run
    lines = [
        "| Category | Tasks | Not run | R4 solved | R4 % of all | Vals glm-5.3 % |",
        "|---|---|---|---|---|---|",
    ]
    for cat in sorted(groups, key=lambda c: -len(groups[c])):
        vals = groups[cat]
        solved = sum(1 for v in vals if v >= 1.0)
        lines.append(
            f"| {cat} | {len(vals)} | {skipped[cat]} | {solved} | {100 * solved / len(vals):.1f} "
            f"| {VALS_CATEGORY.get(cat, '–')} |"
        )
    return lines


def outcomes(trials: dict[str, dict[str, Any]]) -> list[str]:
    kinds = Counter(
        _exception(t) or ("solved" if _reward(t) >= 1.0 else "failed (verifier)")
        for t in trials.values()
    )
    durations = [s for s in (_seconds(t) for t in trials.values()) if s is not None]
    lines = ["| Outcome | Trials |", "|---|---|"] + [
        f"| {k} | {v} |" for k, v in kinds.most_common()
    ]
    if durations:
        lines += [
            "",
            f"Trial duration: mean {statistics.mean(durations) / 60:.0f} min, median "
            f"{statistics.median(durations) / 60:.0f} min, max {max(durations) / 3600:.1f} h "
            f"(official glm-5.3 row: mean {BOARD_TRIAL_SECONDS / 60:.0f} min, "
            f"as recorded {RECORDED_ON}†).",
        ]
    return lines


def _latency_line(rows: list[dict[str, Any]]) -> str:
    dts = sorted(r["dt_s"] for r in rows)
    if not dts:
        return ""
    p90 = dts[int(0.9 * (len(dts) - 1))]
    return f"Request latency: median {statistics.median(dts):.0f} s, p90 {p90:.0f} s, max {dts[-1]:.0f} s."


def proxy_usage(path: Path, n_trials: int) -> list[str]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ok = [r for r in rows if r.get("status") == 200]
    prompt = sum(r.get("prompt_tokens") or 0 for r in ok)
    completion = sum(r.get("completion_tokens") or 0 for r in ok)
    per_trial = (prompt + completion) / max(1, n_trials)
    return [
        f"Model requests: {len(rows)} ({len(rows) - len(ok)} non-200); tokens {prompt / 1e6:.1f}M prompt + "
        f"{completion / 1e6:.2f}M completion = {per_trial / 1e6:.1f}M per trial "
        f"(official glm-5.3 row: {BOARD_TOKENS_PER_TRIAL / 1e6:.1f}M per trial, derived).",
        _latency_line(ok),
    ]


def merged_runs(runs: list[Path]) -> dict[str, dict[str, Any]]:
    """Each task's latest finished trial across the runs (a re-run pass wins)."""
    merged: dict[str, dict[str, Any]] = {}
    for run in runs:
        for name, trial in load_arm(run).items():
            if name not in merged or trial["finished_at"] > merged[name]["finished_at"]:
                merged[name] = trial
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--run",
        type=Path,
        action="append",
        required=True,
        help="repeat to merge a re-run pass (each task's latest finished trial counts)",
    )
    ap.add_argument("--tasks", type=Path, required=True)
    ap.add_argument("--proxy-log", type=Path)
    ap.add_argument(
        "--not-run", default="", help="comma-separated task names that could not be run here"
    )
    a = ap.parse_args()
    cats = task_categories(a.tasks)
    not_run = {t if "/" in t else f"terminal-bench/{t}" for t in a.not_run.split(",") if t}
    trials = {k: v for k, v in merged_runs(a.run).items() if k in cats and k not in not_run}
    out = ["## Overall", ""] + overall(trials, cats, not_run) + ["", "## By category", ""]
    out += by_category(trials, cats, not_run) + ["", "## Outcomes", ""] + outcomes(trials)
    if a.proxy_log and a.proxy_log.is_file():
        out += ["", "## Model usage (proxy log)", ""] + proxy_usage(a.proxy_log, len(trials))
    out += ["", "## Public references", ""] + public_section()
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
