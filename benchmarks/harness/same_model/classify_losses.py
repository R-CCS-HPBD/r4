#!/usr/bin/env python3
"""Classify why baseline R4 lost questions that Claude Code / OpenCode answered better (same model, same arm).

glm-5.3@rikyu judges each lost case from: gold key facts, R4's final answer (tail), and structural signals
(status, model requests, peak context). Output: one cause label per case. Prints counts only (no content).
"""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import grade_r4 as G  # noqa: E402
import paths as P  # noqa: E402  (code dir on sys.path)
from glmjudge import glm, parse_json  # noqa: E402

HERE = P.OUT
H = P.LANE
CAUSES = [
    "timeout_no_answer",
    "incomplete_missed_facts",
    "wrong_value_or_source",
    "stale_or_superseded",
    "computation_error",
    "wrong_abstention",
    "overclaimed_or_hallucinated",
    "other",
]
SYS = (
    "You diagnose why an AI research assistant's answer scored below another assistant's on the same question. "
    f"Choose exactly one primary cause from {CAUSES}. Reply JSON only: "
    '{"cause": "<one of the list>", "secondary": "<one of the list or none>", "why": "<=25 words"}'
)


def _scores(grades):
    """(lane, arm, qid) → score of every graded result."""
    score = {}
    for k, g in grades.items():
        if g.get("score") is None:
            continue
        lane, fn = k.split("/", 1)
        arm, qid = fn[:-5].split("__")
        score[(lane, arm, qid)] = float(g["score"])
    return score


def _r4_qids(score, arm):
    return sorted({q for (ln, a, q) in score if ln == "r4" and a == arm})


def _lost(score, arm):
    """(qid, r4 score, best of Claude Code / OpenCode) where baseline R4 scored lower."""
    for qid in _r4_qids(score, arm):
        r4s = score.get(("r4", arm, qid))
        best = max([score.get((ln, arm, qid), -1) for ln in ("claude", "opencode")])
        if r4s is None or best < 0 or r4s >= best:
            continue
        yield qid, r4s, best


def _user(q, r, st, final, body):
    return (
        f"QUESTION: {q['question']}\nKEY FACTS:\n"
        + "\n".join(f"- {f}" for f in q["key_facts"])
        + f"\n\nASSISTANT STATUS: {r.get('status')} | model requests: {st.get('n_req')} | peak context tokens: {st.get('max_prompt')}"
        + f"\nASSISTANT FINAL ANSWER:\n{final or '(none)'}\n\nASSISTANT REPLY TAIL:\n{body[-3000:]}"
    )


def _classify(arm, qid, r4s, best, gap):
    p = H / "results_r4" / f"{arm}__{qid}.json"
    r = json.loads(p.read_text())
    q = G.GOLD[qid]
    final, body = G.answer_text(r)
    st = gap.get(("r4", arm, qid), {})
    j = parse_json(glm(SYS, _user(q, r, st, final, body))) or {}
    return {
        "cause": j.get("cause", "unparsed"),
        "secondary": j.get("secondary"),
        "r4_score": r4s,
        "best_other": best,
        "status": r.get("status"),
        "n_req": st.get("n_req"),
        "category": q["category"],
    }


def _print_counts(out):
    print("lost cases:", len(out))
    for arm in ("A", "C"):
        c = Counter(v["cause"] for k, v in out.items() if k.startswith(arm))
        cat = Counter(v["category"] for k, v in out.items() if k.startswith(arm))
        print(f"arm {arm}: causes {dict(c)} | categories {dict(cat)}")


def main():
    grades = json.loads((HERE / "grades_all.json").read_text())
    gap = {
        (c["lane"], c["arm"], c["qid"]): c
        for c in json.loads((HERE / "gap_struct.json").read_text())
    }
    score = _scores(grades)
    out = {}
    for arm in ("A", "C"):
        for qid, r4s, best in _lost(score, arm):
            out[f"{arm}__{qid}"] = _classify(arm, qid, r4s, best, gap)
    (HERE / "loss_causes.json").write_text(json.dumps(out, indent=1))
    _print_counts(out)


if __name__ == "__main__":
    main()
