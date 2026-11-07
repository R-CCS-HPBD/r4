"""Live health view of a running Terminal-Bench lane (one line per trial).

    .venv/bin/python benchmarks/harness/terminal/tb4_watch.py benchmarks/results/terminal-bench/<run>

For every trial: state (live/done + reward), minutes, model requests and the
longest one (proxy rows matched by the trial's container-ip.txt), tool calls,
and the harness signals that mean trouble — output-limit cuts, continue nudges
(length / empty / announced-step / stuck-guard), closed deliberations (thinking
budget), decision-model gate entries (decision lane: asked, escalated, errors,
slowest), platform-tool calls, and a final reply that is the interactive
checkpoint digest or a ladder stop. Live
transcripts are read from the running containers; finished ones from the trial
dir. Counts only, never message text.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NUDGES = {
    "len": "Output limit reached",
    "empty": "Empty reply",
    "intent": "Announced a step",
    "guard": "unattended run",
    "think": "Thinking budget reached",
    "argcut": "planning was being written into code",
    "textcut": "Reply text cut off without a tool call",
}
PLATFORM = (
    "platform_health",
    "endpoint_",
    "job_",
    "list_models",
    "model_",
    "rag_",
    "search_docs",
    "workflow_",
)
BAD_ENDINGS = {"digest": "Checkpoint after", "ladder_stop": "exhausted its output budget"}


def _container_transcript(trial: str) -> list[dict[str, Any]]:
    name = f"{trial.lower()}__env-main-1"
    cmd = ["docker", "exec", name, "sh", "-c", "cat $HOME/.r4bench/transcript.jsonl 2>/dev/null"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [json.loads(line) for line in out.splitlines() if line.strip().startswith("{")]


def _records(trial_dir: Path) -> list[dict[str, Any]]:
    path = trial_dir / "agent" / "transcript.jsonl"
    if path.is_file():
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return _container_transcript(trial_dir.name)


def _texts(records: list[dict[str, Any]], kind: str) -> list[str]:
    return [str(r.get("message") or "") for r in records if r.get("kind") == kind]


def _tool_names(records: list[dict[str, Any]]) -> list[str]:
    calls = [r for r in records if r.get("kind") == "tool_call"]
    return [str((r.get("data") or {}).get("name") or "") for r in calls]


def _gate_entries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r.get("data") or {} for r in records if r.get("kind") == "decision"]


def _gate_signals(records: list[dict[str, Any]]) -> dict[str, int]:
    """Decision-model gate entries (decision lane): asked, escalated, errors, slowest."""
    gate = _gate_entries(records)
    outcomes = [g.get("outcome") for g in gate]
    asked = [g for g in gate if g.get("outcome") == "asked"]
    return {
        "gate": len(asked),
        "esc": sum(bool(g.get("escalated")) for g in gate),
        "gate_err": outcomes.count("error"),
        "gate_max_ms": max((int(g.get("gate_ms") or 0) for g in asked), default=0),
    }


def _signals(records: list[dict[str, Any]]) -> dict[str, int]:
    thinking = " \n".join(_texts(records, "thinking"))
    tools = _tool_names(records)
    out = {k: thinking.count(marker) for k, marker in NUDGES.items()}
    out.update(_gate_signals(records))
    out["tools"] = len(tools)
    out["platform"] = len([t for t in tools if t.startswith(PLATFORM)])
    ends = _texts(records, "turn_end")
    final = ends[-1] if ends else ""
    out.update({name: int(marker in final) for name, marker in BAD_ENDINGS.items()})
    return out


def _proxy(run: Path) -> list[dict[str, Any]]:
    path = run / "model-proxy.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
    for row in rows:
        row["_t"] = datetime.strptime(row["ts"], "%Y-%m-%dT%H:%M:%S").astimezone(timezone.utc)
    return rows


def _state(trial_dir: Path) -> str:
    result = trial_dir / "result.json"
    if not result.is_file():
        return "live"
    rewards = (json.loads(result.read_text()).get("verifier_result") or {}).get("rewards") or {}
    return f"done r={rewards.get('reward')}"


def _own_rows(
    trial_dir: Path, rows: list[dict[str, Any]], started: datetime
) -> list[dict[str, Any]]:
    try:
        ip = (trial_dir / "agent" / "container-ip.txt").read_text().split()[0]
    except (OSError, IndexError):
        return []
    return [r for r in rows if r["peer"] == ip and r["_t"] >= started]


def _trial_line(trial_dir: Path, rows: list[dict[str, Any]]) -> str:
    started = datetime.fromtimestamp(trial_dir.stat().st_ctime, timezone.utc)
    own = _own_rows(trial_dir, rows, started)
    minutes = (time.time() - started.timestamp()) / 60
    s = _signals(_records(trial_dir))
    flags = " ".join(f"{k}={v}" for k, v in s.items() if v and k != "tools")
    longest = max((r["dt_s"] for r in own), default=0) / 60
    cuts = [r.get("finish") for r in own].count("length")
    return (
        f"{trial_dir.name.split('__')[0][:30]:30s} {_state(trial_dir):10s} {minutes:5.0f}m "
        f"req={len(own):3d} longest={longest:4.0f}m cuts={cuts} tools={s['tools']:3d} {flags}"
    )


def main() -> int:
    run = Path(sys.argv[1])
    rows = _proxy(run)
    for trial_dir in sorted(run.glob("r4*/*/*/")):
        if (trial_dir / "agent").is_dir():
            print(_trial_line(trial_dir, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
