"""ADR-093 S6: the repaired R4 consumers, measured on the stand-alone stack.

The S6 gate (ADR-093, "Slices, components and gates") reads: "Tests fail
before and pass after. search_agentic grounded >= 90% on 30 claims. Loop
digest finds the gold doc in >= 3 of 5 goals. Cleanup commit separate." The
first and last are review receipts; this probe measures the middle two.

    export XDG_CONFIG_HOME=<an r4 config logged in with `standalone.sh token`>
    P=".venv/bin/python benchmarks/retrieval/consumer_probe.py"
    $P plan --data <scifact>/data                 # the seeded selection, no stack call
    $P run  --data <scifact>/data                 # both legs, one result file
    $P run  --data <scifact>/data --legs loop     # one leg

Two legs:

- ``agentic``: the companion's ``search_agentic`` tool, built as a session
  builds it (``get_builtin_tools(client, session_model=MODEL)``) and called
  with its own defaults (``depth=shallow``, ``scope=all``; ``--scope`` and
  ``--depth`` change them), over 30 SciFact dev claims: a seeded draw of 10
  per label (``SEED``). The ten NOT_ENOUGH_INFO claims are reported apart.
  The query is the bare claim (``QUERY_TEMPLATE``). Every
  ``client.rag.retrieve`` call the tool makes is recorded, so each citation
  is checked against what the tool itself retrieved.
- ``loop``: the local research loop's evidence digest, the call a loop
  iteration makes before its model turn
  (``LocalAgentIterationExecutor._knowledge_context``: ``rag.retrieve`` at
  the loop's ``k_max`` with the loop model's reader fields, rendered by the
  shared context pack), for 5 research goals. Each goal is made from one dev
  evidence claim (``GOAL_TEMPLATE``), a seeded draw disjoint from the 30. No
  model call: the digest is what the iteration reads. UMRS's own loop
  (``SpineKnowledgeStore.search_artifacts``, where MD-6 removed the recency
  fallback) has no HTTP route short of running a whole loop; it calls the
  same retrieval core and is covered by the umrs tests.

Definitions (recorded in every result file under ``definitions``):

grounded (one search_agentic answer), all of:
  1. the tool answered (an outage of the retrieve verb inside the search is
     retried, then recorded as an error row);
  2. the answer cites at least one source: an inline ``[n]``, each number of
     a group such as ``[1, 2]`` counted;
  3. every cited number names a source the search handed its synthesizer
     (``sources[n-1]`` of the tool's JSON, else the tool's own ``citations``
     entry for ``n``): no phantom ``[n]``;
  4. at least one cited source is a passage that a ``rag.retrieve`` call of
     this search returned (a ``rag_document`` whose node id the recorder saw).
  Gate: ``grounded_itt`` >= 0.9 over all 30 claims, an errored claim counted
  as not grounded. ``cites_gold`` (a gold evidence doc is among the cited
  passages) and ``gold_retrieved`` are reported, never gated.

gold in the loop digest: a gold evidence doc of the goal's claim is one of
  the passages the iteration's pack renders (its ``[n]`` map); a digest that
  fell back to the graph digest, or said no passage addresses the goal, finds
  none. Gate: >= 3 of 5 goals, an errored goal counted as not found.

JSON out under ``results/`` (``ADR093_RESULTS``), with the provenance every
harness file carries (``backends.provenance``).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backends  # noqa: E402
import infer  # noqa: E402
import metrics  # noqa: E402
import run  # noqa: E402
import scifact  # noqa: E402

NEI = run.NEI
SEED = scifact.SEED
PER_LABEL = 10
LOOP_GOALS = 5
GROUNDED_MIN = 0.9
LOOP_MIN_FOUND = 3
QUERY_TEMPLATE = "{claim}"
GOAL_TEMPLATE = "Find out what the project's literature says about this claim: {claim}"
DEFAULT_SCOPE, DEFAULT_DEPTH = "all", "shallow"
LEGS = ("agentic", "loop")
#: ``r4.search.core.SourceType.RAG_DOCUMENT``: a passage of the retrieve verb.
RAG_DOCUMENT = "rag_document"
#: What ``search_agentic`` returns instead of JSON when the search raised.
TOOL_ERROR_PREFIX = "Error running agentic search"
#: The loop's graph-digest fallback reads this graph; the probe loads none
#: (the fallback finds no gold either way, and a project graph is large).
EMPTY_GRAPH: dict[str, Any] = {"nodes": [], "edges": [], "note": "not loaded by the probe"}

DEFINITIONS = {
    "grounded": (
        "the tool answered; the answer cites >= 1 [n]; every cited [n] names a source "
        "the search handed its synthesizer (no phantom); >= 1 cited source is a passage "
        "a rag.retrieve call of this search returned"
    ),
    "agentic_gate": "grounded_itt >= 0.9 over the 30 claims, an errored claim not grounded",
    "loop_gold": "a gold evidence doc is among the passages the iteration's pack renders",
    "loop_gate": ">= 3 of 5 goals, an errored goal not found",
}


# ── the seeded selection ─────────────────────────────────────────────────────


def select_agentic(
    claims: Sequence[scifact.Claim], *, per_label: int = PER_LABEL, seed: int = SEED
) -> list[scifact.Claim]:
    """``per_label`` claims of each label, a seeded draw from the claims in
    id order (one generator, labels in ``scifact.LABELS`` order)."""
    rng = random.Random(seed)
    chosen: list[scifact.Claim] = []
    for label in scifact.LABELS:
        pool = sorted((c for c in claims if c.label == label), key=lambda c: c.id)
        drawn = rng.sample(pool, min(per_label, len(pool)))
        chosen.extend(sorted(drawn, key=lambda c: c.id))
    return chosen


def select_goals(
    claims: Sequence[scifact.Claim],
    *,
    n: int = LOOP_GOALS,
    seed: int = SEED,
    exclude: Sequence[scifact.Claim] = (),
) -> list[scifact.Claim]:
    """``n`` dev claims with gold docs, a seeded draw disjoint from ``exclude``."""
    skip = {c.id for c in exclude}
    pool = sorted((c for c in claims if c.evidence and c.id not in skip), key=lambda c: c.id)
    return sorted(random.Random(seed + 1).sample(pool, min(n, len(pool))), key=lambda c: c.id)


def goal_for(claim: scifact.Claim) -> str:
    """The research goal a claim stands for (deterministic)."""
    return GOAL_TEMPLATE.format(claim=claim.text.strip().rstrip("."))


def selection(claims: Sequence[scifact.Claim]) -> dict[str, Any]:
    """The claim ids each leg runs, by label for the agentic leg."""
    agentic = select_agentic(claims)
    goals = select_goals(claims, exclude=agentic)
    return {
        "seed": SEED,
        "agentic": {label: [c.id for c in agentic if c.label == label] for label in scifact.LABELS},
        "loop": [c.id for c in goals],
    }


# ── what the consumer retrieved ──────────────────────────────────────────────


class RetrieveRecorder:
    """Records every ``client.rag.retrieve`` call made inside the block: the
    query, the reader fields sent, the server's verdict and each passage
    (node id, artifact, source name, SciFact doc). A call that raised is kept
    in ``errors`` and re-raised to the consumer unchanged."""

    def __init__(self, client: Any, doc_of: Callable[..., str | None]) -> None:
        self.rag = client.rag
        self.doc_of = doc_of
        self.calls: list[dict[str, Any]] = []
        self.errors: list[str] = []

    def __enter__(self) -> RetrieveRecorder:
        self._own = "retrieve" in vars(self.rag)
        self._original = self.rag.retrieve
        original = self._original

        def retrieve(query: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                result = original(query, *args, **kwargs)
            except Exception as exc:
                self.errors.append(f"{type(exc).__name__}: {exc}"[:300])
                raise
            self.calls.append(self._record(query, kwargs, result))
            return result

        self.rag.retrieve = retrieve
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._own:
            self.rag.retrieve = self._original
        else:
            del self.rag.retrieve

    def _record(self, query: Any, kwargs: dict[str, Any], result: Any) -> dict[str, Any]:
        verdict = getattr(result, "sufficiency", None)
        return {
            "query": str(query)[:300],
            "k_max": kwargs.get("k_max"),
            "profile": kwargs.get("profile"),
            "reader_model": kwargs.get("reader_model"),
            "status": getattr(verdict, "status", ""),
            "searched": getattr(result, "searched", None),
            "passages": [self._passage(p) for p in getattr(result, "passages", ()) or ()],
        }

    def _passage(self, passage: Any) -> dict[str, Any]:
        return {
            "source_id": passage.source_id,
            "artifact_id": passage.artifact_id,
            "source_name": passage.source_name,
            "doc": self.doc_of(passage.artifact_id, passage.source_name, passage.source_id),
        }

    def passages(self) -> list[dict[str, Any]]:
        """The passages the consumer received, in call order: an
        ``insufficient`` result gives none (``sources_from_retrieve`` and the
        loop's digest both drop it)."""
        kept = [c for c in self.calls if c["status"] != "insufficient"]
        return [p for call in kept for p in call["passages"]]

    def passage(self, source_id: str = "", title: str = "") -> dict[str, Any] | None:
        """The received passage with this node id; by its source name only
        when no node id is known (a citation the tool lists by title alone)."""
        received = self.passages()
        if source_id:
            return next((p for p in received if p["source_id"] == source_id), None)
        return next((p for p in received if title and p["source_name"] == title), None)

    def docs(self) -> list[str]:
        """Every received passage as a ranked doc (another artifact's as
        ``other:<artifact>``), deduplicated in order."""
        docs = [
            backends.doc_or_other(p["doc"], p["artifact_id"] or p["source_id"], n)
            for n, p in enumerate(self.passages())
        ]
        return metrics.dedup(docs)

    def summary(self) -> list[dict[str, Any]]:
        """The calls as a row keeps them (no passage list, the counts)."""
        return [
            {**{k: v for k, v in call.items() if k != "passages"}, "n": len(call["passages"])}
            for call in self.calls
        ]

    def raise_outage(self, leg: str) -> None:
        """A retrieve that raised is an outage, never the consumer's result:
        the claim is retried (``infer.with_retries``), then an error row."""
        if self.errors:
            raise infer.TransientError(f"{leg}: rag.retrieve failed: {self.errors[0]}")


# ── the agentic leg ──────────────────────────────────────────────────────────


class AgenticError(RuntimeError):
    """search_agentic answered with its error string, not a result."""


def companion_tool(client: Any, name: str, model: str) -> Callable[..., str]:
    """One companion tool, built as a session of ``model`` builds it."""
    from r4.companion.tools import get_builtin_tools

    for tool in get_builtin_tools(client, session_model=model):
        if tool["name"] == name:
            return tool["execute"]
    raise KeyError(f"this r4 has no companion tool {name!r}")


def agentic_payload(output: str) -> dict[str, Any]:
    """The tool's JSON. Its error string is an outage (retried) when it
    names one, else an ``AgenticError``."""
    text = (output or "").strip()
    if text.startswith(TOOL_ERROR_PREFIX):
        infer.check_transient(text)
        raise AgenticError(text[:300])
    data = json.loads(text)
    if not isinstance(data, dict):
        raise AgenticError(f"search_agentic returned {type(data).__name__}, not an object")
    return data


def _citation_index(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for entry in payload.get("citations") or []:
        if isinstance(entry, dict) and str(entry.get("index", "")).isdigit():
            out[int(entry["index"])] = entry
    return out


def _named_source(n: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    """The source ``[n]`` names in the synthesizer's numbering: the tool's
    ``sources[n-1]`` (it lists the first 10), else its citation entry."""
    sources = payload.get("sources") or []
    if 1 <= n <= len(sources) and isinstance(sources[n - 1], dict):
        return sources[n - 1]
    entry = _citation_index(payload).get(n)
    if entry is None:
        return None
    return {"title": entry.get("source_title"), "type": None, "metadata": {}}


def cited_source(n: int, payload: dict[str, Any], seen: RetrieveRecorder) -> dict[str, Any]:
    """One cited number: the source it names, and whether that source is a
    passage this search's retrieve calls returned."""
    source = _named_source(n, payload)
    if source is None:
        return {"n": n, "phantom": True, "retrieved_passage": False, "doc": None}
    meta = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    source_id, title = str(meta.get("source_id") or ""), str(source.get("title") or "")
    passage = seen.passage(source_id, title) if source.get("type") in (RAG_DOCUMENT, None) else None
    return {
        "n": n,
        "phantom": False,
        "type": source.get("type"),
        "title": title,
        "source_id": source_id,
        "retrieved_passage": passage is not None,
        "doc": passage["doc"] if passage else None,
    }


def _gold(docs: Sequence[str], evidence: set[str]) -> bool | None:
    """Whether any doc is gold; None for a claim with no gold doc (NEI)."""
    return bool(set(docs) & evidence) if evidence else None


def _cited(answer: str, payload: dict[str, Any], seen: RetrieveRecorder) -> dict[str, Any]:
    """Every number the answer cites, its phantoms and the retrieved
    passages among them (by doc)."""
    cited = [cited_source(n, payload, seen) for n in sorted(run.cite_numbers(answer))]
    passages = [c for c in cited if c["retrieved_passage"]]
    return {
        "cited": cited,
        "phantoms": [c["n"] for c in cited if c["phantom"]],
        "passages": len(passages),
        "cited_docs": sorted({c["doc"] for c in passages if c["doc"]}),
    }


def grounding(
    payload: dict[str, Any], seen: RetrieveRecorder, evidence: set[str]
) -> dict[str, Any]:
    """The grounded verdict of one answer, with what it rests on."""
    answer = str(payload.get("answer") or "")
    cited = _cited(answer, payload, seen)
    retrieved = seen.docs()
    grounded = bool(cited["cited"]) and not cited["phantoms"] and cited["passages"] > 0
    return {
        "answer": answer[: run.ANSWER_CHARS],
        **{k: v for k, v in cited.items() if k != "passages"},
        "grounded": grounded,
        "cites_gold": _gold(cited["cited_docs"], evidence),
        "gold_retrieved": _gold(retrieved, evidence),
        "retrieved_docs": retrieved[:20],
        "sources_returned": len(payload.get("sources") or []),
        "confidence": payload.get("confidence"),
        "warnings": [str(w)[:200] for w in (payload.get("warnings") or [])[:5]],
    }


def agentic_row(
    stack: Any, claim: scifact.Claim, tool: Callable[..., str], *, scope: str, depth: str
) -> dict[str, Any]:
    """One claim through search_agentic, as the agent calls it."""
    query = QUERY_TEMPLATE.format(claim=claim.text.strip())
    started = time.monotonic()
    with RetrieveRecorder(stack.client, stack.doc_of) as seen:
        output = tool(query=query, depth=depth, scope=scope, project_id=stack.project)
    seconds = time.monotonic() - started
    seen.raise_outage("search_agentic")
    payload = agentic_payload(output)
    return {
        "claim": claim.id,
        "label": claim.label,
        "query": query,
        **grounding(payload, seen, set(claim.evidence)),
        "seconds": round(seconds, 2),
        "retrieve_calls": seen.summary(),
    }


def run_agentic(
    stack: Any,
    claims: Sequence[scifact.Claim],
    tool: Callable[..., str],
    *,
    scope: str = DEFAULT_SCOPE,
    depth: str = DEFAULT_DEPTH,
) -> list[dict[str, Any]]:
    rows = []
    for n, claim in enumerate(claims, start=1):
        make = lambda c=claim: infer.with_retries(  # noqa: E731 - one claim, retried
            lambda: agentic_row(stack, c, tool, scope=scope, depth=depth)
        )
        rows.append(run.attempt(claim, make))
        print(f"  agentic: {n}/{len(claims)}", file=sys.stderr, flush=True)
    return rows


# ── the loop leg ─────────────────────────────────────────────────────────────


def loop_executor(stack: Any, model: str) -> Any:
    """The local loop's iteration executor, as ``LocalRuntime`` builds it."""
    from r4.runtime.agent_executor import LocalAgentIterationExecutor

    return LocalAgentIterationExecutor(stack.client, stack.project, model=model)


def _first_gold(docs: Sequence[str], evidence: set[str]) -> int | None:
    return next((rank for rank, doc in enumerate(docs, start=1) if doc in evidence), None)


def loop_row(stack: Any, claim: scifact.Claim, executor: Any) -> dict[str, Any]:
    """One goal through the iteration's evidence digest (no model call)."""
    goal = goal_for(claim)
    started = time.monotonic()
    with RetrieveRecorder(stack.client, stack.doc_of) as seen:
        knowledge = executor._knowledge_context(goal, dict(EMPTY_GRAPH))
    seconds = time.monotonic() - started
    seen.raise_outage("loop digest")
    labels = knowledge.get("citations") or {}
    packed = [seen.passage(str(source_id)) for source_id in labels.values()]
    docs = metrics.dedup(
        [
            backends.doc_or_other(p["doc"], p["artifact_id"] or p["source_id"], n)
            for n, p in enumerate(packed)
            if p is not None
        ]
    )
    evidence, retrieved = set(claim.evidence), seen.docs()
    return {
        "claim": claim.id,
        "label": claim.label,
        "goal": goal,
        "basis": knowledge.get("basis"),
        "note": knowledge.get("note"),
        "digest_docs": docs,
        "gold_in_digest": bool(set(docs) & evidence),
        "gold_rank_in_digest": _first_gold(docs, evidence),
        "gold_retrieved": bool(set(retrieved) & evidence),
        "retrieved_docs": retrieved[:20],
        "digest_chars": len(str(knowledge.get("text") or "")),
        "seconds": round(seconds, 2),
        "retrieve_calls": seen.summary(),
    }


def run_loop(
    stack: Any, claims: Sequence[scifact.Claim], make_executor: Callable[[], Any]
) -> list[dict[str, Any]]:
    rows = []
    for n, claim in enumerate(claims, start=1):
        make = lambda c=claim: infer.with_retries(  # noqa: E731 - one goal, retried
            lambda: loop_row(stack, c, make_executor())
        )
        rows.append(run.attempt(claim, make))
        print(f"  loop: {n}/{len(claims)}", file=sys.stderr, flush=True)
    return rows


# ── summaries and gates ──────────────────────────────────────────────────────


def _share(rows: Sequence[dict[str, Any]], key: str) -> float | None:
    """The share of ``rows`` whose ``key`` is True; None without rows."""
    return sum(r.get(key) is True for r in rows) / len(rows) if rows else None


def label_block(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Grounding of one group of claims: over judged claims and (``_itt``)
    over every claim, an errored one not grounded."""
    ok = [r for r in rows if "error" not in r]
    scored = [r for r in ok if r.get("cites_gold") is not None]
    return {
        "n": len(rows),
        "errors": len(rows) - len(ok),
        "grounded": _share(ok, "grounded"),
        "grounded_itt": _share(rows, "grounded"),
        "cites_gold": _share(scored, "cites_gold"),
        "cites_gold_n": len(scored),
        "gold_retrieved": _share(scored, "gold_retrieved"),
        "phantom_answers": sum(bool(r.get("phantoms")) for r in ok),
    }


def _with_label(rows: Sequence[dict[str, Any]], *labels: str) -> list[dict[str, Any]]:
    return [r for r in rows if r["label"] in labels]


EVIDENCE_LABELS = tuple(label for label in scifact.LABELS if label != NEI)


def agentic_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    seconds = [r["seconds"] for r in rows if "error" not in r]
    return {
        **label_block(rows),
        "by_label": {label: label_block(_with_label(rows, label)) for label in scifact.LABELS},
        "evidence": label_block(_with_label(rows, *EVIDENCE_LABELS)),
        "nei": label_block(_with_label(rows, NEI)),
        "latency": {"seconds": metrics.percentiles(seconds)},
    }


def loop_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if "error" not in r]
    return {
        "goals": len(rows),
        "errors": len(rows) - len(ok),
        "gold_found": sum(r.get("gold_in_digest") is True for r in rows),
        "gold_retrieved": sum(r.get("gold_retrieved") is True for r in rows),
        "basis": dict(Counter(str(r.get("basis")) for r in ok)),
        "latency": {"seconds": metrics.percentiles([r["seconds"] for r in ok])},
    }


def agentic_gate(summary: dict[str, Any] | None, expected: int = 3 * PER_LABEL) -> dict:
    """search_agentic grounded >= 90% on 30 claims (ITT)."""
    if summary is None:
        return {"pass": None, "why": "leg not run"}
    value = summary["grounded_itt"]
    passed = summary["n"] == expected and value is not None and value >= GROUNDED_MIN
    return {"threshold": GROUNDED_MIN, "value": value, "n": summary["n"], "pass": passed}


def loop_gate(summary: dict[str, Any] | None, expected: int = LOOP_GOALS) -> dict:
    """The loop digest finds the gold doc in >= 3 of 5 goals."""
    if summary is None:
        return {"pass": None, "why": "leg not run"}
    found = summary["gold_found"]
    passed = summary["goals"] == expected and found >= LOOP_MIN_FOUND
    return {"threshold": LOOP_MIN_FOUND, "found": found, "n": summary["goals"], "pass": passed}


def summarize(rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    agentic = agentic_summary(rows["agentic"]) if "agentic" in rows else None
    loop = loop_summary(rows["loop"]) if "loop" in rows else None
    gates = {"agentic": agentic_gate(agentic), "loop": loop_gate(loop)}
    verdicts = [g["pass"] for g in gates.values()]
    return {
        "agentic": agentic,
        "loop": loop,
        "gates": gates,
        "pass": None if None in verdicts else all(verdicts),
    }


# ── the command ──────────────────────────────────────────────────────────────


def _settings(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "legs": list(args.legs),
        "model": args.model,
        "project": args.project,
        "graph_fingerprinted": args.graph,
        "search_scope": "project-wide (both consumers send no graph)",
        "agentic_scope": args.scope,
        "agentic_depth": args.depth,
        "query_template": QUERY_TEMPLATE,
        "goal_template": GOAL_TEMPLATE,
        "seed": SEED,
        "per_label": PER_LABEL,
        "loop_goals": LOOP_GOALS,
    }


def measure(
    stack: Any,
    claims: Sequence[scifact.Claim],
    args: argparse.Namespace,
    *,
    tool: Callable[..., str] | None = None,
    make_executor: Callable[[], Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """The rows of each leg ``args.legs`` names."""
    agentic = select_agentic(claims)
    rows: dict[str, list[dict[str, Any]]] = {}
    if "agentic" in args.legs:
        tool = tool or companion_tool(stack.client, "search_agentic", args.model)
        rows["agentic"] = run_agentic(stack, agentic, tool, scope=args.scope, depth=args.depth)
    if "loop" in args.legs:
        goals = select_goals(claims, exclude=agentic)
        factory = make_executor or (lambda: loop_executor(stack, args.model))
        rows["loop"] = run_loop(stack, goals, factory)
    return rows


def record(
    args: argparse.Namespace,
    claims: Sequence[scifact.Claim],
    rows: dict[str, list[dict[str, Any]]],
    prov: dict[str, Any],
    stamp: str,
) -> dict[str, Any]:
    return {
        "schema_version": run.SCHEMA_VERSION,
        "kind": "consumers",
        "slice": "S6",
        "claims_set": "dev",
        "label": args.label,
        "at": stamp,
        "model": args.model,
        "settings": _settings(args),
        "definitions": DEFINITIONS,
        "selection": selection(claims),
        "provenance": prov,
        "summary": summarize(rows),
        "rows": rows,
    }


def write(rec: dict[str, Any], out_dir: Path | None = None) -> Path:
    """``<stamp>-consumers-dev-s6[-label].json`` under ``results/``."""
    out_dir = out_dir or run.RESULTS
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{rec['label']}" if rec.get("label") else ""
    path = out_dir / f"{rec['at']}-consumers-dev-s6{suffix}.json"
    path.write_text(json.dumps(rec, indent=1))
    return path


def cmd_plan(args: argparse.Namespace) -> int:
    claims = scifact.claims_for(Path(args.data), "dev")
    print(json.dumps(selection(claims), indent=1))
    return 0


def cmd_run(args: argparse.Namespace, stack: Any = None) -> int:
    claims = scifact.claims_for(Path(args.data), "dev")
    stack = stack or backends.Stack(project=args.project, graph=args.graph)
    prov = backends.provenance(
        project=args.project,
        graph=args.graph,
        model=args.model,
        reads_verdicts=True,
        tool_reader=args.model,
    )
    rows = measure(stack, claims, args)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rec = record(args, claims, rows, prov, stamp)
    path = write(rec)
    print(json.dumps({"file": str(path), **_headline(rec["summary"])}), flush=True)
    return 0


def _headline(summary: dict[str, Any]) -> dict[str, Any]:
    return {"gates": summary["gates"], "pass": summary["pass"]}


def _legs(text: str) -> tuple[str, ...]:
    legs = tuple(part.strip() for part in text.split(",") if part.strip())
    unknown = sorted(set(legs) - set(LEGS))
    if unknown or not legs:
        raise argparse.ArgumentTypeError(f"legs are a comma list of {LEGS}, got {text!r}")
    return legs


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    plan = sub.add_parser("plan", help="print the seeded selection (no stack call)")
    plan.add_argument("--data", required=True, help="SciFact data/ directory")
    go = sub.add_parser("run", help="measure the legs on the stack")
    go.add_argument("--data", required=True, help="SciFact data/ directory")
    go.add_argument("--legs", type=_legs, default=LEGS, help="comma list of agentic,loop")
    go.add_argument("--project", default="default")
    go.add_argument("--graph", default="scifact-eval", help="the graph the provenance fingerprints")
    go.add_argument("--model", default=run.DEFAULT_MODEL, help="the session / loop model")
    go.add_argument("--scope", default=DEFAULT_SCOPE, help="search_agentic scope")
    go.add_argument("--depth", default=DEFAULT_DEPTH, help="search_agentic depth")
    go.add_argument("--label", default="", help="suffix for the result file name")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return cmd_plan(args) if args.cmd == "plan" else cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
