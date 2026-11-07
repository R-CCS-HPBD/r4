#!/usr/bin/env python3
"""Blind glm-5.3 grading for the hardened same-model lanes (harness/results_{r4,claude,opencode}).

Reuses grade_r4's rubric and glmjudge (glm-5.3@rikyu; never the Anthropic API). Incremental;
writes grades_all.json keyed '<lane>/<file>'. Prints counts only.
"""

import concurrent.futures as cf
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import grade_r4 as G  # noqa: E402  (SYS rubric, answer_text, GOLD)
import paths as P  # noqa: E402  (code dir on sys.path)
from glmjudge import det_containment, glm, parse_json  # noqa: E402

HERE = P.OUT
LANES = {
    "r4": HERE / "harness/results_r4",
    "claude": HERE / "harness/results_claude",
    "opencode": HERE / "harness/results_opencode",
    "r4deployed": HERE / "results",
    "r4fixall": HERE / "harness/results_r4_fixall",
    "r4fixhist": HERE / "harness/results_r4_fixhist",
}
for _d in sorted(
    (HERE / "harness").glob("results_r4_*")
):  # auto-discover all R4 variants (ablations, replicates)
    LANES.setdefault("r4" + _d.name[len("results_r4_") :], _d)
OUT = HERE / "grades_all.json"


def grade(item):
    key, path = item
    r = json.loads(path.read_text())
    q = G.GOLD[r["qid"]]
    final, body = G.answer_text(r)
    if r.get("status") != "success" or not (final or body):
        return key, {
            "score": 0,
            "stale": False,
            "hallucination": False,
            "abstention_ok": q["category"] != "abstention",
            "evidence_cited": False,
            "comment": f"no answer ({r.get('status')})",
            "det": 0.0,
            "judge_ok": True,
            "run_failed": True,
        }
    user = (
        f"QUESTION: {q['question']}\nCATEGORY: {q['category']}\nGOLD ANSWER: {q['answer']}\nKEY FACTS:\n"
        + "\n".join(f"- {f}" for f in q["key_facts"])
        + f"\n\nANSWER (final line):\n{final or '(none)'}\n\nANSWER (full reply, may be truncated):\n{body}"
    )
    j = parse_json(glm(G.SYS, user))
    ok = isinstance(j, dict) and "score" in j
    g = j if ok else {"score": None, "comment": "judge failed"}
    g["judge_ok"] = ok
    g["det"] = det_containment(q["key_facts"], final + "\n" + body)
    return key, g


def main():
    grades = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = []
    for lane, d in LANES.items():
        for p in sorted(d.glob("*__*.json")):
            k = f"{lane}/{p.name}"
            if k not in grades or not grades[k].get("judge_ok"):
                todo.append((k, p))
    with cf.ThreadPoolExecutor(3) as ex:
        for k, g in ex.map(grade, todo):
            grades[k] = g
            OUT.write_text(json.dumps(grades, indent=1, ensure_ascii=False))
    print(
        f"graded {len(todo)} new; total {len(grades)}; judge failures {sum(1 for g in grades.values() if not g.get('judge_ok'))}"
    )


if __name__ == "__main__":
    main()
