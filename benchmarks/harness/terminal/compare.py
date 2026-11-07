"""Compare Terminal-Bench arms of the R4 companion (markdown on stdout).

    .venv/bin/python benchmarks/harness/terminal/compare.py \
        off=benchmarks/results/terminal-bench/<run>/off \
        nimble=benchmarks/results/terminal-bench/<run>/nimble

Each argument is ``label=<harbor -o dir>``; every trial ``result.json`` under
it counts once (a harbor job dir may hold several job timestamps after a
resume; a task's latest finished trial wins). A trial with no verifier reward
(agent timeout, agent error) scores 0, as on the public leaderboard.
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _reward(trial: dict[str, Any]) -> float:
    rewards = ((trial.get("verifier_result") or {}).get("rewards")) or {}
    try:
        return float(rewards.get("reward") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _seconds(trial: dict[str, Any]) -> float | None:
    try:
        start = datetime.fromisoformat(trial["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(trial["finished_at"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError):
        return None
    return (end - start).total_seconds()


def _driver_meta(trial: dict[str, Any]) -> dict[str, Any]:
    meta = (trial.get("agent_result") or {}).get("metadata") or {}
    return meta.get("r4_driver") or {}


def load_arm(root: Path) -> dict[str, dict[str, Any]]:
    """task name → its latest finished trial."""
    trials: dict[str, dict[str, Any]] = {}
    for path in root.glob("*/*/result.json"):
        trial = json.loads(path.read_text())
        task = trial.get("task_name")
        if not task or not trial.get("finished_at"):
            continue
        if task not in trials or trial["finished_at"] > trials[task]["finished_at"]:
            trials[task] = trial
    return trials


def _exception(trial: dict[str, Any]) -> str | None:
    return (trial.get("exception_info") or {}).get("exception_type")


def _sum_decisions(trials) -> dict[str, int]:
    total = {"tool_gate_calls": 0, "tool_gate_escalations": 0, "decision_evaluate_calls": 0}
    for trial in trials:
        decisions = _driver_meta(trial).get("decisions") or {}
        for key in total:
            total[key] += int(decisions.get(key) or 0)
    return total


def _input_tokens(trial: dict[str, Any]) -> int:
    return (trial.get("agent_result") or {}).get("n_input_tokens") or 0


def _median_minutes(values: list[dict[str, Any]]) -> float | None:
    durations = [s for s in (_seconds(t) for t in values) if s is not None]
    return statistics.median(durations) / 60 if durations else None


def summarize(trials: dict[str, dict[str, Any]]) -> dict[str, Any]:
    values = list(trials.values())
    rewards = [_reward(t) for t in values]
    exceptions = [e for e in (_exception(t) for t in values) if e]
    timeouts = exceptions.count("AgentTimeoutError")
    return {
        "n": len(values),
        "solved": sum(1 for r in rewards if r >= 1.0),
        "score": 100 * statistics.mean(rewards) if rewards else 0.0,
        "timeouts": timeouts,
        "other_errors": len(exceptions) - timeouts,
        "median_min": _median_minutes(values),
        "input_tokens_m": sum(_input_tokens(t) for t in values) / 1e6,
        "decisions": _sum_decisions(values),
    }


def _row(label: str, s: dict[str, Any]) -> str:
    d = s["decisions"]
    median = f"{s['median_min']:.1f}" if s["median_min"] is not None else "–"
    return (
        f"| {label} | {s['n']} | {s['solved']} | **{s['score']:.1f}** | {s['timeouts']} | "
        f"{s['other_errors']} | {median} | {s['input_tokens_m']:.1f} | {d['tool_gate_calls']} | "
        f"{d['tool_gate_escalations']} | {d['decision_evaluate_calls']} |"
    )


def paired(a: dict[str, dict], b: dict[str, dict]) -> tuple[int, int, int]:
    """(tasks both ran, solved only by a, solved only by b)."""
    common = set(a) & set(b)
    only_a = sum(1 for t in common if _reward(a[t]) >= 1.0 > _reward(b[t]))
    only_b = sum(1 for t in common if _reward(b[t]) >= 1.0 > _reward(a[t]))
    return len(common), only_a, only_b


def main(argv: list[str]) -> int:
    arms = {}
    for arg in argv:
        label, _, path = arg.partition("=")
        arms[label] = load_arm(Path(path))
    print(
        "| Arm | Tasks | Solved | Score % | Agent timeouts | Other errors | Median min/task "
        "| Input tokens (M) | Gate calls | Gate escalations | decision_evaluate calls |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for label, trials in arms.items():
        print(_row(label, summarize(trials)))
    labels = list(arms)
    if len(labels) == 2:
        common, only_a, only_b = paired(arms[labels[0]], arms[labels[1]])
        print(
            f"\nPaired over {common} tasks: solved only by {labels[0]}: {only_a}; "
            f"only by {labels[1]}: {only_b}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
