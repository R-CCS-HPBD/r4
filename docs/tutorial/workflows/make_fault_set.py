"""Write the Failure-clinic bundles (Part 2): copies of seismic-wf, one fault each.

    python make_fault_set.py            # → ./faults/<name>/

Each fault changes only ``workflow.json`` so attendees diagnose from the run's
error and logs, not from a code diff. The answer for each is in FAULTS below
and in the facilitator guide.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE / "seismic-wf"
COPY = ("steps.py", "seismic_core.py", "eikonal.py")


def _step(wf: dict, name: str) -> dict:
    return next(s for s in wf["steps"] if s["name"] == name)


def _bad_entrypoint(wf: dict) -> None:
    _step(wf, "search_low")["entrypoint"] = "steps:ModelSearchSteps"


def _missing_key(wf: dict) -> None:
    _step(wf, "search_high")["config"]["picks_key"] = "picks.csv"


def _timeout(wf: dict) -> None:
    step = _step(wf, "search_low")
    step["timeout_seconds"] = 5
    step["config"]["debug_delay_seconds"] = 30


def _lost_parents(wf: dict) -> None:
    _step(wf, "rank")["depends_on"] = []


# name → (mutation, symptom, fix)
FAULTS = {
    "f1-bad-entrypoint": (
        _bad_entrypoint,
        "step 'search_low' fails before it runs: the class is not found",
        "entrypoint must be steps:ModelSearchStep",
    ),
    "f2-missing-key": (
        _missing_key,
        "step 'search_high' fails reading its input (no such key)",
        "picks_key must name the uploaded file, p_picks.csv",
    ),
    "f3-timeout": (
        _timeout,
        "step 'search_low' exceeds its 5 s budget",
        "remove debug_delay_seconds, or raise timeout_seconds",
    ),
    "f4-lost-parents": (
        _lost_parents,
        "step 'rank' starts at once and finds no scores to rank",
        'rank must declare depends_on: ["search_low", "search_high"]',
    ),
}


def write_fault(name: str, mutate) -> Path:
    out = HERE / "faults" / name
    out.mkdir(parents=True, exist_ok=True)
    for fname in COPY:
        shutil.copyfile(BASE / fname, out / fname)
    wf = json.loads((BASE / "workflow.json").read_text(encoding="utf-8"))
    wf["name"] = f"seismic-{name}"
    mutate(wf)
    (out / "workflow.json").write_text(json.dumps(wf, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> None:
    for name, (mutate, symptom, _fix) in FAULTS.items():
        print(f"{write_fault(name, mutate)}  — {symptom}")


if __name__ == "__main__":
    main()
