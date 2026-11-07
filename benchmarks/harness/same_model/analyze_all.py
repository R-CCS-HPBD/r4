#!/usr/bin/env python3
"""Aggregate the same-model (glm-5.3@rikyu) harness comparison + KG arms. Aggregates only, no answer text.

Cells: (lane, arm). lanes: r4 (hardened, decision model off) | claude (Claude Code, BYOM bridge) |
opencode (OpenCode v2, passthrough) | r4deployed (run_glm.py: as-deployed R4, secondary condition).
Cost accounting for hardened lanes comes from the proxy (proxy_usage) so it is identical across harnesses.
Writes report_all.json.
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
KINDS = json.loads((BENCH / "results/source_kinds.json").read_text())
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
PLATFORM_TOOLS = (
    "lab_catalog_search",
    "rag_query",
    "search_docs",
    "knowledge_query",
    "knowledge_show",
    "knowledge_status",
    "knowledge_graph_list",
    "knowledge_ingest",
    "vision_submit",
    "endpoint",
    "job_",
    "source_",
    "search_agentic",
)


def _cost_deployed(r, m):
    # run_glm.py lane: run metrics, no proxy accounting
    return {
        "tokens": m.get("total_tokens"),
        "requests": m.get("llm_calls"),
        "completion": m.get("completion_tokens"),
        "cached": None,
        "wall": m.get("wallclock_sec") or r.get("outer_wall_s"),
        "tools": None,
        "platform_calls": None,
    }


def _platform_calls(called):
    if not isinstance(called, dict):
        return None
    return sum(v for k, v in called.items() if any(k.startswith(p) for p in PLATFORM_TOOLS))


def _tool_calls(m, called):
    tools = m.get("tool_calls")
    if tools is None and isinstance(called, dict):
        tools = sum(called.values())
    return tools


def cost(r, lane):
    pu = r.get("proxy_usage") or {}
    m = r.get("metrics") or {}
    if lane == "r4deployed":
        return _cost_deployed(r, m)
    called = pu.get("called") or {}
    plat = _platform_calls(called)
    return {
        "tokens": pu.get("agent_total_tokens") or pu.get("total_tokens"),
        "requests": pu.get("agent_requests") or pu.get("requests"),
        "completion": pu.get("completion_tokens"),
        "cached": pu.get("cached_tokens"),
        "wall": r.get("wall_s"),
        "tools": _tool_calls(m, called),
        "platform_calls": plat,
    }


def load():
    grades = json.loads((HERE / "grades_all.json").read_text())
    rows = []
    for lane, d in LANES.items():
        for p in sorted(d.glob("*__*.json")):
            g = grades.get(f"{lane}/{p.name}")
            if not g or g.get("score") is None:
                continue
            r = json.loads(p.read_text())
            q = GOLD[r["qid"]]
            rows.append(
                {
                    "lane": lane,
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
                    "contaminated": bool(sum((r.get("bench_path_mentions") or {}).values()))
                    if r.get("bench_path_mentions")
                    else False,
                    **cost(r, lane),
                }
            )
    return rows


def sign_p(w, ls):
    n = w + ls
    if not n:
        return 1.0
    k = max(w, ls)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / 2**n)


def ci(d, it=5000, seed=11):
    if not d:
        return None
    rnd = random.Random(seed)
    ms = sorted(statistics.mean(rnd.choices(d, k=len(d))) for _ in range(it))
    return [round(ms[int(0.025 * it)], 3), round(ms[int(0.975 * it)], 3)]


def _stat(fn, ok, k):
    """fn (mean/median) of field k over the rows that have it, 1 decimal; None when none do."""
    v = [r[k] for r in ok if r[k] is not None]
    return round(fn(v), 1) if v else None


def _quality(rs):
    return {
        "n": len(rs),
        "mean_score": round(statistics.mean(r["score"] for r in rs), 3),
        "full": sum(r["score"] >= 1 for r in rs),
        "stale": sum(r["stale"] for r in rs),
        "halluc": sum(r["halluc"] for r in rs),
        "abst_fail": sum(not r["abst_ok"] for r in rs),
        "run_failed": sum(r["status"] != "success" for r in rs),
        "contaminated": sum(r["contaminated"] for r in rs),
    }


def _per_correct(ok, s):
    """(tokens, wall s) per correct answer; s = summed score."""
    if not (s and ok):
        return None, None
    return (
        round(sum(r["tokens"] for r in ok) / s),
        round(sum(r["wall"] for r in ok if r["wall"]) / s, 1),
    )


def cell_summary(rs):
    ok = [r for r in rs if r["tokens"]]
    tpc, wpc = _per_correct(ok, sum(r["score"] for r in rs))
    mean, med = statistics.mean, statistics.median
    return {
        **_quality(rs),
        "tokens_mean": _stat(mean, ok, "tokens"),
        "tokens_median": _stat(med, ok, "tokens"),
        "requests_mean": _stat(mean, ok, "requests"),
        "completion_mean": _stat(mean, ok, "completion"),
        "wall_mean_s": _stat(mean, ok, "wall"),
        "wall_median_s": _stat(med, ok, "wall"),
        "tools_mean": _stat(mean, ok, "tools"),
        "platform_calls_total": sum(r["platform_calls"] or 0 for r in rs),
        "tokens_per_correct": tpc,
        "wall_per_correct_s": wpc,
    }


def summarize(rows, sel=lambda _r: True):
    cells = defaultdict(list)
    for r in rows:
        if sel(r):
            cells[f"{r['lane']}/{r['arm']}"].append(r)
    return {k: cell_summary(v) for k, v in sorted(cells.items())}


def paired(rows, a, b, key="score", only_common=True):
    idx = defaultdict(dict)
    for r in rows:
        idx[r["qid"]][f"{r['lane']}/{r['arm']}"] = r
    d, w, ls = [], 0, 0
    for cells in idx.values():
        if a in cells and b in cells and cells[a][key] is not None and cells[b][key] is not None:
            x, y = cells[a][key], cells[b][key]
            d.append(y - x)
            w += y > x
            ls += y < x
    return {
        "n": len(d),
        "wins": w,
        "losses": ls,
        "ties": len(d) - w - ls,
        "mean_diff": round(statistics.mean(d), 3) if d else None,
        "ci95": ci(d),
        "sign_p": round(sign_p(w, ls), 4),
    }


def main():
    rows = load()
    rep = {"n_rows": len(rows), "cells": summarize(rows)}
    for c in sorted({r["category"] for r in rows}):
        rep[f"cat:{c}"] = summarize(rows, lambda r, c=c: r["category"] == c)
    for p in sorted({r["project"] for r in rows}):
        rep[f"proj:{p}"] = summarize(rows, lambda r, p=p: r["project"] == p)
    for k in sorted({r["kind"] for r in rows}):
        rep[f"kind:{k}"] = summarize(rows, lambda r, k=k: r["kind"] == k)
    pairs = [
        ("claude/A", "r4/A"),
        ("opencode/A", "r4/A"),
        ("claude/A", "opencode/A"),
        ("claude/C", "r4/C"),
        ("opencode/C", "r4/C"),
        ("claude/C", "opencode/C"),
        ("r4/A", "r4/C"),
        ("claude/A", "claude/C"),
        ("opencode/A", "opencode/C"),
        ("r4/A", "r4/B"),
        ("r4/A", "r4/P"),
        ("r4/C", "r4/P"),
        ("r4/B", "r4/C"),
        ("r4deployed/A", "r4/A"),
        ("r4deployed/C", "r4/C"),
        ("r4/A", "r4fixall/A"),
        ("r4/C", "r4fixall/C"),
        ("r4/A", "r4fixhist/A"),
        ("claude/A", "r4fixall/A"),
        ("opencode/A", "r4fixall/A"),
        ("claude/C", "r4fixall/C"),
        ("opencode/C", "r4fixall/C"),
    ]
    for key in ("score", "tokens", "requests", "wall"):
        rep[f"paired_{key}"] = {f"{b} vs {a}": paired(rows, a, b, key) for a, b in pairs}
    (HERE / "report_all.json").write_text(json.dumps(rep, indent=1))
    print(
        json.dumps(
            {"n_rows": rep["n_rows"], "cells": rep["cells"], "paired_score": rep["paired_score"]},
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
