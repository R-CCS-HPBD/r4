#!/usr/bin/env python3
"""Post-process toolbench.json: gold source-file sizes (corpus-relative paths) + per-category containment."""

import json
import re
import statistics
from collections import defaultdict

import paths as P

BENCH = P.BENCH
GOLD = {g["id"]: g for g in json.loads((BENCH / "gold/gold.json").read_text())}
rows = json.loads((P.OUT / "toolbench.json").read_text())
KINDS = ("B-off", "B-on", "C-brief", "C-search")
for r in rows:
    seen, tot = set(), 0
    for s in GOLD[r["id"]]["sources"]:
        f = BENCH / "corpus" / re.split(r"[\s:#(§]", s.strip())[0]
        if f.is_file() and f.resolve() not in seen:
            seen.add(f.resolve())
            tot += f.stat().st_size
    r["src_bytes"], r["src_files"] = tot, len(seen)
(P.OUT / "toolbench.json").write_text(json.dumps(rows, indent=1))
sb = [r["src_bytes"] for r in rows if r["src_bytes"]]
print(
    f"gold source files per question: median {statistics.median(sb):,.0f} B, mean {statistics.mean(sb):,.0f} B, "
    f"files median {statistics.median([r['src_files'] for r in rows if r['src_files']])} (n={len(sb)} of {len(rows)})"
)
summ = {
    "n": len(rows),
    "src_bytes_median": statistics.median(sb),
    "src_bytes_mean": statistics.mean(sb),
    "tools": {},
    "by_category": {},
}
for k in KINDS:
    g = [r[k]["glm"] for r in rows if r[k]["glm"] is not None]
    summ["tools"][k] = {
        "bytes_median": statistics.median(r[k]["bytes"] for r in rows),
        "glm_mean": round(statistics.mean(g), 3),
        "any_fact": sum(1 for x in g if x > 0),
        "half_or_more": sum(1 for x in g if x >= 0.5),
        "n": len(g),
        "lat_median_s": statistics.median(r[k]["latency_s"] for r in rows),
    }
    print(
        f"{k:9} answer-bearing (>=1 key fact) {summ['tools'][k]['any_fact']:2}/{len(g)}  >=half of facts {summ['tools'][k]['half_or_more']:2}/{len(g)}"
    )
cats = defaultdict(list)
for r in rows:
    cats[GOLD[r["id"]]["category"]].append(r)
print("\nper category glm containment (B-on / C-brief / C-search):")
for c, rs in sorted(cats.items()):
    vals = []
    for k in ("B-on", "C-brief", "C-search"):
        g = [r[k]["glm"] for r in rs if r[k]["glm"] is not None]
        vals.append(round(statistics.mean(g), 2) if g else float("nan"))
    summ["by_category"][c] = dict(zip(("B-on", "C-brief", "C-search"), vals, strict=True)) | {
        "n": len(rs)
    }
    print(f"  {c:18} n={len(rs):2}  {vals}")
(P.OUT / "toolbench_summary.json").write_text(json.dumps(summ, indent=1))
