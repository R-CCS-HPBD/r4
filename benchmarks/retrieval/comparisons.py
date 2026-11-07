"""Comparing result files (ADR-093 C0): ``compare``, ``parity``, ``repro``, ``headroom``,
``sufficiency``.

Every comparison pairs rows on claim ids and says what it left out
(``completeness``), which factors the files did not hold fixed (``factors``:
reader, prompt, budget, threshold, query encoding, corpus, the K3 limit a
sized tool arm sent, ...) and which metric definitions differ between the
files' schema versions (``definitions``). A B0 file (schema 1, written by
the harness B0 ran with) still compares against a new one on the fields both
carry; every mismatch is reported and warned about on stderr, never silently
compared. ``run.py rescore`` rewrites an old file's verdict and citation
fields under the current definitions.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import metrics
import scifact

NEI = "NOT_ENOUGH_INFO"
#: The record schema this harness writes. B0's harness wrote schema 1 (its
#: records carry no ``schema_version``).
SCHEMA_VERSION = 2
Pairs = list[tuple[dict[str, Any], dict[str, Any]]]


def has_evidence(row: dict[str, Any]) -> bool:
    return row["label"] != NEI


def macro_f1_rows(rows: list[dict[str, Any]]) -> float:
    return metrics.macro_f1(
        [r["label"] for r in rows], [r["verdict"] for r in rows], scifact.LABELS
    )


def load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def _ok_rows(record: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {r["claim"]: r for r in record.get("rows") or [] if "error" not in r}


def _errors(rows: list[dict[str, Any]]) -> int:
    return sum("error" in r for r in rows)


# ── sufficiency ──────────────────────────────────────────────────────────────


def group(row: dict[str, Any]) -> str:
    """``nonsense`` (out-of-KB probes), ``evidence`` (in-KB S/C) or ``nei``."""
    if scifact.is_nonsense(row["claim"]):
        return "nonsense"
    return "nei" if row["label"] == NEI else "evidence"


def sufficiency_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Insufficient rates per group against the gates, plus feature AUROCs.
    An errored row counts in its group's ``errors``; the gates are judged
    with errored claims counted as failures (``metrics.sufficiency_report``)."""
    statuses: dict[str, list[str]] = defaultdict(list)
    features: dict[str, list[dict]] = defaultdict(list)
    errors: Counter = Counter()
    for row in rows:
        if "error" in row:
            errors[group(row)] += 1
        elif "sufficiency" in row:
            statuses[group(row)].append(row["sufficiency"])
            features[group(row)].append(row.get("features") or {})
    judged = [s for values in statuses.values() for s in values]
    return {
        **metrics.sufficiency_report(statuses, dict(errors)),
        "status_counts": dict(Counter(judged)),
        "features": metrics.feature_aurocs(features),
    }


def _pooled_row(row: dict[str, Any], held: dict[str, Any] | None) -> bool:
    """Whether ``row`` replaces what the pool holds for its claim: a judged
    row always does; an errored one only fills an empty slot."""
    if "error" in row:
        return held is None
    return "sufficiency" in row


def cmd_sufficiency(args: Any) -> int:
    """Pool the rows of several result files (e.g. dev + nonsense runs of one
    ``retrieve:*`` arm) and report the sufficiency gates and feature AUROCs.
    Errored claims stay in the pool: each gate reports them and counts them
    against itself."""
    pooled: dict[tuple[str, int], dict[str, Any]] = {}
    arm_names = set()
    for path in args.files:
        record = load(path)
        arm_names.add(record.get("arm"))
        for row in record.get("rows") or []:
            key = (record.get("claims_set") or "", row["claim"])
            if _pooled_row(row, pooled.get(key)):
                pooled[key] = row
    if len(arm_names) > 1:
        print(f"warning: pooling different arms {sorted(map(str, arm_names))}", file=sys.stderr)
    report = {"files": list(args.files), "arms": sorted(map(str, arm_names))}
    report.update(sufficiency_block(list(pooled.values())))
    _warn_gate_errors(report)
    print(json.dumps(report, indent=1))
    return 0


def _warn_gate_errors(report: dict[str, Any]) -> None:
    errored = {k: v["errors"] for k, v in report.items() if isinstance(v, dict) and v.get("errors")}
    if errored:
        print(f"warning: errored claims counted against the gates: {errored}", file=sys.stderr)


# ── what a comparison holds fixed, and what its files define differently ──────

#: What changed from schema 1 (B0's harness) to 2, by the record field it
#: touches. A comparison reports the entries whose fields it reads.
DEFINITION_CHANGES: dict[str, str] = {
    "ranked": (
        "v2 keeps another artifact's passage at its rank as other:<artifact> "
        "(v1 dropped it): recall@k, MRR and nDCG of project-wide arms"
    ),
    "shown": (
        "v2 keeps another artifact's shown passage as other:<artifact> (v1 "
        "dropped it): ctx_precision, passages and parity of project-wide arms"
    ),
    "verdict": (
        "v2 reads a verdict only from a final answer: a reply cut at max_tokens "
        "(finish_reason length) or with no answer is an error row (v1 scored "
        "it); its prose scan needs exactly one label word with no negation "
        "before it (v1: the first label word found)"
    ),
    "cited": (
        "v2 keeps a ref that names nothing shown as phantom:<ref> (v1 dropped "
        "it), maps a bare SciFact id of a shown doc to that doc, and reads a "
        "passage number only from a ref that is one ([2], 'passage 2', '1, 2')"
    ),
    "abstained": (
        "v2 takes /rag/query's metadata.abstained when the server reports it "
        "(v1: the phrase anywhere in the answer; abstained_phrase keeps that rule)"
    ),
    "answer_cited": "v2 counts each label of a grouped citation [1, 2] (v1: single [n] only)",
}
#: The fields each kind of comparison reads.
_COMPARE_READS = {
    "retrieve": ("ranked", "shown"),
    "infer": ("ranked", "shown", "verdict"),
    "answer": ("abstained", "answer_cited"),
}


def schema_version(record: dict[str, Any]) -> int:
    return int(record.get("schema_version") or 1)


def field_versions(record: dict[str, Any]) -> dict[str, int]:
    """The definition each field of a record follows: its schema's, except
    the fields a rescore could not rewrite (``rescore.v1_fields``)."""
    version = schema_version(record)
    kept = set((record.get("rescore") or {}).get("v1_fields") or ())
    return {key: 1 if key in kept else version for key in DEFINITION_CHANGES}


def definitions(records: dict[str, dict[str, Any]], reads: tuple[str, ...]) -> dict[str, Any]:
    """``{"definitions": ...}`` when the files define a field the comparison
    reads differently, else {}."""
    versions = {name: field_versions(record) for name, record in records.items()}
    changed = {
        key: DEFINITION_CHANGES[key]
        for key in reads
        if len({v[key] for v in versions.values()}) > 1
    }
    if not changed:
        return {}
    schemas = {name: schema_version(record) for name, record in records.items()}
    return {"definitions": {"schema_versions": schemas, "changed": changed}}


RECORDED, INFERRED, UNRECORDED = "recorded", "inferred", "unrecorded"


def _v1_default(key: str, v1_value: Any) -> Callable[[dict[str, Any]], tuple[Any, str]]:
    """A record field B0's harness did not write but held fixed at ``v1_value``."""

    def get(record: dict[str, Any]) -> tuple[Any, str]:
        if key in record:
            return record[key], RECORDED
        if schema_version(record) == 1:
            return v1_value, INFERRED
        return None, UNRECORDED

    return get


def _key(key: str) -> Callable[[dict[str, Any]], tuple[Any, str]]:
    def get(record: dict[str, Any]) -> tuple[Any, str]:
        return (record[key], RECORDED) if key in record else (None, UNRECORDED)

    return get


def _measured(value: Any) -> tuple[Any, str]:
    if value is None or str(value).startswith("unavailable"):
        return None, UNRECORDED
    return value, RECORDED


def _threshold(record: dict[str, Any]) -> tuple[Any, str]:
    """/rag/query's threshold: B0's ``answer`` sent 0.7 by default, and its
    files record no threshold (and carry no ``-t0.7``)."""
    if "threshold" in record:
        return record["threshold"], RECORDED
    if schema_version(record) == 1 and record.get("kind") == "answer":
        return 0.7, INFERRED
    return None, UNRECORDED


def _prompt(record: dict[str, Any]) -> tuple[Any, str]:
    """The verdict prompt every row read with (each arm's own by default)."""
    rows = [r for r in record.get("rows") or [] if "error" not in r]
    prompts = sorted({str(r["prompt"]) for r in rows if r.get("prompt")})
    if prompts:
        return ",".join(prompts), RECORDED
    return record.get("prompt"), RECORDED if record.get("prompt") else UNRECORDED


#: The query encoding every file written before schema 2 ran under: B0's
#: server sent ``passage: query: <q>`` (fixed 2026-10-10), which
#: ``UMRS_RAG_QUERY_PREFIX=b0`` reproduces for receipt 4's parity run.
V1_QUERY_PREFIX = "b0"


def _query_prefix(record: dict[str, Any]) -> tuple[Any, str]:
    """The query encoding the rag service ran (``/rag/stats``); a B0 file,
    which could not record it, ran B0's."""
    models = (record.get("provenance") or {}).get("models")
    embedder = models.get("embedder") if isinstance(models, dict) else None
    read = _measured(embedder.get("query_prefix") if isinstance(embedder, dict) else None)
    if read[1] == UNRECORDED and schema_version(record) == 1:
        return V1_QUERY_PREFIX, INFERRED
    return read


def _chunks_md5(record: dict[str, Any]) -> tuple[Any, str]:
    corpus = (record.get("provenance") or {}).get("corpus")
    graphs = corpus.get("graphs") if isinstance(corpus, dict) else None
    first = graphs[0] if isinstance(graphs, list) and graphs else {}
    return _measured(first.get("chunks_md5") if isinstance(first, dict) else None)


#: How the ``tool:*`` arms ran before C23 (wave D, 2026-10-11): the harness
#: built the companion's tools with no reader, so no tool named a profile
#: (the site's default applied, which no file records) and ``knowledge_query``
#: asked K3 for a fixed 10 and showed 200 characters of each hit
#: (``knowledge_graph_tools.UNSIZED_LIMIT``, ``UNSIZED_CHARS``). Since C23b a
#: hit's budget is counted in the pack estimator's tokens, and those 200
#: characters read as the 50 tokens they come to in ASCII text
#: (``UNSIZED_TOKENS``, 4 characters a token): exact for SciFact, which is
#: English (a CJK passage's 200 characters would be about 200), and what an
#: unsized run records today, so the two compare alike.
UNSIZED_TOOLS: dict[str, Any] = {
    "profile": None,
    "knowledge_query_limit": 10,
    "knowledge_query_tokens": 50,
}
#: ``tool_sizing`` keys added after C23, with the value a file that recorded
#: its sizing before the key existed ran at: before C23b (2026-10-11) every
#: hit's content was cut to 200 characters (50 tokens) whatever the reader.
SIZING_ADDED: dict[str, Any] = {"knowledge_query_tokens": 50}
#: Keys an interim build recorded in place of a ``SIZING_ADDED`` one, which a
#: file carrying them did NOT run at the added key's inferred value: C23b
#: before its review (2026-10-11) shared the pack in characters
#: (``knowledge_query_chars``, 1,200 for the small reader), and how many
#: tokens those came to depends on the passages' script, so the file's
#: tokens are unrecorded and compare and parity warn, never read as 50.
SIZING_INTERIM: dict[str, str] = {"knowledge_query_chars": "knowledge_query_tokens"}
#: The ``tool_sizing`` keys that size what each tool arm SENDS or shows, so
#: the ones a comparison of its runs holds fixed (ADR-093 D-14, C23, C23b).
#: ``knowledge_query`` sends K3 only its ``limit`` and shows each hit within
#: its ``tokens``: the reader's profile picks both and is never sent.
#: ``tool:rag_query`` has no entry: it sends ``top_k`` 5 and no profile
#: whatever its reader. An arm whose tool sends the profile (``search_docs``)
#: would list ``"profile"``.
SIZED_TOOL_ARMS: dict[str, tuple[str, ...]] = {
    "tool:knowledge_query": ("knowledge_query_limit", "knowledge_query_tokens"),
}


def _tool_sizing(record: dict[str, Any]) -> tuple[Any, str]:
    """What sized a tool arm's calls (ADR-093 D-14, C23): the
    ``provenance.tool_sizing`` keys its tool sends (``SIZED_TOOL_ARMS``; the
    scale, its basis and an unsent profile decide nothing beyond them). A
    file written before C23 records no ``tool_sizing`` and ran unsized; one
    written before C23b records the limit but not the tokens of a hit, which
    were 200 characters, 50 tokens (``SIZING_ADDED``, inferred); one whose
    sizing failed or is None, or that an interim build recorded in characters
    (``SIZING_INTERIM``), says nothing."""
    sent = SIZED_TOOL_ARMS.get(str(record.get("arm") or ""), ())
    provenance = record.get("provenance") or {}
    if "tool_sizing" not in provenance:
        return {key: UNSIZED_TOOLS[key] for key in sent}, INFERRED
    return _recorded_sizing(provenance["tool_sizing"], sent)


def _recorded_sizing(sizing: Any, sent: tuple[str, ...]) -> tuple[Any, str]:
    """The ``sent`` keys of a recorded ``tool_sizing``. A key added after
    the file was written (``SIZING_ADDED``) reads as inferred at the value
    the file then ran at, unless an interim key stood in for it
    (``SIZING_INTERIM``); any other absent key leaves it unrecorded."""
    if not isinstance(sizing, dict):
        return None, UNRECORDED
    absent = set(sent) - sizing.keys()
    interim = {SIZING_INTERIM[key] for key in sizing.keys() & SIZING_INTERIM.keys()}
    if not absent <= SIZING_ADDED.keys() - interim:
        return None, UNRECORDED
    values = {key: sizing.get(key, SIZING_ADDED.get(key)) for key in sent}
    return values, INFERRED if absent else RECORDED


#: How each factor is read from a record (and what B0's harness held it at).
FACTORS: dict[str, Callable[[dict[str, Any]], tuple[Any, str]]] = {
    "claims_set": _key("claims_set"),
    "model": _key("model"),
    "reader_model": _v1_default("reader_model", None),
    "prompt": _prompt,
    "order": _v1_default("order", None),
    "scorer_gate": _v1_default("scorer_gate", False),
    "verdict_max_tokens": _v1_default("verdict_max_tokens", 200),
    "threshold": _threshold,
    "query_prefix": _query_prefix,
    "chunks_md5": _chunks_md5,
    "tool_sizing": _tool_sizing,
}
#: The factors a paired comparison of each kind assumes fixed (the arm, the
#: factor under test, is named by ``a``/``b`` instead).
KIND_FACTORS = {
    "retrieve": ("claims_set", "query_prefix", "chunks_md5"),
    "infer": (
        "claims_set",
        "model",
        "reader_model",
        "prompt",
        "order",
        "scorer_gate",
        "verdict_max_tokens",
        "query_prefix",
        "chunks_md5",
    ),
    "answer": ("claims_set", "threshold", "query_prefix", "chunks_md5"),
}
#: The factors a comparison also assumes fixed when EVERY file ran an arm
#: that carries them: a tool arm whose calls its reader sizes (C23,
#: ``SIZED_TOOL_ARMS``). Against any other arm (``tool:rag_query``,
#: ``retrieve:*``, the agent, the closed/oracle brackets) the other side made
#: no call the sizing moved, so there is nothing to hold fixed.
ARM_FACTORS: dict[str, tuple[str, ...]] = dict.fromkeys(SIZED_TOOL_ARMS, ("tool_sizing",))


def _arm_factor_names(records: dict[str, dict[str, Any]]) -> list[str]:
    carried = [ARM_FACTORS.get(str(r.get("arm") or ""), ()) for r in records.values()]
    first = carried[0] if carried else ()
    return [name for name in first if all(name in names for names in carried)]


def _factor_names(records: dict[str, dict[str, Any]]) -> list[str]:
    kinds = sorted({str(r.get("kind") or "infer") for r in records.values()})
    names = [n for kind in kinds for n in KIND_FACTORS.get(kind, KIND_FACTORS["infer"])]
    return list(dict.fromkeys(names + _arm_factor_names(records)))


def _fingerprint(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def factor_report(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The factors the files did not hold fixed (``differ``), could not show
    (``unrecorded``: an older file) or show only by the harness's default
    (``inferred``: a B0 file's threshold, budget and reader, a pre-C23 tool
    arm's sizing, a pre-C23b one's hit tokens)."""
    out: dict[str, dict] = {"differ": {}, "unrecorded": {}, "inferred": {}}
    for name in _factor_names(records):
        _record_factor(out, name, {label: FACTORS[name](rec) for label, rec in records.items()})
    return {key: value for key, value in out.items() if value}


def _split_reads(read: dict[str, tuple[Any, str]]) -> dict[str, dict[str, Any]]:
    """The files' values of one factor, by how each was read."""
    by_how: dict[str, dict[str, Any]] = {RECORDED: {}, INFERRED: {}, UNRECORDED: {}}
    for label, (value, how) in read.items():
        by_how[how][label] = value
    return by_how


def _record_factor(out: dict[str, dict], name: str, read: dict[str, tuple[Any, str]]) -> None:
    """File one factor under ``differ``, ``unrecorded`` and ``inferred``."""
    by_how = _split_reads(read)
    known = {label: value for label, (value, how) in read.items() if how != UNRECORDED}
    if len({_fingerprint(v) for v in known.values()}) > 1:
        out["differ"][name] = known
    if by_how[UNRECORDED]:
        out["unrecorded"][name] = list(by_how[UNRECORDED])
    if by_how[INFERRED]:
        out["inferred"][name] = by_how[INFERRED]


def checks(records: dict[str, dict[str, Any]], reads: tuple[str, ...]) -> dict[str, Any]:
    """The factor report and any definition mismatch, for a comparison."""
    return {"factors": factor_report(records), **definitions(records, reads)}


def warn(report: dict[str, Any]) -> None:
    """Every mismatch a comparison found, on stderr."""
    factors = report.get("factors") or {}
    for key, what in (("differ", "factors differ"), ("unrecorded", "factors not recorded")):
        if factors.get(key):
            print(f"warning: {what}: {json.dumps(factors[key])}", file=sys.stderr)
    if report.get("definitions"):
        changed = sorted(report["definitions"]["changed"])
        print(
            f"warning: the files define {changed} differently "
            f"(schemas {report['definitions']['schema_versions']}); see definitions, "
            "or rescore the older file (run.py rescore)",
            file=sys.stderr,
        )
    if report.get("complete") is False:
        print(
            f"warning: incomplete pairing (errors {report.get('errors')}, "
            f"unpaired {report.get('unpaired')}); see accuracy_itt",
            file=sys.stderr,
        )


# ── completeness ─────────────────────────────────────────────────────────────


def _any_row(_row: dict[str, Any]) -> bool:
    return True


def _scored(row: dict[str, Any]) -> bool:
    """A row ``compare`` pairs something of (a retrieval or context block, a
    verdict, an answer); an NEI row of a retrieval run carries none."""
    return any(key in row for key in ("retrieval", "context", "verdict", "abstained"))


def _split_unpaired(rows: list[dict[str, Any]], scored: Callable) -> tuple[int, int]:
    counted = sum(1 for r in rows if "error" in r or scored(r))
    return counted, len(rows) - counted


def completeness(
    rows_a: list[dict[str, Any]],
    rows_b: list[dict[str, Any]],
    *,
    scored: Callable[[dict[str, Any]], bool] = _any_row,
) -> dict[str, Any]:
    """What a paired comparison leaves out: each side's errored claims and the
    claims only one side ran. ``complete`` is False when anything the
    comparison reads was left out (an arm that fails its hardest claims must
    not look better for it); claims only one side ran that carry nothing it
    reads (``scored`` False) are counted apart as ``unpaired_unscored``."""
    by_a = {r["claim"]: r for r in rows_a}
    by_b = {r["claim"]: r for r in rows_b}
    errors = {"a": _errors(rows_a), "b": _errors(rows_b)}
    only_a = _split_unpaired([by_a[c] for c in by_a.keys() - by_b.keys()], scored)
    only_b = _split_unpaired([by_b[c] for c in by_b.keys() - by_a.keys()], scored)
    unpaired = {"only_in_a": only_a[0], "only_in_b": only_b[0]}
    out: dict[str, Any] = {
        "errors": errors,
        "unpaired": unpaired,
        "complete": not (any(errors.values()) or any(unpaired.values())),
    }
    if only_a[1] or only_b[1]:
        out["unpaired_unscored"] = {"only_in_a": only_a[1], "only_in_b": only_b[1]}
    return out


# ── compare ──────────────────────────────────────────────────────────────────


def cmd_compare(args: Any) -> int:
    a, b = load(args.a), load(args.b)
    rows_a, rows_b = _ok_rows(a), _ok_rows(b)
    shared = sorted(set(rows_a) & set(rows_b))
    report: dict[str, Any] = {"a": a.get("arm"), "b": b.get("arm"), "claims": len(shared)}
    report.update(completeness(a.get("rows") or [], b.get("rows") or [], scored=_scored))
    pairs = [(rows_a[c], rows_b[c]) for c in shared]
    report.update(_paired_blocks(pairs))
    report.update(_paired_rows(pairs))
    report.update(_paired_outcomes(pairs, a.get("rows") or [], b.get("rows") or []))
    report.update(checks({"a": a, "b": b}, compare_reads(a, b)))
    warn(report)
    print(json.dumps(report, indent=1))
    return 0


def compare_reads(*records: dict[str, Any]) -> tuple[str, ...]:
    """The record fields a comparison of these files reads (by their kinds)."""
    reads = [k for record in records for k in _COMPARE_READS.get(record.get("kind"), ())]
    return tuple(dict.fromkeys(reads))


def _paired_outcomes(pairs: Pairs, rows_a: list[dict], rows_b: list[dict]) -> dict[str, Any]:
    """The verdict receipts (E-A) and the answer receipts (E-B) the pairs carry."""
    out: dict[str, Any] = {}
    if _judged(pairs):
        out.update(_paired_judged(pairs))
        out["accuracy_itt"] = itt_accuracy(rows_a, rows_b)
    if _answered(pairs):
        out.update(paired_answers(pairs))
    return out


def _paired_blocks(pairs: Pairs) -> dict[str, Any]:
    out = {}
    for block, keys in (
        ("retrieval", ("recall@1", "recall@5", "mrr@10", "ndcg@10")),
        ("context", ("ctx_precision", "ctx_recall", "passages")),
    ):
        for key in keys:
            scored = [(x[block][key], y[block][key]) for x, y in pairs if block in x and block in y]
            if scored:
                out[key] = metrics.paired_delta([x for x, _ in scored], [y for _, y in scored])
    return out


def _paired_rows(pairs: Pairs) -> dict[str, Any]:
    """Paired deltas of the per-row context measures both runs recorded."""
    out = {}
    for key in ("context_tokens", "rationale_coverage"):
        scored = [(x[key], y[key]) for x, y in pairs if key in x and key in y]
        if scored:
            out[key] = metrics.paired_delta([x for x, _ in scored], [y for _, y in scored])
    return out


def _paired_verdicts(pairs: Pairs) -> dict[str, Any]:
    hits = [(x["verdict"] == x["label"], y["verdict"] == y["label"]) for x, y in pairs]
    delta = metrics.paired_delta([float(x) for x, _ in hits], [float(y) for _, y in hits])
    return {**delta, "non_inferior": metrics.non_inferior(delta), "mcnemar": metrics.mcnemar(hits)}


def _paired_rate(
    pairs: Pairs, keep: Callable, hit: Callable, *, lower_is_better: bool = True
) -> dict[str, Any]:
    """A conditional rate, paired over the claims ``keep(a_row, b_row)``
    selects (a failure rate unless ``lower_is_better`` is False);
    ``ci_excludes_zero`` is the receipt."""
    chosen = [(x, y) for x, y in pairs if keep(x, y)]
    if not chosen:  # undefined, never "no change"
        return {
            "delta": None,
            "ci95": None,
            "p_not_better": None,
            "n": 0,
            "ci_excludes_zero": False,
        }
    delta = metrics.paired_delta(
        [float(bool(hit(x))) for x, _ in chosen],
        [float(bool(hit(y))) for _, y in chosen],
        lower_is_better=lower_is_better,
    )
    low, high = delta["ci95"]
    return {**delta, "n": len(chosen), "ci_excludes_zero": high < 0 or low > 0}


def _paired_judged(pairs: Pairs) -> dict[str, Any]:
    """The verdict receipts: accuracy (McNemar, non-inferiority), macro-F1
    (bootstrapped, non-inferiority at 3 points), wrong_abstain_when_shown
    over claims whose evidence both arms showed, unsupported_verdict over
    claims without evidence."""
    f1 = metrics.paired_statistic([x for x, _ in pairs], [y for _, y in pairs], macro_f1_rows)
    return {
        "accuracy": _paired_verdicts(pairs),
        "macro_f1": {**f1, "non_inferior": metrics.non_inferior(f1)},
        "wrong_abstain_when_shown": _paired_rate(
            pairs,
            lambda x, y: has_evidence(x) and x["gold_in_context"] and y["gold_in_context"],
            lambda r: r["verdict"] == NEI,
        ),
        "unsupported_verdict": _paired_rate(
            pairs, lambda x, _y: not has_evidence(x), lambda r: r["verdict"] != NEI
        ),
    }


def _right(row: dict[str, Any]) -> float:
    """A correct verdict; an errored claim is wrong (intention to treat)."""
    return float("error" not in row and row.get("verdict") == row["label"])


def itt_accuracy(rows_a: list[dict[str, Any]], rows_b: list[dict[str, Any]]) -> dict[str, Any]:
    """Accuracy over every claim both files hold, an errored claim scored wrong
    (intention to treat): an arm cannot gain by failing hard claims."""
    a = {r["claim"]: r for r in rows_a}
    b = {r["claim"]: r for r in rows_b}
    shared = sorted(set(a) & set(b))
    delta = metrics.paired_delta([_right(a[c]) for c in shared], [_right(b[c]) for c in shared])
    return {"claims": len(shared), **delta}


def _judged(pairs: Pairs) -> bool:
    return bool(pairs) and "verdict" in pairs[0][0] and "verdict" in pairs[0][1]


def _answered(pairs: Pairs) -> bool:
    return bool(pairs) and "abstained" in pairs[0][0] and "abstained" in pairs[0][1]


def _abstain_rule(pairs: Pairs) -> str:
    """``server`` when every row of both files carries /rag/query's own flag,
    else ``phrase`` (the rule a B0 file used, kept as ``abstained_phrase``)."""
    flagged = all(isinstance(r.get("abstained_server"), bool) for pair in pairs for r in pair)
    return "server" if flagged else "phrase"


def _abstained_by(rule: str) -> Callable[[dict[str, Any]], bool]:
    if rule == "server":
        return lambda r: bool(r["abstained"])
    return lambda r: bool(r.get("abstained_phrase", r["abstained"]))


def paired_answers(pairs: Pairs) -> dict[str, Any]:
    """The E-B receipts, paired: abstention on evidence claims (lower is
    better; also over claims whose evidence both answers were shown), on NEI
    claims (higher is better) and citing the evidence (higher is better).
    Both files are read with one abstention rule (``abstained_rule``)."""
    rule = _abstain_rule(pairs)
    abstained = _abstained_by(rule)

    def shown(x: dict[str, Any], y: dict[str, Any]) -> bool:
        return has_evidence(x) and x["gold_in_context"] and y["gold_in_context"]

    return {
        "abstained_rule": rule,
        "abstain_rate_evidence": _paired_rate(pairs, lambda x, _y: has_evidence(x), abstained),
        "abstain_rate_when_shown": _paired_rate(pairs, shown, abstained),
        "abstain_rate_nei": _paired_rate(
            pairs, lambda x, _y: not has_evidence(x), abstained, lower_is_better=False
        ),
        "cites_evidence_rate": _paired_rate(
            pairs,
            lambda x, _y: has_evidence(x),
            lambda r: r["cited_evidence"],
            lower_is_better=False,
        ),
    }


# ── parity ───────────────────────────────────────────────────────────────────


def _parity_ids(x: dict[str, Any], y: dict[str, Any]) -> tuple[list, list, str]:
    """Node ids in context order when both rows recorded them, else doc ids
    (files written before ``shown_refs`` keep only docs)."""
    if "shown_refs" in x and "shown_refs" in y:
        return list(x["shown_refs"]), list(y["shown_refs"]), "refs"
    return list(x.get("shown") or []), list(y.get("shown") or []), "docs"


def _without_other(ids: list) -> list:
    return [i for i in ids if not str(i).startswith("other:")]


def parity_outcome(x: dict[str, Any], y: dict[str, Any]) -> tuple[str, str]:
    """``(basis, identical | undecidable | mismatch)``. On the docs basis a
    file written before v2 never recorded another artifact's passage, so a
    claim that matches only once ``other:`` entries are dropped is
    undecidable, never a mismatch nor identical."""
    ids_a, ids_b, how = _parity_ids(x, y)
    if ids_a == ids_b:
        return how, "identical"
    kept_a, kept_b = _without_other(ids_a), _without_other(ids_b)
    if how == "docs" and kept_a == kept_b:
        return how, "undecidable"
    return how, "mismatch"


def cmd_parity(args: Any) -> int:
    """Receipt 4 / S1 legacy parity: identical ids in identical order per claim."""
    a, b = load(args.a), load(args.b)
    rows_a, rows_b = _ok_rows(a), _ok_rows(b)
    shared = sorted(set(rows_a) & set(rows_b))
    outcomes, basis, mismatches = Counter(), Counter(), []
    for claim in shared:
        how, outcome = parity_outcome(rows_a[claim], rows_b[claim])
        basis[how] += 1
        outcomes[outcome] += 1
        if outcome != "identical" and len(mismatches) < 10:
            ids_a, ids_b, _how = _parity_ids(rows_a[claim], rows_b[claim])
            mismatches.append({"claim": claim, "outcome": outcome, "a": ids_a, "b": ids_b})
    report = {
        "a": a.get("arm"),
        "b": b.get("arm"),
        "claims": len(shared),
        "identical": outcomes["identical"],
        "undecidable": outcomes["undecidable"],
        "basis": dict(basis),
        **completeness(a.get("rows") or [], b.get("rows") or []),
        "mismatches": mismatches,
        **checks({"a": a, "b": b}, ("shown",)),
    }
    warn(report)
    print(json.dumps(report, indent=1))
    return 0


# ── repro: does a run reproduce itself? ──────────────────────────────────────

#: Per-claim fields two runs of one configuration are compared on.
_REPRO_FIELDS = ("verdict", "reply", "shown_refs", "cited")


def _same(x: dict[str, Any], y: dict[str, Any], key: str) -> bool | None:
    """Whether both rows hold ``key`` with one value; None when either lacks it."""
    if key not in x or key not in y:
        return None
    return x[key] == y[key]


def _agreement(a: dict, b: dict, shared: list[int], key: str) -> dict[str, Any]:
    """``same_<key>``: claims whose two rows agree on ``key`` (None when no
    pair recorded it), and ``same_<key>_n``: the pairs that did."""
    compared = [s for s in (_same(a[c], b[c], key) for c in shared) if s is not None]
    return {f"same_{key}": sum(compared) if compared else None, f"same_{key}_n": len(compared)}


def reproduction(rows_a: list[dict[str, Any]], rows_b: list[dict[str, Any]]) -> dict[str, Any]:
    """Two runs of one configuration (ADR-093 S9: the reader at temperature
    0, twice), claim by claim: how many claims both judged give the same
    verdict, the same reply, the same shown passages and the same citations.
    ``reproduces`` holds when every claim both judged gives the same verdict
    and neither run left a claim out (an error, or a claim one run lacks)."""
    a, b = _ok_rows({"rows": rows_a}), _ok_rows({"rows": rows_b})
    shared = sorted(set(a) & set(b))
    out: dict[str, Any] = {"claims": len(shared)}
    for key in _REPRO_FIELDS:
        out.update(_agreement(a, b, shared, key))
    differ = [c for c in shared if _same(a[c], b[c], "verdict") is False]
    out["verdict_agreement"] = (len(shared) - len(differ)) / len(shared) if shared else None
    out["verdict_differs"] = differ[:50]
    out.update(completeness(rows_a, rows_b))
    out["reproduces"] = bool(shared) and not differ and out["complete"]
    return out


def cmd_repro(args: Any) -> int:
    """S9: whether two runs of one configuration reproduce each other."""
    a, b = load(args.a), load(args.b)
    report = {"a": a.get("arm"), "b": b.get("arm")}
    report.update(reproduction(a.get("rows") or [], b.get("rows") or []))
    report.update(checks({"a": a, "b": b}, compare_reads(a, b)))
    warn(report)
    print(json.dumps(report, indent=1))
    return 0


# ── headroom ─────────────────────────────────────────────────────────────────


def _accuracy(rows: list[dict[str, Any]]) -> float:
    return metrics.mean([float(r["verdict"] == r["label"]) for r in rows])


def headroom_used(arm: float, closed: float, oracle: float) -> float | None:
    """(arm - closed) / (oracle - closed); None when the brackets coincide."""
    span = oracle - closed
    return None if abs(span) < 1e-12 else (arm - closed) / span


def headroom_completeness(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Per file: its errored claims and the claims the other files ran that
    it did not. ``complete`` is False when any file left one out."""
    claims = {name: {r["claim"] for r in rec.get("rows") or []} for name, rec in records.items()}
    every = set().union(*claims.values())
    files = {
        name: {"errors": _errors(rec.get("rows") or []), "missing": len(every - claims[name])}
        for name, rec in records.items()
    }
    return {
        "files": files,
        "complete": not any(f["errors"] or f["missing"] for f in files.values()),
    }


def itt_headroom(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Accuracy and headroom over every claim all three files hold, an
    errored claim scored wrong: an arm cannot gain by failing hard claims."""
    rows = {name: {r["claim"]: r for r in rec.get("rows") or []} for name, rec in records.items()}
    shared = sorted(set.intersection(*(set(by) for by in rows.values())))
    arm, closed, oracle = (metrics.mean([_right(by[c]) for c in shared]) for by in rows.values())
    return {
        "claims": len(shared),
        "arm": arm,
        "closed": closed,
        "oracle": oracle,
        "headroom_used": headroom_used(arm, closed, oracle),
    }


def cmd_headroom(args: Any) -> int:
    """C0 EFFECT: how much of the closed-book to oracle gap an arm closes, on
    the claims all three files judged, and (``accuracy_itt``) on every claim
    all three hold with errors scored wrong. The three files should share a
    reader, prompt and claim set: ``factors`` says where they do not."""
    records = {name: load(getattr(args, name)) for name in ("arm", "closed", "oracle")}
    keyed = [_ok_rows(record) for record in records.values()]
    shared = sorted(set.intersection(*(set(rows) for rows in keyed)))
    report: dict[str, Any] = {
        "arms": [r.get("arm") for r in records.values()],
        "claims": len(shared),
    }
    for name, stat in (("accuracy", _accuracy), ("macro_f1", macro_f1_rows)):
        arm, closed, oracle = (stat([rows[c] for c in shared]) for rows in keyed)
        report[name] = {
            "arm": arm,
            "closed": closed,
            "oracle": oracle,
            "headroom_used": headroom_used(arm, closed, oracle),
        }
    report["completeness"] = headroom_completeness(records)
    report["accuracy_itt"] = itt_headroom(records)
    report.update(checks(records, ("verdict",)))
    warn(report)
    if not report["completeness"]["complete"]:
        files = report["completeness"]["files"]
        print(f"warning: incomplete brackets {files}; see accuracy_itt", file=sys.stderr)
    print(json.dumps(report, indent=1))
    return 0
