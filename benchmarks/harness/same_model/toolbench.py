#!/usr/bin/env python3
"""Tool-level retrieval benchmark (no agent): what does each KG tool return for each gold question?

For every gold question, first call of each tool with the question verbatim (what an agent typically does first):
  B-off  kgq-current (today's knowledge_query; LLM extraction off = live state)
  B-on   kgq-current (LLM extraction emulated = best case)
  C-brief kgq brief  (prototype context pack, <=1.2 KB)
  C-search kgq search -k 10 (prototype hybrid search)
Measures bytes, latency, key-fact containment (deterministic numbers + glm-5.3 judge), and the size of the
gold source files an agent would otherwise open. Writes toolbench.json; prints aggregates only.
"""

import concurrent.futures as cf
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import paths as P  # noqa: E402  (code dir on sys.path)
from glmjudge import det_containment, glm, parse_json  # noqa: E402

BENCH = P.BENCH
GOLD = json.loads((BENCH / "gold/gold.json").read_text())
OUT = P.OUT / "toolbench.json"
KINDS = ("B-off", "B-on", "C-brief", "C-search")

SYS = (
    "You audit search-tool outputs. Given a list of KEY FACTS and a TOOL OUTPUT, decide for each key fact whether the "
    "tool output alone states it (present), states part of it (partial) or not (absent). Judge only what the output "
    'says; do not use outside knowledge. Reply with JSON only: {"facts": ["present|partial|absent", ...]}'
)


def projects_for(q):
    return (
        ["example-project-a", "example-project-b"]
        if q["project"] == "cross-project"
        else [q["project"]]
    )


def run_tool(kind, q):
    outs, t0 = [], time.monotonic()
    for p in projects_for(q):
        if kind.startswith("B"):
            env = {"KG_VARIANT": "llm-on" if kind == "B-on" else "llm-off"}
            cmd = [
                "bash",
                "-c",
                f'cd {BENCH}/current && KG_VARIANT={env["KG_VARIANT"]} ./kgq-current "$Q" --project {p}',
            ]
        elif kind == "C-brief":
            cmd = ["bash", "-c", f'{BENCH}/proto/kgq --project {p} brief "$Q"']
        else:
            cmd = ["bash", "-c", f'{BENCH}/proto/kgq --project {p} search "$Q" -k 10']
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env={**__import__("os").environ, "Q": q["question"]},
        )
        outs.append(r.stdout)
    return "\n".join(outs), round(time.monotonic() - t0, 3)


def source_bytes(q):
    """Bytes of distinct gold source files an agent would open (resolved inside the corpus)."""
    seen, total = set(), 0
    for s in q.get("sources", []):
        rel = re.split(r"[\s:#(§]", s.strip())[0]
        for p in projects_for(q):
            f = BENCH / "corpus" / p / rel
            try:
                if f.is_file() and f not in seen:
                    seen.add(f)
                    total += f.stat().st_size
                    break
            except OSError:
                pass
    return total, len(seen)


def _judge_user(facts, text):
    return (
        "KEY FACTS:\n"
        + "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts))
        + "\n\nTOOL OUTPUT:\n"
        + text[:12000]
    )


def _label(x):
    """A judge label is a string, or a dict carrying it under status/label/verdict."""
    if isinstance(x, dict):
        return x.get("status") or x.get("label") or x.get("verdict") or ""
    return x


def judge(q, text):
    facts = q["key_facts"]
    j = parse_json(glm(SYS, _judge_user(facts, text), max_tokens=4000))
    labels = (j or {}).get("facts") if isinstance(j, dict) else None
    if not isinstance(labels, list) or len(labels) != len(facts):
        return None
    labels = [_label(x) for x in labels]
    sc = {"present": 1.0, "partial": 0.5}
    return round(sum(sc.get(str(x).strip().lower(), 0.0) for x in labels) / len(facts), 3)


def one(q):
    row = {"id": q["id"], "project": q["project"], "category": q["category"]}
    row["src_bytes"], row["src_files"] = source_bytes(q)
    for kind in KINDS:
        text, lat = run_tool(kind, q)
        row[kind] = {
            "bytes": len(text.encode()),
            "latency_s": lat,
            "det": det_containment(q["key_facts"], text),
            "glm": judge(q, text),
        }
    return row


def _kind_line(rows, kind):
    b = [r[kind]["bytes"] for r in rows]
    lat = [r[kind]["latency_s"] for r in rows]
    g = [r[kind]["glm"] for r in rows if r[kind]["glm"] is not None]
    d = [r[kind]["det"] for r in rows if r[kind]["det"] == r[kind]["det"]]  # NaN dropped
    return (
        f"{kind:9} bytes med={statistics.median(b):7.0f} mean={statistics.mean(b):7.0f} | "
        f"lat med={statistics.median(lat):.2f}s | glm-contain mean={statistics.mean(g):.3f} (n={len(g)}) "
        f"| det-contain mean={statistics.mean(d):.3f} (n={len(d)})"
    )


def main():
    rows = []
    with cf.ThreadPoolExecutor(4) as ex:
        for r in ex.map(one, GOLD):
            rows.append(r)
            print(f"{r['id']} done", flush=True)
    OUT.write_text(json.dumps(rows, indent=1))
    print(f"\n=== aggregates (n={len(rows)}) ===")
    for kind in KINDS:
        print(_kind_line(rows, kind))
    sb = [r["src_bytes"] for r in rows if r["src_bytes"]]
    print(
        f"gold source files: med={statistics.median(sb):.0f} B mean={statistics.mean(sb):.0f} B (n={len(sb)})"
    )


if __name__ == "__main__":
    main()
