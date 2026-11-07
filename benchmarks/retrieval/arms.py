"""Eval arms: what the model is shown for one claim, one factor per arm (ADR-093 C0).

An arm takes the stack and a claim and returns a ``Retrieved``: the hits (for
retrieval metrics), the docs actually shown, the exact context string, the
shown passages (what a pack prompt renders) and the prompt variant the
verdict reader uses by default. Today's surfaces are the baseline (B0);
ADR-093 slices register theirs with ``register``.

``retrieve:<preset>`` arms call ``GET /gw/rag/retrieve`` with explicit knobs
(``PRESETS``) and ``trace=1``; they report the server's sufficiency, so the
scorer gate and the sufficiency metrics apply to them. ``retrieve:large`` is
the ADR-093 D-14 large profile (the rikyu readers' retrieval); who reads its
passages is the verdict reader's choice (``run.py infer --reader-config``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import backends
import infer
import scifact

from r4_contracts.models import RETRIEVAL_PROFILES

Arm = Callable[["backends.Stack", "scifact.Claim"], "backends.Retrieved"]
ARMS: dict[str, Arm] = {}
GRAPH = "scifact-eval"


def register(name: str, arm: Arm) -> Arm:
    ARMS[name] = arm
    return arm


def numbered(hits: list[backends.Hit], *, width: int = 1200) -> str:
    """Passages as a harness shows them: numbered, named, in the order given."""
    lines = []
    for n, hit in enumerate(hits, start=1):
        name = scifact.doc_filename(hit.doc_id) if hit.doc_id else hit.ref
        lines.append(f"[{n}] source {name}\n{hit.text[:width]}")
    return "\n\n".join(lines)


def _numbered_context(got: backends.Retrieved, hits: list[backends.Hit], width: int) -> None:
    """The arm's own context, and the cut texts it shows (what a json or
    legacy reader sees, and what its rationale coverage is scored on)."""
    got.context = numbered(hits, width=width)
    got.rendered = [hit.text[:width] for hit in hits]


def _shown(hits: list[backends.Hit]) -> list[str]:
    """The docs in the context, in order; a passage of another artifact keeps
    its place as a sentinel (it is shown, and it is not evidence)."""
    return [backends.hit_doc(h, n) for n, h in enumerate(hits)]


def _passages(got: backends.Retrieved, hits: list[backends.Hit]) -> backends.Retrieved:
    got.passages = backends.as_passages(hits)
    return got


def _closed(_stack: backends.Stack, _claim: scifact.Claim) -> backends.Retrieved:
    return backends.Retrieved()


def _oracle(stack: backends.Stack, claim: scifact.Claim) -> backends.Retrieved:
    """The gold abstracts (the cited ones for an NEI claim): the upper bracket."""
    docs = sorted(claim.evidence or claim.cited)
    hits = []
    for doc in docs:
        entry = stack.corpus.docs.get(doc)
        if entry:
            hits.append(backends.Hit(doc, 1.0, f"{entry['title']}\n{entry['abstract']}"))
    got = backends.Retrieved(hits=hits, shown=_shown(hits))
    _numbered_context(got, hits, 4000)
    return _passages(got, hits)


def _k3(scope: str, limit: int) -> Arm:
    def run(stack: backends.Stack, claim: scifact.Claim) -> backends.Retrieved:
        got = stack.k3(claim.text, scope=scope, limit=limit)
        got.shown = _shown(got.hits[:5])
        _numbered_context(got, got.hits[:5], 1200)
        return _passages(got, got.hits[:5])

    return run


def _tool(name: str, **extra: Any) -> Arm:
    """A companion tool as the arm; a callable ``extra`` value is resolved
    against the stack at call time (``--graph`` reaches the tool)."""

    def run(stack: backends.Stack, claim: scifact.Claim) -> backends.Retrieved:
        kwargs = {k: v(stack) if callable(v) else v for k, v in extra.items()}
        got = stack.tool(name, query=claim.text, **kwargs)
        got.shown = _shown(got.hits)
        return _passages(got, got.hits)

    return run


def _eval_graph(stack: backends.Stack) -> list[str]:
    return [stack.graph]


def _legacy(stack: backends.Stack, claim: scifact.Claim) -> backends.Retrieved:
    """Today's answer path, read by the verdict reader: /rag/query's own
    passages (top 5 at cosine >= 0.7, full chunk text), its own layout and
    its prompt wording (question first, strict abstention)."""
    got = stack.rag_sources(claim.text, top_k=5, threshold=0.7)
    got.shown = _shown(got.hits)
    got.context = "\n\n".join(
        f"[{n}] {scifact.doc_filename(h.doc_id) if h.doc_id else h.ref}\n{h.text}"
        for n, h in enumerate(got.hits, start=1)
    )
    got.prompt = "legacy"
    return _passages(got, got.hits)


# GET /gw/rag/retrieve presets: every knob explicit, so an arm measures one
# factor whatever the site defaults are (the request sizes too: k_max, pool and
# per_artifact at the ADR-093 defaults, never UMRS_RAG_FINAL_K_MAX & co).
# ``default`` is the site's own (auto, site sizes).
SIZES: dict[str, Any] = {"k_max": 4, "pool": 50, "per_artifact": 2}
PRESETS: dict[str, dict[str, Any]] = {
    "legacy": {"threshold": 0.7, "k_max": 5, "lexical": "off", "rerank": "off", "merge": "off"},
    "dense": {"cutoff": "adaptive", **SIZES, "lexical": "off", "rerank": "off", "merge": "off"},
    "fused": {"cutoff": "adaptive", **SIZES, "lexical": "on", "rerank": "off", "merge": "off"},
    "rerank": {"cutoff": "adaptive", **SIZES, "lexical": "on", "rerank": "on", "merge": "off"},
    "merge": {"cutoff": "adaptive", **SIZES, "lexical": "on", "rerank": "off", "merge": "on"},
    "default": {"lexical": "auto", "rerank": "auto", "merge": "auto"},
    # ADR-093 D-14: what a site whose reader is large (rikyu's kimi-k3,
    # glm-5.3) retrieves. The `merge` preset at the large profile's sizes,
    # read from the shared table so the arm always measures the current
    # profile (MD-14, 2026-10-13: the medium sizes; runs before that date
    # record k_max 10 / pool 100 in their `preset`). `profile` also sets what
    # explicit knobs cannot (the partial cap).
    "large": {
        "profile": "large",
        "reader_model": "kimi-k3",
        "cutoff": "adaptive",
        "k_max": RETRIEVAL_PROFILES["large"].k_max,
        "pool": RETRIEVAL_PROFILES["large"].pool,
        "per_artifact": 2,
        "lexical": "on",
        "rerank": "off",
        "merge": "on",
    },
}
#: The lane reports a preset's ``on`` knob requires, with the statuses that
#: count as having run: anything else means the server fell back (fused ran
#: as dense, rerank as fused, RRF as concatenation). ``fusion`` may be ``off``
#: when only one ranking had results.
REQUIRED_LANES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "lexical": (("lex", ("ok",)), ("ident", ("ok",)), ("fusion", ("ok", "off"))),
    "rerank": (("rerank", ("ok",)),),
    "merge": (("merge", ("ok",)),),
}
RETRIEVE_PREFIX = "retrieve:"
#: Presets that search the whole project, not the eval graph: ``legacy`` is
#: the receipt-4 parity arm, and only a project-wide threshold request runs
#: the core's frozen pre-ADR-093 statement, which /gw/rag/query used in B0.
PROJECT_WIDE: frozenset[str] = frozenset({"legacy"})


class PresetNotMeasured(infer.NotMeasured):
    """A stage the preset turned on did not run: the row is an error, never
    a result filed under the preset's name. Not retried (a missing migration
    or an unstaged reranker does not heal in minutes); a stage that did not
    run because of an outage that heals (``reranker unreachable
    (ReadTimeout)``) raises ``infer.TransientError`` instead and is retried."""


def unmet_stages(knobs: dict[str, Any], lanes: dict[str, Any]) -> list[str]:
    """The lanes a preset's ``on`` knobs require that did not run."""
    unmet = []
    for knob, required in REQUIRED_LANES.items():
        if knobs.get(knob) != "on":
            continue
        for lane, ran in required:
            report = lanes.get(lane) or {}
            if report.get("status") not in ran:
                unmet.append(
                    f"{lane}={report.get('status', 'missing')} ({report.get('reason', '')})"
                )
    return unmet + unmet_profile(knobs, lanes)


def unmet_profile(knobs: dict[str, Any], lanes: dict[str, Any]) -> list[str]:
    """A preset that names a profile needs the server to report it
    (``lanes.profile = {name, reader_model}``): a server that ignored it ran
    the small profile's partial cap under the large preset's name."""
    wanted = knobs.get("profile")
    if not wanted:
        return []
    report = lanes.get("profile") or {}
    got = report.get("name") if isinstance(report, dict) else None
    return [] if got == wanted else [f"profile={got or 'missing'} (asked {wanted})"]


def _retrieve(preset: str) -> Arm:
    knobs = PRESETS[preset]
    project_wide = preset in PROJECT_WIDE

    def run(stack: backends.Stack, claim: scifact.Claim) -> backends.Retrieved:
        got = stack.retrieve(claim.text, project_wide=project_wide, **knobs)
        unmet = unmet_stages(knobs, got.meta.get("lanes") or {})
        infer.raise_unmet(unmet, f"retrieve:{preset}", PresetNotMeasured)
        got.meta = {
            "preset": preset,
            "knobs": dict(knobs),
            "project_wide": project_wide,
            **got.meta,
        }
        return got

    return run


def reports_sufficiency(name: str) -> bool:
    """Whether an arm carries the server's sufficiency verdict."""
    return name.startswith(RETRIEVE_PREFIX)


def default_prompt(name: str) -> str:
    """The verdict prompt an arm reads with when ``--prompt`` is not given."""
    if reports_sufficiency(name):
        return "pack-soft"
    return "legacy" if name == "legacy" else "json"


register("closed", _closed)
register("oracle", _oracle)
register("legacy", _legacy)
register("k3-embeddings", _k3("embeddings", 20))
register("k3-all", _k3("all", 20))
register("tool:knowledge_query", _tool("knowledge_query", graphs=_eval_graph))
register("tool:rag_query", _tool("rag_query"))
for _preset in PRESETS:
    register(f"{RETRIEVE_PREFIX}{_preset}", _retrieve(_preset))
