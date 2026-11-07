"""The stand-alone stack as the eval sees it: the real r4 client, the real tools.

Every retrieval config goes through a surface a user or the agent reaches
today (or, once built, the ADR-093 one), with the scientist's identity from
an isolated r4 config. Hits are mapped back to SciFact doc ids through the
artifact's ``source_name``, read once from Postgres with ``standalone.sh
exec`` (the only store access valid in both profiles).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import metrics
import scifact
import server_probe

from r4_contracts.models import profile_for
from r4_contracts.retrieval import RetrievedPassage, RetrieveResult

# The repository the stack is deployed from (a frozen copy of the harness
# can run against it: ADR093_REPO_ROOT).
ROOT = Path(os.environ.get("ADR093_REPO_ROOT") or Path(__file__).resolve().parents[2])
STANDALONE = ROOT / "deploy" / "standalone" / "standalone.sh"
HARNESS_DIR = Path(__file__).resolve().parent  # the copy that is running
ART_RE = re.compile(r"art-[0-9a-f]{12}")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
#: A passage the harness cannot map to a SciFact doc (another artifact of the
#: project): it keeps its rank and counts in ctx_precision as a non-evidence doc.
OTHER = "other:"


def doc_or_other(doc: str | None, ref: str, position: int) -> str:
    """The SciFact doc id, else a sentinel naming the passage's artifact (its
    node when the surface names no artifact)."""
    return doc or f"{OTHER}{ref or position}"


def is_other(doc: str) -> bool:
    return str(doc).startswith(OTHER)


@dataclass
class Hit:
    doc_id: str | None
    score: float | None
    text: str
    ref: str = ""  # node or chunk id as the surface reported it
    artifact: str = ""  # the artifact the chunk belongs to, when the surface says


def hit_doc(hit: Hit, position: int) -> str:
    """A hit as a ranked doc: its SciFact doc, else ``other:<artifact>``, so
    two chunks of another artifact take one rank, as two chunks of one SciFact
    doc do (``metrics.dedup``)."""
    return doc_or_other(hit.doc_id, hit.artifact or hit.ref, position)


@dataclass
class Retrieved:
    hits: list[Hit] = field(default_factory=list)
    context: str = ""  # exactly what the model is shown for this config
    answer: str = ""  # a server-side answer, when the surface composes one
    seconds: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)
    shown: list[str] = field(default_factory=list)  # doc ids in the context
    prompt: str = "json"  # the verdict reader's prompt variant (infer.PROMPTS)
    # The passages the context shows, in its numbering: what a pack prompt
    # renders and what a cited [n] maps back to.
    passages: list[RetrievedPassage] = field(default_factory=list)
    status: str = ""  # the server's sufficiency verdict; "" when the arm has none
    # The passage texts as the arm's own context shows them, when it cuts them
    # (the oracle at 4,000 characters, K3 at 1,200); empty = ``passages`` whole.
    rendered: list[str] = field(default_factory=list)


def as_passages(hits: list[Hit]) -> list[RetrievedPassage]:
    """Hits as pack passages, labelled with the SciFact file name (trusted:
    it comes from artifact metadata, never from the passage text)."""
    out = []
    for rank, hit in enumerate(hits, start=1):
        name = scifact.doc_filename(hit.doc_id) if hit.doc_id else (hit.ref or "source")
        score = _float(hit.score)
        out.append(
            RetrievedPassage(
                rank=rank,
                source_id=hit.ref or name,
                artifact_id="",
                source_name=name,
                text=hit.text,
                scores={} if score is None else {"dense": score},
            )
        )
    return out


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


_PSQL = 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F "\t"'
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _standalone_env() -> dict[str, str]:
    # podman finds its machine connection under XDG_CONFIG_HOME, which the
    # eval points at an isolated r4 config: the engine must see the real one.
    env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
    env["STANDALONE_PROFILE"] = os.environ.get("STANDALONE_PROFILE", "dev")
    return env


def sql(query: str, **params: str) -> list[list[str]]:
    """Rows of one read-only query. ``params`` are bound as psql variables
    (``:'name'`` in the query, quoted by psql), never formatted into it."""
    if params:
        bad = [name for name in params if not _IDENT_RE.match(name)]
        if bad:
            raise ValueError(f"not a psql variable name: {bad}")
        script = f'q="$1"; shift; printf "%s\\n" "$q" | {_PSQL} -v ON_ERROR_STOP=1 "$@"'
        args = [f"--set={name}={value}" for name, value in params.items()]
    else:
        script, args = f'{_PSQL} -c "$1"', []
    proc = subprocess.run(
        [str(STANDALONE), "exec", "postgres", "sh", "-c", script, "sh", query, *args],
        env=_standalone_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    # The compose provider prefixes the first line with a colour reset.
    lines = (_ANSI_RE.sub("", line) for line in proc.stdout.splitlines())
    return [line.split("\t") for line in lines if "\t" in line]


def artifact_doc_map() -> dict[str, str]:
    """artifact id -> SciFact doc id, for every scifact-*.md artifact."""
    rows = sql(
        "SELECT artifact_id, metadata->>'source_name' FROM knowledge_artifacts "
        "WHERE metadata->>'source_name' LIKE 'scifact-%'"
    )
    return {art: doc for art, name in rows if (doc := scifact.doc_id_of(name))}


def doc_of_refs(art_to_doc: dict[str, str], *refs: Any) -> str | None:
    """The SciFact doc the first mappable ref names: an artifact id it
    contains, else a ``scifact-<id>.md`` name."""
    for ref in refs:
        text = str(ref or "")
        for art in ART_RE.findall(text):
            if art in art_to_doc:
                return art_to_doc[art]
        doc = scifact.doc_id_of(text)
        if doc:
            return doc
    return None


def accepts(method: Any, knobs: dict[str, Any]) -> bool:
    """Whether ``method`` takes every knob as a keyword."""
    try:
        params = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return True
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return True
    return all(name in params for name in knobs)


class Stack:
    """The r4 client against the stand-alone gateway, scoped to the eval graph.

    ``reader`` is the remote verdict reader (``ReaderClient``), set only when
    ``run.py infer --reader-config`` names one; None reads with ``client``.
    ``session_model`` is the model the companion tools are built for, as a
    session builds them (ADR-093 D-14: ``knowledge_query`` and
    ``search_docs`` size their cut by it); None builds them with no reader.
    """

    reader: ReaderClient | None = None
    session_model: str | None = None

    def __init__(
        self, project: str = "default", graph: str = "scifact-eval", corpus: Any = None
    ) -> None:
        from r4.client import Client

        self.client = Client()
        self.project = project
        self.graph = graph
        self.corpus = corpus  # scifact.Corpus: the oracle arm reads gold text here
        self.art_to_doc = artifact_doc_map()

    def doc_of(self, *refs: Any) -> str | None:
        return doc_of_refs(self.art_to_doc, *refs)

    # ── K3 facade lanes (GET /umrs/knowledge/query) ─────────────────────

    def k3(self, query: str, *, scope: str = "embeddings", limit: int = 20) -> Retrieved:
        started = time.monotonic()
        payload = self.client.knowledge.query(
            query, project_id=self.project, scope=scope, graphs=[self.graph], limit=limit
        )
        result = payload.get("query") or {}
        check_k3_lanes(result, scope)
        hits = [
            Hit(
                doc_id=self.doc_of(h.get("source_id"), h.get("title"), h.get("metadata")),
                score=h.get("score"),
                text=h.get("content") or "",
                ref=str(h.get("source_id") or ""),
                artifact=_artifact_of(h),
            )
            for h in result.get("results") or []
        ]
        return Retrieved(
            hits=hits,
            seconds=time.monotonic() - started,
            meta={
                "lane": result.get("embeddings_lane"),
                "rag_lane": result.get("rag_lane"),
                "total": result.get("total_results"),
            },
        )

    # ── GET /gw/rag/retrieve: the ADR-093 verb ──────────────────────────

    def retrieve(self, query: str, *, project_wide: bool = False, **knobs: Any) -> Retrieved:
        """One retrieve call with explicit knobs and the stage trace.

        Goes through ``client.rag.retrieve`` when the client has it and takes
        every knob (an r4 before ADR-093 D-14 has no ``profile`` or
        ``reader_model``), else a raw GET with the same parameters. Both wait
        up to ``RETRIEVE_TIMEOUT_S`` (the client's own default is 10 s); the
        bound used is kept in ``meta``. A client that fell back to
        ``/rag/query`` (an older server) measured the wrong surface: that is
        an error, never a silent result. ``project_wide`` sends no graph, so
        the whole project is searched (the legacy parity arm).
        """
        started = time.monotonic()
        rag = self.client.rag
        graph = "" if project_wide else self.graph
        if hasattr(rag, "retrieve") and accepts(rag.retrieve, knobs):
            result, bound = self._retrieve_call(rag.retrieve, query, graph, knobs)
        else:
            params = retrieve_params(query, self.project, graph, knobs)
            url = f"{self.client.config.gateway_url.rstrip('/')}/gw/rag/retrieve"
            result = self.client.http.get(url, params=params, timeout=RETRIEVE_TIMEOUT_S)
            bound = RETRIEVE_TIMEOUT_S
        if "fallback" in (result.get("lanes") or {}):
            raise RuntimeError("server has no /rag/retrieve: the client fell back to /rag/query")
        check_outage(result)
        got = retrieved_from_result(result, self.doc_of)
        got.seconds = time.monotonic() - started
        got.meta["retrieve_timeout_s"] = bound
        return got

    def _retrieve_call(
        self, method: Any, query: str, graph: str, knobs: dict[str, Any]
    ) -> tuple[dict[str, Any], Any]:
        """``client.rag.retrieve`` at the harness's bound when it takes one
        (``RAGClient`` caps it at the client's own timeout), and that bound."""
        extra = {"timeout": RETRIEVE_TIMEOUT_S} if accepts(method, {"timeout": 1}) else {}
        result = method(
            query,
            project_id=self.project,
            graphs=[graph] if graph else None,
            trace=True,
            **extra,
            **knobs,
        ).to_dict()
        return result, _effective_bound(self.client, extra)

    # ── /rag/query: today's answer path ─────────────────────────────────

    def rag_sources(self, query: str, *, top_k: int, threshold: float | None) -> Retrieved:
        """The passages /rag/query composed from, plus its answer. An outage
        (the server's lane report, else its own failure answer) is an error."""
        started = time.monotonic()
        response = self.client.rag.query(
            query, project_id=self.project, top_k=top_k, threshold=threshold
        )
        from infer import check_transient

        metadata = _response_metadata(response)
        check_outage(metadata)
        answer = response.answer or ""
        check_transient(answer if not response.sources else _compose_failure(answer))
        hits = [
            Hit(
                doc_id=self.doc_of(s.document_id, (s.metadata or {}).get("source_name")),
                score=s.score,
                text=s.content or "",
                ref=s.document_id,
                artifact=str((s.metadata or {}).get("artifact_id") or ""),
            )
            for s in response.sources
        ]
        return Retrieved(
            hits=hits,
            answer=response.answer,
            seconds=time.monotonic() - started,
            meta=answer_metadata(metadata),
        )

    # ── the agent's tools, exactly as the companion builds them ─────────

    def tool(self, name: str, **kwargs: Any) -> Retrieved:
        """Run one companion tool; its output string IS the context."""
        started = time.monotonic()
        output = self._tool(name)(**kwargs)
        from infer import check_tool_output

        check_tool_output(output)
        hits = _hits_in_tool_output(self, output)
        return Retrieved(hits=hits, context=output, seconds=time.monotonic() - started)

    def _tool(self, name: str):
        cache = self.__dict__.setdefault("_tools", {})
        if name not in cache:
            from r4.companion.tools import get_builtin_tools

            for tool in get_builtin_tools(self.client, session_model=self.session_model):
                cache[tool["name"]] = tool["execute"]
        return cache[name]


#: How long one retrieve call may take (the raw GET and the client method
#: alike). ``RAGClient.retrieve`` defaults to 10 s (``R4_RAG_RETRIEVE_TIMEOUT``),
#: which a rerank or large retrieve under host load can exceed.
RETRIEVE_TIMEOUT_S = 120.0


def _effective_bound(client: Any, extra: dict[str, Any]) -> Any:
    """The bound a client-method retrieve ran under: the harness's, capped at
    the client's own timeout, else the client's default."""
    if not extra:
        return "client default (R4_RAG_RETRIEVE_TIMEOUT, else 10 s)"
    ceiling = getattr(getattr(client, "config", None), "timeout", None)
    if isinstance(ceiling, (int, float)) and ceiling > 0:
        return min(RETRIEVE_TIMEOUT_S, float(ceiling))
    return RETRIEVE_TIMEOUT_S


def _artifact_of(hit: dict[str, Any]) -> str:
    meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return str(hit.get("artifact_id") or meta.get("artifact_id") or "")


def check_k3_lanes(result: dict[str, Any], scope: str) -> None:
    """A K3 answer whose lane failed is not a result of its scope: a dead
    dense lane is an outage (retried); a dead lexical lane (``rag_lane``,
    the scopes ``rag`` and ``all``) is retried when its reason names an
    outage that heals, else not measured (a missing ``search_text``)."""
    import infer

    lane = result.get("embeddings_lane") or {}
    if scope in ("embeddings", "all") and lane.get("lane") == "unavailable":
        raise infer.TransientError(f"embeddings lane unavailable: {lane.get('reason')}")
    lexical = result.get("rag_lane") or {}
    if scope in ("rag", "all") and lexical.get("status") == "unavailable":
        reason = f"lexical lane unavailable ({lexical.get('reason') or 'no reason'})"
        infer.raise_unmet([reason], f"k3 {scope}")


def answer_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """What /rag/query's post-check and profile say about one answer (an
    older server reports none of it: every value is then None)."""
    profile = (metadata.get("lanes") or {}).get("profile")
    return {
        "abstained": metadata.get("abstained"),
        "partial_answer": metadata.get("partial_answer"),
        "uncited": metadata.get("uncited"),
        "answer_model": metadata.get("model"),
        "profile": profile.get("name") if isinstance(profile, dict) else None,
    }


def retrieve_outage(result: dict[str, Any]) -> str:
    """Why a retrieve result (or /rag/query's metadata) reports an outage
    rather than a verdict, or "". The server answers 200 when the query
    encoder times out (``sufficiency.basis == "unavailable"``, the dense lane
    ``unavailable``), and a failed null lane silently turns the verdict basis
    lexical-only: neither is a measurement of the arm."""
    verdict = result.get("sufficiency") or {}
    if verdict.get("basis") == "unavailable":
        return f"sufficiency unavailable: {verdict.get('detail') or 'no reason'}"
    lanes = result.get("lanes") or {}
    for lane in ("dense", "null"):
        report = lanes.get(lane) or {}
        if report.get("status") == "unavailable":
            return f"{lane} lane unavailable: {report.get('reason') or 'no reason'}"
    return ""


def check_outage(result: dict[str, Any]) -> None:
    """Raise ``infer.TransientError`` on an outage: retried, never scored."""
    reason = retrieve_outage(result)
    if reason:
        from infer import TransientError

        raise TransientError(reason)


def _response_metadata(response: Any) -> dict[str, Any]:
    raw = getattr(response, "raw_response", None)
    meta = raw.get("metadata") if isinstance(raw, dict) else None
    return meta if isinstance(meta, dict) else {}


_COMPOSE_FAILED = "Could not generate an answer"


def _compose_failure(answer: str) -> str:
    """/rag/query's compose-failure answer (it comes with sources), else ""."""
    return answer if answer.startswith(_COMPOSE_FAILED) else ""


def retrieve_params(query: str, project: str, graph: str, knobs: dict[str, Any]) -> dict[str, Any]:
    """The query string of ``GET /gw/rag/retrieve`` for the raw-GET path: the
    same names ``RAGClient.retrieve`` sends, None-valued knobs left out."""
    params: dict[str, Any] = {"q": [query], "project_id": project, "trace": 1}
    if graph:
        params["graphs"] = graph
    params.update({k: v for k, v in knobs.items() if v is not None})
    return params


def retrieved_from_result(result: dict[str, Any], doc_of: Any) -> Retrieved:
    """A ``RetrieveResult.to_dict()`` as the harness reads it.

    ``hits`` is the pre-cut ranking (``trace.candidates``) when the server
    traced one, else the kept passages; ``passages``/``shown`` are what the
    cut kept, the context a model sees. ``meta`` keeps the verdict, lanes,
    timing, the kept passages and the sufficiency features.
    """
    parsed = RetrieveResult.from_dict(result)
    passages = list(parsed.passages)
    docs = [doc_of(p.artifact_id, p.source_name, p.source_id) for p in passages]
    got = Retrieved(
        hits=_candidate_hits(parsed, doc_of)
        or [
            Hit(d, p.scores.get("dense"), p.text, p.source_id, p.artifact_id)
            for d, p in zip(docs, passages, strict=True)
        ],
        shown=[
            doc_or_other(d, p.artifact_id or p.source_id, i)
            for i, (d, p) in enumerate(zip(docs, passages, strict=True))
        ],
        passages=passages,
        status=parsed.sufficiency.status,
        prompt="pack-soft",
    )
    got.context = "\n\n".join(
        f"[{n}] {p.source_name}\n{p.text}" for n, p in enumerate(passages, start=1)
    )
    got.meta = _result_meta(parsed, result, docs)
    return got


def _candidate_hits(parsed: RetrieveResult, doc_of: Any) -> list[Hit]:
    rows = parsed.trace.get("candidates") or []
    return [
        Hit(
            doc_id=doc_of(row.get("artifact_id"), row.get("node_id")),
            score=(row.get("scores") or {}).get("fused") or (row.get("scores") or {}).get("dense"),
            text="",
            ref=str(row.get("node_id") or ""),
            artifact=str(row.get("artifact_id") or ""),
        )
        for row in rows
        if isinstance(row, dict)
    ]


def _result_meta(parsed: RetrieveResult, raw: dict[str, Any], docs: list) -> dict[str, Any]:
    kept = []
    for doc, passage in zip(docs, parsed.passages, strict=True):
        entry = passage.to_dict()
        entry["text"] = entry["text"][:1500]
        kept.append({"doc": doc, "chars": len(passage.text), **entry})
    return {
        "sufficiency": parsed.sufficiency.to_dict(),
        "lanes": parsed.lanes,
        "timing_ms": parsed.timing_ms,
        "searched": parsed.searched,
        "space": parsed.space,
        "mode": parsed.trace.get("mode"),
        "passages": kept,
        "features": metrics.sufficiency_features(raw),
    }


def _hits_in_tool_output(stack: Stack, output: str) -> list[Hit]:
    """The docs a tool's JSON output names, in order (scored where it says)."""
    try:
        data = json.loads(output)
    except (TypeError, ValueError):
        return []
    items = data.get("results") or data.get("sources") or []
    hits = []
    for item in items:
        if not isinstance(item, dict):
            continue
        doc = stack.doc_of(*item.values())
        hits.append(Hit(doc_id=doc, score=item.get("score"), text=str(item.get("content", ""))))
    return hits


# ── the agent itself: the companion decides whether and what to retrieve ────

AGENT_PROMPT = (
    "Using the literature in my project's knowledge graph '{graph}', is this "
    "scientific claim supported, contradicted, or is there not enough "
    "information to say? Reply with one JSON object and nothing else: "
    '{{"verdict": "SUPPORT" | "CONTRADICT" | "NOT_ENOUGH_INFO", '
    '"evidence": [the source names you used]}}\n\nCLAIM: {claim}'
)


class _Recorder:
    """Wraps the client's retrieval calls so the eval sees what the agent saw.

    Every surface a companion turn retrieves through is wrapped: the K3
    facade (``knowledge.query``), ``rag.query`` and the ADR-093 verb
    ``rag.retrieve`` (``search_docs`` and the pre-retrieval router call it).
    A call made while the router decides a turn is recorded as
    ``router:rag.retrieve``, and each router decision is kept in ``routes``.
    """

    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        self.calls: list[str] = []
        self.docs: list[str] = []
        self.routes: list[dict[str, Any]] = []
        self._routing = False
        self._saved: list[tuple[Any, str, Any]] = []

    def __enter__(self) -> _Recorder:
        client = self.stack.client
        knowledge, rag = getattr(client, "knowledge", None), getattr(client, "rag", None)
        self._wrap(knowledge, "query", self._k3)
        self._wrap(rag, "query", self._rag_query)
        self._wrap(rag, "retrieve", self._rag_retrieve)
        self._wrap(_router_module(), "route_turn_decision", self._route)
        return self

    def __exit__(self, *_exc: object) -> None:
        for owner, name, original in reversed(self._saved):
            setattr(owner, name, original)
        self._saved.clear()

    def _wrap(self, owner: Any, name: str, make: Any) -> None:
        original = getattr(owner, name, None) if owner is not None else None
        if callable(original):
            self._saved.append((owner, name, original))
            setattr(owner, name, make(original))

    def _seen(self, *refs: Any) -> None:
        doc = self.stack.doc_of(*refs)
        if doc:
            self.docs.append(doc)

    def _k3(self, original: Any) -> Any:
        def k3(query, *args, **kwargs):
            payload = original(query, *args, **kwargs)
            self.calls.append("knowledge.query")
            for hit in (payload.get("query") or {}).get("results") or []:
                self._seen(hit.get("source_id"), hit.get("title"))
            return payload

        return k3

    def _rag_query(self, original: Any) -> Any:
        def rag_query(query, *args, **kwargs):
            result = original(query, *args, **kwargs)
            self.calls.append("rag.query")
            for source in getattr(result, "sources", []) or []:
                self._seen(*vars(source).values())
            return result

        return rag_query

    def _rag_retrieve(self, original: Any) -> Any:
        def retrieve(query, *args, **kwargs):
            result = original(query, *args, **kwargs)
            self.calls.append("router:rag.retrieve" if self._routing else "rag.retrieve")
            for passage in getattr(result, "passages", ()) or ():
                self._seen(passage.artifact_id, passage.source_name, passage.source_id)
            return result

        return retrieve

    def _route(self, original: Any) -> Any:
        def route(*args, **kwargs):
            self._routing = True
            try:
                decision = original(*args, **kwargs)
            finally:
                self._routing = False
            data = getattr(decision, "event_data", None)
            self.routes.append(data() if callable(data) else {"action": str(decision)})
            return decision

        return route


def _router_module() -> Any:
    """The pre-retrieval router (ADR-093 D-8), when this r4 has one."""
    try:
        from r4.companion import retrieval_router
    except ImportError:
        return None
    return retrieval_router


def agent_turn(stack: Stack, claim: str, model: str, tag: str) -> dict[str, Any]:
    """One fresh companion session, one turn, every tool pre-approved."""
    from r4.companion.permissions import PermissionMode
    from r4.companion.session import CompanionSession

    session = CompanionSession(
        session_id=f"adr093-eval-{tag}",
        name="adr093-eval",
        client=stack.client,
        model=model,
        permission_mode=PermissionMode.TRUST,
        endpoint_refresh_interval_sec=86_400,
    )
    session.permissions.set_approval_callback(lambda *_a, **_k: True)
    started = time.monotonic()
    with _Recorder(stack) as seen:
        reply = session.send(AGENT_PROMPT.format(graph=stack.graph, claim=claim))
    usage = session.usage_summary.to_dict()
    return {
        "reply": reply,
        "seconds": round(time.monotonic() - started, 2),
        "retrieval_calls": seen.calls,
        "docs_seen": seen.docs,
        "routes": seen.routes,
        "usage": {
            "prompt_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
            "completion_tokens": int(
                usage.get("output_tokens") or usage.get("completion_tokens") or 0
            ),
        },
    }


# ── the remote verdict reader (ADR-093 D-14) ─────────────────────────────────
#
# The large profile's reader effect: the passages the stand-alone stack
# retrieved, read by a large model behind another gateway (rikyu's kimi-k3 or
# glm-5.3). Built only from an explicit ``--reader-config`` directory; it
# offers ``chat_completion`` alone, so retrieval cannot go there.


class ReaderConfigError(RuntimeError):
    """The reader directory cannot read: no r4 config, no credential, or the
    stack's own gateway (that is not a remote reader)."""


#: Process-wide r4 state that belongs to one config directory (credentials,
#: the current project, the global Config). The reader swaps in its own while
#: it resolves or calls, so neither side ever sees the other's.
_R4_SINGLETONS = (
    ("r4.auth", "_auth_manager"),
    ("r4.project", "_project_manager"),
    ("r4.config", "_config"),
)


def _r4_env_keys() -> list[str]:
    return [key for key in os.environ if key.startswith("R4_")]


def _swap_singletons(values: dict[str, Any]) -> dict[str, Any]:
    """Install ``values`` as r4's singletons; return the ones they replaced."""
    import importlib

    previous = {}
    for module_name, attr in _R4_SINGLETONS:
        module = importlib.import_module(module_name)
        key = f"{module_name}.{attr}"
        previous[key] = getattr(module, attr, None)
        setattr(module, attr, values.get(key))
    return previous


def _restore_env(saved: dict[str, str | None]) -> None:
    for key in _r4_env_keys():
        os.environ.pop(key, None)
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@contextmanager
def reader_scope(config_dir: Path, state: dict[str, Any]) -> Iterator[None]:
    """r4 as a process started with ``XDG_CONFIG_HOME=<config_dir>`` and no
    ``R4_*`` variables sees it: the stand-alone run's gateway, key, profile
    and source never reach the reader, and the reader's never leave the
    scope. ``state`` keeps the reader's own singletons between scopes."""
    saved_env = {key: os.environ.get(key) for key in [*_r4_env_keys(), "XDG_CONFIG_HOME"]}
    for key in saved_env:
        os.environ.pop(key, None)
    os.environ["XDG_CONFIG_HOME"] = str(config_dir)
    outer = _swap_singletons(state)
    try:
        yield
    finally:
        state.update(_swap_singletons(outer))
        _restore_env(saved_env)


def public_url(url: str | None) -> str:
    """A URL without the parts that can carry a credential (user info, query,
    fragment): what provenance may record."""
    if not url:
        return ""
    parts = urlsplit(str(url))
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _same_gateway(a: str | None, b: str | None) -> bool:
    return bool(a and b) and public_url(a).rstrip("/") == public_url(b).rstrip("/")


def _reader_r4_client(stack_gateway: str | None) -> Any:
    """The r4 Client a reader directory configures (call inside its scope)."""
    from r4.client import Client
    from r4.config import Config

    config = Config.from_env()
    if _same_gateway(config.gateway_url, stack_gateway):
        raise ReaderConfigError(
            f"the reader config reads through the stack's own gateway "
            f"({public_url(config.gateway_url)}); use --model for a stack model"
        )
    if not _has_credential(config):
        raise ReaderConfigError("the reader config holds no credential: log in there first")
    return Client(config=config)


def _has_credential(config: Any) -> bool:
    """An API key, or a login whose token has not expired (r4 never
    refreshes an OIDC token: ``get_auth_header`` is None once it expires)."""
    from r4.auth import get_auth_manager

    return bool(config.api_key or get_auth_manager(config.profile).get_auth_header())


class ReaderClient:
    """The verdict reader on a second r4 config (e.g. one logged into the
    rikyu gateway). ``chat_completion`` is all it offers: retrieval stays on
    the stand-alone stack. ``model`` is the model every verdict asks for."""

    def __init__(
        self, config_dir: str | Path, model: str, *, stack_gateway: str | None = None
    ) -> None:
        self.config_dir = Path(config_dir).expanduser().resolve()
        if not (self.config_dir / "r4").is_dir():
            raise ReaderConfigError(
                f"no r4 config under {self.config_dir}: log in with "
                f"XDG_CONFIG_HOME={self.config_dir} r4 login first"
            )
        self.model = model
        self._state: dict[str, Any] = {}
        with self._scope():
            self._client = _reader_r4_client(stack_gateway)

    def _scope(self) -> Any:
        return reader_scope(self.config_dir, self._state)

    def _relogin(self) -> str:
        return f"log in again: XDG_CONFIG_HOME={self.config_dir} r4 login"

    def check(self) -> None:
        """The reader can still read (run before each arm): an expired login
        stops the run here, naming the command that renews it."""
        with self._scope():
            if not _has_credential(self._client.config):
                raise ReaderConfigError(f"the reader's login has expired; {self._relogin()}")

    def chat_completion(self, messages: list[Any], **kwargs: Any) -> Any:
        """One verdict call. A refused credential (401/403) is fatal for the
        run, never one claim's error row: every later call would fail too."""
        from r4.errors import AuthenticationError

        with self._scope():
            try:
                return self._client.chat_completion(messages, **kwargs)
            except AuthenticationError as exc:
                raise ReaderConfigError(
                    f"the reader gateway refused the login ({exc}); {self._relogin()}"
                ) from exc

    @property
    def gateway_url(self) -> str:
        return public_url(self._client.config.gateway_url)

    def _route(self) -> dict[str, Any] | str:
        """Where the verdict model resolves (a ``model_routes`` entry or a
        source can send it past the gateway), credentials stripped."""
        try:
            from r4.model_routing import resolve_model_route

            with self._scope():
                route = resolve_model_route(self._client.config, self.model)
        except Exception as exc:  # noqa: BLE001 — provenance never fails a run
            return _unavailable(exc)
        return {"kind": route.kind, "base_url": public_url(route.base_url)}

    def provenance(self) -> dict[str, Any]:
        """Who read the verdicts: the gateway URL and the model, never a key."""
        return {
            "kind": "remote",
            "config_dir": str(self.config_dir),
            "r4_profile": self._client.config.profile,
            "gateway_url": self.gateway_url,
            "route": self._route(),
            "model": self.model,
            "retrieval_profile": profile_for(self.model)[0],
        }


#: A run that names no reading model (``answer``): UMRS sizes /rag/query from
#: its own registry answer model, so the profile is the server's (each E-B
#: row records ``profile`` from the response's ``lanes.profile``).
SERVER_RESOLVED = "server-resolved (each row records the server's lanes.profile)"


def reader_provenance(
    reader: ReaderClient | None, model: str | None, *, reads_verdicts: bool = True
) -> dict[str, Any] | None:
    """The verdict reader: the remote one, else the stack's own model; None
    when nothing read verdicts (``retrieve``)."""
    if not reads_verdicts:
        return None
    if reader is not None:
        return reader.provenance()
    if not model:
        return {"kind": "stack", "model": None, "retrieval_profile": SERVER_RESOLVED}
    return {"kind": "stack", "model": model, "retrieval_profile": profile_for(model)[0]}


# ── provenance: what produced a result file ──────────────────────────────────
#
# Every field that cannot be read says so ("unavailable: <why>"), never {}.

UNAVAILABLE = "unavailable"


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _unavailable(exc: BaseException) -> str:
    return f"{UNAVAILABLE}: {type(exc).__name__}: {exc}"[:300]


def _exec(service: str, *argv: str, timeout: int = 60) -> str:
    """stdout of one read-only command in a stack container."""
    return subprocess.run(
        [str(STANDALONE), "exec", service, *argv],
        env=_standalone_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    ).stdout


def stack_settings(service: str = "umrs") -> dict[str, str] | str:
    """Every UMRS_RAG_* / UMRS_KNOWLEDGE_* / OLLAMA_* value a service runs with."""
    try:
        out = _exec(service, "printenv")
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable(exc)
    keep = ("UMRS_RAG_", "UMRS_KNOWLEDGE_", "OLLAMA_")
    return dict(
        line.split("=", 1) for line in out.splitlines() if "=" in line and line.startswith(keep)
    )


def store_versions() -> dict[str, str] | str:
    """Postgres and the retrieval extensions the store runs."""
    try:
        rows = sql(
            "SELECT 'postgres', current_setting('server_version') UNION ALL "
            "SELECT extname, extversion FROM pg_extension "
            "WHERE extname IN ('vector', 'pg_trgm', 'unaccent')"
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable(exc)
    return {row[0]: row[1] for row in rows if len(row) == 2}


def _git_state(*prefix: str) -> dict[str, Any]:
    """A checkout's commit, and a fingerprint of everything not in it: the
    status, the diff against HEAD (tracked files) and the content of every
    untracked file (``git diff`` leaves them out; status names them only)."""
    status = _git(*prefix, "status", "--porcelain")
    return {
        "commit": _git(*prefix, "rev-parse", "HEAD"),
        "dirty": bool(status),
        "status_sha256": _sha(status),
        "diff_sha256": _sha(_git(*prefix, "diff", "HEAD")),
        **_untracked_sha256(*prefix),
    }


#: An untracked file larger than this is fingerprinted by its size alone.
_HASH_MAX_BYTES = 64 * 1024 * 1024


def _file_bytes(path: Path) -> bytes:
    try:
        size = path.stat().st_size
        return path.read_bytes() if size <= _HASH_MAX_BYTES else f"size:{size}".encode()
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}".encode()


def _untracked_sha256(*prefix: str) -> dict[str, Any]:
    """sha256 over (path, bytes) of the checkout's untracked, not-ignored files."""
    listing = _git(*prefix, "ls-files", "--others", "--exclude-standard", "-z")
    paths = sorted(path for path in listing.split("\0") if path)
    base = ROOT / prefix[1] if prefix[:1] == ("-C",) else ROOT
    digest = hashlib.sha256()
    for rel in paths:
        digest.update(rel.encode() + b"\0" + _file_bytes(base / rel) + b"\0")
    return {"untracked_files": len(paths), "untracked_sha256": digest.hexdigest()}


def imported_packages(names: tuple[str, ...] = ("r4", "r4_contracts")) -> dict[str, Any]:
    """The r4 packages this process imported, by path and content hash: a
    frozen copy run through ``PYTHONPATH`` is not the checkout ``parent``
    describes, and the pack renderer and profile table live here."""
    import importlib

    out: dict[str, Any] = {}
    for name in names:
        try:
            module = importlib.import_module(name)
        except ImportError as exc:
            out[name] = _unavailable(exc)
            continue
        base = Path(module.__file__).resolve().parent
        out[name] = {"dir": str(base), **server_probe.files_sha256(str(base), ("**/*.py",))}
    return out


def code_provenance() -> dict[str, Any]:
    """The code a result depends on, untracked files included: the running
    harness copy, the r4 packages it imported, and the checkout's code the
    containers mount (the umrs retrieval and rag code, ``r4_contracts``), by
    content hash (``server_probe.files_sha256``: the containers hash the same
    way)."""
    umrs_dir = ROOT / "umrs" / "src" / "umrs"
    contracts_dir = ROOT / "r4-contracts" / "src" / "r4_contracts"
    harness = server_probe.files_sha256(str(HARNESS_DIR), ("*.py",))
    return {
        "parent": _git_state(),
        "umrs": _git_state("-C", "umrs"),
        "harness": {"dir": str(HARNESS_DIR), **harness},
        "imported": imported_packages(),
        "umrs_retrieval": {"dir": str(umrs_dir), **server_probe.files_sha256(str(umrs_dir))},
        "umrs_rag": {
            "dir": str(umrs_dir),
            **server_probe.files_sha256(str(umrs_dir), server_probe.RAG_CODE),
        },
        "contracts_checkout": {
            "dir": str(contracts_dir),
            **server_probe.files_sha256(str(contracts_dir), server_probe.PACKAGE_CODE),
        },
    }


def _last_json_line(text: str) -> str:
    lines = [line for line in _ANSI_RE.sub("", text).splitlines() if line.startswith("{")]
    if not lines:
        raise ValueError("the probe printed no JSON")
    return lines[-1]


def server_probe_result(ollama_url: str | None = None, role: str = "umrs") -> dict[str, Any] | str:
    """``server_probe`` run inside a container: the code it sees and when it
    started; in the umrs container also the rag service's models and the
    chat model digests (ollama sits on an internal network the host cannot
    reach). ``role`` ``rag`` probes the rag container (the query encoder)."""
    url = ollama_url or os.environ.get("ADR093_OLLAMA_URL") or server_probe.DEFAULT_OLLAMA_URL
    source = Path(server_probe.__file__).read_text()
    try:
        return json.loads(_last_json_line(_exec(role, "python", "-c", source, url, role)))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return _unavailable(exc)


def _block(probe: dict[str, Any], key: str) -> dict[str, Any]:
    value = probe.get(key)
    return value if isinstance(value, dict) else {}


def code_check(probe: dict[str, Any], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Per code set a container hashed (``code``, ``contracts``): whether it
    is the checkout's; and whether any of it changed after the container's
    PID 1 started (then it is not what runs)."""
    matches = {
        key: _block(probe, key).get("sha256") == want.get("sha256")
        for key, want in expected.items()
    }
    newest = [_block(probe, key).get("mtime_max") for key in expected]
    mtimes = [m for m in newest if isinstance(m, (int, float))]
    started = probe.get("started_at")
    timed = isinstance(started, (int, float)) and bool(mtimes)
    return {
        "started_at": started,
        "matches": matches,
        "matches_checkout": all(matches.values()),
        "edited_after_start": max(mtimes) > started if timed else None,
    }


def _container(probe: dict[str, Any], code: dict[str, Any], key: str) -> dict[str, Any]:
    expected = {"code": code[key], "contracts": code["contracts_checkout"]}
    return {
        "code_dir": probe.get("code_dir"),
        "code": probe.get("code"),
        "contracts_dir": probe.get("contracts_dir"),
        "contracts": probe.get("contracts"),
        **code_check(probe, expected),
    }


def server_summary(
    probe: dict[str, Any] | str, code: dict[str, Any], rag: dict[str, Any] | str | None = None
) -> dict[str, Any] | str:
    """Whether the umrs container (and the rag container, when probed) sees
    the checkout's code (umrs retrieval, K3 and rag code, ``r4_contracts``),
    and whether it changed after the process started. The top-level
    ``matches_checkout``/``edited_after_start`` cover both containers."""
    if not isinstance(probe, dict):
        return probe
    out = _container(probe, code, "umrs_retrieval")
    if rag is None:
        return out
    out["rag"] = _container(rag, code, "umrs_rag") if isinstance(rag, dict) else rag
    if isinstance(rag, dict):
        out["matches_checkout"] = out["matches_checkout"] and out["rag"]["matches_checkout"]
        edited = [out["edited_after_start"], out["rag"]["edited_after_start"]]
        out["edited_after_start"] = None if None in edited else any(edited)
    return out


def _digests(tags: Any) -> dict[str, str] | str:
    if not isinstance(tags, dict):
        return str(tags)
    return {str(m.get("name")): str(m.get("digest")) for m in tags.get("models") or []}


def _chat_digest(digests: dict[str, str] | str, model: str | None) -> str:
    if not isinstance(digests, dict):
        return digests
    if not model:
        return f"{UNAVAILABLE}: the server's own chat model (see ollama_models)"
    found = digests.get(model) or digests.get(f"{model}:latest")
    return found or f"{UNAVAILABLE}: {model} is not served"


_UNREPORTED = f"{UNAVAILABLE}: /rag/stats does not report it (a rag service before the switch)"


def model_provenance(probe: dict[str, Any] | str, model: str | None) -> dict[str, Any]:
    """The chat model's digest (ollama ``/api/tags``), and the embedder and
    reranker the rag service loaded (``/rag/stats``)."""
    if not isinstance(probe, dict):
        return {
            "chat_model": model,
            "chat_model_digest": probe,
            "embedder": probe,
            "reranker": probe,
        }
    digests = _digests(probe.get("ollama_tags"))
    stats = probe.get("rag_stats")
    if not isinstance(stats, dict):
        stats = {"embedding_model": str(stats), "reranker": str(stats)}
    return {
        "chat_model": model,
        "chat_model_digest": _chat_digest(digests, model),
        "ollama_models": digests,
        "embedder": {
            "model": stats.get("embedding_model"),
            "source": stats.get("embedding_model_source"),
            "dimensions": stats.get("embedding_dimensions"),
            # The query encoding in force (UMRS_RAG_QUERY_PREFIX, a rag-container
            # knob ``stack_settings`` cannot see): receipt-4 parity runs with
            # ``b0``, every later arm pairs against B0' (``fixed``).
            "query_prefix": stats.get("query_prefix", _UNREPORTED),
            "query_encoding": stats.get("query_encoding", _UNREPORTED),
        },
        # {model, revision} when staged, else "unavailable (...)" and why
        "reranker": {"model": stats.get("reranker"), "reason": stats.get("reranker_reason")},
    }


#: The corpus a result was measured on: the graph's chunk set and its vectors
#: (a re-chunk or reindex changes the counts, the newest vector time or the
#: md5 over node ids, chunk md5s and embedded-text hashes).
CORPUS_SQL = (
    "SELECT g.graph_id, count(DISTINCT n.content->>'artifact_id'), count(*), count(e.node_id), "
    "coalesce(max(e.updated_at)::text, ''), coalesce(string_agg(DISTINCT e.model_name, ','), ''), "
    "md5(string_agg(n.node_id || ':' || coalesce(n.content->>'chunk_md5', '') || ':' "
    "|| coalesce(e.content_hash, ''), ',' ORDER BY n.node_id)) "
    "FROM knowledge_graphs g "
    "JOIN knowledge_nodes n ON n.graph_id = g.graph_id AND n.content->>'kind' = 'chunk' "
    "LEFT JOIN knowledge_node_embeddings e ON e.node_id = n.node_id "
    "WHERE g.project_id = :'project' AND (g.name = :'graph' OR g.graph_id = :'graph') "
    "GROUP BY g.graph_id"
)
CORPUS_FIELDS = (
    "graph_id",
    "artifacts",
    "chunks",
    "embedded",
    "embedded_at_max",
    "embedding_models",
    "chunks_md5",
)
#: Project-wide arms (legacy, /rag/query) also search the project's other chunks.
PROJECT_SQL = (
    "SELECT count(*), count(e.node_id) FROM knowledge_nodes n "
    "LEFT JOIN knowledge_node_embeddings e ON e.node_id = n.node_id "
    "WHERE n.project_id = :'project' AND n.content->>'kind' = 'chunk'"
)


#: Whether migration 0120's lexical column exists (two columns: ``sql``
#: keeps tab-separated lines only).
SEARCH_TEXT_COLUMN_SQL = (
    "SELECT 'search_text', count(*) FROM information_schema.columns "
    "WHERE table_name = 'knowledge_nodes' AND column_name = 'search_text'"
)
#: The corpus the lexical lanes search: how many of the graph's chunks carry
#: ``search_text`` (the lane's IDF counts only those) and an md5 over it.
LEXICAL_SQL = (
    "SELECT g.graph_id, count(n.search_text), "
    "md5(coalesce(string_agg(md5(n.search_text), ',' ORDER BY n.node_id), '')) "
    "FROM knowledge_graphs g "
    "JOIN knowledge_nodes n ON n.graph_id = g.graph_id AND n.content->>'kind' = 'chunk' "
    "WHERE g.project_id = :'project' AND (g.name = :'graph' OR g.graph_id = :'graph') "
    "GROUP BY g.graph_id"
)
LEXICAL_FIELDS = ("graph_id", "indexed_chunks", "search_text_md5")


def lexical_fingerprint(project: str, graph: str) -> list[dict[str, str]] | str:
    """What the lexical presets search: a partial backfill and a full one are
    different corpora; a store without the column says so."""
    try:
        column = sql(SEARCH_TEXT_COLUMN_SQL)
        if not column or column[0][-1] == "0":
            return f"{UNAVAILABLE}: knowledge_nodes.search_text is missing (migration 0120)"
        rows = sql(LEXICAL_SQL, project=project, graph=graph)
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable(exc)
    return [dict(zip(LEXICAL_FIELDS, row, strict=False)) for row in rows]


def corpus_fingerprint(project: str, graph: str, chunk_scheme: str = "") -> dict[str, Any] | str:
    try:
        rows = sql(CORPUS_SQL, project=project, graph=graph)
        totals = sql(PROJECT_SQL, project=project)
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable(exc)
    graphs = [dict(zip(CORPUS_FIELDS, row, strict=False)) for row in rows]
    project_row = totals[0] if totals else ["", ""]
    return {
        "project": project,
        "graph": graph,
        "chunk_scheme": chunk_scheme or "unset (fixed-v0)",
        "graphs": graphs or f"{UNAVAILABLE}: no graph {graph!r} in project {project!r}",
        "search_text": lexical_fingerprint(project, graph),
        "project_chunks": project_row[0],
        "project_embedded": project_row[1] if len(project_row) > 1 else "",
    }


#: The process environment that sizes R4's retrieval, recorded with every
#: result: ``R4_RAG_*``, and the model-scale override ``R4_MODEL_SCALE``
#: (``r4.model_scale.SCALE_ENV``), which moves the tool arms' profile and K3
#: limit under the same model name (ADR-093 D-14; c23-mf-2).
R4_SIZING_ENV = ("R4_RAG_", "R4_MODEL_SCALE")


def tool_sizing(client: Any, model: str | None) -> dict[str, Any] | None:
    """How the companion tools are sized for ``model`` on ``client``, resolved
    as a session of it resolves them (ADR-093 D-14): the detected scale and
    the step that decided it (``r4.model_scale``: ``env``, ``override``, a
    probe, ``name`` ...), the retrieval profile ``search_docs`` sends, the
    K3 limit ``knowledge_query`` asks for and the estimated tokens of each
    hit it shows (C23b; ``knowledge_graph_tools.query_sizing``, resolved
    once; an unsized query's 200 characters are recorded as the 50 tokens
    they come to in ASCII text, ``UNSIZED_TOKENS``). Those two are what this
    moves in a ``tool:*`` arm: ``knowledge_query`` never sends the profile,
    and ``rag_query`` sends ``top_k`` 5 and no profile whatever the model.
    The model name alone does not fix these: ``R4_MODEL_SCALE`` or an ``r4
    model scale set`` pin moves them (c23-mf-2). None without a model; a
    failed detection is recorded, never fatal."""
    if not model:
        return None
    from r4 import model_scale
    from r4.companion.knowledge_graph_tools import query_sizing
    from r4.companion.retrieval_router import reader_fields

    try:
        scale = model_scale.detect_model_scale(model, client=client)
        profile = reader_fields(model, client=client).get("profile")
    except Exception as exc:  # noqa: BLE001 - the sizing is recorded, never fatal
        return {"error": type(exc).__name__}
    sizing = query_sizing(client, model)
    return {
        "scale": scale.size_class,
        "scale_basis": scale.basis,
        "provisional": scale.provisional,
        "profile": profile,
        "knowledge_query_limit": sizing.limit,
        "knowledge_query_tokens": sizing.tokens,
    }


def provenance(
    *,
    project: str = "default",
    graph: str = "scifact-eval",
    model: str | None = None,
    reader: ReaderClient | None = None,
    reads_verdicts: bool = True,
    tool_reader: str | None = None,
    tool_sizing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What produced a result file: the code (untracked files included), the
    server processes, the models, the corpus, the settings, the store, the
    verdict reader (a remote one by gateway URL and model, never a key;
    None when nothing read verdicts), ``tool_reader``, the model the
    companion tools were sized for (``Stack.session_model``), and
    ``tool_sizing``, what that model resolved to (:func:`tool_sizing`; None
    when no ``tool:*`` arm ran). ``r4_rag_env`` holds ``R4_SIZING_ENV``."""
    code = code_provenance()
    settings = stack_settings()
    probe = server_probe_result()
    rag_probe = server_probe_result(role="rag")
    scheme = settings.get("UMRS_KNOWLEDGE_CHUNK_SCHEME", "") if isinstance(settings, dict) else ""
    return {
        # the flat keys B0 files carry (dirty now counts untracked files)
        "parent_commit": code["parent"]["commit"],
        "parent_dirty": code["parent"]["dirty"],
        "umrs_commit": code["umrs"]["commit"],
        "umrs_dirty": code["umrs"]["dirty"],
        "code": code,
        "server": server_summary(probe, code, rag_probe),
        "models": model_provenance(probe, model),
        "corpus": corpus_fingerprint(project, graph, scheme),
        "settings": settings,
        "models_settings": stack_settings("models"),
        "store": store_versions(),
        "r4_rag_env": {k: v for k, v in os.environ.items() if k.startswith(R4_SIZING_ENV)},
        "reader": reader_provenance(reader, model, reads_verdicts=reads_verdicts),
        "tool_reader": tool_reader,
        "tool_sizing": tool_sizing,
    }
