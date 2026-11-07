#!/usr/bin/env python3
"""Grade missing results first, then retry judge failures with a larger output budget (the reasoning model
can spend the whole 4000-token budget thinking and return no JSON). Same rubric and judge as grade_all.py
(glm-5.3@rikyu; never the Anthropic API). Prints counts only."""

import concurrent.futures as cf
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import glmjudge as GJ  # noqa: E402
import grade_all as GA  # noqa: E402


def run(todo, threads):
    grades = json.loads(GA.OUT.read_text())
    with cf.ThreadPoolExecutor(threads) as ex:
        for k, g in ex.map(GA.grade, todo):
            if g.get("judge_ok") or k not in grades:
                grades[k] = g
            GA.OUT.write_text(json.dumps(grades, indent=1, ensure_ascii=False))
    return grades


def _judge_failures(grades):
    return sum(1 for g in grades.values() if not g.get("judge_ok"))


def _missing(grades):
    """(key, path) of every result file without a grade."""
    missing = []
    for lane, d in GA.LANES.items():
        for p in sorted(d.glob("*__*.json")):
            if f"{lane}/{p.name}" not in grades:
                missing.append((f"{lane}/{p.name}", p))
    return missing


def _failed(grades):
    return [
        (k, GA.LANES[k.split("/", 1)[0]] / k.split("/", 1)[1])
        for k, g in grades.items()
        if not g.get("judge_ok")
    ]


def main():
    grades = json.loads(GA.OUT.read_text())
    missing = _missing(grades)
    print(f"missing {len(missing)}", flush=True)
    grades = run(missing, 4)
    print(
        f"after missing pass: total {len(grades)}; judge failures {_judge_failures(grades)}",
        flush=True,
    )
    GA.glm = lambda s, u: GJ.glm(s, u, max_tokens=12000, timeout=600)
    grades = run(_failed(grades), 4)
    print(
        f"after retry pass: total {len(grades)}; judge failures {_judge_failures(grades)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
