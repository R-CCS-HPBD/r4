#!/usr/bin/env python3
"""Judge reliability: re-grade a stratified random sample of already-graded answers with the same glm-5.3 rubric,
blind, and measure agreement with the first grade (exact score match, within-0.5, Cohen's kappa on 3 levels,
flag agreement). Prints aggregates only; writes judge_reliability.json.
"""

import concurrent.futures as cf
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import grade_all as GA  # noqa: E402
import paths as P  # noqa: E402  (code dir on sys.path)

HERE = P.OUT
PER_CELL = 6
SEED = 20261004
FLAGS = ("stale", "hallucination", "abstention_ok", "evidence_cited")


def kappa(a, b, labels=(0.0, 0.5, 1.0)):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[lab] * cb[lab] for lab in labels) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def _cells(grades):
    """(lane, arm) → graded keys of the known lanes, run failures excluded."""
    cells = defaultdict(list)
    for k, g in grades.items():
        if g.get("score") is None or g.get("run_failed"):
            continue
        lane, fn = k.split("/", 1)
        if lane not in GA.LANES:
            continue
        arm = fn.split("__")[0]
        cells[(lane, arm)].append(k)
    return cells


def _sample(cells):
    """Stratified: up to PER_CELL keys per cell, seeded."""
    rnd = random.Random(SEED)
    sample = []
    for key in sorted(cells):
        ks = sorted(cells[key])
        sample += rnd.sample(ks, min(PER_CELL, len(ks)))
    return sample


def _regrade(sample):
    items = [(k, GA.LANES[k.split("/", 1)[0]] / k.split("/", 1)[1]) for k in sample]
    out = {}
    with cf.ThreadPoolExecutor(2) as ex:
        for k, g in ex.map(GA.grade, items):
            out[k] = g
    return out


def _regraded(out, sample):
    """Sampled keys whose regrade produced a score."""
    return [k for k in sample if out.get(k, {}).get("score") is not None]


def _flag_agreement(grades, out, keys):
    return {
        f: sum(bool(grades[k].get(f)) == bool(out[k].get(f)) for k in keys) / len(keys)
        for f in FLAGS
    }


def _summary(grades, out, sample):
    keys = _regraded(out, sample)
    pairs = [(float(grades[k]["score"]), float(out[k]["score"])) for k in keys]
    a, b = [x for x, _ in pairs], [y for _, y in pairs]
    flags = _flag_agreement(grades, out, keys)
    return {
        "n_sampled": len(sample),
        "n_regraded": len(pairs),
        "exact_agree": round(sum(x == y for x, y in pairs) / len(pairs), 3),
        "within_half": round(sum(abs(x - y) <= 0.5 for x, y in pairs) / len(pairs), 3),
        "kappa": round(kappa(a, b), 3),
        "mean_first": round(sum(a) / len(a), 3),
        "mean_second": round(sum(b) / len(b), 3),
        "flag_agreement": {k: round(v, 3) for k, v in flags.items()},
        "confusion": dict(Counter(f"{x}->{y}" for x, y in pairs)),
    }


def main():
    grades = json.loads((HERE / "grades_all.json").read_text())
    sample = _sample(_cells(grades))
    out = _regrade(sample)
    res = _summary(grades, out, sample)
    (HERE / "judge_reliability.json").write_text(
        json.dumps({"summary": res, "regrades": out, "sample": sample}, indent=1)
    )
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
