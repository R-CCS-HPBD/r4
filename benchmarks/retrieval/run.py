"""ADR-093 retrieval + inference eval on the stand-alone stack (C0).

    # once: the stack is up (STANDALONE_PROFILE=dev), the corpus is ingested
    # into the graph `scifact-eval` (see README.md), and r4 is logged in:
    export XDG_CONFIG_HOME=<an r4 config logged in with `standalone.sh token`>
    R=".venv/bin/python benchmarks/retrieval/run.py"
    $R retrieve --data <scifact>/data --arms k3-embeddings,legacy,retrieve:dense
    $R infer    --data <scifact>/data --arms closed,oracle,legacy,k3-embeddings
    $R infer    --data <scifact>/data --arms retrieve:dense --prompt pack-soft --scorer-gate
    $R infer    --data <scifact>/data --arms agent            # 20 per label
    $R infer    --data <scifact>/data --arms retrieve:large \
                --reader-config <XDG dir logged into rikyu> --reader-model kimi-k3
    $R answer   --data <scifact>/data                         # /rag/query, E-B
    $R compare  results/A.json results/B.json
    $R parity   results/A.json results/B.json
    $R repro    results/<run 1>.json results/<run 2>.json     # S9: a run reproduces itself
    $R headroom results/<arm>.json results/<closed>.json results/<oracle>.json
    $R sufficiency results/<dev retrieve:X>.json results/<nonsense retrieve:X>.json
    $R rescore  --data <scifact>/data results/<a B0 file>.json   # schema 1 -> 2

``retrieve`` scores the ranked documents against each claim's evidence.
``infer`` asks the chat model for a verdict from exactly the context an arm
produces (E-A). ``answer`` runs the real answer path, /rag/query (E-B), with
no threshold unless ``--threshold`` names one (0.7 is the B0 bracket).
``compare`` gives paired deltas on matched claims plus McNemar on verdicts,
the S1 receipts (wrong_abstain_when_shown, unsupported_verdict, macro-F1
with non-inferiority) and what the pairing left out. ``parity`` counts
claims with identical shown ids in identical order; ``headroom`` places an
arm between the closed-book and oracle brackets; ``repro`` says whether two
runs of one configuration give the same verdicts, replies, passages and
citations claim by claim (ADR-093 S9: the reader at temperature 0, twice).
``sufficiency`` pools result files of a ``retrieve:*`` arm and reports the
insufficient rates against their gates and the AUROC of the scorer's features.
Every comparison (``comparisons.py``) reports the factors its files did not hold
fixed and the metric definitions they differ in; ``rescore`` rewrites a B0
file's verdict and citation fields under the current definitions.
``--claims dev|tune|nonsense`` picks the claim set (tune for tuning only);
``--prompt`` the verdict prompt for every arm (default: each arm's own);
``--scorer-gate`` answers NOT_ENOUGH_INFO with no model call when the arm
reports insufficient. ``infer --reader-config DIR --reader-model M`` sends
only the verdict call through a second r4 client built from DIR (ADR-093
D-14: the same passages read by a large remote model); without the flag no
remote reader is ever used. Results land in ``results/`` as JSON, with the
provenance that produced them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import arms  # noqa: E402
import backends  # noqa: E402
import comparisons  # noqa: E402
import infer  # noqa: E402
import metrics  # noqa: E402
import scifact  # noqa: E402

from r4_contracts.context_pack import estimate_tokens  # noqa: E402

RESULTS = Path(os.environ.get("ADR093_RESULTS") or Path(__file__).resolve().parent / "results")
NEI = comparisons.NEI
SCHEMA_VERSION = comparisons.SCHEMA_VERSION
#: How much of a reply and an answer a row stores (``infer.ask``, ``_answer_row``).
REPLY_CHARS, ANSWER_CHARS = 600, 800
# shared with comparisons.py (the comparisons); kept here under their old names
sufficiency_block = comparisons.sufficiency_block
headroom_used = comparisons.headroom_used
completeness = comparisons.completeness
_has_evidence = comparisons.has_evidence


def _subset(claims, limit: int | None, *, stratified: bool):
    """The first ``limit`` claims, or ``limit`` per label when stratified."""
    if not limit:
        return claims
    if not stratified:
        return claims[:limit]
    return [c for label in scifact.LABELS for c in [c for c in claims if c.label == label][:limit]]


def is_fatal(exc: BaseException) -> bool:
    """A failure every later claim would hit too: a refused or expired login
    (r4 never refreshes an OIDC token), on the stack or the remote reader.
    It stops the run instead of filling the file with error rows."""
    from r4.errors import AuthenticationError

    return isinstance(exc, (AuthenticationError, backends.ReaderConfigError))


def attempt(claim: scifact.Claim, make_row: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """One claim's row, or its error row (with any fields the error carries,
    e.g. a cut reply's ``finish_reason``); a fatal failure is raised."""
    try:
        return make_row()
    except Exception as exc:  # noqa: BLE001 — one claim's failure is recorded, not fatal
        if is_fatal(exc):
            raise
        fields = getattr(exc, "fields", None) or {}
        return {"claim": claim.id, "label": claim.label, "error": repr(exc)[:300], **fields}


def _run_arm(stack, claims, name: str, *, do_infer: bool, opts: Any) -> list[dict[str, Any]]:
    arm = arms.ARMS[name]
    rows = []
    for n, claim in enumerate(claims, start=1):
        rows.append(
            attempt(claim, lambda c=claim: _claim_row(stack, arm, c, do_infer=do_infer, opts=opts))
        )
        if n % 25 == 0:
            print(f"  {name}: {n}/{len(claims)}", file=sys.stderr, flush=True)
    return rows


def _claim_row(stack, arm, claim: scifact.Claim, *, do_infer: bool, opts: Any) -> dict[str, Any]:
    got = infer.with_retries(lambda: arm(stack, claim))
    prompt = getattr(opts, "prompt", None) or got.prompt
    context, docs = _context_for(stack, claim, got, prompt, getattr(opts, "order", "rank"))
    row = _row(claim, got, context, getattr(stack, "corpus", None), prompt)
    row["prompt"] = prompt
    if not do_infer:
        return row
    if getattr(opts, "scorer_gate", False) and got.status == "insufficient":
        row.update(_gated(claim, got))
        _withhold(row, claim)
    else:
        row.update(_verdict(stack, claim, got, (context, docs), prompt, opts.model))
    return row


# What the reader is shown. A gated row's reader is shown nothing: these read
# zero, and what the cut kept moves under ``retrieved``.
_READER_KEYS = (
    "context_chars",
    "context_tokens",
    "passages_shown",
    "context",
    "rationale_coverage",
)


def _withhold(row: dict[str, Any], claim: scifact.Claim) -> None:
    row["retrieved"] = {key: row[key] for key in _READER_KEYS if key in row}
    row.update(context_chars=0, context_tokens=0, passages_shown=0)
    if "context" in row:
        row["context"] = metrics.context_scores([], set(claim.evidence))
    if "rationale_coverage" in row:
        row["rationale_coverage"] = 0.0


def _context_for(
    stack, claim: scifact.Claim, got: backends.Retrieved, prompt: str, order: str
) -> tuple[str, list[str | None]]:
    """The exact context the verdict reader sees, and the doc behind each [n].

    A pack prompt renders the arm's passages through ``render_pack`` (so the
    same passages can be read under every prompt); the others read the arm's
    own context string, numbered in passage (else hit) order.
    """
    if prompt in infer.PACK_PROMPTS:
        status = got.status or "sufficient"
        text, citations = infer.pack_context(
            claim.text, got.passages, prompt=prompt, status=status, order=order
        )
        by_id = {p.source_id: p for p in got.passages}
        labels = [f"[{n}]" for n in range(1, len(citations) + 1)]
        return text, [_passage_doc(stack, by_id.get(citations[label])) for label in labels]
    if got.passages:
        return got.context, [_passage_doc(stack, p) for p in got.passages]
    return got.context, [h.doc_id for h in got.hits]


def _passage_doc(stack, passage) -> str | None:
    if passage is None:
        return None
    return stack.doc_of(passage.artifact_id, passage.source_name, passage.source_id)


def _row(
    claim: scifact.Claim,
    got: backends.Retrieved,
    context: str,
    corpus: Any,
    prompt: str | None = None,
) -> dict[str, Any]:
    # A hit of another artifact keeps its rank as a sentinel: it pushes the
    # evidence down the ranking and dilutes the context like any other miss.
    ranked = [backends.hit_doc(h, n) for n, h in enumerate(got.hits)]
    row: dict[str, Any] = {
        "claim": claim.id,
        "label": claim.label,
        "ranked": metrics.dedup(ranked)[:20],
        "shown": got.shown,
        **_shown_ids(got),
        "retrieve_seconds": round(got.seconds, 3),
        "context_chars": len(context),
        "context_tokens": estimate_tokens(context),
        "passages_shown": len(got.passages) or len(got.shown),
        "prompt": got.prompt,
        "meta": got.meta,
    }
    if got.status:
        row["sufficiency"] = got.status
        row["features"] = got.meta.get("features") or {}
    if claim.evidence:
        row["retrieval"] = metrics.retrieval_scores(ranked, set(claim.evidence))
        row["context"] = metrics.context_scores(got.shown, set(claim.evidence))
        row.update(_coverage(claim, got, corpus, prompt or got.prompt))
    return row


def _shown_ids(got: backends.Retrieved) -> dict[str, Any]:
    """The shown passages' node ids in order (``parity`` compares them), and
    how many shown passages belong to no SciFact doc."""
    refs = [p.source_id for p in got.passages] or [h.ref for h in got.hits]
    return {"shown_refs": refs, "intruders": sum(backends.is_other(d) for d in got.shown)}


def _coverage(
    claim: scifact.Claim, got: backends.Retrieved, corpus: Any, prompt: str
) -> dict[str, float]:
    """Rationale-sentence coverage of what the reader was shown, when computable."""
    if corpus is None:
        return {}
    texts = _rendered_texts(got, prompt)
    value = metrics.rationale_coverage(texts, scifact.rationale_sentences(corpus, claim))
    return {} if value is None else {"rationale_coverage": value}


def _rendered_texts(got: backends.Retrieved, prompt: str) -> list[str]:
    """The passage texts a prompt actually renders: a pack prompt shows each
    passage whole; the others show the arm's own context, which cuts them
    where the arm cuts them (``rendered``: the oracle at 4,000 characters,
    K3 at 1,200)."""
    if got.rendered and prompt not in infer.PACK_PROMPTS:
        return list(got.rendered)
    return [p.text for p in got.passages] or [h.text for h in got.hits if h.doc_id in got.shown]


def _gated(claim: scifact.Claim, got: backends.Retrieved) -> dict[str, Any]:
    """The scorer gate: the server said insufficient, so no model call."""
    return {
        "verdict": NEI,
        "parsed": True,
        "cited": [],
        "gold_in_context": bool(set(claim.evidence) & set(got.shown)),
        "infer_seconds": 0.0,
        "usage": {},
        "reply": "",
        "gated": True,
    }


def _run_agent(stack, claims, model: str) -> list[dict[str, Any]]:
    """The companion agent end to end, one fresh session per claim."""
    rows = []
    for n, claim in enumerate(claims, start=1):
        rows.append(attempt(claim, lambda c=claim, k=n: _agent_row(stack, c, model, k)))
        print(f"  agent: {n}/{len(claims)}", file=sys.stderr, flush=True)
    return rows


def _agent_row(stack, claim: scifact.Claim, model: str, n: int) -> dict[str, Any]:
    turn = backends.agent_turn(stack, claim.text, model, f"{claim.id}-{n}")
    verdict, evidence, parsed = infer.parse(turn["reply"])
    seen = turn["docs_seen"]
    return {
        "claim": claim.id,
        "label": claim.label,
        "ranked": metrics.dedup(seen)[:20],
        "shown": metrics.dedup(seen),
        "retrieve_seconds": 0.0,
        "context_chars": 0,
        "verdict": verdict,
        "parsed": parsed,
        # the agent cites names; a name it never retrieved is a phantom
        "cited": cited_set(stack, evidence, [], set(seen)),
        "gold_in_context": bool(set(claim.evidence) & set(seen)),
        "infer_seconds": turn["seconds"],
        "usage": turn["usage"],
        "retrieval_calls": turn["retrieval_calls"],
        "routes": turn.get("routes", []),
        "reply": turn["reply"][:600],
    }


def verdict_reader(stack, model: str) -> tuple[Any, str]:
    """The client and model the verdict call goes through: the remote reader
    when ``--reader-config`` built one (its own model), else the stack's
    client and ``--model``. Retrieval never goes through the reader."""
    reader = getattr(stack, "reader", None)
    return (reader, reader.model) if reader is not None else (stack.client, model)


def _verdict(stack, claim, got, shown: tuple[str, list], prompt: str, model: str) -> dict:
    context, docs = shown
    client, model = verdict_reader(stack, model)
    answer = infer.ask(client, model, claim.text, context, prompt)
    shown_docs = {d for d in docs if d}
    return {
        "verdict": answer["verdict"],
        "parsed": answer["parsed"],
        "cited": cited_set(stack, answer["evidence"], docs, shown_docs),
        "gold_in_context": bool(set(claim.evidence) & set(got.shown)),
        "infer_seconds": answer["seconds"],
        "usage": answer["usage"],
        "finish_reason": answer["finish_reason"],
        "completion_tokens": answer["completion_tokens"],
        "max_tokens": answer["max_tokens"],
        "reply": answer["reply"],
        "gated": False,
    }


#: A cited ref that names nothing the reader was shown (no such passage
#: number, or a doc outside the context): it counts against citation precision.
PHANTOM = "phantom:"


def cited_set(stack, refs: list[Any], docs: list[str | None], shown: set[str]) -> list[str]:
    """Every doc the cited refs name, sorted (a ref may name several)."""
    return sorted({doc for ref in refs for doc in _cited_docs(stack, ref, docs, shown)})


def _cited_docs(stack, ref: Any, docs: list[str | None], shown: set[str]) -> list[str]:
    """What a cited ref names: a SciFact doc (by name, artifact id, or a bare
    id of a shown doc: ``14717500``), passages by number (``[2]``, ``1, 3``;
    another artifact's passage is ``other:[n]``), else ``phantom:<ref>``. A
    doc outside the context and a number past the last passage are phantoms."""
    doc = stack.doc_of(ref) or _bare_doc(ref, shown)
    if doc:
        return [doc if doc in shown else f"{PHANTOM}{doc}"]
    numbers = passage_numbers(ref)
    if numbers is None:
        return [f"{PHANTOM}{ref}"]
    return [_numbered_doc(number, docs) for number in numbers]


def _bare_doc(ref: Any, shown: set[str]) -> str | None:
    """A shown doc a ref names by its bare SciFact id (``14717500``, also
    bracketed or as ``14717500.md``): the context labels each passage with
    ``scifact-<id>.md``, and a small model often cites the id alone."""
    text = str(ref).strip().strip("[]").strip()
    name = text if text.startswith(scifact.DOC_PREFIX) else f"{scifact.DOC_PREFIX}{text}"
    doc = scifact.doc_id_of(name)
    return doc if doc in shown else None


# One passage reference: "2", "[2]", "passage 2", "source 2". A list of them
# ("1, 2", "[1][3]", "1 and 2") is split first. Anything else ("GNB2", "0.5",
# "HR 1.2") names no passage.
_PASSAGE_RE = re.compile(r"\[?\s*(?:passage|source)?\s*#?\s*(\d{1,2})\s*\]?", re.IGNORECASE)
_REF_LIST_RE = re.compile(r"\]\s*\[|[,;]|\band\b", re.IGNORECASE)


def passage_numbers(ref: Any) -> list[int] | None:
    """The passage numbers a ref cites, or None when it is not a passage
    reference (never the digits of a name or a number in prose)."""
    parts = [part.strip() for part in _REF_LIST_RE.split(str(ref)) if part.strip()]
    found = [_PASSAGE_RE.fullmatch(part) for part in parts]
    if not found or not all(found):
        return None
    return [int(match.group(1)) for match in found if match]


def _numbered_doc(number: int, docs: list[str | None]) -> str:
    if not 1 <= number <= len(docs):
        return f"{PHANTOM}{number}"
    return docs[number - 1] or f"{backends.OTHER}[{number}]"


# ── summaries ────────────────────────────────────────────────────────────────


def _mean_ci(values: list[float]) -> dict[str, Any]:
    return {"mean": metrics.mean(values), "ci95": metrics.bootstrap(values), "n": len(values)}


def summarize(rows: list[dict[str, Any]], evidence: dict[int, set[str]]) -> dict[str, Any]:
    ok = [r for r in rows if "error" not in r]
    out: dict[str, Any] = {"claims": len(rows), "errors": len(rows) - len(ok)}
    out.update(_block_means(ok, "retrieval"))
    out.update(_block_means(ok, "context"))
    out.update(_shown_means(ok))
    if ok and "verdict" in ok[0]:
        out.update(_verdict_summary(ok, evidence))
    if any("sufficiency" in r for r in ok):
        out["sufficiency"] = sufficiency_block(rows)  # errored claims count against the gates
    lanes = lane_status_counts(ok)
    if lanes:
        out["lane_status"] = lanes
    out["retrieve_seconds_mean"] = metrics.mean([r["retrieve_seconds"] for r in ok])
    out["latency"] = latency_block(ok)
    return out


def lane_status_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """How often each lane reported each status (a retrieve arm's fallbacks
    and outages, visible per run); the profile report counts by its name."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        for lane, report in ((row.get("meta") or {}).get("lanes") or {}).items():
            counts[lane][_lane_key(lane, report or {})] += 1
    return {lane: dict(c) for lane, c in sorted(counts.items())}


def _lane_key(lane: str, report: dict[str, Any]) -> str:
    """A lane's status; the profile report (``{status: ok, name, ...}``) by
    the profile's name, which is what a summary must show."""
    first, then = ("name", "status") if lane == "profile" else ("status", "name")
    return str(report.get(first) or report.get(then))


def latency_block(rows: list[dict[str, Any]]) -> dict[str, dict]:
    """p50/p95 of the per-claim costs the slice gates read: retrieve and
    reader seconds, prompt tokens (a gated row costs 0), and each stage of
    the server's ``timing_ms``."""
    series = {
        "retrieve_seconds": [r["retrieve_seconds"] for r in rows if "retrieve_seconds" in r],
        "infer_seconds": [r["infer_seconds"] for r in rows if "infer_seconds" in r],
        "prompt_tokens": [r["usage"].get("prompt_tokens", 0) for r in rows if "usage" in r],
        **_stage_series(rows),
    }
    return {key: metrics.percentiles(values) for key, values in series.items() if values}


def _stage_series(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    stages: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for stage, ms in ((row.get("meta") or {}).get("timing_ms") or {}).items():
            stages[f"timing_ms.{stage}"].append(float(ms))
    return dict(stages)


def _block_means(rows: list[dict[str, Any]], block: str) -> dict[str, Any]:
    scored = [r[block] for r in rows if block in r]
    return {key: _mean_ci([s[key] for s in scored]) for key in (scored[0] if scored else ())}


def _shown_means(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-claim cost and content of the context: tokens (ADR-085 D-2
    estimate), passages shown, and rationale-sentence coverage."""
    out = {}
    for key in ("context_tokens", "passages_shown", "rationale_coverage"):
        values = [float(r[key]) for r in rows if key in r]
        if values:
            out[key] = _mean_ci(values)
    return out


def _rate(rows: list[dict[str, Any]], keep: Callable, hit: Callable) -> float | None:
    """The share of the rows ``keep`` selects for which ``hit`` holds; None
    when it selects none (undefined, never the best value 0.0)."""
    chosen = [float(bool(hit(r))) for r in rows if keep(r)]
    return metrics.mean(chosen) if chosen else None


def _rated(rows: list[dict[str, Any]], key: str, keep: Callable, hit: Callable) -> dict[str, Any]:
    """``{key: rate | None, key_n: denominator}``."""
    return {key: _rate(rows, keep, hit), f"{key}_n": sum(1 for r in rows if keep(r))}


def _verdict_summary(rows: list[dict[str, Any]], evidence: dict[int, set[str]]) -> dict[str, Any]:
    gold = [r["label"] for r in rows]
    pred = [r["verdict"] for r in rows]
    return {
        "accuracy": _mean_ci([float(r["verdict"] == r["label"]) for r in rows]),
        "macro_f1": metrics.macro_f1(gold, pred, scifact.LABELS),
        "recall_by_label": {
            label: _rate(
                rows, lambda r, x=label: r["label"] == x, lambda r, x=label: r["verdict"] == x
            )
            for label in scifact.LABELS
        },
        **_failure_rates(rows),
        **_citations(rows, evidence),
        "parse_rate": metrics.mean([float(r["parsed"]) for r in rows]),
        "gated_rate": metrics.mean([float(r.get("gated", False)) for r in rows]),
        "infer_seconds_mean": metrics.mean([r["infer_seconds"] for r in rows]),
        "prompt_tokens_mean": metrics.mean([r["usage"].get("prompt_tokens", 0) for r in rows]),
    }


def _failure_rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The two small-model failure modes, each with its denominator."""
    return {
        # A verdict asserted where the corpus holds no evidence.
        **_rated(
            rows,
            "unsupported_verdict",
            lambda r: not _has_evidence(r),
            lambda r: r["verdict"] != NEI,
        ),
        # An abstention although the evidence was in what retrieval kept (for a
        # gated row: kept, then withheld by the gate).
        **_rated(
            rows,
            "wrong_abstain_when_shown",
            lambda r: _has_evidence(r) and r["gold_in_context"],
            lambda r: r["verdict"] == NEI,
        ),
        **_rated(rows, "gold_shown_rate", _has_evidence, lambda r: r["gold_in_context"]),
    }


def _citations(rows: list[dict[str, Any]], evidence: dict[int, set[str]]) -> dict[str, Any]:
    """Over correct verdicts on claims with evidence: did it cite, and cite
    right? A phantom ref (nothing the reader was shown) counts as a wrong
    citation; ``phantom_rate`` is the share of all cited refs that are."""
    right = [r for r in rows if _has_evidence(r) and r["verdict"] == r["label"]]
    cited = [r for r in right if r["cited"]]
    precision = [len(set(r["cited"]) & evidence[r["claim"]]) / len(r["cited"]) for r in cited]
    return {
        "citation_precision": metrics.mean(precision) if precision else None,
        "citation_precision_n": len(cited),
        "cited_rate_when_right": len(cited) / len(right) if right else None,
        "cited_rate_when_right_n": len(right),
        **_phantoms(rows),
    }


def _phantoms(rows: list[dict[str, Any]]) -> dict[str, Any]:
    refs = [ref for r in rows for ref in r.get("cited") or []]
    phantoms = sum(str(ref).startswith(PHANTOM) for ref in refs)
    return {"phantom_rate": phantoms / len(refs) if refs else None, "phantom_rate_n": len(refs)}


# ── commands ─────────────────────────────────────────────────────────────────


#: The arms built from the companion's own tools (``arms.register("tool:...")``).
TOOL_PREFIX = "tool:"


def _setup(args: argparse.Namespace) -> tuple[scifact.Corpus, backends.Stack, dict[str, Any]]:
    corpus = scifact.load(Path(args.data), distractors=None, claims=args.claims)
    stack = backends.Stack(project=args.project, graph=args.graph, corpus=corpus)
    stack.reader = remote_reader(args, stack)
    stack.session_model = tool_reader(args, stack.reader)
    prov = backends.provenance(
        project=args.project,
        graph=args.graph,
        model=getattr(args, "model", None),
        reader=stack.reader,
        reads_verdicts=getattr(args, "cmd", "infer") != "retrieve",
        tool_reader=stack.session_model,
        tool_sizing=sized_tools(args, stack),
    )
    return corpus, stack, prov


def sized_tools(args: argparse.Namespace, stack: Any) -> dict[str, Any] | None:
    """What the run's ``tool:*`` arms are sized by (``backends.tool_sizing``:
    scale, its basis, profile, K3 limit), resolved once before any arm runs,
    and only when one of them runs: the scale probe is a request to the stack
    no other arm makes (c23-mf-2)."""
    names = str(getattr(args, "arms", "") or "").split(",")
    if not any(name.startswith(TOOL_PREFIX) for name in names):
        return None
    return backends.tool_sizing(stack.client, stack.session_model)


def tool_reader(args: argparse.Namespace, reader: Any) -> str | None:
    """The model the companion tools are sized for, as a session sizes them
    (ADR-093 D-14): the remote reader's when one reads the verdicts, else
    ``--model``; None for a command with neither (``answer``)."""
    if reader is not None:
        return reader.model
    return getattr(args, "model", None)


def remote_reader(args: argparse.Namespace, stack: Any) -> backends.ReaderClient | None:
    """The remote verdict reader (ADR-093 D-14): built only when
    ``--reader-config`` names a directory, never from the environment."""
    config_dir = getattr(args, "reader_config", None)
    if not config_dir:
        return None
    gateway = getattr(getattr(stack.client, "config", None), "gateway_url", None)
    reader = backends.ReaderClient(config_dir, args.reader_model, stack_gateway=gateway)
    print(
        f"verdict reader: {reader.model} via {reader.gateway_url} ({reader.config_dir})",
        file=sys.stderr,
        flush=True,
    )
    return reader


def _gate_applies(args: argparse.Namespace, name: str) -> bool:
    """The scorer gate can only fire on an arm that reports sufficiency."""
    return bool(getattr(args, "scorer_gate", False)) and arms.reports_sufficiency(name)


def _order(args: argparse.Namespace, name: str) -> str | None:
    """The pack order this arm reads with; None when its prompt is not a pack
    (``--order`` changes nothing there, so it is neither named nor recorded)."""
    prompt = getattr(args, "prompt", None) or arms.default_prompt(name)
    return getattr(args, "order", "rank") if prompt in infer.PACK_PROMPTS else None


def _variant(args: argparse.Namespace, name: str = "") -> str:
    """The file-name suffix of the non-default reader options (and of an
    explicit /rag/query threshold, the B0 bracket of ``answer``)."""
    parts = []
    if getattr(args, "prompt", None):
        parts.append(f"p-{args.prompt}")
    if _order(args, name) not in (None, "rank"):
        parts.append(f"o-{args.order}")
    if _gate_applies(args, name):
        parts.append("gate")
    if getattr(args, "threshold", None) is not None:
        parts.append(f"t{args.threshold:g}")
    if remote_model(args):
        parts.append(f"r-{_slug(args.reader_model)}")
    if _names_model(args, name):
        parts.append(f"m-{_slug(args.model)}")
    return "".join(f"-{part}" for part in parts)


#: ``--model``'s default: the stand-alone stack's reference reader.
DEFAULT_MODEL = "llama3.2:3b"


def _names_model(args: argparse.Namespace, name: str) -> bool:
    """A retrieve file names a non-default ``--model`` when it decides what
    the arm retrieves: a ``tool:*`` arm is sized by it (ADR-093 D-14, C23)
    and the agent searches with it, yet a retrieve comparison does not hold
    the model fixed (c23-retrieve-model-factor). An infer file needs no name:
    its comparison does, and the other arms retrieve the same for any model."""
    if getattr(args, "cmd", None) != "retrieve":
        return False
    if not (name.startswith(TOOL_PREFIX) or name == "agent"):
        return False
    return getattr(args, "model", DEFAULT_MODEL) != DEFAULT_MODEL


def remote_model(args: argparse.Namespace) -> str | None:
    """The remote reader's model, when ``--reader-config`` names a reader."""
    return getattr(args, "reader_model", None) if getattr(args, "reader_config", None) else None


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", name).strip("-")


def verdict_budget_of(args: argparse.Namespace, kind: str, name: str) -> int | None:
    """The ``max_tokens`` every verdict call of this arm asked for (None: no
    verdict call, or the reader's provider default)."""
    if kind != "infer" or name == "agent":
        return None
    return infer.verdict_budget(remote_model(args) or getattr(args, "model", None))


def _write(kind: str, name: str, args, rows, evidence, prov, stamp: str) -> dict[str, Any]:
    RESULTS.mkdir(exist_ok=True)
    record = {
        "schema_version": SCHEMA_VERSION,
        "arm": name,
        "kind": kind,
        "claims_set": args.claims,
        "label": args.label,
        "at": stamp,
        "model": getattr(args, "model", None),
        # the remote verdict reader's model (None: ``model`` read the verdicts)
        "reader_model": remote_model(args),
        "prompt": getattr(args, "prompt", None),
        "order": _order(args, name) if hasattr(args, "order") else None,
        "threshold": getattr(args, "threshold", None),
        "scorer_gate": _gate_applies(args, name),
        "verdict_max_tokens": verdict_budget_of(args, kind, name),
        "preset": arms.PRESETS.get(name.removeprefix(arms.RETRIEVE_PREFIX))
        if arms.reports_sufficiency(name)
        else None,
        "provenance": prov,
        "summary": summarize(rows, evidence) if kind != "answer" else answer_summary(rows),
        "rows": rows,
    }
    suffix = f"-{args.label}" if args.label else ""
    slug = name.replace(":", "-") + _variant(args, name)
    path = RESULTS / f"{stamp}-{kind}-{args.claims}-{slug}{suffix}.json"
    path.write_text(json.dumps(record, indent=1))
    print(json.dumps({"arm": name, "kind": kind, **_headline(record["summary"])}), flush=True)
    return record


def _arm_claims(corpus: scifact.Corpus, name: str, args, *, do_infer: bool):
    """Retrieval alone scores evidence claims; an arm with a sufficiency
    verdict also runs the rest (its verdict on them is the point)."""
    if name == "agent":
        return _subset(corpus.claims, args.limit or 20, stratified=True)
    keep_all = do_infer or arms.reports_sufficiency(name)
    claims = corpus.claims if keep_all else [c for c in corpus.claims if c.evidence]
    return _subset(claims, args.limit, stratified=False)


def cmd_run(args: argparse.Namespace, *, do_infer: bool) -> int:
    corpus, stack, prov = _setup(args)
    evidence = {c.id: set(c.evidence) for c in corpus.claims}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for name in args.arms.split(","):
        print(f"{name} ...", file=sys.stderr, flush=True)
        if getattr(stack, "reader", None) is not None:
            stack.reader.check()  # an expired reader login stops here, not as error rows
        claims = _arm_claims(corpus, name, args, do_infer=do_infer)
        if name == "agent":
            rows = _run_agent(stack, claims, args.model)
        else:
            rows = _run_arm(stack, claims, name, do_infer=do_infer, opts=args)
        _write("infer" if do_infer else "retrieve", name, args, rows, evidence, prov, stamp)
    return 0


# E-B: the real answer path. Abstention is the reply's own words, as a user
# would read them; citations are the [n] the answer names, single or grouped
# ("[1, 2]": the server's own post-check rewrites groups that way).
_ABSTAIN_RE = re.compile(
    r"(knowledge base (does not|doesn't) (answer|cover)|not in (the )?knowledge base|"
    r"no knowledge in this project|do(es)? not contain (the|an) answer|cannot answer)",
    re.IGNORECASE,
)
_CITE_RE = re.compile(r"\[(\s*\d{1,3}(?:\s*,\s*\d{1,3})*\s*)\]")


def cite_numbers(text: str) -> set[int]:
    """Every passage number an answer cites: ``[2]`` and each of ``[1, 3]``."""
    return {int(n) for group in _CITE_RE.findall(text or "") for n in group.split(",")}


def cmd_answer(args: argparse.Namespace) -> int:
    corpus, stack, prov = _setup(args)
    claims = _subset(corpus.claims, args.limit, stratified=False)
    rows = []
    for n, claim in enumerate(claims, start=1):
        rows.append(attempt(claim, lambda c=claim: _answer_row(stack, c, args.threshold)))
        if n % 25 == 0:
            print(f"  answer: {n}/{len(claims)}", file=sys.stderr, flush=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _write("answer", "rag:query", args, rows, {}, prov, stamp)
    return 0


def _answer_row(stack, claim: scifact.Claim, threshold: float | None) -> dict[str, Any]:
    question = f"Is this claim supported by the literature? {claim.text}"
    started = time.monotonic()
    got = infer.with_retries(lambda: stack.rag_sources(question, top_k=5, threshold=threshold))
    cited = _answer_cited(got)
    return {
        "claim": claim.id,
        "label": claim.label,
        **abstention(got),
        "cited": cited,
        "cited_evidence": bool(set(cited) & set(claim.evidence)),
        "shown": [backends.hit_doc(h, n) for n, h in enumerate(got.hits)],
        "gold_in_context": bool(set(claim.evidence) & {h.doc_id for h in got.hits}),
        "seconds": round(time.monotonic() - started, 2),
        "answer": got.answer[:ANSWER_CHARS],
    }


def _answer_cited(got: backends.Retrieved) -> list[str]:
    """The SciFact docs behind every [n] the answer cites."""
    hits = got.hits
    numbers = [n for n in cite_numbers(got.answer) if 1 <= n <= len(hits)]
    return sorted({hits[n - 1].doc_id for n in numbers if hits[n - 1].doc_id})


def abstention(got: backends.Retrieved) -> dict[str, Any]:
    """Whether the answer abstained: the server's own post-check
    (``metadata.abstained``: a cited answer that names a gap is a partial
    answer, not an abstention) when the server reports it, else the phrase
    anywhere in the answer (B0's rule, kept as ``abstained_phrase``)."""
    phrase = bool(_ABSTAIN_RE.search(got.answer)) or not got.answer.strip()
    server = got.meta.get("abstained")
    flagged = isinstance(server, bool)
    return {
        "abstained": server if flagged else phrase,
        "abstained_basis": "server" if flagged else "phrase",
        "abstained_server": server if flagged else None,
        "abstained_phrase": phrase,
        "partial_answer": got.meta.get("partial_answer"),
        "profile": got.meta.get("profile"),
        "answer_model": got.meta.get("answer_model"),
    }


def _abstained(row: dict[str, Any]) -> bool:
    return bool(row["abstained"])


def _abstained_phrase(row: dict[str, Any]) -> bool:
    return bool(row.get("abstained_phrase", row["abstained"]))


def _disagrees(row: dict[str, Any]) -> bool:
    return row["abstained_server"] != row["abstained_phrase"]


def answer_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """E-B: abstention and citation rates. ``abstained`` is the server's flag
    where it reports one; ``abstain_rate_evidence_phrase`` keeps B0's rule,
    and ``abstain_disagreement`` counts the rows where the two differ."""
    ok = [r for r in rows if "error" not in r]
    abstained = _abstained
    return {
        "claims": len(rows),
        "errors": len(rows) - len(ok),
        **_rated(ok, "abstain_rate_evidence", _has_evidence, abstained),
        **_rated(
            ok,
            "abstain_rate_when_shown",
            lambda r: _has_evidence(r) and r["gold_in_context"],
            abstained,
        ),
        **_rated(ok, "abstain_rate_nei", lambda r: not _has_evidence(r), abstained),
        **_rated(ok, "cites_evidence_rate", _has_evidence, lambda r: r["cited_evidence"]),
        **_rated(ok, "abstain_rate_evidence_phrase", _has_evidence, _abstained_phrase),
        **_rated(
            ok,
            "abstain_disagreement",
            lambda r: isinstance(r.get("abstained_server"), bool),
            _disagrees,
        ),
        "abstained_basis": dict(Counter(str(r.get("abstained_basis", "phrase")) for r in ok)),
        "profile": dict(Counter(str(r.get("profile")) for r in ok)),
        "seconds_mean": metrics.mean([r["seconds"] for r in ok]),
        "latency": {"seconds": metrics.percentiles([r["seconds"] for r in ok])},
    }


_HEADLINE_KEYS = (
    "recall@1",
    "recall@5",
    "ndcg@10",
    "ctx_precision",
    "ctx_recall",
    "passages",
    "context_tokens",
    "rationale_coverage",
    "accuracy",
    "macro_f1",
    "unsupported_verdict",
    "wrong_abstain_when_shown",
    "citation_precision",
    "phantom_rate",
    "gated_rate",
    "abstain_rate_evidence",
    "abstain_rate_nei",
    "cites_evidence_rate",
    "errors",
)


def _headline(summary: dict[str, Any]) -> dict[str, Any]:
    """The printed one-liner. A conditional rate prints with its denominator
    (``<key>_n``), and an undefined one as None."""
    out: dict[str, Any] = {}
    for key in (k for k in _HEADLINE_KEYS if k in summary):
        value = summary[key]
        if isinstance(value, dict):
            value = value["mean"]
        out[key] = None if value is None else round(value, 3)
        if f"{key}_n" in summary:
            out[f"{key}_n"] = summary[f"{key}_n"]
    out.update(_sufficiency_headline(summary))
    return out


def _sufficiency_headline(summary: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, block in (summary.get("sufficiency") or {}).items():
        if isinstance(block, dict) and block.get("rate") is not None:
            out[key] = round(block["rate"], 3)
    return out


# ── rescore: an older file under the current definitions ─────────────────────

#: What a rescore cannot rewrite: B0's harness never recorded another
#: artifact's passages, so its ranked and shown docs stay as it wrote them.
_V1_FIELDS = {"infer": ["ranked", "shown"], "retrieve": ["ranked", "shown"], "answer": []}
#: B0 arms that searched the whole project. B0's harness dropped another
#: artifact's passage from ``shown``, so a row of these arms that shows fewer
#: docs than its file's fullest row may have lost one: its ``[n]`` can no
#: longer be mapped to the passage the reader saw, and its v1 ``cited`` stays.
_V1_PROJECT_WIDE = frozenset({"legacy", "tool:rag_query", "rag:query"})
_NUMBERING_LOST = "rescored; v1 cited kept: its [n] numbering is lost (a dropped passage)"


def _numbering_known(record: dict[str, Any], row: dict[str, Any], fullest: int) -> bool:
    """Whether a v1 row's ``shown`` still numbers the passages its reader saw."""
    lost = record.get("arm") in _V1_PROJECT_WIDE and len(row.get("shown") or []) < fullest
    return not lost


def _cited_v2(stack: Any, refs: list[str], row: dict[str, Any], *, numbered: bool):
    """A v1 row's cited refs under the v2 rules, and the note for the row."""
    shown = list(row.get("shown") or [])
    if not numbered and any(passage_numbers(ref) for ref in refs):
        return list(row.get("cited") or []), _NUMBERING_LOST
    docs = [] if "retrieval_calls" in row else shown
    return cited_set(stack, refs, docs, set(shown)), "rescored"


def _verdict_v2(
    row: dict[str, Any], stack: Any, *, numbered: bool = True
) -> tuple[dict[str, Any], str]:
    """One E-A or agent row under the v2 rules, and what was done to it."""
    if "error" in row or "reply" not in row:
        return row, "kept (no reply)"
    agent = "retrieval_calls" in row
    tokens = (row.get("usage") or {}).get("completion_tokens", 0)
    if not agent and tokens >= infer.VERDICT_MAX_TOKENS:
        why = f"ReplyNotScored: {tokens} completion tokens at max_tokens 200 (cut, inferred)"
        return {"claim": row["claim"], "label": row["label"], "error": why}, "error (cut reply)"
    if len(row["reply"]) >= REPLY_CHARS:
        return {**row, "rescore": "kept: the stored reply is cut"}, "kept (stored reply cut)"
    verdict, refs, parsed = infer.parse(row["reply"])
    cited, note = _cited_v2(stack, refs, row, numbered=numbered or agent)
    out = {**row, "verdict": verdict, "parsed": parsed, "cited": cited}
    return ({**out, "rescore": note} if note != "rescored" else out), note


def _answer_v2(
    row: dict[str, Any], evidence: set[str], *, numbered: bool = True
) -> tuple[dict[str, Any], str]:
    """One E-B row under the v2 rules: grouped citations counted, the v1
    abstention kept as the phrase rule (a v1 file has no server flag)."""
    if "error" in row:
        return row, "kept (error)"
    answer = row.get("answer") or ""
    out = {
        **row,
        "abstained_phrase": row["abstained"],
        "abstained_server": None,
        "abstained_basis": "phrase",
    }
    if len(answer) >= ANSWER_CHARS or not numbered:
        why = "the stored answer is cut" if numbered else "its [n] numbering is lost"
        return {**out, "rescore": f"v1 cited kept: {why}"}, f"v1 cited kept ({why})"
    shown = list(row.get("shown") or [])
    cited = sorted({shown[n - 1] for n in cite_numbers(answer) if 1 <= n <= len(shown)})
    return {**out, "cited": cited, "cited_evidence": bool(set(cited) & evidence)}, "rescored"


def _rescored_rows(
    record: dict[str, Any], stack: Any, evidence: dict[int, set[str]]
) -> tuple[list[dict[str, Any]], Counter]:
    rows, notes = [], Counter()
    old = record.get("rows") or []
    fullest = max((len(row.get("shown") or []) for row in old), default=0)
    for row in old:
        numbered = _numbering_known(record, row, fullest)
        if record.get("kind") == "answer":
            new, note = _answer_v2(row, evidence.get(row["claim"], set()), numbered=numbered)
        else:
            new, note = _verdict_v2(row, stack, numbered=numbered)
        rows.append(new)
        notes[note] += 1
    return rows, notes


def rescore_record(
    record: dict[str, Any], stack: Any, evidence: dict[int, set[str]], *, source: str
) -> dict[str, Any]:
    """An older record with its verdict, citation and abstention fields
    rewritten under the current definitions, and its summary recomputed.
    ``rescore.v1_fields`` names what could not be rewritten (comparisons
    report those as still v1); a row whose numbering was lost keeps its v1
    citations and says so (``rescore`` in the row and in ``rescore.rows``)."""
    kind = record.get("kind") or "infer"
    rows, notes = _rescored_rows(record, stack, evidence)
    summary = answer_summary(rows) if kind == "answer" else summarize(rows, evidence)
    return {
        **record,
        "schema_version": SCHEMA_VERSION,
        "rescore": {
            "from": {"path": source, "schema_version": comparisons.schema_version(record)},
            "rows": dict(notes),
            "v1_fields": _V1_FIELDS.get(kind, []),
        },
        "summary": summary,
        "rows": rows,
    }


def cmd_rescore(args: argparse.Namespace) -> int:
    """Rewrite each older file next to itself as ``<name>-rescored.json``.
    Artifact ids in citations are mapped through the stack's store (one
    read-only SELECT) unless ``--no-db``."""
    art_to_doc = {} if args.no_db else backends.artifact_doc_map()
    stack = SimpleNamespace(doc_of=lambda *refs: backends.doc_of_refs(art_to_doc, *refs))
    for path in args.files:
        record = comparisons.load(path)
        if comparisons.schema_version(record) >= SCHEMA_VERSION:
            print(f"{path}: already schema {SCHEMA_VERSION}", file=sys.stderr)
            continue
        claims = scifact.claims_for(Path(args.data), record.get("claims_set") or "dev")
        evidence = {c.id: set(c.evidence) for c in claims}
        out = rescore_record(record, stack, evidence, source=str(path))
        target = Path(path).with_name(f"{Path(path).stem}-rescored.json")
        target.write_text(json.dumps(out, indent=1))
        print(json.dumps({"file": str(target), "rows": out["rescore"]["rows"]}), flush=True)
    return 0


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--data", required=True, help="SciFact data/ directory")
    p.add_argument("--claims", default="dev", choices=scifact.CLAIM_SETS)
    p.add_argument("--project", default="default")
    p.add_argument("--graph", default=arms.GRAPH)
    p.add_argument("--limit", type=int, default=None, help="first N (agent: N per label, 20)")
    p.add_argument("--label", default="", help="suffix for the result file name")


def _reader_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--prompt",
        default=None,
        choices=infer.PROMPT_NAMES,
        help="verdict prompt for every arm (default: each arm's own — legacy for "
        "legacy, pack-soft for retrieve:*, json otherwise)",
    )
    p.add_argument(
        "--order", default="rank", choices=("rank", "edge"), help="pack order (pack prompts)"
    )
    p.add_argument(
        "--scorer-gate",
        action="store_true",
        help="NOT_ENOUGH_INFO with no model call when the arm reports insufficient",
    )


def _remote_reader_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--reader-config",
        default=None,
        metavar="XDG_DIR",
        help="read the verdicts through a second r4 client built from this "
        "XDG_CONFIG_HOME directory (e.g. one logged into the rikyu gateway); "
        "retrieval stays on the stand-alone stack. No remote reader without it",
    )
    p.add_argument(
        "--reader-model",
        default=None,
        help="the remote reader's model (kimi-k3, glm-5.3); goes with --reader-config",
    )


def check_reader_flags(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """The remote reader needs both flags, and reads verdicts only: the agent
    arm retrieves and reads through one session on the stack."""
    config_dir, model = getattr(args, "reader_config", None), getattr(args, "reader_model", None)
    if bool(config_dir) != bool(model):
        parser.error("--reader-config and --reader-model go together")
    if config_dir and "agent" in args.arms.split(","):
        parser.error("--reader-config reads verdicts; the agent arm reads through its session")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("retrieve", "infer"):
        p = sub.add_parser(name)
        _common(p)
        p.add_argument("--arms", required=True, help=f"comma list of {sorted(arms.ARMS)} or agent")
        p.add_argument("--model", default=DEFAULT_MODEL)
        _reader_options(p)
        if name == "infer":
            _remote_reader_options(p)
    p = sub.add_parser("answer")
    _common(p)
    p.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="legacy absolute cut sent to /rag/query (0.7 = the B0 bracket); "
        "omitted by default, as R4 sends it since ADR-093 wave A; -1 = omit",
    )
    for name in ("compare", "parity", "repro"):
        p = sub.add_parser(name)
        p.add_argument("a")
        p.add_argument("b")
    p = sub.add_parser("headroom")
    for name in ("arm", "closed", "oracle"):
        p.add_argument(name, help=f"the {name} result file")
    p = sub.add_parser("sufficiency")
    p.add_argument("files", nargs="+", help="result files of retrieve:* arms")
    _rescore_parser(sub)
    return parser


def _rescore_parser(sub: Any) -> None:
    p = sub.add_parser("rescore", help="rewrite older result files under the current definitions")
    p.add_argument("--data", required=True, help="SciFact data/ directory (claim evidence)")
    p.add_argument(
        "--no-db", action="store_true", help="map citations by name only (no store read)"
    )
    p.add_argument("files", nargs="+", help="result files of an older schema (B0)")


_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "compare": comparisons.cmd_compare,
    "parity": comparisons.cmd_parity,
    "repro": comparisons.cmd_repro,
    "headroom": comparisons.cmd_headroom,
    "sufficiency": comparisons.cmd_sufficiency,
    "rescore": cmd_rescore,
}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    check_reader_flags(parser, args)
    if args.cmd in _COMMANDS:
        return _COMMANDS[args.cmd](args)
    if args.cmd == "answer":
        if args.threshold is not None and args.threshold < 0:
            args.threshold = None
        return cmd_answer(args)
    return cmd_run(args, do_infer=args.cmd == "infer")


if __name__ == "__main__":
    raise SystemExit(main())
