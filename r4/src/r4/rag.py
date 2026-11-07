"""
R4 RAG Client.

Retrieval-Augmented Generation interface for the R4 SDK.

Separation of concerns:
- DataClient: File upload/download (UMRS ZeroMQ)
- RAGClient: Embedding and semantic search (HTTP)
- WorkflowClient: n8n pipeline triggers (HTTP)
"""

from __future__ import annotations

import json
import logging
import math
import operator
import os
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import quote

from r4_contracts.models import PROFILE_OVERRIDES
from r4_contracts.retrieval import RetrievalSufficiency, RetrievedPassage, RetrieveResult

from .errors import APIError, R4Error

if TYPE_CHECKING:
    from .client import Client
    from .knowledge import ArtifactDeleteResult

logger = logging.getLogger(__name__)

#: ``GET /gw/rag/retrieve`` takes 1-12 passages and 1-3 query variants (ADR-093 D-2).
RETRIEVE_K_MAX = 12
RETRIEVE_MAX_VARIANTS = 3
#: How long one retrieve (or passage fetch) may wait. The verb makes no chat
#: call (ADR-093 C12 budgets it at ~0.2-0.5 s), so it never inherits the
#: client's R4_TIMEOUT (300 s, sized for generation): a hung UMRS must not
#: stall every routed turn. ``R4_RAG_RETRIEVE_TIMEOUT`` overrides the default;
#: the bound never exceeds R4_TIMEOUT.
RETRIEVE_TIMEOUT_ENV = "R4_RAG_RETRIEVE_TIMEOUT"
RETRIEVE_TIMEOUT_SEC = 10.0
#: Sources a fallback ``/gw/rag/query`` call asks for: the retrieve verb's default k_max.
_FALLBACK_TOP_K = 4
_FALLBACK_LANE = {"status": "ok", "reason": "server has no /rag/retrieve"}
#: ``r4 chat --knowledge`` exports the bundle's path here, so the companion it
#: launches (the console's bridge process too) searches the same bundle.
KNOWLEDGE_BUNDLE_ENV = "R4_KNOWLEDGE_BUNDLE"
#: The offline reader (ADR-093 D-12): BM25 over chunk text, plus a dense lane
#: only for a caller's query vector in the bundle's space; reciprocal rank
#: fusion (k = 60) over each lane's top ``pool`` chunks; no relevance cut and
#: no sufficiency verdict (no calibrated null travels with a bundle).
_LOCAL_RRF_K = 60
_LOCAL_POOL = 50
_LOCAL_POOL_MIN, _LOCAL_POOL_MAX = 10, 200
_LANE_SCORE_KEYS = {"lex": "lexical", "dense": "dense"}
#: Retrieve options the offline reader applies; any other one set is reported
#: in ``lanes["local_bundle"]["unapplied"]`` (the bundle has no graph ids, no
#: calibrated null and no reranker).
_OFFLINE_APPLIED = ("k_max", "pool")
_OFFLINE_DETAIL = "local bundle: no calibrated null travels with it, so no verdict"
_TOKEN_RE = re.compile(r"\w+")
#: Which score a reader is shown for a passage, first present wins.
_SCORE_ORDER = ("rerank_p", "dense", "fused", "lexical")
#: Source-name keys in a RAG source's metadata, most trusted first.
_SOURCE_NAME_KEYS = ("source_name", "filename", "source", "s3_object")


def passage_score(passage: RetrievedPassage) -> float:
    """The score shown for one passage: the reranker's probability when it ran,
    else the dense cosine, else the fused or lexical score.

    Display only. Which passages are relevant is UMRS's decision (ADR-093 D-3,
    CONSTITUTION §1.3); a consumer never re-cuts on this value.
    """
    for key in _SCORE_ORDER:
        if key in passage.scores:
            return float(passage.scores[key])
    return 0.0


# ============================================================================
# Shared context-injection helper
# ============================================================================


def inject_context_into_messages(
    messages: List[Dict[str, str]],
    sources: List[Any],
    *,
    max_context_tokens: int = 2000,
    context_prefix: str = "\n\n---\nRelevant context:\n",
    context_suffix: str = "\n---\n\n",
) -> List[Dict[str, str]]:
    """Inject RAG context from *sources* into *messages*.

    The caller is responsible for obtaining ``sources`` (platform or
    local bundle) — this function only builds the context block and
    injects it into the system message.

    Args:
        messages: List of chat messages.
        sources: List of source objects with ``.content`` and ``.metadata``
            attributes (e.g. :class:`RAGSource`).
        max_context_tokens: Approximate token budget for injected context.
        context_prefix: Text prepended to the context block.
        context_suffix: Text appended to the context block.

    Returns:
        Modified messages with RAG context injected into the system message.
    """
    if not messages or not sources:
        return messages

    context_parts = _build_context_parts(sources, max_context_tokens)
    if not context_parts:
        return messages

    context_block = context_prefix + "\n".join(context_parts) + context_suffix

    # Add source references.
    sources_info = "Sources: " + ", ".join(
        s.metadata.get("source", f"doc-{i}") for i, s in enumerate(sources[: len(context_parts)], 1)
    )
    context_block += sources_info

    return _inject_context_block(messages, context_block)


def _build_context_parts(sources: List[Any], max_context_tokens: int) -> List[str]:
    """Build context snippets from *sources* within the token budget."""
    context_parts: List[str] = []
    current_tokens = 0
    chars_per_token = 4  # Rough approximation

    for i, source in enumerate(sources, 1):
        source_text = f"[{i}] {source.content}"
        source_tokens = len(source_text) // chars_per_token

        if current_tokens + source_tokens > max_context_tokens:
            remaining_tokens = max_context_tokens - current_tokens
            if remaining_tokens > 50:  # Only include if meaningful space
                truncated = source_text[: remaining_tokens * chars_per_token]
                context_parts.append(truncated + "...")
            break

        context_parts.append(source_text)
        current_tokens += source_tokens

    return context_parts


def _inject_context_block(
    messages: List[Dict[str, str]], context_block: str
) -> List[Dict[str, str]]:
    """Merge *context_block* into the first system message, or prepend one."""
    modified_messages: List[Dict[str, str]] = []
    system_found = False

    for msg in messages:
        if msg.get("role") == "system" and not system_found:
            modified_messages.append(
                {
                    "role": "system",
                    "content": msg.get("content", "") + context_block,
                }
            )
            system_found = True
        else:
            modified_messages.append(msg)

    if not system_found:
        modified_messages.insert(
            0,
            {
                "role": "system",
                "content": f"You are a helpful assistant.{context_block}",
            },
        )

    return modified_messages


@dataclass
class RAGQuery:
    """A RAG query request.

    ``threshold`` is the legacy absolute similarity cut. It defaults to None and
    is then omitted, so the server decides relevance (ADR-093 D-3, D-10).
    """

    query: str
    project_id: str
    top_k: int = 5
    threshold: Optional[float] = None
    include_sources: bool = True
    filters: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API format."""
        d: Dict[str, Any] = {
            "query": self.query,
            "project_id": self.project_id,
            "top_k": self.top_k,
            "include_sources": self.include_sources,
        }
        if self.threshold is not None:
            d["threshold"] = self.threshold
        if self.filters:
            d["filters"] = self.filters
        return d


@dataclass
class RAGSource:
    """A source document from RAG retrieval."""

    document_id: str
    content: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def source_name(self) -> str:
        """The artifact's name from server metadata, else the document id."""
        meta = self.metadata or {}
        for key in _SOURCE_NAME_KEYS:
            if meta.get(key):
                return str(meta[key])
        return self.document_id or ""

    @property
    def section(self) -> str:
        return str((self.metadata or {}).get("section") or "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_name": self.source_name,
            "section": self.section,
            "content": self.content,
            "score": self.score,
            "metadata": dict(self.metadata or {}),
        }


@dataclass
class RAGResponse:
    """Response from a RAG query."""

    answer: str
    sources: List[RAGSource] = field(default_factory=list)
    usage: Optional[Dict[str, int]] = None
    raw_response: Optional[Dict[str, Any]] = None

    @property
    def documents(self) -> List[RAGSource]:
        """Read-only alias of :attr:`sources`, for consumers that read ``documents``."""
        return self.sources

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "RAGResponse":
        """Create from API response."""
        sources = []
        for src in data.get("sources", []):
            sources.append(
                RAGSource(
                    document_id=src.get("document_id", ""),
                    content=src.get("content", ""),
                    score=src.get("score", 0.0),
                    metadata=src.get("metadata", {}),
                )
            )

        return cls(
            answer=data.get("answer", ""),
            sources=sources,
            usage=data.get("usage"),
            raw_response=data,
        )


@dataclass
class IndexResult:
    """Result of indexing a document.

    Deprecated (ADR-027): retained for import compatibility only; see the
    RAGClient.index() retirement note.
    """

    document_id: str
    status: str  # "indexed", "pending", "error"
    chunks_created: int = 0
    error: Optional[str] = None


@dataclass
class UploadResult:
    """Result of uploading a document."""

    document_id: str
    filename: str
    status: str  # "completed", "pending", "failed"
    chunks: int = 0
    error: Optional[str] = None


# ============================================================================
# The retrieve verb (ADR-093 D-2): request building and legacy fallback shapes
# ============================================================================


def _query_variants(query: str | Sequence[str]) -> List[str]:
    """1-3 non-empty query strings; the first is the user's own wording."""
    raw = [query] if isinstance(query, str) else list(query)
    variants = [str(text).strip() for text in raw if str(text).strip()]
    if not variants:
        raise R4Error("a non-empty query is required for RAG retrieval")
    if len(variants) > RETRIEVE_MAX_VARIANTS:
        raise R4Error(f"at most {RETRIEVE_MAX_VARIANTS} query variants per retrieve call")
    return variants


def _param_value(key: str, value: Any) -> Any:
    """Wire form of one retrieve parameter: graph lists as csv, k_max in 1-12."""
    if key in ("graphs", "exclude_graphs") and not isinstance(value, str):
        return ",".join(str(item) for item in value)
    if key == "k_max":
        return min(max(int(value), 1), RETRIEVE_K_MAX)
    return value


#: The retrieve route's bound on ``reader_model`` (umrs ``api/rag_retrieve.py``
#: ``READER_MODEL_CHARS``); a longer name is clamped, never refused with a 422.
READER_MODEL_MAX_CHARS = 200


def _reader_options(profile: str | None, reader_model: str | None) -> Dict[str, Any]:
    """``profile`` and ``reader_model`` for the platform verb (ADR-093 D-14).

    They size the server's cut and pool for the model that will read the
    passages; a local bundle applies no profile, so they are kept apart from
    the retrieve options (and from its ``unapplied`` report). Unset → omitted.
    """
    name = str(profile or "").strip().lower()
    if name and name not in PROFILE_OVERRIDES:
        raise R4Error(f"profile must be one of {', '.join(PROFILE_OVERRIDES)}; got {profile!r}")
    reader = str(reader_model or "").strip()[:READER_MODEL_MAX_CHARS].strip()
    return {"profile": name or None, "reader_model": reader or None}


def _retrieve_params(
    variants: List[str], project_id: str, options: Dict[str, Any], trace: bool
) -> Dict[str, Any]:
    """Query parameters for ``GET /gw/rag/retrieve``; unset options are omitted."""
    params: Dict[str, Any] = {"q": variants, "project_id": project_id}
    for key, value in options.items():
        wire = None if value is None else _param_value(key, value)
        if wire is not None and wire != "":
            params[key] = wire
    if trace:
        params["trace"] = 1
    return params


def _env_retrieve_timeout() -> float:
    raw = os.environ.get(RETRIEVE_TIMEOUT_ENV, "").strip()
    try:
        value = float(raw) if raw else RETRIEVE_TIMEOUT_SEC
    except ValueError:
        logger.warning(
            "%s=%r is not a number; using %s s", RETRIEVE_TIMEOUT_ENV, raw, RETRIEVE_TIMEOUT_SEC
        )
        return RETRIEVE_TIMEOUT_SEC
    return value if value > 0 else RETRIEVE_TIMEOUT_SEC


def _retrieve_timeout(config: Any, timeout: float | None) -> float:
    """Seconds one retrieve or passage fetch may wait: the caller's bound, else
    ``R4_RAG_RETRIEVE_TIMEOUT``, else 10 s; never above the client's timeout."""
    bound = float(timeout) if timeout is not None and timeout > 0 else _env_retrieve_timeout()
    ceiling = getattr(config, "timeout", None)
    if isinstance(ceiling, (int, float)) and ceiling > 0:
        return min(bound, float(ceiling))
    return bound


def _raise_if_stopped(turn_cancel: Any) -> None:
    """A stopped turn is the user's stop, not a failed call: the http layer's
    ``TurnCancelledError`` (not an ``R4Error``) must reach the send path as
    the token's own cancellation, never wrapped as a retrieval failure."""
    if turn_cancel is not None:
        turn_cancel.check()


def _passage_from_source(rank: int, source: RAGSource, lane: str) -> RetrievedPassage:
    meta = source.metadata or {}
    return RetrievedPassage(
        rank=rank,
        source_id=source.document_id,
        artifact_id=str(meta.get("artifact_id") or ""),
        source_name=source.source_name,
        text=source.content,
        section=source.section,
        text_source=str(meta.get("text_source") or "artifact"),
        scores={"dense": float(source.score or 0.0)},
        lanes=(lane,),
    )


def _result_from_sources(
    query: str,
    sources: List[RAGSource],
    *,
    lane: str,
    lane_report: Dict[str, Any],
    sufficiency: RetrievalSufficiency,
) -> RetrieveResult:
    """A :class:`RetrieveResult` over legacy ``RAGSource`` rows, in their order."""
    passages = tuple(
        _passage_from_source(rank, source, lane) for rank, source in enumerate(sources, start=1)
    )
    space = str((sources[0].metadata or {}).get("model_name") or "") if sources else ""
    return RetrieveResult(
        query=query,
        passages=passages,
        sufficiency=sufficiency,
        searched=len(passages),
        space=space,
        lanes={lane: dict(lane_report)},
    )


def _fallback_sufficiency(response: RAGResponse) -> RetrievalSufficiency:
    """What an older ``/rag/query`` can say: nothing passed its absolute cut, or
    something did, which is not a calibrated verdict (``unknown``)."""
    if response.sources:
        return RetrievalSufficiency(
            status="unknown",
            basis="threshold",
            detail="older server: legacy /rag/query similarity filter, no sufficiency",
        )
    return RetrievalSufficiency(
        status="insufficient", basis="threshold", detail=(response.answer or "")[:300]
    )


# ============================================================================
# Local (bundle-backed) retrieval helpers
# ============================================================================


class _SimpleBM25:
    """Okapi BM25 over chunk text (each chunk's ``content``), no external deps.

    An inverted index (term -> postings), so a query costs the postings of its
    terms rather than a pass over every chunk. :meth:`score` returns only the
    chunks that share a query term, best first.
    """

    def __init__(self, chunks: List[Dict[str, Any]], k1: float = 1.5, b: float = 0.75):
        self._k1 = k1
        self._b = b
        self._n = len(chunks)
        self._doc_len: List[int] = []
        self._postings: Dict[str, List[tuple[int, int]]] = {}
        self._avgdl = 0.0
        self._build_index(chunks)

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return _TOKEN_RE.findall(text.lower())

    def _build_index(self, chunks: List[Dict[str, Any]]) -> None:
        for idx, chunk in enumerate(chunks):
            # A chunk's C9 header is searched with it, as the server's lexical lane does.
            text = f"{chunk.get('header') or ''} {chunk.get('content') or ''}"
            tokens = self._tokenize(text)
            self._doc_len.append(len(tokens))
            for token, tf in Counter(tokens).items():
                self._postings.setdefault(token, []).append((idx, tf))
        self._avgdl = sum(self._doc_len) / self._n if self._n else 0.0

    def score(self, query: str, top_k: int = 10) -> List[tuple[int, float]]:
        """``(chunk index, score)`` of the best ``top_k`` chunks sharing a
        query term (each distinct term counted once), ties in bundle order."""
        scores: Dict[int, float] = {}
        for token in dict.fromkeys(self._tokenize(query)):
            self._add_term(token, scores)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:top_k]

    def _add_term(self, token: str, scores: Dict[int, float]) -> None:
        postings = self._postings.get(token, [])
        df = len(postings)
        idf = math.log((self._n - df + 0.5) / (df + 0.5) + 1.0)
        avgdl = max(self._avgdl, 1.0)
        for idx, tf in postings:
            norm = self._k1 * (1 - self._b + self._b * self._doc_len[idx] / avgdl)
            scores[idx] = scores.get(idx, 0.0) + idf * tf * (self._k1 + 1) / (tf + norm)


class _SimpleVectorSearch:
    """Exact cosine over the bundle's chunk vectors, each keyed by its chunk
    (node) id. R4 never embeds: the query vector is the caller's, and a row
    of another dimension never matches it."""

    def __init__(self, embeddings: Iterable[Dict[str, Any]]):
        self.keys: List[str] = []
        self._vectors: List[Sequence[float]] = []
        self._norms: List[float] = []
        for row in embeddings:
            key = str(row.get("node_id") or row.get("chunk_id") or "")
            self._add(key, row.get("embedding") or row.get("vector") or [])

    @classmethod
    def from_vectors(cls, vectors: Mapping[str, Sequence[float]]) -> "_SimpleVectorSearch":
        """An index over ``{chunk id: vector}`` (the bundle reader's output)."""
        search = cls([])
        for key, vector in vectors.items():
            search._add(key, vector)
        return search

    def _add(self, key: str, vector: Sequence[float]) -> None:
        self.keys.append(key)
        self._vectors.append(vector)
        self._norms.append(math.sqrt(sum(x * x for x in vector)))

    @property
    def size(self) -> int:
        return len(self._vectors)

    @property
    def dimension(self) -> int:
        return len(self._vectors[0]) if self._vectors else 0

    def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int = 10,
        threshold: float = 0.0,
    ) -> List[tuple[int, float]]:
        """Return ``(row index, cosine)`` pairs, best first, at or above ``threshold``."""
        if not self._vectors or not query_embedding:
            return []
        ranked = sorted(self._cosines(query_embedding), key=lambda item: (-item[1], item[0]))
        return [(idx, s) for idx, s in ranked if s >= threshold][:top_k]

    def search_keys(
        self, query_embedding: Sequence[float], *, top_k: int
    ) -> List[tuple[str, float]]:
        """``(chunk id, cosine)`` of the ``top_k`` nearest rows, no cut."""
        hits = self.search(query_embedding, top_k=top_k, threshold=-math.inf)
        return [(self.keys[idx], score) for idx, score in hits]

    def _cosines(self, query: Sequence[float]) -> Iterator[tuple[int, float]]:
        q_norm = math.sqrt(sum(x * x for x in query))
        for idx, vector in enumerate(self._vectors):
            norm = self._norms[idx] * q_norm
            if len(vector) == len(query) and norm > 0:
                yield idx, sum(map(operator.mul, vector, query)) / norm


@dataclass
class _LocalHit:
    """One chunk of the loaded bundle after reciprocal rank fusion."""

    index: int
    fused: float = 0.0
    scores: Dict[str, float] = field(default_factory=dict)
    lanes: List[str] = field(default_factory=list)

    def add(self, lane: str, rank: int, score: float) -> None:
        self.fused += 1.0 / (_LOCAL_RRF_K + rank)
        key = _LANE_SCORE_KEYS.get(lane, lane)
        self.scores[key] = max(score, self.scores.get(key, score))
        if lane not in self.lanes:
            self.lanes.append(lane)

    def score_map(self) -> Dict[str, float]:
        return {"fused": self.fused, **self.scores}


def _rrf_fuse(runs: List[tuple[str, List[tuple[int, float]]]]) -> List[_LocalHit]:
    """Reciprocal rank fusion (k = 60) of ranked ``(chunk index, score)``
    runs, one per lane and query variant; best first, ties in bundle order."""
    hits: Dict[int, _LocalHit] = {}
    for lane, run in runs:
        for rank, (index, score) in enumerate(run, start=1):
            hits.setdefault(index, _LocalHit(index)).add(lane, rank, score)
    return sorted(hits.values(), key=lambda hit: (-hit.fused, hit.index))


def _chunk_key(chunk: Dict[str, Any]) -> str:
    """A bundle chunk's id: its node id (v1.2/v1.3), else its legacy chunk id."""
    return str(chunk.get("node_id") or chunk.get("chunk_id") or "")


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _as_span(value: Any) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    return None


def _local_source(chunk: Dict[str, Any], hit: _LocalHit) -> RAGSource:
    """A bundle chunk as a legacy :class:`RAGSource`: its score is the fused
    (RRF) score; each lane's score rides ``metadata["scores"]``."""
    metadata = dict(chunk.get("metadata") or {})
    metadata.update(scores=hit.score_map(), lanes=list(hit.lanes), backend="local_bundle")
    return RAGSource(
        document_id=str(chunk.get("document_id") or ""),
        content=str(chunk.get("content") or ""),
        score=hit.fused,
        metadata=metadata,
    )


def _local_passage(rank: int, chunk: Dict[str, Any], hit: _LocalHit) -> RetrievedPassage:
    """A bundle chunk as a retrieve passage: the node id is its handle, the
    label is the artifact's (never passage text), ``text_source`` says where
    the text came from (an ``excerpt`` is only the first 500 characters)."""
    source = _local_source(chunk, hit)
    return RetrievedPassage(
        rank=rank,
        source_id=_chunk_key(chunk) or source.document_id,
        artifact_id=str(source.metadata.get("artifact_id") or ""),
        source_name=source.source_name,
        text=source.content,
        section=source.section,
        chunk_index=_as_int(chunk.get("chunk_index")),
        chunk_count=_as_int(chunk.get("chunk_count")),
        span=_as_span(chunk.get("span")),
        text_source=str(chunk.get("text_source") or "node"),
        scores=hit.score_map(),
        lanes=tuple(hit.lanes),
    )


def _local_k_max(k_max: Any) -> int:
    """Passages an offline retrieve returns: k_max clamped to 1-12, default 4."""
    return min(max(int(k_max), 1), RETRIEVE_K_MAX) if k_max else _FALLBACK_TOP_K


def _local_pool(pool: Any) -> int:
    """Chunks each offline lane ranks before fusion (10-200, default 50)."""
    return min(max(int(pool), _LOCAL_POOL_MIN), _LOCAL_POOL_MAX) if pool else _LOCAL_POOL


def _offline_lane(options: Dict[str, Any], chunks: int, version: str) -> Dict[str, Any]:
    """``lanes["local_bundle"]``: the backend, and the options it did not apply."""
    report: Dict[str, Any] = {
        "status": "ok",
        "reason": "local knowledge bundle (offline)",
        "n": chunks,
        "version": version,
    }
    unapplied = sorted(k for k, v in options.items() if v is not None and k not in _OFFLINE_APPLIED)
    if unapplied:
        report["unapplied"] = unapplied
    return report


def _lexical_report(
    bm25: Optional[_SimpleBM25], runs: List[tuple[str, List[tuple[int, float]]]]
) -> Dict[str, Any]:
    if bm25 is None:
        return {"status": "unavailable", "reason": "the bundle has no chunk text"}
    return {"status": "ok", "n": len({index for _lane, run in runs for index, _score in run})}


def _dense_report(
    search: Optional[_SimpleVectorSearch], query_vector: Optional[Sequence[float]], space: str
) -> Optional[Dict[str, Any]]:
    """Why the offline dense lane does not run, or None when it can."""
    if search is None or not search.size:
        return {"status": "unavailable", "reason": "the bundle carries no chunk vectors"}
    if not query_vector:
        return {
            "status": "off",
            "reason": (
                "no query vector: R4 loads no embedding model; a caller may pass one "
                f"in the bundle's space ({space})"
            ),
        }
    if len(query_vector) != search.dimension:
        return {
            "status": "unavailable",
            "reason": (
                f"the query vector has {len(query_vector)} dimensions; the bundle's "
                f"space ({space}) has {search.dimension}"
            ),
        }
    return None


class RAGClient:
    """
    RAG (Retrieval-Augmented Generation) client.

    Access via Client.rag:
        client = Client()
        response = client.rag.query("What does the data show?", project_id="my-proj")

    Supports two backends:
    - **Platform** (default): queries the Gateway → UMRS RAG service
    - **Local bundle**: loads a ``.kb`` knowledge bundle and searches locally

    Switch backends with :meth:`load_bundle` / :meth:`unload_bundle`.

    Features:
    - Retrieve ranked passages with the server's sufficiency verdict
      (:meth:`retrieve`, :meth:`fetch_passage`; ADR-093 D-2)
    - Query with vector retrieval (semantic search) and a composed answer
    - Index documents for embedding
    - Manage document collections

    Note: For file upload/download, use client.data instead.
    """

    def __init__(self, parent: "Client"):
        """Initialize RAG client."""
        self._parent = parent
        # Local bundle state
        self._bundle_chunks: List[Dict[str, Any]] = []
        self._bundle_manifest: Dict[str, Any] = {}
        self._bundle_space = ""
        self._local_bm25: Optional[_SimpleBM25] = None
        self._local_vector_search: Optional[_SimpleVectorSearch] = None
        # R4_KNOWLEDGE_BUNDLE is read once, on the first local-bundle check.
        self._bundle_env_checked = False

    @property
    def _config(self):
        """Get config from parent."""
        return self._parent.config

    @property
    def _api_prefix(self) -> str:
        """'/gw' via the edge, '/umrs' for direct UMRS (parent Client's probe;
        duck-typed test doubles default to /gw — workflows.py twin)."""
        return getattr(self._parent, "_api_prefix", "/gw")

    # =========================================================================
    # Bundle management (local backend)
    # =========================================================================

    def load_bundle(self, bundle_path: Path) -> "RAGClient":
        """Load a knowledge bundle for local (offline) RAG retrieval.

        After loading, :meth:`query` and :meth:`retrieve` search the bundle
        instead of the platform Gateway (ADR-093 D-12). Every chunk node of a
        v1.2/v1.3 bundle loads, its text the v1.3 node text, else the
        md5-verified raw slice, else the excerpt; chunk vectors load keyed by
        node id, in the bundle's one embedding space.

        Args:
            bundle_path: A .kb file, a .kb.tar.gz archive or a bundle directory.

        Returns:
            self (for chaining)
        """
        from .knowledge_bundle import KnowledgeBundleReader

        reader = KnowledgeBundleReader(Path(bundle_path))
        try:
            manifest = reader.read_manifest().to_dict()
            chunks = reader.read_chunks()
            space, vectors = reader.chunk_vectors(_chunk_key(chunk) for chunk in chunks)
        finally:
            reader.cleanup()
        self._bundle_env_checked = True
        self._bundle_chunks = chunks
        self._bundle_manifest = manifest
        self._bundle_space = space
        self._local_bm25 = _SimpleBM25(chunks) if chunks else None
        self._local_vector_search = _SimpleVectorSearch.from_vectors(vectors) if vectors else None
        logger.info(
            "Loaded knowledge bundle: %d chunks, %d chunk vectors (space %s)",
            len(chunks),
            len(vectors),
            space or "-",
        )
        return self

    def unload_bundle(self) -> None:
        """Unload the knowledge bundle and revert to platform backend."""
        self._bundle_chunks = []
        self._bundle_manifest = {}
        self._bundle_space = ""
        self._local_bm25 = None
        self._local_vector_search = None
        self._bundle_env_checked = True  # R4_KNOWLEDGE_BUNDLE does not undo an unload
        logger.info("Unloaded knowledge bundle")

    @property
    def has_local_bundle(self) -> bool:
        """Whether a local knowledge bundle is the backend: any accepted
        version (v1.2 and v1.3 included), even one with no chunk, so a loaded
        bundle never silently falls back to the platform.

        The first check also loads the bundle ``R4_KNOWLEDGE_BUNDLE`` names:
        ``r4 chat --knowledge`` sets it for the companion it launches, the
        console's bridge process included.
        """
        if not self._bundle_env_checked:
            self._load_bundle_from_env()
        return bool(self._bundle_chunks or self._bundle_manifest)

    def _load_bundle_from_env(self) -> None:
        self._bundle_env_checked = True
        path = os.environ.get(KNOWLEDGE_BUNDLE_ENV, "").strip()
        if not path:
            return
        try:
            self.load_bundle(Path(path))
        except Exception as exc:  # noqa: BLE001 - the platform stays the backend
            logger.warning("%s=%s was not loaded: %s", KNOWLEDGE_BUNDLE_ENV, path, exc)

    def local_bundle_summary(self) -> Dict[str, Any]:
        """What the loaded bundle offers offline: version and project, chunk
        count, where chunk text came from (``node``/``artifact``/``excerpt``,
        ``none`` for a chunk without text), and the chunk vectors' space."""
        search = self._local_vector_search
        text_sources = Counter(
            str(chunk.get("text_source", "node") or "none") for chunk in self._bundle_chunks
        )
        return {
            "version": str(self._bundle_manifest.get("version") or ""),
            "project_id": str(self._bundle_manifest.get("project_id") or ""),
            "chunks": len(self._bundle_chunks),
            "text_sources": dict(text_sources),
            "vectors": search.size if search is not None else 0,
            "space": self._bundle_space,
        }

    def retrieve(
        self,
        query: str | Sequence[str],
        *,
        project_id: str | None = None,
        graphs: Sequence[str] | None = None,
        exclude_graphs: Sequence[str] | None = None,
        k_max: int | None = None,
        pool: int | None = None,
        per_artifact: int | None = None,
        lexical: str | None = None,
        rerank: str | None = None,
        merge: str | None = None,
        cutoff: str | None = None,
        threshold: float | None = None,
        trace: bool = False,
        timeout: float | None = None,
        profile: str | None = None,
        reader_model: str | None = None,
        _turn_cancel: Any = None,
    ) -> RetrieveResult:
        """The passages UMRS keeps for ``query``, best first, and its verdict.

        ``GET /gw/rag/retrieve`` (ADR-093 D-2): no chat call, so it costs no
        model time. Relevance, the cut and sufficiency are the server's
        (D-3); read ``result.sufficiency.status`` and never re-cut
        ``result.passages``. Options left as None are omitted, so the server
        defaults apply (k_max 4, adaptive cut). ``threshold`` selects the
        legacy absolute cut.

        Args:
            query: The question, or 1-3 variants of it fused server-side.
            project_id: Project to search (the client's project by default).
            graphs / exclude_graphs: Graph ids to restrict to or to leave out.
            k_max: Upper bound on passages (clamped to 1-12).
            pool, per_artifact: Candidate pool size and per-artifact cap.
            lexical, rerank, merge: Stage switches, ``auto|on|off``.
            cutoff: ``adaptive`` or ``fixed``.
            threshold: Legacy absolute similarity cut.
            trace: Ask for the stage trace.
            timeout: Seconds to wait, for this call and its one fallback
                (default ``R4_RAG_RETRIEVE_TIMEOUT``, 10 s; never above the
                client's ``R4_TIMEOUT``).
            profile: The model-size profile (ADR-093 D-14) that sizes the
                server's defaults (cut, pool, merge): ``auto``, ``small``,
                ``medium`` or ``large``. Explicit knobs above still win.
            reader_model: The model that will read the passages (the
                session model); the server resolves ``auto`` from it and
                reports it in ``lanes["profile"]``.

        A stopped turn (``_turn_cancel`` set) raises the token's
        ``TurnCancelledError``, never an ``R4Error``.

        An older server without the verb (HTTP 404/405) gets ONE fallback
        call to ``/gw/rag/query``; the result then carries
        ``lanes["fallback"]`` and sufficiency ``unknown`` (or
        ``insufficient`` when nothing passed the legacy cut). A loaded local
        bundle is searched instead of the platform (ADR-093 D-12): BM25 per
        variant fused by RRF, no cut, sufficiency ``unknown`` with basis
        ``offline`` (no calibrated null travels with a bundle); options it
        cannot apply are listed in ``lanes["local_bundle"]["unapplied"]``
        (``profile`` and ``reader_model`` are not retrieve options there).
        """
        variants = _query_variants(query)
        reader = _reader_options(profile, reader_model)
        options = {
            "graphs": graphs,
            "exclude_graphs": exclude_graphs,
            "k_max": k_max,
            "pool": pool,
            "per_artifact": per_artifact,
            "lexical": lexical,
            "rerank": rerank,
            "merge": merge,
            "cutoff": cutoff,
            "threshold": threshold,
        }
        if self.has_local_bundle:
            return self._retrieve_local(variants, options)
        project_id = project_id or self._config.project_id
        if not project_id:
            raise R4Error("project_id is required for RAG retrieval")
        params = _retrieve_params(variants, project_id, {**options, **reader}, trace)
        url = f"{self._config.gateway_url}{self._api_prefix}/rag/retrieve"
        bound = _retrieve_timeout(self._config, timeout)
        try:
            data = self._get_json(url, params, _turn_cancel, "RAG retrieve", bound)
        except APIError as exc:
            if exc.status_code not in (404, 405):
                raise
            logger.info(
                "rag retrieve: HTTP %s for /gw/rag/retrieve; one fallback to /gw/rag/query",
                exc.status_code,
            )
            return self._retrieve_via_query(variants[0], project_id, params, _turn_cancel, bound)
        return RetrieveResult.from_dict(data)

    def fetch_passage(
        self,
        node_id: str,
        *,
        project_id: str | None = None,
        window: int = 0,
        max_chars: int = 4000,
        timeout: float | None = None,
    ) -> Dict[str, Any]:
        """One passage by its handle (``RetrievedPassage.source_id``).

        ``GET /gw/rag/passages/{node_id}`` under the retrieve verb's access
        rule; ``window`` (0-2) adds neighbouring chunks on each side. Returns
        ``{source_id, artifact_id, source_name, text, span, window,
        text_source}``. A passage the caller cannot see is an HTTP 404,
        raised as :class:`~r4.errors.APIError`.

        ``project_id`` is the project the handle was retrieved in (the
        client's project by default), as for :meth:`retrieve`: the server
        checks the caller may read it (a shared corpus included) and looks
        the handle up there. ``timeout`` as for :meth:`retrieve`.
        """
        if not node_id:
            raise R4Error("node_id is required to fetch a passage")
        safe_node = quote(str(node_id), safe="")
        url = f"{self._config.gateway_url}{self._api_prefix}/rag/passages/{safe_node}"
        params: Dict[str, Any] = {"window": window, "max_chars": max_chars}
        project = project_id or self._config.project_id
        if project:
            params["project_id"] = project
        bound = _retrieve_timeout(self._config, timeout)
        return self._get_json(url, params, None, "RAG passage fetch", bound)

    def _get_json(
        self, url: str, params: Dict[str, Any], turn_cancel: Any, what: str, timeout: float
    ) -> Dict[str, Any]:
        try:
            return self._parent.http.get(
                url,
                params=params,
                timeout=timeout,
                cancel_check=turn_cancel.check if turn_cancel is not None else None,
                register_abort=turn_cancel.register_close if turn_cancel is not None else None,
            )
        except R4Error:
            raise
        except Exception as e:
            _raise_if_stopped(turn_cancel)
            raise R4Error(f"{what} failed: {e}") from e

    def _retrieve_via_query(
        self,
        query: str,
        project_id: str,
        params: Dict[str, Any],
        turn_cancel: Any,
        timeout: float,
    ) -> RetrieveResult:
        """The one fallback for a server without ``/rag/retrieve``, under the
        same time bound as the verb call it replaces."""
        response = self._query_via_http(
            query,
            project_id,
            params.get("k_max") or _FALLBACK_TOP_K,
            params.get("threshold"),
            True,
            None,
            turn_cancel,
            timeout=timeout,
        )
        return _result_from_sources(
            query,
            response.sources,
            lane="fallback",
            lane_report=_FALLBACK_LANE,
            sufficiency=_fallback_sufficiency(response),
        )

    def _retrieve_local(self, variants: List[str], options: Dict[str, Any]) -> RetrieveResult:
        """Retrieve from the loaded bundle (ADR-093 D-12): BM25 over chunk
        text per query variant, fused by RRF (k = 60); no cut and no verdict,
        so sufficiency is ``unknown`` on basis ``offline``. R4 holds no query
        vector here, so the dense lane reports ``off``."""
        top_k = _local_k_max(options.get("k_max"))
        hits, lanes = self._local_search(variants, top_k, _local_pool(options.get("pool")), None)
        version = str(self._bundle_manifest.get("version") or "")
        lanes["local_bundle"] = _offline_lane(options, len(self._bundle_chunks), version)
        passages = tuple(
            _local_passage(rank, self._bundle_chunks[hit.index], hit)
            for rank, hit in enumerate(hits, start=1)
        )
        return RetrieveResult(
            query=variants[0],
            passages=passages,
            sufficiency=RetrievalSufficiency(
                status="unknown", basis="offline", detail=_OFFLINE_DETAIL
            ),
            searched=len(self._bundle_chunks),
            space=str(lanes["dense"].get("space") or ""),
            lanes=lanes,
        )

    def query(
        self,
        query: str,
        *,
        project_id: Optional[str] = None,
        collection: Optional[str] = None,
        top_k: int = 5,
        threshold: Optional[float] = None,
        include_sources: bool = True,
        filters: Optional[Dict[str, Any]] = None,
        query_embedding: Optional[List[float]] = None,
        _turn_cancel: Any = None,
    ) -> RAGResponse:
        """
        Query the RAG system.

        Dispatches to the local bundle backend when :meth:`load_bundle`
        has been called, otherwise queries the platform Gateway.

        Args:
            query: The question to ask.
            project_id: Project to query (uses default if not provided).
            collection: Alias for project_id used by notebook/integration helpers.
            top_k: Number of source documents to retrieve.
            threshold: Legacy absolute similarity cut. None (the default) is
                omitted from the request, so the server decides relevance
                (ADR-093 D-10). A local bundle applies no cut (D-12).
            include_sources: Include source documents in response.
            filters: Optional metadata filters.
            query_embedding: A query vector in the loaded bundle's embedding
                space (its manifest's model); adds the dense lane to the local
                search. R4 never loads a model to make one.

        Returns:
            RAGResponse with answer and sources.

        For passages without a composed answer use :meth:`retrieve`.
        """
        if self.has_local_bundle:
            return self._query_local(query, top_k, query_embedding)

        project_id = project_id or collection or self._config.project_id
        if not project_id:
            raise R4Error("project_id is required for RAG queries")

        return self._query_via_http(
            query,
            project_id,
            top_k,
            threshold,
            include_sources,
            filters,
            _turn_cancel,
        )

    def _query_local(
        self,
        query: str,
        top_k: int,
        query_embedding: Optional[Sequence[float]] = None,
    ) -> RAGResponse:
        """Query the loaded bundle (ADR-093 D-12).

        BM25 over chunk text; a dense lane only when the caller passes a
        query vector in the bundle's space (R4 never loads a model); the
        lanes fused by RRF (k = 60) and no relevance cut. Each source's
        ``score`` is its fused score; ``metadata["scores"]`` holds each
        lane's (``lexical``, ``dense``) and ``metadata["lanes"]`` the lanes.

        Args:
            query: The search query text.
            top_k: Maximum number of results.
            query_embedding: Query vector in the bundle's space, or None.
        """
        hits, _lanes = self._local_search([query], max(int(top_k), 1), _LOCAL_POOL, query_embedding)
        sources = [_local_source(self._bundle_chunks[hit.index], hit) for hit in hits]
        return RAGResponse(answer="", sources=sources)

    def _local_search(
        self,
        variants: List[str],
        top_k: int,
        pool: int,
        query_vector: Optional[Sequence[float]],
    ) -> tuple[List[_LocalHit], Dict[str, Dict[str, Any]]]:
        """The fused offline ranking: each lane's top ``pool`` chunks, RRF,
        the best ``top_k`` chunks that have text; with the lane reports."""
        depth = max(top_k, pool)
        lexical = self._local_lexical_runs(variants, depth)
        dense, dense_lane = self._local_dense_run(query_vector, depth)
        fused = _rrf_fuse(lexical + dense)
        kept = [hit for hit in fused if self._bundle_chunks[hit.index].get("content")]
        lanes = {"lex": _lexical_report(self._local_bm25, lexical), "dense": dense_lane}
        return kept[:top_k], lanes

    def _local_lexical_runs(
        self, variants: List[str], depth: int
    ) -> List[tuple[str, List[tuple[int, float]]]]:
        """One BM25 ranking per query variant."""
        bm25 = self._local_bm25
        if bm25 is None:
            return []
        return [("lex", bm25.score(text, top_k=depth)) for text in variants]

    def _local_dense_run(
        self, query_vector: Optional[Sequence[float]], depth: int
    ) -> tuple[List[tuple[str, List[tuple[int, float]]]], Dict[str, Any]]:
        """The dense ranking for a caller's query vector in the bundle's
        space, joined to chunks by node id; ``off``/``unavailable`` otherwise."""
        search = self._local_vector_search
        report = _dense_report(search, query_vector, self._bundle_space)
        if report is not None or search is None or query_vector is None:
            return [], report or {"status": "off"}
        positions = {_chunk_key(chunk): idx for idx, chunk in enumerate(self._bundle_chunks)}
        nearest = search.search_keys(query_vector, top_k=depth)
        run = [(positions[key], score) for key, score in nearest if key in positions]
        return [("dense", run)], {"status": "ok", "n": len(run), "space": self._bundle_space}

    def search(
        self,
        query: str,
        *,
        collection: Optional[str] = None,
        project_id: Optional[str] = None,
        top_k: int = 5,
        threshold: Optional[float] = None,
        include_sources: bool = True,
        filters: Optional[Dict[str, Any]] = None,
        _turn_cancel: Any = None,
    ) -> RAGResponse:
        """Alias for query() used by integration adapters."""
        return self.query(
            query,
            project_id=project_id,
            collection=collection,
            top_k=top_k,
            threshold=threshold,
            include_sources=include_sources,
            filters=filters,
            _turn_cancel=_turn_cancel,
        )

    def _query_via_http(
        self,
        query: str,
        project_id: str,
        top_k: int,
        threshold: Optional[float],
        include_sources: bool,
        filters: Optional[Dict[str, Any]],
        turn_cancel: Any = None,
        *,
        timeout: Optional[float] = None,
    ) -> RAGResponse:
        """Execute RAG query via HTTP Gateway (``timeout`` defaults to the
        client's; a stopped turn raises the token's ``TurnCancelledError``)."""
        rag_query = RAGQuery(
            query=query,
            project_id=project_id,
            top_k=top_k,
            threshold=threshold,
            include_sources=include_sources,
            filters=filters,
        )

        url = f"{self._config.gateway_url}{self._api_prefix}/rag/query"
        try:
            data = self._parent.http.post(
                url,
                json_body=rag_query.to_dict(),
                timeout=self._config.timeout if timeout is None else timeout,
                cancel_check=turn_cancel.check if turn_cancel is not None else None,
                register_abort=turn_cancel.register_close if turn_cancel is not None else None,
            )
        except R4Error:
            raise
        except Exception as e:
            _raise_if_stopped(turn_cancel)
            raise R4Error(f"RAG query failed: {e}")
        return RAGResponse.from_api_response(data)

    def upload(
        self,
        file_path: str,
        *,
        project_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        proposal_id: Optional[str] = None,
    ) -> UploadResult:
        """
        Upload a document into the Gateway-backed RAG document path.

        This is intentionally distinct from :class:`DataClient`: RAG uploads
        are sent to ``/gw/rag/upload`` so UMRS can route them to the active
        mini-RiVault RAG service for indexing.

        The knowledge layer's actor and session headers ride along (review of
        K1 part A, 2026-10-24): UMRS hands the upload to the central
        knowledge ingest, which needs an approved proposal (``proposal_id``,
        sent as ``x-proposal-id``) from an agent and lands its rows
        ``proposed``. Without them an agent's upload read as the person's and
        landed established.
        """
        import httpx

        project_id = project_id or self._config.project_id
        if not project_id:
            raise R4Error("project_id is required for RAG uploads")

        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise R4Error(f"File not found: {file_path}")

        url = f"{self._config.gateway_url}{self._api_prefix}/rag/upload"
        headers = self._upload_headers(proposal_id)
        form_data: dict[str, str] = {"project_id": project_id}
        if metadata:
            form_data["metadata"] = json.dumps(metadata)

        try:
            with path.open("rb") as file_obj:
                files = {"file": (path.name, file_obj)}
                with httpx.Client(
                    timeout=self._config.timeout,
                    verify=self._config.verify_ssl,
                ) as client:
                    response = client.post(
                        url,
                        files=files,
                        data=form_data,
                        headers=headers,
                    )
                    self._parent._handle_response_errors(response)
                    result = response.json()
        except R4Error:
            raise
        except httpx.RequestError as e:
            raise R4Error(f"RAG upload failed: {e}") from e
        except ValueError as e:
            raise R4Error(f"RAG upload returned invalid JSON: {e}") from e

        return UploadResult(
            document_id=result.get("document_id", ""),
            filename=result.get("filename", path.name),
            status=result.get("status", "success"),
            chunks=result.get("chunks", 0),
            error=result.get("error"),
        )

    def _upload_headers(self, proposal_id: Optional[str]) -> Dict[str, str]:
        """Auth headers plus the knowledge actor/session and the proposal."""
        headers = self._parent._build_headers()
        headers.pop("Content-Type", None)
        knowledge = getattr(self._parent, "knowledge", None)
        actor_headers = getattr(knowledge, "_actor_headers", None)
        if callable(actor_headers):
            headers.update(actor_headers())
        if proposal_id:
            headers["x-proposal-id"] = proposal_id
        return headers

    def index(
        self,
        file_ids: List[str],
        *,
        project_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        wait: bool = False,
    ) -> List[IndexResult]:
        """
        RETIRED (ADR-027): this endpoint never had a server route.

        Historically this called ``POST /gw/rag/index``, which has no
        matching UMRS route — calls always failed with an opaque 404.
        Uploads now index inline: use ``client.rag.upload()`` (chunk +
        embed + index on upload), or ``knowledge_ingest`` for graph-backed
        knowledge.

        Raises:
            R4Error: Always. Kept for import compatibility; removal
                candidate after one release.
        """
        raise R4Error(
            "client.rag.index() is retired: POST /gw/rag/index has no "
            "server route (ADR-027). Use client.rag.upload() — uploads "
            "chunk, embed, and index inline — or knowledge_ingest for "
            "graph-backed knowledge."
        )

    def list_documents(
        self,
        project_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List documents in a project.

        Args:
            project_id: Project to list documents from.

        Returns:
            List of document metadata.
        """
        import httpx  # noqa: F401 - retained for backward-compat import hooks

        project_id = project_id or self._config.project_id
        if not project_id:
            raise R4Error("project_id is required")

        url = f"{self._config.gateway_url}{self._api_prefix}/rag/documents"
        params = {"project_id": project_id}
        try:
            data = self._parent.http.get(url, params=params, timeout=30)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"List documents failed: {e}")
        if isinstance(data, list):
            return data
        return data.get("documents", [])

    def delete_document(
        self,
        document_id: str,
        project_id: Optional[str] = None,
    ) -> "ArtifactDeleteResult":
        """
        Delete a document: the knowledge artifact it names and everything
        derived from it (ADR-093 MD-16).

        The server resolves the id (the artifact id, the id ``rag upload``
        returned, or the central upload's ingest job) and runs the artifact
        delete in one transaction; see
        :meth:`r4.knowledge.KnowledgeClient.delete_artifact`.

        Args:
            document_id: Document to delete.
            project_id: Project the document belongs to.

        Returns:
            The rows deleted per kind and one outcome per stored object.

        Raises:
            APIError: 404 when the id resolves to no artifact of the project
                (nothing was deleted); 403 without write scope.
        """
        from .knowledge import ArtifactDeleteResult

        project_id = project_id or self._config.project_id
        if not project_id:
            raise R4Error("project_id is required")

        safe_doc = quote(document_id, safe="")
        url = f"{self._config.gateway_url}{self._api_prefix}/rag/documents/{safe_doc}"
        params = {"project_id": project_id}
        try:
            data = self._parent.http.delete(url, params=params, timeout=30)
        except R4Error:
            raise
        except Exception as e:
            raise R4Error(f"Delete document failed: {e}")
        return ArtifactDeleteResult.from_dict(data or {})
