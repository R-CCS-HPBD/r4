#!/usr/bin/env python3
"""Aggregate the real-R4 (glm-5.3) benchmark: accuracy, paired tests, cost and efficiency per arm.

Arms: A files only | B + today's platform KG tools | C + prototype typed multimodal KG (kgq) | P = C + context pack.
Prints aggregates only (no answer text). Writes report_r4.json.
"""

import json
import math
import random
import statistics
from collections import defaultdict

import paths as P

HERE = P.OUT
BENCH = P.BENCH
GOLD = {g["id"]: g for g in json.loads((BENCH / "gold/gold.json").read_text())}
KINDS = (
    json.loads((BENCH / "results/source_kinds.json").read_text())
    if (BENCH / "results/source_kinds.json").exists()
    else {}
)
ARMS = ["A", "B", "C", "P"]
SCORE_PAIRS = [("A", "B"), ("A", "C"), ("A", "P"), ("B", "C"), ("C", "P")]
COST_PAIRS = [("A", "B"), ("A", "C"), ("A", "P"), ("C", "P")]
PRINTED = ("n_rows", "overall", "paired_score", "paired_tokens", "paired_wall", "paired_llm")


def load():
    grades = json.loads((HERE / "grades_r4.json").read_text())
    rows = []
    for p in sorted((HERE / "results").glob("*__*.json")):
        r = json.loads(p.read_text())
        g = grades.get(p.name)
        if not g or g.get("score") is None:
            continue
        m = r.get("metrics") or {}
        q = GOLD[r["qid"]]
        rows.append(
            {
                "arm": r["arm"],
                "qid": r["qid"],
                "category": q["category"],
                "project": q["project"],
                "kind": KINDS.get(r["qid"], "?"),
                "status": r.get("status"),
                "score": float(g["score"]),
                "stale": bool(g.get("stale")),
                "halluc": bool(g.get("hallucination")),
                "abst_ok": bool(g.get("abstention_ok")),
                "cited": bool(g.get("evidence_cited")),
                "det": g.get("det"),
                "tokens": m.get("total_tokens"),
                "prompt": m.get("prompt_tokens"),
                "completion": m.get("completion_tokens"),
                "llm": m.get("llm_calls"),
                "wall": m.get("wallclock_sec") or r.get("outer_wall_s"),
            }
        )
    return rows


def sign_test(w, ls):
    n = w + ls
    if n == 0:
        return 1.0
    k = max(w, ls)
    p = sum(math.comb(n, i) for i in range(k, n + 1)) / 2**n
    return min(1.0, 2 * p)


def boot_ci(diffs, it=5000, seed=7):
    if not diffs:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    ms = sorted(statistics.mean(rnd.choices(diffs, k=len(diffs))) for _ in range(it))
    return (round(ms[int(0.025 * it)], 3), round(ms[int(0.975 * it)], 3))


def _arm_quality(rs):
    return {
        "n": len(rs),
        "mean_score": round(statistics.mean(r["score"] for r in rs), 3),
        "full": sum(r["score"] >= 1 for r in rs),
        "stale": sum(r["stale"] for r in rs),
        "halluc": sum(r["halluc"] for r in rs),
        "abst_fail": sum(not r["abst_ok"] for r in rs),
        "cited": sum(r["cited"] for r in rs),
        "failed_runs": sum(r["status"] != "success" for r in rs),
    }


def _arm_cost(ok):
    """Cost of the runs with token accounting (ok); all None when there are none."""
    if not ok:
        return dict.fromkeys(
            ("tokens_mean", "tokens_median", "llm_mean", "wall_mean_s", "wall_median_s")
        )
    return {
        "tokens_mean": round(statistics.mean(r["tokens"] for r in ok)),
        "tokens_median": round(statistics.median(r["tokens"] for r in ok)),
        "llm_mean": round(statistics.mean(r["llm"] for r in ok), 1),
        "wall_mean_s": round(statistics.mean(r["wall"] for r in ok), 1),
        "wall_median_s": round(statistics.median(r["wall"] for r in ok), 1),
    }


def _arm_per_correct(ok, correct):
    if not (correct and ok):
        return {"tokens_per_correct": None, "wall_per_correct_s": None}
    return {
        "tokens_per_correct": round(sum(r["tokens"] for r in ok) / correct),
        "wall_per_correct_s": round(sum(r["wall"] for r in ok) / correct, 1),
    }


def _arm_summary(rs):
    ok = [r for r in rs if r["tokens"]]
    correct = sum(r["score"] for r in rs)
    return {**_arm_quality(rs), **_arm_cost(ok), **_arm_per_correct(ok, correct)}


def summarize(rows, sel=lambda _r: True):
    out = {}
    for a in ARMS:
        rs = [r for r in rows if r["arm"] == a and sel(r)]
        if rs:
            out[a] = _arm_summary(rs)
    return out


def paired(rows, a, b, key="score"):
    by = defaultdict(dict)
    for r in rows:
        by[r["qid"]][r["arm"]] = r
    d, w, ls = [], 0, 0
    for arms in by.values():
        if a in arms and b in arms and arms[a][key] is not None and arms[b][key] is not None:
            x, y = arms[a][key], arms[b][key]
            d.append(y - x)
            w += y > x
            ls += y < x
    return {
        "n": len(d),
        "wins": w,
        "losses": ls,
        "ties": len(d) - w - ls,
        "mean_diff": round(statistics.mean(d), 3) if d else None,
        "ci95": boot_ci(d),
        "sign_p": round(sign_test(w, ls), 4),
    }


def _breakdowns(rows):
    """summarize() per category, project and answer-source kind."""
    rep = {}
    for c in sorted({r["category"] for r in rows}):
        rep[f"cat:{c}"] = summarize(rows, lambda r, c=c: r["category"] == c)
    for p in sorted({r["project"] for r in rows}):
        rep[f"proj:{p}"] = summarize(rows, lambda r, p=p: r["project"] == p)
    for k in sorted({r["kind"] for r in rows}):
        rep[f"kind:{k}"] = summarize(rows, lambda r, k=k: r["kind"] == k)
    return rep


def main():
    rows = load()
    rep = {"n_rows": len(rows), "overall": summarize(rows), **_breakdowns(rows)}
    rep["paired_score"] = {f"{b} vs {a}": paired(rows, a, b) for a, b in SCORE_PAIRS}
    for key in ("tokens", "wall", "llm"):
        rep[f"paired_{key}"] = {f"{b} vs {a}": paired(rows, a, b, key) for a, b in COST_PAIRS}
    (HERE / "report_r4.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: rep[k] for k in PRINTED}, indent=1))


if __name__ == "__main__":
    main()
