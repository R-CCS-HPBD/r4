"""One Terminal-Bench 4.0 report: R4 lanes, terminus-2 and the public rows (markdown on stdout).

    PYTHONPATH=$PWD .venv/bin/python benchmarks/harness/terminal/tb4_combined_report.py \
        --lane r4=benchmarks/results/terminal-bench/tb40-full-<date> \
        --lane terminus-2=benchmarks/results/terminal-bench/tb40-terminus-full-<date> \
        [--out benchmarks/results/terminal-bench/tb40-report-<date>.md]

A lane is ``label=DIR[,DIR...]``. A DIR is a run directory (its harbor job roots,
e.g. ``r4``, ``r4-rerun`` or ``terminus``, are the subdirectories holding
``<job>/<trial>/result.json``; ``model-proxy.jsonl`` and ``run-meta.json`` beside
them are read when present) or a harbor job root. Per task, a finished trial that
is not an infrastructure failure wins over one that is (a re-run replaces a
host-killed trial); among real attempts the first one counts, so an extra dir
never makes a lane best-of-n. A cancelled trial counts as not run. The first two
lanes are paired; a task whose kept trial is an infrastructure failure in either
lane is left out of the paired test and counted.

Partial runs work: scores are given over the finished trials and over 63 / 66
tasks (unfinished = 0), each with and without the tasks Epoch's review flags
(``tb4_trial_diagnostics.EPOCH_FLAGGED``). Efficiency counts the whole proxy log,
running trials included. Read-only: it writes only ``--out``.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmarks.harness.terminal.compare import _exception, _reward, _seconds, load_arm
from benchmarks.harness.terminal.tb4_public_compare import public_section, wilson
from benchmarks.harness.terminal.tb4_trial_diagnostics import (
    _INFRA_KILL,
    EPOCH_FLAGGED,
    EPOCH_SOURCE,
    PM_SOURCE,
    task_tags,
)

N_ALL = 66
GPU_EXCLUDED = frozenset({"fp8-rmsnorm-gemm", "jax-speedrun-gpu", "math-eval-grader"})
N_RUNNABLE = N_ALL - len(GPU_EXCLUDED)
FLAGGED_ALL = len(EPOCH_FLAGGED)
FLAGGED_RUNNABLE = len(set(EPOCH_FLAGGED) - GPU_EXCLUDED)
BLOCKING_SEC = 60  # a tool call this long counts as blocking (terminus-2 waits at most 60 s)
_TIMEOUT_TEXT = re.compile(r"timed out after \d+ ?s", re.I)
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n[ \t]*\2[ \t]*(?:\n|$)", re.S)
_SEPARATORS = re.compile(r"&&|\|\||;|\n")
_INFRA_EXCEPTIONS = {"EnvironmentStartTimeoutError", "DockerError"}
# AA's methodology: "A verifier that exceeds its timeout counts as a failure"; a
# slow solution or a process the agent left running can cause it, so it is its
# own category and never infra (review 2026-10-24).
_VERIFIER_TIMEOUT = "VerifierTimeoutError"


@dataclass
class Lane:
    label: str
    trials: dict[str, dict[str, Any]] = field(default_factory=dict)  # bare task → trial
    dirs: dict[str, Path] = field(default_factory=dict)  # bare task → trial dir
    running: set[str] = field(default_factory=set)  # no result yet, job still live
    abandoned: set[str] = field(default_factory=set)  # no result, job ended
    cancelled: set[str] = field(default_factory=set)
    log_dirs: list[Path] = field(default_factory=list)  # every trial dir, for log ratios
    rows: list[dict[str, Any]] = field(default_factory=list)  # model-proxy rows
    meta: dict[str, Any] = field(default_factory=dict)  # run-meta.json (L4/B3)


# ── loading ────────────────────────────────────────────────────────────────────


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _bare(task: str) -> str:
    return task.split("/")[-1]


def job_roots(path: Path) -> list[Path]:
    """``path`` itself when it is a harbor job root, else its subdirectories that are."""
    if not path.is_dir():
        return []
    if _is_job_root(path):
        return [path]
    return sorted(d for d in path.iterdir() if d.is_dir() and _is_job_root(d))


def _is_job_root(path: Path) -> bool:
    """A harbor ``-o`` dir: job dirs with a ``job.log`` (a live run has no trial result yet)."""
    return any(path.glob("*/job.log")) or any(path.glob("*/*/trial.log"))


def _task_of(trial_dir: Path) -> str:
    return trial_dir.name.rsplit("__", 1)[0]


def _scan_job(lane: Lane, job: Path, dirs: dict[str, Path]) -> None:
    """Record a job's trial dirs; a trial with no finished result is running while
    the job is live (its ``result.json`` has no ``finished_at``), else abandoned."""
    open_set = (
        lane.abandoned if _read_json(job / "result.json").get("finished_at") else lane.running
    )
    for trial_dir in (d for d in job.iterdir() if d.is_dir() and "__" in d.name):
        lane.log_dirs.append(trial_dir)
        result = _read_json(trial_dir / "result.json")
        if result.get("finished_at"):
            dirs[result.get("trial_name") or trial_dir.name] = trial_dir
        else:
            open_set.add(_task_of(trial_dir))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # a row being written while the run is live
    return rows


def _preferred(new: dict[str, Any], current: dict[str, Any] | None) -> bool:
    """Whether *new* replaces *current* as the task's trial.

    A real attempt beats an infrastructure failure; among infrastructure
    failures the latest counts, among real attempts the first."""
    if current is None:
        return True
    new_infra, current_infra = is_infra_trial(new), is_infra_trial(current)
    if new_infra != current_infra:
        return current_infra
    if current_infra:
        return new["finished_at"] > current["finished_at"]
    return new["finished_at"] < current["finished_at"]


def _add_root(lane: Lane, root: Path) -> None:
    dirs: dict[str, Path] = {}
    for job in (d for d in root.iterdir() if d.is_dir()):
        _scan_job(lane, job, dirs)
    for name, trial in load_arm(root).items():
        task = _bare(name)
        if _exception(trial) == "CancelledError":  # stopped by the operator: not run
            lane.cancelled.add(task)
            continue
        if _preferred(trial, lane.trials.get(task)):
            lane.trials[task] = trial
            lane.dirs[task] = dirs.get(trial.get("trial_name"), root)


def load_lane(label: str, paths: list[Path]) -> Lane:
    lane = Lane(label)
    for path in paths:
        for root in job_roots(path):
            _add_root(lane, root)
        lane.rows += _jsonl(path / "model-proxy.jsonl")
        lane.meta.update(_read_json(path / "run-meta.json") or {})
    # An infrastructure failure with a re-run still in flight is a task in progress,
    # not a finished one (2026-10-24: 51 out-of-space trials awaiting their re-run).
    for task in [t for t in lane.running if t in lane.trials and is_infra_trial(lane.trials[t])]:
        del lane.trials[task]
    lane.running -= set(lane.trials)
    lane.abandoned -= set(lane.trials) | lane.running
    lane.cancelled -= set(lane.trials) | lane.running | lane.abandoned
    return lane


def started(lane: Lane) -> int:
    """Tasks the lane started: finished, running, abandoned or cancelled."""
    return len(set(lane.trials) | lane.running | lane.abandoned | lane.cancelled)


def attempts(lane: Lane) -> int:
    """Trial dirs (attempts) the lane has, re-runs included: the proxy log holds them all."""
    return len(lane.log_dirs)


# ── statistics ─────────────────────────────────────────────────────────────────


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Exact two-sided McNemar p on the discordant pairs (binomial, p = 1/2)."""
    n = only_a + only_b
    if not n:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(only_a, only_b) + 1)) / 2**n
    return min(1.0, 2 * tail)


def solved(trial: dict[str, Any] | None) -> bool:
    return bool(trial) and _reward(trial) >= 1.0


def valid_tasks(lane: Lane) -> set[str]:
    """Finished tasks whose kept trial is a real attempt, not an infrastructure failure."""
    return {t for t, trial in lane.trials.items() if not is_infra_trial(trial)}


def score_rows(lane: Lane) -> list[tuple[str, int, int]]:
    """(denominator label, solved, n): all tasks, then without the Epoch-flagged ones.
    The finished columns count real attempts only: an infrastructure failure is no
    verdict on the agent (it still counts 0 in the fixed /63 and /66 columns)."""
    wins = {t for t, trial in lane.trials.items() if solved(trial)}
    clean = wins - set(EPOCH_FLAGGED)
    valid = valid_tasks(lane)
    finished_clean = len(valid - set(EPOCH_FLAGGED))
    return [
        ("finished trials", len(wins), len(valid)),
        (f"/{N_RUNNABLE} runnable", len(wins), N_RUNNABLE),
        (f"/{N_ALL} (3 GPU tasks = 0)", len(wins), N_ALL),
        ("finished, unflagged", len(clean), finished_clean),
        (
            f"/{N_RUNNABLE - FLAGGED_RUNNABLE} runnable unflagged",
            len(clean),
            N_RUNNABLE - FLAGGED_RUNNABLE,
        ),
        (f"/{N_ALL - FLAGGED_ALL} unflagged", len(clean), N_ALL - FLAGGED_ALL),
    ]


def infra_in_either(a: Lane, b: Lane) -> set[str]:
    """Tasks both lanes finished whose kept trial is an infrastructure failure in one."""
    common = set(a.trials) & set(b.trials)
    return {t for t in common if is_infra_trial(a.trials[t]) or is_infra_trial(b.trials[t])}


def paired_counts(a: Lane, b: Lane, unflagged: bool = False) -> tuple[int, int, int, int]:
    """(tasks paired, both solved, solved only by a, solved only by b).

    Paired = both lanes finished the task and neither trial is an
    infrastructure failure (:func:`infra_in_either` counts those)."""
    common = set(a.trials) & set(b.trials)
    common -= infra_in_either(a, b)
    if unflagged:
        common -= set(EPOCH_FLAGGED)
    sa = {t for t in common if solved(a.trials[t])}
    sb = {t for t in common if solved(b.trials[t])}
    return len(common), len(sa & sb), len(sa - sb), len(sb - sa)


def _host_out_of_space(message: str, exc: str | None) -> bool:
    """The bench host ran out of disk or inodes: harbor's own OSError (Errno 28) or
    docker's runc failing to write its exec spec (2026-10-24: /tmp's inodes ran out
    and 61 terminus-2 trials died in setup). A full disk inside the task container
    is the agent's outcome and raises neither."""
    if "no space left on device" not in message.lower():
        return False
    return exc == "OSError" or "OCI runtime exec failed" in message


def _is_infra(trial: dict[str, Any], exc: str | None) -> bool:
    """A host kill (exit −9), an environment error, the host out of space, or no
    verifier result at all."""
    message = str((trial.get("exception_info") or {}).get("exception_message"))
    if _INFRA_KILL in message or exc in _INFRA_EXCEPTIONS:
        return True
    if _host_out_of_space(message, exc):
        return True
    return not exc and not trial.get("verifier_result")


def is_infra_trial(trial: dict[str, Any]) -> bool:
    """An unsolved trial whose outcome the infrastructure decided, not the agent."""
    return not solved(trial) and _is_infra(trial, _exception(trial))


def _exception_category(exc: str | None, driver_status: str | None) -> str | None:
    if exc == "AgentTimeoutError":
        return "timeout"
    if exc == _VERIFIER_TIMEOUT:
        return "verifier-timeout"
    if exc or driver_status not in (None, "success"):
        return "harness"
    return None


def failure_category(trial: dict[str, Any], driver_status: str | None = None) -> str:
    """Heuristic per-trial category: solved / infra / timeout / verifier-timeout / harness /
    flagged-task / model.

    flagged-task is only a failure on a task Epoch lists as a false negative (a correct
    answer can be marked wrong). A failure on a false-positive task (the tests accept
    wrong answers) is a real failure and reads as model, still tagged ``epoch-fp``."""
    if solved(trial):
        return "solved"
    exc = _exception(trial)
    if _is_infra(trial, exc):
        return "infra"
    category = _exception_category(exc, driver_status)
    if category:
        return category
    if EPOCH_FLAGGED.get(_bare(str(trial.get("task_name")))) == "fn":
        return "flagged-task"
    return "model"


# ── per-trial logs: commands, requests, blocking time ──────────────────────────


def shell_segments(command: str) -> int:
    """Commands in one shell string: split on ``&&``, ``;``, ``||`` and newlines after
    heredoc bodies are removed (the same rule for both lanes; quoting is ignored)."""
    body = _HEREDOC.sub("\n", command)
    return sum(1 for part in _SEPARATORS.split(body) if part.strip())


_COMMAND_PREFIX = re.compile(r'^\s*\{\s*"command"\s*:\s*"')


def _clipped_command(text: str) -> str:
    """The command of arguments JSON the transcript clipped (8,000 chars): the raw
    string after ``{"command": "``, unescaped as far as it goes."""
    match = _COMMAND_PREFIX.match(text)
    if match is None:
        return ""
    raw = text[match.end() :]
    return raw.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def _command(arguments: Any) -> str:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return _clipped_command(arguments)
    return str((arguments or {}).get("command") or "") if isinstance(arguments, dict) else ""


def _ts(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def _call_seconds(records: list[dict[str, Any]]) -> list[float]:
    opened: dict[str, float] = {}
    spans = []
    for rec in records:
        data = rec.get("data") or {}
        call_id = data.get("tool_call_id")
        if rec.get("kind") == "tool_call" and call_id:
            opened[call_id] = _ts(rec["ts"])
        elif rec.get("kind") == "tool_result" and call_id in opened:
            spans.append(_ts(rec["ts"]) - opened.pop(call_id))
    return spans


def _r4_record(rec: dict[str, Any]) -> Counter:
    data = rec.get("data") or {}
    kind = rec.get("kind")
    out: Counter = Counter(requests=int(kind == "model_round"))
    if kind == "tool_call" and data.get("name") == "shell_exec":
        out.update(shell_calls=1, segments=shell_segments(_command(data.get("arguments"))))
    if kind == "tool_result" and _TIMEOUT_TEXT.search(str(data.get("result") or "")):
        out["shell_timeouts"] += 1
    return out


def r4_log_stats(trial_dir: Path) -> Counter:
    """Requests, shell calls and segments, blocking seconds and shell timeouts (transcript)."""
    records = [r for r in _jsonl(trial_dir / "agent" / "transcript.jsonl") if isinstance(r, dict)]
    stats: Counter = sum(map(_r4_record, records), Counter())
    stats["blocking_s"] += sum(s for s in _call_seconds(records) if s >= BLOCKING_SEC)
    stats["logs"] += bool(records)
    return stats


def _bash_calls(step: dict[str, Any]) -> list[dict[str, Any]]:
    calls = step.get("tool_calls") or []
    return [c for c in calls if c.get("function_name") == "bash_command"]


def _terminus_step(step: dict[str, Any]) -> Counter:
    calls = _bash_calls(step)
    out: Counter = Counter(requests=1, no_action=int(not calls), shell_calls=len(calls))
    for call in calls:
        args = call.get("arguments") or {}
        out["segments"] += shell_segments(str(args.get("keystrokes") or ""))
        out["blocking_s"] += float(args.get("duration") or 0)
    return out


def terminus_log_stats(trial_dir: Path) -> Counter:
    """Requests (agent steps), keystroke batches and segments, waits, no-action steps."""
    steps = (_read_json(trial_dir / "agent" / "trajectory.json") or {}).get("steps") or []
    agent_steps = [s for s in steps if s.get("source") == "agent"]
    stats: Counter = sum(map(_terminus_step, agent_steps), Counter())
    stats["logs"] += bool(steps)
    return stats


def log_stats(lane: Lane) -> Counter:
    """Summed over every trial dir the lane has (finished, running or cancelled)."""
    total: Counter = Counter()
    for trial_dir in lane.log_dirs:
        total += r4_log_stats(trial_dir) + terminus_log_stats(trial_dir)
    return total


# ── proxy efficiency ───────────────────────────────────────────────────────────


def _is_cut(row: dict[str, Any]) -> bool:
    return bool(row.get("cut")) or row.get("status") == 499


def _estimated(row: dict[str, Any]) -> int:
    return int(((row.get("usage_estimated") or {}).get("completion_tokens")) or 0)


def _unmeasured(row: dict[str, Any]) -> bool:
    """A finished (200) stream without measured usage: the proxy's estimate counts."""
    return row.get("status") == 200 and row.get("completion_tokens") is None


def _unmeasured_rows(ok: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in ok if _unmeasured(r) and not _is_cut(r)]


def _decode_tps(row: dict[str, Any]) -> float | None:
    if row.get("decode_tps"):
        return float(row["decode_tps"])
    ttft, dt, out = row.get("ttft_s"), row.get("dt_s") or 0, row.get("completion_tokens") or 0
    if ttft is None or out <= 0 or dt <= ttft:
        return None
    return out / (dt - ttft)


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _median_of(values: Any) -> float | None:
    return _median([v for v in values if v])


def _val(row: dict[str, Any], key: str) -> float:
    return row.get(key) or 0


def _total(rows: list[dict[str, Any]], key: str) -> float:
    return sum(_val(r, key) for r in rows)


def _e2e_tps(row: dict[str, Any]) -> float | None:
    out, dt = _val(row, "completion_tokens"), _val(row, "dt_s")
    return out / dt if out and dt else None


def _no_tool_reply(row: dict[str, Any]) -> bool:
    return row.get("finish") == "stop" and not row.get("tool_calls")


def proxy_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Lane totals from a model-proxy log; cut streams (499 or ``cut``) add the proxy's
    completion estimate when the row has one (L4/B1), else count as unestimated."""
    ok = [r for r in rows if r.get("status") == 200]
    cuts = [r for r in rows if _is_cut(r)]
    prompt = _total(ok, "prompt_tokens")
    unmeasured = _unmeasured_rows(ok)
    return {
        "requests": len(rows),
        "non_200": len(rows) - len(ok),
        "prompt": prompt,
        "completion": _total(ok, "completion_tokens") + sum(map(_estimated, unmeasured)),
        "unmeasured": len(unmeasured),
        "cut_estimate": sum(map(_estimated, cuts)),
        "cuts": len(cuts),
        "cuts_unestimated": sum(not _estimated(r) for r in cuts),
        "cut_min": _total(cuts, "dt_s") / 60,
        "model_min": _total(rows, "dt_s") / 60,
        "cache_hit": _div(_total(ok, "cached_tokens"), prompt),
        "decode_tps": _median_of(map(_decode_tps, ok)),
        "e2e_tps": _median_of(map(_e2e_tps, ok)),
        "native_tools": any(_val(r, "tool_calls") > 0 for r in ok),
        "no_tool_replies": sum(map(_no_tool_reply, ok)),
    }


# ── rendering ──────────────────────────────────────────────────────────────────


def _pct(k: int, n: int) -> str:
    if not n:
        return "– (0)"
    lo, hi = wilson(k, n)
    return f"{100 * k / n:.1f}% ({k}/{n}; {max(0.0, lo):.1f}–{min(100.0, hi):.1f})"


def _num(value: float | None, fmt: str = "{:.1f}") -> str:
    return "–" if value is None else fmt.format(value)


def _div(a: float, b: float) -> float | None:
    return a / b if b else None


def progress_section(lanes: list[Lane]) -> list[str]:
    lines = [
        "| Lane | Finished | Infra failure (no valid attempt yet) | Running | Abandoned (no result) "
        "| Cancelled (not run) | Solved | Proxy requests |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for lane in lanes:
        wins = sum(1 for t in lane.trials.values() if solved(t))
        valid = len(valid_tasks(lane))
        lines.append(
            f"| {lane.label} | {valid} | {len(lane.trials) - valid} | {len(lane.running)} "
            f"| {len(lane.abandoned)} | {len(lane.cancelled)} "
            f"| {wins} | {len(lane.rows)} |"
        )
    lines += [
        "",
        "Running = a trial dir with no result while its job's result.json has no "
        "`finished_at`; a job killed without finishing also reads as running. Cancelled "
        "trials (operator stops) count as not run. Finished = a real attempt; an "
        "infrastructure failure (host kill, environment error, host out of space, no "
        "verifier result) is listed apart, and one whose re-run is still going counts "
        "as running.",
    ]
    return lines


def accuracy_section(lanes: list[Lane]) -> list[str]:
    labels = [label for label, _, _ in score_rows(Lane(""))]
    lines = ["| Lane | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for lane in lanes:
        cells = [_pct(k, n) for _, k, n in score_rows(lane)]
        lines.append(f"| {lane.label} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Cells: pass@1 (solved/n; Wilson 95 % CI). Unfinished tasks count 0 in the /63 and /66 "
        f"columns. Unflagged = without the {FLAGGED_ALL} tasks Epoch flags "
        f"({FLAGGED_RUNNABLE} of them runnable here); source: {EPOCH_SOURCE}.",
    ]
    return lines


def _verdict(p: float) -> str:
    return "significant at α = 0.05" if p < 0.05 else "no detectable difference"


def _paired_line(a: Lane, b: Lane, unflagged: bool) -> str:
    n, both, only_a, only_b = paired_counts(a, b, unflagged)
    p = mcnemar_exact(only_a, only_b)
    scope = "unflagged tasks" if unflagged else "all tasks"
    return f"| {scope} | {n} | {both} | {only_a} | {only_b} | {p:.3f} | {_verdict(p)} |"


def _task_row(task: str, a: Lane, b: Lane) -> str:
    cells = []
    for lane in (a, b):
        trial = lane.trials[task]
        status = _driver_status(lane.dirs.get(task))
        cells += [f"{_reward(trial):g}", failure_category(trial, status)]
    return f"| {task} | " + " | ".join(cells) + f" | {', '.join(task_tags(task)) or '–'} |"


def paired_section(a: Lane, b: Lane) -> list[str]:
    lines = [
        f"| Scope | Both finished | Both solved | Only {a.label} | Only {b.label} "
        "| McNemar exact p | Reading |",
        "|---|---|---|---|---|---|---|",
        _paired_line(a, b, False),
        _paired_line(a, b, True),
        "",
        "Pre-registered rule (plan §5): exact two-sided McNemar on the discordant tasks, "
        "α = 0.05; one paired 63-task run detects about 15–20 pp, so a smaller gap reads as "
        "no detectable difference, not parity. "
        f"Left out of the test: {len(infra_in_either(a, b))} task(s) both lanes finished "
        "whose kept trial is an infrastructure failure in either lane (host kill, "
        "environment error or no verifier result).",
        "",
        f"| Task | {a.label} reward | {a.label} category | {b.label} reward "
        f"| {b.label} category | Tags |",
        "|---|---|---|---|---|---|",
    ]
    common = sorted(set(a.trials) & set(b.trials))
    lines += [_task_row(task, a, b) for task in common] or ["| (no task finished in both) |"]
    return lines


def _driver_status(trial_dir: Path | None) -> str | None:
    if trial_dir is None:
        return None
    return (_read_json(trial_dir / "agent" / "driver-result.json") or {}).get("status")


def _trial_minutes(lane: Lane) -> list[float]:
    real = (t for t in lane.trials.values() if not is_infra_trial(t))
    return [s / 60 for s in (_seconds(t) for t in real) if s is not None]


def efficiency_row(lane: Lane) -> list[str]:
    p, logs = proxy_stats(lane.rows), log_stats(lane)
    n_attempts = attempts(lane) or started(lane)
    wins = sum(1 for t in lane.trials.values() if solved(t))
    tokens = p["prompt"] + p["completion"] + p["cut_estimate"]
    minutes = _trial_minutes(lane)
    no_action = p["no_tool_replies"] if p["native_tools"] else logs["no_action"]
    return [
        lane.label,
        f"{started(lane)} / {n_attempts}",
        _num(_div(p["requests"], n_attempts), "{:.0f}"),
        _num(_div(tokens, n_attempts * 1e6), "{:.1f}M"),
        _num(_div(tokens, wins * 1e6), "{:.1f}M"),
        f"{p['cuts']} ({p['cuts_unestimated']} unestimated)",
        _num(_div(logs["segments"], logs["requests"]), "{:.2f}"),
        _num(_div(logs["segments"], logs["shell_calls"]), "{:.2f}"),
        _num(_median(minutes), "{:.0f}"),
        f"{p['cut_min']:.0f} ({_num(_div(100 * p['cut_min'], p['model_min']), '{:.0f}')} %)",
        f"{logs['blocking_s'] / 60:.0f}",
        _num(p["cache_hit"] and 100 * p["cache_hit"], "{:.0f} %"),
        f"{_num(p['decode_tps'])} / {_num(p['e2e_tps'])}",
        str(no_action),
        f"{sum(1 for t in lane.trials.values() if _exception(t) == 'AgentTimeoutError')}"
        f" / {logs['shell_timeouts']}",
    ]


EFFICIENCY_HEADER = [
    "Lane",
    "Tasks started / attempts",
    "Requests / attempt",
    "Tokens / attempt",
    "Tokens / solved",
    "Cut requests",
    "Shell segments / request",
    "Segments / shell call",
    "Median min / finished trial",
    "Cut-deliberation min (share of model time)",
    "Blocking or wait min",
    "Prefix-cache hit",
    "Decode / end-to-end tok/s",
    "No-action replies",
    "Agent / shell timeouts",
]


def efficiency_section(lanes: list[Lane]) -> list[str]:
    lines = ["| " + " | ".join(EFFICIENCY_HEADER) + " |", "|" + "---|" * len(EFFICIENCY_HEADER)]
    lines += ["| " + " | ".join(efficiency_row(lane)) + " |" for lane in lanes]
    lines += [
        "",
        "- Tokens come from the lane's model-proxy log (prompt + completion of 200 rows, plus "
        "the proxy's estimate for cut streams and for finished streams without measured "
        "usage when it logged one; older logs have none, so their cut requests are counted "
        "as unestimated and add 0). Per attempt = over every trial dir the lane has "
        "(finished, running, abandoned or cancelled, infra re-runs included), since the log "
        "holds them all; tokens per solved task divide the same total.",
        "- Shell segments: R4 `shell_exec` commands and terminus-2 keystroke batches, split on "
        "`&&`, `;`, `||` and newlines with heredoc bodies removed; per request = over the "
        "model requests in the same trial logs (R4 transcripts are written when a trial "
        "ends; terminus-2 trajectories also while it runs). Blocking or wait minutes and "
        "shell timeouts below count the same logs.",
        f"- Blocking or wait: R4 tool calls of {BLOCKING_SEC} s or more (transcript); "
        "terminus-2 requested keystroke waits (it returns after at most 60 s).",
        "- Decode tok/s = completion / (dt − ttft), only for rows with a ttft (streamed); "
        "end-to-end = completion / dt over all 200 rows.",
        "- No-action replies: R4 replies with no tool call (proxy); terminus-2 steps without "
        "a keystroke batch (trajectory).",
    ]
    return lines


def failure_section(lanes: list[Lane]) -> list[str]:
    kinds = ["solved", "model", "flagged-task", "timeout", "verifier-timeout", "harness", "infra"]
    lines = ["| Lane | " + " | ".join(kinds) + " |", "|---|" + "---|" * len(kinds)]
    for lane in lanes:
        counts = Counter(
            failure_category(t, _driver_status(lane.dirs.get(task)))
            for task, t in lane.trials.items()
        )
        lines.append(f"| {lane.label} | " + " | ".join(str(counts[k]) for k in kinds) + " |")
    lines += [
        "",
        "Heuristic: infra = host kill (exit −9), environment error or no verifier "
        "result; timeout = agent timeout; verifier-timeout = the verifier ran past its limit "
        "(a failure, as on AA's board: a slow solution or a process left running can cause "
        "it); harness = any other agent exception or a driver status other than success; "
        "flagged-task = a verifier failure on a task Epoch lists as a false negative (a "
        "correct answer can be marked wrong); model = the rest, including failures on "
        "false-positive tasks (tagged epoch-fp). "
        "Automatic and coarse: a harness cause inside a verifier failure (plan §2.3's manual "
        f"attribution) reads as model here. Trap notes: {PM_SOURCE}.",
    ]
    return lines


def _meta_value(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in sorted(value.items()))
    return str(value)


def _knob_on(knobs: dict[str, Any], name: str, default: bool) -> bool:
    value = str(knobs.get(name, "")).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


def unattended_settings(meta: dict[str, Any]) -> list[str]:
    """Behaviour an R4 lane ran with because of ``R4_AUTONOMOUS=1`` (not a product default
    of interactive sessions): the completion review (H4), detach-on-timeout (H18), the
    same-call guard (H5) and harness notes as user messages (H8)."""
    knobs = meta.get("agent_knobs") or {}
    if meta.get("lane") != "r4" or not _knob_on(knobs, "R4_AUTONOMOUS", False):
        return []
    switches = (
        ("completion review, one per task (H4)", "R4_COMPANION_COMPLETION_REVIEW"),
        ("shell detach on timeout (H18)", "R4_SHELL_DETACH_ON_TIMEOUT"),
        ("same-call loop guard (H5)", "R4_COMPANION_SAME_CALL_GUARD"),
        ("mid-turn harness notes as user messages (H8)", "R4_COMPANION_NUDGES_AS_USER"),
    )
    return [
        f"{label}: {'on' if _knob_on(knobs, name, True) else 'off'}" for label, name in switches
    ]


def meta_section(lanes: list[Lane]) -> list[str]:
    lines = []
    for lane in (lane for lane in lanes if lane.meta):
        lines += [f"**{lane.label}**", ""]
        lines += [f"- {k}: {_meta_value(v)}" for k, v in sorted(lane.meta.items())]
        unattended = unattended_settings(lane.meta)
        if unattended:
            lines.append("- unattended-run behaviour (R4_AUTONOMOUS=1): " + "; ".join(unattended))
        lines.append("")
    if not lines:
        return ["No `run-meta.json` in these runs (written by run_tb4*.sh since B3)."]
    return lines + [
        "Caveats: an R4 lane with R4_AUTONOMOUS=1 adds one completion-review turn per task "
        '("Assume the result will be checked by programmatic tests"; no task-specific '
        "content) and leaves timed-out shell commands running; the public Claude Code and "
        "mini-swe-agent rows have neither. R4 also leaves python3/pip/venv/curl, "
        "/opt/r4bench and <workdir>/.r4 in the task container, which the verifier can see "
        "(terminus-2 installs tmux only)."
    ]


def render(lanes: list[Lane]) -> str:
    out = ["# Terminal-Bench 4.0: R4 against terminus-2 and the public rows", ""]
    out += ["## Progress", ""] + progress_section(lanes)
    out += ["", "## Accuracy", ""] + accuracy_section(lanes)
    if len(lanes) >= 2:
        out += ["", f"## Paired: {lanes[0].label} vs {lanes[1].label}", ""]
        out += paired_section(lanes[0], lanes[1])
    out += ["", "## Efficiency", ""] + efficiency_section(lanes)
    out += ["", "## Failure categories", ""] + failure_section(lanes)
    out += ["", "## Public references", ""] + public_section()
    out += ["", "## Run settings", ""] + meta_section(lanes)
    return "\n".join(out).rstrip() + "\n"


def _parse_lane(spec: str) -> Lane:
    label, sep, paths = spec.partition("=")
    if not sep or not paths:
        raise argparse.ArgumentTypeError(f"--lane wants label=DIR[,DIR...], got {spec!r}")
    return load_lane(label, [Path(p) for p in paths.split(",") if p])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lane", action="append", required=True, type=_parse_lane)
    ap.add_argument("--out", type=Path, help="also write the markdown here")
    a = ap.parse_args(argv)
    text = render(a.lane)
    if a.out:
        a.out.write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
