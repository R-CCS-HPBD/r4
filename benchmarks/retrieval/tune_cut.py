"""Tune the relevance cut on SciFact train only (ADR-093 D-1, D-3).

    export XDG_CONFIG_HOME=<an r4 config logged in to the stand-alone stack>
    .venv/bin/python benchmarks/retrieval/tune_cut.py collect --data <scifact>/data
    .venv/bin/python benchmarks/retrieval/tune_cut.py grid

``collect`` asks the live retrieve verb for the pre-cut candidates of every
tune claim that has evidence (``trace=1``: dense, lexical and fused scores)
and caches them. ``grid`` replays UMRS's own ``cutoff.cut`` over the cache for
every constant in the grid. It reads the constants from the environment on
each call, as the server does. Rules are scored by what would reach the
reader: ctx_precision, ctx_recall and passages. A rule is admissible when its
ctx_recall is within ``--recall-margin`` of top-5 (the legacy shape, the
pre-registered S1 bound); among admissible rules the highest ctx_precision
wins. The dev split is never read here.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backends  # noqa: E402
import metrics  # noqa: E402
import scifact  # noqa: E402

CACHE = Path(
    os.environ.get("ADR093_TUNE_CACHE")
    or Path(__file__).resolve().parent / "results" / "tune_candidates.json"
)

GRID = {
    "UMRS_RAG_DELTA": (0.01, 0.02, 0.03, 0.05),
    "UMRS_RAG_GAMMA": (0.01, 0.015, 0.02, 0.03),
    "UMRS_RAG_LEX_RATIO": (0.5, 0.7, 0.8, 0.9, 1.01),
    "k_max": (2, 3, 4),
}


def collect(args: argparse.Namespace) -> int:
    claims = [c for c in scifact.claims_for(Path(args.data), "tune") if c.evidence]
    stack = backends.Stack(graph=args.graph)
    out = []
    for n, claim in enumerate(claims, start=1):
        result = stack.client.rag.retrieve(
            claim.text,
            project_id=stack.project,
            graphs=[stack.graph],
            lexical="on",
            rerank="off",
            merge="off",
            cutoff="adaptive",
            trace=True,
        )
        rows = [
            {**row, "doc": stack.doc_of(row.get("node_id"), row.get("artifact_id"))}
            for row in (result.trace or {}).get("candidates") or []
        ]
        out.append({"claim": claim.id, "evidence": sorted(claim.evidence), "candidates": rows})
        if n % 50 == 0:
            print(f"  {n}/{len(claims)}", file=sys.stderr, flush=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out))
    print(f"{len(out)} tune claims cached at {CACHE}")
    return 0


def _candidates(rows: list[dict[str, Any]]):
    from umrs.knowledge.retrieval.types import Candidate

    out = []
    for rank, row in enumerate(rows, start=1):
        scores = row.get("scores") or {}
        cand = Candidate(node_id=row["node_id"], artifact_id=row.get("artifact_id") or "")
        cand.cosine = scores.get("dense")
        cand.fused = float(scores.get("fused") or 0.0)
        if scores.get("dense") is not None:
            cand.lane_scores["dense"] = float(scores["dense"])
        if scores.get("lexical") is not None:
            cand.lane_scores["lex"] = float(scores["lexical"])
        cand.lane_ranks["fused"] = rank
        cand.content = {"doc": row.get("doc")}
        out.append(cand)
    return out


def _score_rule(cache: list[dict[str, Any]], mode: str, k_max: int) -> dict[str, float]:
    from umrs.knowledge.retrieval import cutoff
    from umrs.knowledge.retrieval.types import RetrieveSpec

    precision, recall, passages = [], [], []
    for item in cache:
        cands = _candidates(item["candidates"])
        if mode == "dense":
            cands = sorted(
                (c for c in cands if c.cosine is not None), key=lambda c: -(c.cosine or 0)
            )
        if mode == "top5":
            kept = cands[:5]
        else:
            spec = RetrieveSpec(queries=("q",), project_id="p", k_max=k_max)
            kept = cutoff.cut(cands, spec, mode=mode) if cands else []
        shown = [c.content.get("doc") for c in kept if c.content.get("doc")]
        scores = metrics.context_scores(metrics.dedup(shown), set(item["evidence"]))
        precision.append(scores["ctx_precision"])
        recall.append(scores["ctx_recall"])
        passages.append(scores["passages"])
    return {
        "ctx_precision": metrics.mean(precision),
        "ctx_recall": metrics.mean(recall),
        "passages": metrics.mean(passages),
    }


def grid(args: argparse.Namespace) -> int:
    cache = json.loads(CACHE.read_text())
    reference = _score_rule(cache, "top5", 5)
    floor = reference["ctx_recall"] - args.recall_margin
    results = []
    names = list(GRID)
    for values in itertools.product(*(GRID[n] for n in names)):
        setting = dict(zip(names, values, strict=True))
        for key in ("UMRS_RAG_DELTA", "UMRS_RAG_GAMMA", "UMRS_RAG_LEX_RATIO"):
            os.environ[key] = str(setting[key])
        for mode in ("dense", "fused"):
            if mode == "dense" and setting["UMRS_RAG_LEX_RATIO"] != GRID["UMRS_RAG_LEX_RATIO"][0]:
                continue  # the lexical ratio does not touch the dense rule
            scored = _score_rule(cache, mode, setting["k_max"])
            results.append(
                {"mode": mode, **setting, **scored, "admissible": scored["ctx_recall"] >= floor}
            )
    best = _best(results)
    report = {
        "tune_claims": len(cache),
        "top5": reference,
        "recall_floor": floor,
        "best": best,
        "rules": results,
    }
    out = CACHE.with_name("tune_grid.json")
    out.write_text(json.dumps(report, indent=1))
    print(json.dumps({"top5": reference, "recall_floor": round(floor, 4), "best": best}, indent=1))
    return 0


def _best(results: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for mode in ("dense", "fused"):
        ok = [r for r in results if r["mode"] == mode and r["admissible"]]
        if ok:
            out[mode] = max(ok, key=lambda r: (r["ctx_precision"], -r["passages"]))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("collect")
    p.add_argument("--data", required=True)
    p.add_argument("--graph", default="scifact-eval")
    p = sub.add_parser("grid")
    p.add_argument("--recall-margin", type=float, default=0.03)
    args = parser.parse_args(argv)
    return collect(args) if args.cmd == "collect" else grid(args)


if __name__ == "__main__":
    raise SystemExit(main())
