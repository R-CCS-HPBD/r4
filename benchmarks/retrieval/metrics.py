"""Retrieval and verdict metrics with bootstrap intervals (ADR-093 S0).

Retrieval is scored at document level against a claim's evidence set:
recall@k, MRR@10 and nDCG@10 with binary gains. Verdicts are scored as
3-class accuracy and macro-F1, with the two failure modes that matter for a
small model reported on their own: a verdict asserted for a claim that has no
evidence (``unsupported_verdict``), and an abstention when the evidence was
retrieved and shown (``wrong_abstain``). Intervals are a seeded percentile
bootstrap over claims; ``paired_delta`` resamples the same claims for two
runs, so a difference is judged on matched items.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence

BOOT = 2000
SEED = 20261010


def recall_at(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant)


def mrr_at(ranked: Sequence[str], relevant: set[str], k: int = 10) -> float:
    for rank, doc in enumerate(ranked[:k], start=1):
        if doc in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at(ranked: Sequence[str], relevant: set[str], k: int = 10) -> float:
    gains = [1.0 / math.log2(rank + 1) for rank, d in enumerate(ranked[:k], 1) if d in relevant]
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    return sum(gains) / ideal


def dedup(ranked: Sequence[str]) -> list[str]:
    """Doc ids in first-seen order: several chunks of one doc count once."""
    seen: set[str] = set()
    out = []
    for doc in ranked:
        if doc not in seen:
            seen.add(doc)
            out.append(doc)
    return out


def retrieval_scores(ranked: Sequence[str], relevant: set[str]) -> dict[str, float]:
    docs = dedup(ranked)
    scores = {f"recall@{k}": recall_at(docs, relevant, k) for k in (1, 3, 5, 10, 20)}
    scores["mrr@10"] = mrr_at(docs, relevant)
    scores["ndcg@10"] = ndcg_at(docs, relevant)
    return scores


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def bootstrap(values: Sequence[float], *, stat: Callable = mean, seed: int = SEED) -> list[float]:
    """[low, high] of a 95% percentile bootstrap of ``stat`` over items."""
    if not values:
        return [0.0, 0.0]
    rng = random.Random(seed)
    n = len(values)
    draws = sorted(stat([values[rng.randrange(n)] for _ in range(n)]) for _ in range(BOOT))
    return [draws[int(0.025 * BOOT)], draws[int(0.975 * BOOT) - 1]]


def paired_delta(
    a: Sequence[float], b: Sequence[float], *, seed: int = SEED, lower_is_better: bool = False
) -> dict:
    """mean(b - a) over matched items, its 95% interval and the share of
    resamples where B is no better (delta <= 0; delta >= 0 when lower is better)."""
    diffs = [y - x for x, y in zip(a, b, strict=True)]
    if not diffs:
        return {"delta": 0.0, "ci95": [0.0, 0.0], "p_not_better": 1.0}
    rng = random.Random(seed)
    n = len(diffs)
    draws = [mean([diffs[rng.randrange(n)] for _ in range(n)]) for _ in range(BOOT)]
    return {"delta": mean(diffs), **_interval(draws, lower_is_better)}


def _interval(draws: list[float], lower_is_better: bool) -> dict:
    draws = sorted(draws)
    worse = sum((d >= 0) if lower_is_better else (d <= 0) for d in draws)
    return {
        "ci95": [draws[int(0.025 * BOOT)], draws[int(0.975 * BOOT) - 1]],
        "p_not_better": worse / BOOT,
    }


def paired_statistic(
    items_a: Sequence, items_b: Sequence, stat: Callable, *, seed: int = SEED
) -> dict:
    """stat(B) - stat(A) over matched items, with a 95% interval from
    resampling the item indices (for a statistic that is not a mean of
    per-item values, such as macro-F1)."""
    n = len(items_a)
    if n == 0 or n != len(items_b):
        return {"delta": 0.0, "ci95": [0.0, 0.0], "p_not_better": 1.0, "n": n}
    rng = random.Random(seed)
    draws = []
    for _ in range(BOOT):
        idx = [rng.randrange(n) for _ in range(n)]
        draws.append(stat([items_b[i] for i in idx]) - stat([items_a[i] for i in idx]))
    return {"delta": stat(items_b) - stat(items_a), **_interval(draws, False), "n": n}


#: Non-inferiority margin on accuracy and macro-F1 (ADR-093 C0): B is
#: non-inferior when the lower CI bound of B - A is at least -3 points.
NI_MARGIN = 0.03


def non_inferior(delta: dict, margin: float = NI_MARGIN) -> bool:
    return delta["ci95"][0] >= -margin


def percentiles(values: Sequence[float], points: Sequence[int] = (50, 95)) -> dict:
    """Nearest-rank percentiles (p50, p95) and n; None without values."""
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return {f"p{p}": None for p in points} | {"n": 0}
    n = len(ordered)
    out: dict = {f"p{p}": ordered[max(0, math.ceil(p / 100 * n) - 1)] for p in points}
    return out | {"n": n}


def macro_f1(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> float:
    f1s = []
    for label in labels:
        tp = sum(g == label and p == label for g, p in zip(gold, pred, strict=True))
        fp = sum(g != label and p == label for g, p in zip(gold, pred, strict=True))
        fn = sum(g == label and p != label for g, p in zip(gold, pred, strict=True))
        denom = 2 * tp + fp + fn
        f1s.append(2 * tp / denom if denom else 0.0)
    return mean(f1s)


def context_scores(shown: Sequence[str], relevant: set[str]) -> dict[str, float]:
    """What reached the model: the share of shown passages that are evidence
    (ctx_precision), whether any evidence was shown (ctx_recall), whether ALL
    of it was (ctx_recall_all: the multi-evidence probe's measure), and how
    many passages were shown. An empty context scores 0 on all three."""
    docs = list(shown)
    hits = sum(doc in relevant for doc in docs)
    return {
        "ctx_precision": hits / len(docs) if docs else 0.0,
        "ctx_recall": float(hits > 0),
        "ctx_recall_all": float(bool(relevant) and relevant <= set(docs)),
        "passages": float(len(docs)),
    }


def mcnemar(pairs: Sequence[tuple[bool, bool]]) -> dict[str, float]:
    """Exact two-sided McNemar on paired right/wrong outcomes (A, B).

    ``b`` counts A right and B wrong, ``c`` A wrong and B right; the p value
    is the binomial tail of the discordant pairs at 1/2."""
    b = sum(1 for x, y in pairs if x and not y)
    c = sum(1 for x, y in pairs if y and not x)
    n = b + c
    if n == 0:
        return {"b": 0, "c": 0, "p": 1.0}
    tail = sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / 2**n
    return {"b": b, "c": c, "p": min(1.0, 2 * tail)}


# ── sufficiency: does the scorer's verdict separate in-KB from out-of-KB? ────

#: ADR-083 receipt 3 / ADR-093 S1: nonsense INSUFFICIENT at >= 0.9, and at most
#: 5% of in-KB evidence claims wrongly INSUFFICIENT.
NONSENSE_GATE = 0.9
FALSE_INSUFFICIENT_GATE = 0.05
FEATURES = ("z_null", "gap12", "lexical", "rerank_p")


def auroc(positives: Sequence[float], negatives: Sequence[float]) -> float | None:
    """P(score of a positive > score of a negative), ties counted half: the
    Mann-Whitney U over n_pos x n_neg. None when either side is empty."""
    if not positives or not negatives:
        return None
    ranked = sorted([(v, 1) for v in positives] + [(v, 0) for v in negatives])
    rank_sum, i = 0.0, 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        mid = (i + 1 + j) / 2  # the average 1-based rank of the tie block
        rank_sum += mid * sum(flag for _, flag in ranked[i:j])
        i = j
    n_pos, n_neg = len(positives), len(negatives)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def insufficient_rate(statuses: Sequence[str]) -> float | None:
    return mean([float(s == "insufficient") for s in statuses]) if statuses else None


def sufficiency_report(
    groups: dict[str, Sequence[str]], errors: dict[str, int] | None = None
) -> dict[str, dict]:
    """Insufficient rates per claim group (``evidence``, ``nei``, ``nonsense``)
    with the two pre-registered gates.

    ``rate`` is over the judged claims; ``errors`` counts the group's claims
    that errored (no verdict). A gate is judged on ``rate_itt``, every
    errored claim counted as the gate's failing outcome (not insufficient on
    nonsense, insufficient on evidence): a scorer cannot pass by failing its
    hard claims. A gate with no claims is not passed."""
    errors = errors or {}
    nonsense = groups.get("nonsense") or []
    evidence = groups.get("evidence") or []
    nei = groups.get("nei") or []
    return {
        "insufficient_nonsense": _gate(nonsense, errors.get("nonsense", 0), at_least=True),
        "false_insufficient_evidence": _gate(evidence, errors.get("evidence", 0), at_least=False),
        "insufficient_nei": {
            "rate": insufficient_rate(nei),
            "n": len(nei),
            "errors": errors.get("nei", 0),
        },
    }


def _gate(statuses: Sequence[str], errors: int, *, at_least: bool) -> dict:
    """One gate: ``at_least`` NONSENSE_GATE, else at most FALSE_INSUFFICIENT_GATE."""
    worst = _itt_rate(statuses, errors, errored_insufficient=not at_least)
    if at_least:
        gate, passed = f">= {NONSENSE_GATE}", worst is not None and worst >= NONSENSE_GATE
    else:
        gate = f"<= {FALSE_INSUFFICIENT_GATE}"
        passed = worst is not None and worst <= FALSE_INSUFFICIENT_GATE
    return {
        "rate": insufficient_rate(statuses),
        "n": len(statuses),
        "errors": errors,
        "rate_itt": worst,
        "gate": gate,
        "pass": passed,
    }


def _itt_rate(statuses: Sequence[str], errors: int, *, errored_insufficient: bool) -> float | None:
    total = len(statuses) + errors
    if not total:
        return None
    hits = sum(s == "insufficient" for s in statuses) + (errors if errored_insufficient else 0)
    return hits / total


def feature_aurocs(features: dict[str, Sequence[dict]]) -> dict[str, dict]:
    """AUROC of each sufficiency feature, in-KB evidence claims (positive)
    against nonsense and against NEI claims. Rows missing a feature are left out."""
    out: dict[str, dict] = {}
    for name in FEATURES:
        pos = _values(features.get("evidence") or [], name)
        out[name] = {
            f"evidence_vs_{neg}": {
                "auroc": auroc(pos, vals),
                "n_pos": len(pos),
                "n_neg": len(vals),
            }
            for neg in ("nonsense", "nei")
            for vals in [_values(features.get(neg) or [], name)]
        }
    return out


def _values(rows: Sequence[dict], name: str) -> list[float]:
    return [float(r[name]) for r in rows if r and r.get(name) is not None]


def sufficiency_features(result: dict) -> dict[str, float | None]:
    """The scorer's evidence for one ``RetrieveResult.to_dict()``:

    - ``z_null``: (best kept cosine - null mu) / null sigma, from the null the
      server reports (``lanes.null``), else the verdict's own z value;
    - ``gap12``: best minus second-best cosine over the pre-cut candidates
      (``trace.candidates``; the kept passages without a trace);
    - ``lexical``: the best lexical score among them (0 when the lexical lane
      ran and matched nothing; None when it did not run);
    - ``rerank_p``: the top passage's reranker probability, when reranked.
    """
    passages = result.get("passages") or []
    candidates = (result.get("trace") or {}).get("candidates") or passages
    lanes = result.get("lanes") or {}
    return {
        "z_null": _z_null(passages, lanes.get("null") or {}, result.get("sufficiency") or {}),
        "gap12": _gap12(candidates),
        "lexical": _lexical(candidates, lanes),
        "rerank_p": _score(passages[0], "rerank_p") if passages else None,
    }


def _score(item: dict, name: str) -> float | None:
    value = (item.get("scores") or {}).get(name)
    return None if value is None else float(value)


def _z_null(passages: Sequence[dict], null: dict, verdict: dict) -> float | None:
    dense = [s for p in passages if (s := _score(p, "dense")) is not None]
    if null.get("status") == "ok" and dense and null.get("sigma") is not None:
        return (max(dense) - float(null["mu"])) / max(float(null["sigma"]), 1e-6)
    if verdict.get("basis") == "z_null" and verdict.get("value") is not None:
        return float(verdict["value"])
    return None


def _gap12(candidates: Sequence[dict]) -> float | None:
    dense = sorted((s for c in candidates if (s := _score(c, "dense")) is not None), reverse=True)
    return dense[0] - dense[1] if len(dense) >= 2 else None


def _lexical(candidates: Sequence[dict], lanes: dict) -> float | None:
    if (lanes.get("lex") or {}).get("status") != "ok":
        return None
    return max((s for c in candidates if (s := _score(c, "lexical")) is not None), default=0.0)


# ── what the shown passages carry of the rationale ─────────────────────────


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def rationale_coverage(shown_texts: Sequence[str], sentences: Sequence[str]) -> float | None:
    """The share of rationale sentences found whole inside one shown passage
    (whitespace and case folded). A sentence split across two chunks counts
    only when a merged passage holds it entire. None without sentences."""
    wanted = [_norm(s) for s in sentences if _norm(s)]
    if not wanted:
        return None
    texts = [_norm(t) for t in shown_texts]
    return mean([float(any(s in t for t in texts)) for s in wanted])
