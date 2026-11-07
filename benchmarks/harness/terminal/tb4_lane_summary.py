"""Markdown summary of Terminal-Bench lanes (finished, cancelled or live trials).

    .venv/bin/python benchmarks/harness/terminal/tb4_lane_summary.py <run dir> [<run dir> ...]

One row per trial: outcome (reward, cancelled, live), agent minutes, model
requests and the longest one (proxy rows matched by container IP), tool calls,
and the harness events from the transcript: thinking-budget closes (think),
tool-call cuts (argcut), reply-text cuts (textcut), output-limit cuts (len),
stuck-loop nudges (guard), decision-gate calls / escalations / errors. Counts
only, never message text. Uses tb4_watch's readers.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tb4_watch import _own_rows, _proxy, _records, _signals  # noqa: E402

_COLUMNS = ("think", "argcut", "textcut", "len", "guard", "gate", "esc", "gate_err")


def _ts(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _outcome(result: dict[str, Any]) -> str:
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    if "reward" in rewards:
        return f"reward {rewards['reward']}"
    exc = (result.get("exception_info") or {}).get("exception_type")
    return "cancelled" if exc == "CancelledError" else (exc or "no result")


def _minutes(result: dict[str, Any], records: list[dict[str, Any]]) -> float:
    agent = result.get("agent_execution") or {}
    start = _ts(agent.get("started_at") or result.get("started_at"))
    end = _ts(agent.get("finished_at") or result.get("finished_at"))
    if not (start and end) and records:
        start, end = _ts(records[0].get("ts")), _ts(records[-1].get("ts"))
    return (end - start).total_seconds() / 60 if start and end else 0.0


def _started(result: dict[str, Any], records: list[dict[str, Any]], trial_dir: Path) -> datetime:
    """Trial start for matching proxy rows: the result, else the transcript, else
    the directory (whose ctime moves when harbor writes the result)."""
    start = _ts(result.get("started_at")) or (_ts(records[0].get("ts")) if records else None)
    return start or datetime.fromtimestamp(trial_dir.stat().st_ctime).astimezone()


def trial_row(trial_dir: Path, rows: list[dict[str, Any]]) -> str:
    result_file = trial_dir / "result.json"
    result = json.loads(result_file.read_text()) if result_file.is_file() else {}
    records = _records(trial_dir)
    own = _own_rows(trial_dir, rows, _started(result, records, trial_dir))
    end = _ts(result.get("finished_at"))
    if end:  # container IPs are reused by the run's later trials
        own = [r for r in own if (r["_t"] - end).total_seconds() <= 60]
    signals = _signals(records)
    longest = max((r["dt_s"] for r in own), default=0) / 60
    task = trial_dir.name.split("__")[0]
    outcome = _outcome(result) if result else "live"
    counts = " | ".join(str(signals.get(c, 0)) for c in _COLUMNS)
    return (
        f"| {task} | {outcome} | {_minutes(result, records):.0f} | {len(own)} "
        f"| {longest:.0f} | {signals['tools']} | {counts} |"
    )


def run_table(run: Path) -> str:
    rows = _proxy(run)
    head = (
        "| task | outcome | agent min | requests | longest min | tools | "
        + " | ".join(_COLUMNS)
        + " |"
    )
    lines = [f"**{run.name}**", "", head, "|" + "---|" * (6 + len(_COLUMNS))]
    for trial_dir in sorted(run.glob("r4*/*/*/")):
        if (trial_dir / "agent").is_dir():
            lines.append(trial_row(trial_dir, rows))
    return "\n".join(lines)


def main() -> int:
    for arg in sys.argv[1:]:
        print(run_table(Path(arg)))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
