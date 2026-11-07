#!/usr/bin/env python3
"""Blind grading of the real-R4 (glm-5.3) answers with glm-5.3 (rikyu) + deterministic key-fact matching.

Same rubric as the 2026-10-03 Claude-proxy round (score 0/0.5/1; stale; hallucination; abstention; citation).
The judge never sees the arm. Incremental: grades only result files without a grade. Prints counts only.
"""

import concurrent.futures as cf
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import paths as P  # noqa: E402  (code dir on sys.path)
from glmjudge import det_containment, glm, parse_json  # noqa: E402

HERE = P.OUT
BENCH = P.BENCH
GOLD = {g["id"]: g for g in json.loads((BENCH / "gold/gold.json").read_text())}
OUT = HERE / "grades_r4.json"

SYS = """You are a strict, blind grader for a research-assistant benchmark. You get a QUESTION, the GOLD answer with KEY FACTS and the question CATEGORY, and one ANSWER to grade.
Score: 1 = all key facts right (numbers within reasonable tolerance; equivalent wording fine; extra correct detail fine); 0.5 = partially right (some key facts missing or slightly off); 0 = wrong, missing or hallucinated.
stale = the answer presents a superseded or retracted value as current. hallucination = the answer asserts specific facts not supported by the gold/material.
For category 'abstention' the correct behaviour is to say the material does not contain the answer: abstention_ok = true only then. For other categories abstention_ok = true unless the answer wrongly claims the information is unavailable.
evidence_cited = the answer cites sources (file paths, sections, tool ids).
Reply with JSON only: {"score": 0|0.5|1, "stale": bool, "hallucination": bool, "abstention_ok": bool, "evidence_cited": bool, "comment": "<=40 words"}"""


def answer_text(r):
    t = r.get("response_text") or ""
    m = re.search(r"FINAL ANSWER:(.*)$", t, re.S)
    final = m.group(1).strip() if m else ""
    body = t[-6000:] if len(t) > 6000 else t
    return final, body


def grade(path):
    r = json.loads(path.read_text())
    q = GOLD[r["qid"]]
    final, body = answer_text(r)
    if r.get("status") != "success" or not (final or body):
        return path.name, {
            "score": 0,
            "stale": False,
            "hallucination": False,
            "abstention_ok": q["category"] != "abstention",
            "evidence_cited": False,
            "comment": f"no answer ({r.get('status')})",
            "det": 0.0,
            "judge_ok": True,
        }
    user = (
        f"QUESTION: {q['question']}\nCATEGORY: {q['category']}\nGOLD ANSWER: {q['answer']}\nKEY FACTS:\n"
        + "\n".join(f"- {f}" for f in q["key_facts"])
        + f"\n\nANSWER (final line):\n{final or '(none)'}\n\nANSWER (full reply, may be truncated):\n{body}"
    )
    j = parse_json(glm(SYS, user))
    ok = isinstance(j, dict) and "score" in j
    g = j if ok else {"score": None, "comment": "judge failed"}
    g["judge_ok"] = ok
    g["det"] = det_containment(q["key_facts"], final + "\n" + body)
    return path.name, g


def main():
    grades = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = [
        p
        for p in sorted((HERE / "results").glob("*__*.json"))
        if p.name not in grades or not grades[p.name].get("judge_ok")
    ]
    with cf.ThreadPoolExecutor(4) as ex:
        for name, g in ex.map(grade, todo):
            grades[name] = g
            OUT.write_text(json.dumps(grades, indent=1, ensure_ascii=False))
    print(
        f"graded {len(todo)} new; total {len(grades)}; judge failures {sum(1 for g in grades.values() if not g.get('judge_ok'))}"
    )


if __name__ == "__main__":
    main()
