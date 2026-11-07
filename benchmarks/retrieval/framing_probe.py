"""Phase 8b: does the router's query focus give the bare claim's retrieval back?

ADR-093 D-8 wave H. Measured 2026-10-11 (retrieval only, small profile,
project-wide): the bare SciFact claim puts the gold abstract in the 4-passage
pack for 165/188 evidence claims and leaves 29/30 nonsense probes
insufficient; the same claims inside the Phase-8 agent prompt drop to 100/188
and 9/30, because the instructions dominate the e5 query embedding. The
router now searches ``query_focus.focus_query(message)``; this probe sends
each claim through the router's query, per framing template:

- ``bare``: the claim alone (template-independent);
- ``framed``: the router's query with ``R4_RAG_FOCUS=off`` (the whole message,
  as before wave H; on the eval template it must reproduce 100/188 and 9/30);
- ``focused``: the router's query as it is now, through the very function the
  router calls (``retrieval_router.focus_query`` and ``bound_queries``);
- ``focused(bare)``: the identity check (no framing, no change), offline.

Retrieval only (``client.rag.retrieve``, the ADR-093 verb), no model call. The
eval template (``backends.AGENT_PROMPT``) is always scored beside the file's
templates; the gate is read over the file's templates only.

Order (wave H build spec §4): run ``--offline`` on the TUNING templates
(content recall >= 0.99, framing leakage <= 0.5 tokens per message); then the
server probe on the tuning templates; then freeze ``focus-v1``; then score the
HELD-OUT templates once (``--label heldout``), English and Japanese reported
apart. Any lexicon or threshold edit after the held-out run needs a new
``FOCUS_RULES_VERSION`` and a new held-out set.

    # offline (no stack): recall, leakage, identity
    .venv/bin/python benchmarks/retrieval/framing_probe.py --offline \\
        --data ~/data/scifact/data --templates framings_tuning.json
    # server probe (the stand-alone stack, an isolated r4 config)
    XDG_CONFIG_HOME=<isolated> .venv/bin/python benchmarks/retrieval/framing_probe.py \\
        --data ~/data/scifact/data --templates framings_tuning.json --label tuning

A template file is ``{"templates": [{"id", "kind", "text"}]}`` with ``{claim}``
where the claim goes. JSON out under ``benchmarks/retrieval/results/``.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import importlib
import json
import os
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from r4.companion import query_focus
from r4.companion.retrieval_router import bound_queries, focus_query

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
EVAL_TEMPLATE_ID = "eval_agent_prompt"
DEFAULT_GRAPH = "scifact-eval"
DEFAULT_READER = "llama3.2:3b"
K_MAX = 4  # the small profile's pack
#: 2026-10-11, the eval template, before wave H (retrieval only, small, project-wide).
BASELINE = {
    "bare": {"gold": 165, "sufficient": 186, "nonsense_insufficient": 29},
    "framed": {"gold": 100, "sufficient": 63, "nonsense_insufficient": 9},
}
#: The held-out gate, as fractions of 188 evidence claims and 30 probes.
GATE = {
    "gold_mean": 156 / 188,
    "gold_min": 145 / 188,
    "sufficient_mean": 170 / 188,
    "nonsense_insufficient_min": 27 / 30,
}
OFFLINE_GATE = {"recall_min": 0.99, "leakage_max": 0.5}


@dataclass(frozen=True)
class Item:
    """One claim (an evidence claim, ``split="dev"``) or one nonsense probe."""

    id: int
    text: str
    split: str
    evidence: tuple[str, ...] = ()


# ------------------------------------------------------------ templates ---


def load_templates(path: Path) -> list[dict[str, str]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    templates = raw["templates"] if isinstance(raw, dict) else raw
    for template in templates:
        if "{claim}" not in template["text"]:
            raise ValueError(f"template {template['id']!r} has no {{claim}}")
    return list(templates)


def eval_template(prompt: str, graph: str) -> dict[str, str]:
    """``backends.AGENT_PROMPT`` (``.format`` style) as a ``{claim}`` template."""
    text = prompt.format(graph=graph, claim="{claim}")
    return {"id": EVAL_TEMPLATE_ID, "kind": "eval", "text": text}


def render(template: dict[str, str], claim: str) -> str:
    return template["text"].replace("{claim}", claim)


def language(template: dict[str, str]) -> str:
    """``ja`` for a Japanese framing, else ``en``."""
    if template.get("kind") == "japanese" or query_focus._CJK_RE.search(template["text"]):
        return "ja"
    return "en"


@contextmanager
def focus_switch(enabled: bool) -> Iterator[None]:
    """``R4_RAG_FOCUS`` for the duration (off reproduces the router before
    wave H)."""
    saved = os.environ.get(query_focus.FOCUS_ENV)
    os.environ[query_focus.FOCUS_ENV] = "on" if enabled else "off"
    try:
        yield
    finally:
        os.environ.pop(query_focus.FOCUS_ENV, None)
        if saved is not None:
            os.environ[query_focus.FOCUS_ENV] = saved


def router_query(message: str, *, focus: bool) -> tuple[str, str]:
    """(the one query the router sends for ``message``, the focus basis)."""
    with focus_switch(focus):
        found = focus_query(message)
    return bound_queries([found.query])[0], found.basis


# -------------------------------------------------------------- offline ---


def _tokens(text: str) -> list[str]:
    return query_focus.content_tokens(text)


def offline_row(template: dict[str, str], item: Item) -> dict[str, Any]:
    """What the focus makes of one framed claim, without a server."""
    query, basis = router_query(render(template, item.text), focus=True)
    gold, got = _tokens(item.text), _tokens(query)
    kept = sum(1 for token in gold if token in set(got))
    return {
        "basis": basis,
        "recall": kept / len(gold) if gold else 1.0,
        "leakage": sum(1 for token in got if token not in set(gold)),
        "exact": query == " ".join(item.text.split()),
    }


def offline_scores(templates: Sequence[dict[str, str]], items: Sequence[Item]) -> dict[str, Any]:
    """Per template: mean content recall, mean framing leakage (tokens per
    message), exact recoveries and the bases used; plus the identity check."""
    per_template = {}
    for template in templates:
        rows = [offline_row(template, item) for item in items]
        per_template[template["id"]] = {
            "language": language(template),
            "recall": round(sum(r["recall"] for r in rows) / len(rows), 4),
            "leakage": round(sum(r["leakage"] for r in rows) / len(rows), 3),
            "exact": sum(r["exact"] for r in rows),
            "n": len(rows),
            "bases": dict(collections.Counter(r["basis"] for r in rows)),
        }
    return {"templates": per_template, "identity": identity(items)}


def identity(items: Sequence[Item]) -> dict[str, Any]:
    """focused(bare): every bare claim and probe is searched as written."""
    moved = [item.id for item in items if router_query(item.text, focus=True)[1] != "whole"]
    return {"n": len(items), "whole": len(items) - len(moved), "moved": moved}


def offline_gate(scores: dict[str, Any], skip: Sequence[str] = (EVAL_TEMPLATE_ID,)) -> dict:
    """The tuning check: recall and leakage per template, identity 100%."""
    failures = [
        template_id
        for template_id, row in scores["templates"].items()
        if template_id not in skip
        and (
            row["recall"] < OFFLINE_GATE["recall_min"]
            or row["leakage"] > OFFLINE_GATE["leakage_max"]
        )
    ]
    identity_ok = not scores["identity"]["moved"]
    return {"passed": not failures and identity_ok, "failing_templates": failures, **OFFLINE_GATE}


# ----------------------------------------------------------------- live ---


class Prober:
    """One retrieve per distinct query (``cache=False``: one per arm),
    exactly as the router asks for a small reader: ``k_max`` 4, the small
    profile, the reader model, project-wide."""

    def __init__(
        self,
        retrieve: Callable[[str], dict[str, Any]],
        *,
        cache: bool = True,
        min_interval: float = 0.0,
    ) -> None:
        self._retrieve = retrieve
        self._cache: dict[str, dict[str, Any]] | None = {} if cache else None
        self._min_interval = max(0.0, min_interval)
        self._last = 0.0
        self.calls = 0

    def ask(self, query: str) -> dict[str, Any]:
        if self._cache is not None and query in self._cache:
            return {**self._cache[query], "cached": True}
        self._pace()
        self.calls += 1
        got = self._retrieve(query)
        if self._cache is not None:
            self._cache[query] = got
        return {**got, "cached": False}

    def _pace(self) -> None:
        """At most one call per ``min_interval`` seconds: the stand-alone
        edge rate-limits /gw/* (nginx answers a burst with 503)."""
        wait = self._last + self._min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()


def stack_retriever(
    stack: Any, *, k_max: int, profile: str, reader: str, timeout: float | None = None
) -> Callable:
    """``client.rag.retrieve`` against the stand-alone stack (``backends.Stack``),
    project-wide (no graph), at the harness's bound when the client takes one."""
    extra = {"timeout": timeout} if timeout else {}

    def retrieve(query: str) -> dict[str, Any]:
        result = stack.client.rag.retrieve(
            query,
            project_id=stack.project,
            k_max=k_max,
            profile=profile,
            reader_model=reader,
            **extra,
        )
        docs = [stack.doc_of(p.source_id, p.artifact_id) for p in result.passages]
        value = result.sufficiency.value
        return {"status": result.sufficiency.status, "value": value, "docs": docs}

    return retrieve


def _arm(prober: Prober, query: str, basis: str) -> dict[str, Any]:
    return {"query_sha": hashlib.sha256(query.encode()).hexdigest()[:16], "basis": basis} | (
        prober.ask(query)
    )


def probe_template(prober: Prober, template: dict[str, str], items: Sequence[Item]) -> list[dict]:
    rows = []
    for item in items:
        message = render(template, item.text)
        framed = _arm(prober, *router_query(message, focus=False))
        focused = _arm(prober, *router_query(message, focus=True))
        rows.append(_row(template["id"], item, framed=framed, focused=focused))
    return rows


def probe_bare(prober: Prober, items: Sequence[Item]) -> list[dict]:
    return [_row("bare", item, bare=_arm(prober, item.text, "whole")) for item in items]


def _row(template_id: str, item: Item, **arms: dict[str, Any]) -> dict[str, Any]:
    head = {"template": template_id, "split": item.split, "claim": item.id}
    return {**head, "evidence": list(item.evidence), **arms}


# -------------------------------------------------------------- summary ---


def _statuses(rows: Sequence[dict], arm: str) -> dict[str, int]:
    return dict(collections.Counter(row[arm]["status"] for row in rows))


def arm_summary(rows: Sequence[dict], arm: str) -> dict[str, Any]:
    """gold-in-pack and judged-sufficient over the evidence claims, status
    counts per split, and nonsense probes flagged insufficient."""
    dev = [row for row in rows if row["split"] == "dev"]
    probes = [row for row in rows if row["split"] == "nonsense"]
    status_dev, status_probes = _statuses(dev, arm), _statuses(probes, arm)
    return {
        "n_dev": len(dev),
        "gold": sum(bool(set(row["evidence"]) & set(row[arm]["docs"])) for row in dev),
        "sufficient": status_dev.get("sufficient", 0),
        "status_dev": status_dev,
        "n_nonsense": len(probes),
        "nonsense_insufficient": status_probes.get("insufficient", 0),
        "status_nonsense": status_probes,
    }


def summarize(rows: Sequence[dict], templates: Sequence[dict[str, str]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "bare": arm_summary([r for r in rows if r["template"] == "bare"], "bare")
    }
    for template in templates:
        own = [row for row in rows if row["template"] == template["id"]]
        out[template["id"]] = {
            "language": language(template),
            "framed": arm_summary(own, "framed"),
            "focused": arm_summary(own, "focused"),
            "bases": dict(collections.Counter(row["focused"]["basis"] for row in own)),
        }
    return out


def _share(count: int, total: int) -> float:
    return count / total if total else 0.0


def gate(summary: dict[str, Any], identity_ok: bool, skip: Sequence[str] = (EVAL_TEMPLATE_ID,)):
    """The held-out gate on the focused arm over the file's templates."""
    focused = [row["focused"] for key, row in summary.items() if key not in ("bare", *skip)]
    if not focused:
        return {"passed": False, "reason": "no templates"}
    gold = [_share(row["gold"], row["n_dev"]) for row in focused]
    sufficient = [_share(row["sufficient"], row["n_dev"]) for row in focused]
    nonsense = [_share(row["nonsense_insufficient"], row["n_nonsense"]) for row in focused]
    checks = {
        "gold_mean": sum(gold) / len(gold) >= GATE["gold_mean"],
        "gold_min": min(gold) >= GATE["gold_min"],
        "sufficient_mean": sum(sufficient) / len(sufficient) >= GATE["sufficient_mean"],
        "nonsense_insufficient_min": min(nonsense) >= GATE["nonsense_insufficient_min"],
        "identity": identity_ok,
    }
    return {"passed": all(checks.values()), "checks": checks, "thresholds": GATE}


def by_language(summary: dict[str, Any], skip: Sequence[str] = (EVAL_TEMPLATE_ID,)) -> dict:
    """Focused-arm means per framing language (English and Japanese apart)."""
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for key, row in summary.items():
        if key not in ("bare", *skip):
            groups[row["language"]].append(row["focused"])
    return {
        lang: {
            "templates": len(rows),
            "gold_mean": sum(r["gold"] for r in rows) / len(rows),
            "nonsense_insufficient_mean": sum(r["nonsense_insufficient"] for r in rows) / len(rows),
        }
        for lang, rows in groups.items()
    }


def baseline_check(summary: dict[str, Any]) -> dict[str, Any]:
    """Does the run reproduce the 2026-10-11 numbers (bare; framed on the
    eval template)? A stack that moved makes the comparison void."""
    got = {
        "bare": summary.get("bare", {}),
        "framed": summary.get(EVAL_TEMPLATE_ID, {}).get("framed", {}),
    }
    return {
        arm: {key: (got[arm].get(key), want) for key, want in expected.items()}
        for arm, expected in BASELINE.items()
    }


# ------------------------------------------------------------------ run ---


def _harness(name: str) -> Any:
    """A sibling harness module: as a package module under pytest, by bare
    name when this file runs as a script (its directory is on sys.path)."""
    try:
        return importlib.import_module(f"benchmarks.retrieval.{name}")
    except ImportError:
        return importlib.import_module(name)


def load_items(data: Path, limit: int | None = None) -> list[Item]:
    scifact = _harness("scifact")
    dev = [c for c in scifact.claims_for(Path(data), "dev") if c.evidence]
    probes = scifact.claims_for(Path(data), "nonsense")
    items = [Item(c.id, c.text, "dev", tuple(sorted(c.evidence))) for c in dev[:limit]]
    return items + [Item(c.id, c.text, "nonsense") for c in probes[:limit]]


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def provenance(args: argparse.Namespace) -> dict[str, Any]:
    router_file = Path(sys.modules[bound_queries.__module__].__file__)
    return {
        "focus_rules": query_focus.FOCUS_RULES_VERSION,
        "rules_digest": query_focus.rules_digest(),
        "query_focus_sha256": _sha256(Path(query_focus.__file__)),
        "router_sha256": _sha256(router_file),
        "templates_file": str(args.templates),
        "templates_sha256": _sha256(args.templates),
        "data": str(args.data),
        "k_max": args.k_max,
        "profile": args.profile,
        "reader_model": args.reader_model,
        "graph_in_eval_template": args.graph,
        "scope": "project-wide",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _templates(args: argparse.Namespace) -> list[dict[str, str]]:
    templates = load_templates(args.templates)
    if args.no_eval_template:
        return templates
    return [eval_template(_harness("backends").AGENT_PROMPT, args.graph), *templates]


def run_offline(args: argparse.Namespace) -> dict[str, Any]:
    templates, items = _templates(args), load_items(args.data, args.limit)
    scores = offline_scores(templates, items)
    return {"mode": "offline", "offline": scores, "offline_gate": offline_gate(scores)}


def run_live(args: argparse.Namespace, retrieve: Callable[[str], dict] | None = None) -> dict:
    templates, items = _templates(args), load_items(args.data, args.limit)
    if retrieve is None:
        retrieve = _stack_retrieve(args)
    prober = Prober(retrieve, cache=not args.no_cache, min_interval=args.min_interval)
    started = time.monotonic()
    rows = probe_bare(prober, items)
    for template in templates:
        rows += probe_template(prober, template, items)
        print(f"[framing] {template['id']}: {prober.calls} calls", file=sys.stderr)
    summary = summarize(rows, templates)
    ident = identity(items)
    return {
        "mode": "live",
        "summary": summary,
        "by_language": by_language(summary),
        "identity": ident,
        "gate": gate(summary, not ident["moved"]),
        "baseline_check": baseline_check(summary),
        "offline": offline_scores(templates, items),
        "calls": prober.calls,
        "seconds": round(time.monotonic() - started, 1),
        "rows": rows,
    }


def _stack_retrieve(args: argparse.Namespace) -> Callable[[str], dict]:
    backends = _harness("backends")
    stack = backends.Stack(project=args.project, graph=args.graph)
    takes = backends.accepts(stack.client.rag.retrieve, {"timeout": 1})
    return stack_retriever(
        stack,
        k_max=args.k_max,
        profile=args.profile,
        reader=args.reader_model,
        timeout=backends.RETRIEVE_TIMEOUT_S if takes else None,
    )


def write_result(result: dict[str, Any], label: str, out_dir: Path = RESULTS) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(out_dir) / f"{stamp}-framing-{result['mode']}-{label}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, required=True, help="SciFact data dir")
    parser.add_argument("--templates", type=Path, required=True, help="framing templates JSON")
    parser.add_argument("--label", default="tuning", help="result file label (tuning|heldout)")
    parser.add_argument("--offline", action="store_true", help="recall/leakage/identity only")
    parser.add_argument("--project", default="default")
    parser.add_argument("--graph", default=DEFAULT_GRAPH, help="the eval template's graph name")
    parser.add_argument("--k-max", type=int, default=K_MAX)
    parser.add_argument("--profile", default="small")
    parser.add_argument("--reader-model", default=DEFAULT_READER)
    parser.add_argument("--limit", type=int, default=None, help="first N claims and probes")
    parser.add_argument("--no-cache", action="store_true", help="one call per arm")
    parser.add_argument(
        "--min-interval", type=float, default=0.25, help="seconds between calls (edge rate limit)"
    )
    parser.add_argument("--no-eval-template", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=RESULTS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_offline(args) if args.offline else run_live(args)
    result["provenance"] = provenance(args)
    path = write_result(result, args.label, args.out_dir)
    report = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps({**report, "written": str(path)}, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
