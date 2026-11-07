"""Offline replica of the retrieval lanes on SciFact (ADR-093 C0, graph-angle G-0).

Replays the dense, BM25, IDF-lexical and RRF lanes in numpy over cached
vectors, so a lane change can be sized without the stack and the live
numbers can be cross-checked. ``build`` chunks exactly the text the live
graph ingested, so the replica replays the platform's own chunks (9,377 on
the full tier). The G-0 prototype's cache chunked ``title\\n\\nabstract``
instead (9,356 chunks); it reproduces the ADR-093 record, dense 0.834,
BM25 0.817, RRF(dense, BM25) 0.876 nDCG@10 over the 188 dev claims with
evidence. ``REFERENCE`` keeps each scheme's numbers apart.

The cache is kept outside git (SciFact is CC BY-NC 2.0; the vectors are
derived from it). ``build`` writes it from a STAGED model directory and
never downloads one (``HF_HUB_OFFLINE=1``):

- ``chunks.json``: ``[[doc_id, chunk_index, text], ...]`` from the platform
  chunker's rule (256 e5 tokens, no overlap) over the exact text the live
  graph ingested (``scifact.doc_text``: ``# title\\n\\nabstract\\n``);
- ``chunk_vecs.npy``: float32, L2-normalised, ``passage: `` prefix;
- ``claims.json``: train + dev claims with their ``split``;
- ``claim_vecs.npy``: float32, L2-normalised, ``query: `` prefix;
- ``meta.json``: the text scheme the chunks were cut from (a cache without
  it is the G-0 prototype's, ``title-abstract-v0``).

    python benchmarks/retrieval/offline_lanes.py eval --cache DIR [--split dev|tune] [--tier full|1k]
    python benchmarks/retrieval/offline_lanes.py build --data <scifact>/data \\
        --model-dir <staged e5-base-v2 directory> --out DIR

The cross-encoder lane is not replayed: it needs the staged reranker (C8).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics  # noqa: E402
import scifact  # noqa: E402

CACHE_FILES = ("chunks.json", "chunk_vecs.npy", "claims.json", "claim_vecs.npy")
META_FILE = "meta.json"  # optional: absent in the prototype's cache
DEFAULT_CACHE = Path.home() / ".cache" / "adr093" / "scifact-e5-base-v2"
WINDOW = 256  # platform chunker: e5 tokens per chunk, no overlap
DEPTH = 100  # docs per lane ranking, and per ranking fed to RRF
RRF_K = 60
BM25_K1, BM25_B = 0.9, 0.4

#: The text a chunk is cut from. ``ingested-md-v1`` is what the live graph
#: holds (``scifact.doc_text``, the markdown ``write_docs`` writes: 9,377
#: chunks on the full tier, as in Postgres). ``title-abstract-v0`` is the G-0
#: prototype's ``title\\n\\nabstract`` (9,356 chunks; its first chunk of every
#: doc lacks the heading), kept only to read the prototype's cache.
TEXT_SCHEME = "ingested-md-v1"
PROTOTYPE_SCHEME = "title-abstract-v0"

#: nDCG@10 over the dev claims with evidence, per text scheme and tier.
#: The prototype's numbers are the ADR-093 Context record (2026-10-10).
REFERENCE: dict[str, dict[str, dict[str, float]]] = {
    PROTOTYPE_SCHEME: {
        "full": {"dense": 0.834, "bm25": 0.817, "rrf": 0.876},
        "1k": {"dense": 0.937, "bm25": 0.900, "rrf": 0.950},
    },
    # ``build`` over the live text, 2026-10-10: the staged e5-base-v2 on MPS
    # (claim vectors agree with the CPU build at cosine >= 0.9999998). 9,377
    # chunks full / 1,842 at 1k, as the live graphs hold.
    TEXT_SCHEME: {
        "full": {"dense": 0.8378, "bm25": 0.8141, "rrf": 0.8704},
        "1k": {"dense": 0.9446, "bm25": 0.8985, "rrf": 0.9504},
    },
}

_TOKEN = re.compile(r"[a-z0-9]+")
# scikit-learn's ENGLISH_STOP_WORDS (318 words), vendored so the replica
# needs numpy only; the prototype's BM25 used exactly this list.
STOP_WORDS = frozenset(
    [
        "a",
        "about",
        "above",
        "across",
        "after",
        "afterwards",
        "again",
        "against",
        "all",
        "almost",
        "alone",
        "along",
        "already",
        "also",
        "although",
        "always",
        "am",
        "among",
        "amongst",
        "amoungst",
        "amount",
        "an",
        "and",
        "another",
        "any",
        "anyhow",
        "anyone",
        "anything",
        "anyway",
        "anywhere",
        "are",
        "around",
        "as",
        "at",
        "back",
        "be",
        "became",
        "because",
        "become",
        "becomes",
        "becoming",
        "been",
        "before",
        "beforehand",
        "behind",
        "being",
        "below",
        "beside",
        "besides",
        "between",
        "beyond",
        "bill",
        "both",
        "bottom",
        "but",
        "by",
        "call",
        "can",
        "cannot",
        "cant",
        "co",
        "con",
        "could",
        "couldnt",
        "cry",
        "de",
        "describe",
        "detail",
        "do",
        "done",
        "down",
        "due",
        "during",
        "each",
        "eg",
        "eight",
        "either",
        "eleven",
        "else",
        "elsewhere",
        "empty",
        "enough",
        "etc",
        "even",
        "ever",
        "every",
        "everyone",
        "everything",
        "everywhere",
        "except",
        "few",
        "fifteen",
        "fifty",
        "fill",
        "find",
        "fire",
        "first",
        "five",
        "for",
        "former",
        "formerly",
        "forty",
        "found",
        "four",
        "from",
        "front",
        "full",
        "further",
        "get",
        "give",
        "go",
        "had",
        "has",
        "hasnt",
        "have",
        "he",
        "hence",
        "her",
        "here",
        "hereafter",
        "hereby",
        "herein",
        "hereupon",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "however",
        "hundred",
        "i",
        "ie",
        "if",
        "in",
        "inc",
        "indeed",
        "interest",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "keep",
        "last",
        "latter",
        "latterly",
        "least",
        "less",
        "ltd",
        "made",
        "many",
        "may",
        "me",
        "meanwhile",
        "might",
        "mill",
        "mine",
        "more",
        "moreover",
        "most",
        "mostly",
        "move",
        "much",
        "must",
        "my",
        "myself",
        "name",
        "namely",
        "neither",
        "never",
        "nevertheless",
        "next",
        "nine",
        "no",
        "nobody",
        "none",
        "noone",
        "nor",
        "not",
        "nothing",
        "now",
        "nowhere",
        "of",
        "off",
        "often",
        "on",
        "once",
        "one",
        "only",
        "onto",
        "or",
        "other",
        "others",
        "otherwise",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "part",
        "per",
        "perhaps",
        "please",
        "put",
        "rather",
        "re",
        "same",
        "see",
        "seem",
        "seemed",
        "seeming",
        "seems",
        "serious",
        "several",
        "she",
        "should",
        "show",
        "side",
        "since",
        "sincere",
        "six",
        "sixty",
        "so",
        "some",
        "somehow",
        "someone",
        "something",
        "sometime",
        "sometimes",
        "somewhere",
        "still",
        "such",
        "system",
        "take",
        "ten",
        "than",
        "that",
        "the",
        "their",
        "them",
        "themselves",
        "then",
        "thence",
        "there",
        "thereafter",
        "thereby",
        "therefore",
        "therein",
        "thereupon",
        "these",
        "they",
        "thick",
        "thin",
        "third",
        "this",
        "those",
        "though",
        "three",
        "through",
        "throughout",
        "thru",
        "thus",
        "to",
        "together",
        "too",
        "top",
        "toward",
        "towards",
        "twelve",
        "twenty",
        "two",
        "un",
        "under",
        "until",
        "up",
        "upon",
        "us",
        "very",
        "via",
        "was",
        "we",
        "well",
        "were",
        "what",
        "whatever",
        "when",
        "whence",
        "whenever",
        "where",
        "whereafter",
        "whereas",
        "whereby",
        "wherein",
        "whereupon",
        "wherever",
        "whether",
        "which",
        "while",
        "whither",
        "who",
        "whoever",
        "whole",
        "whom",
        "whose",
        "why",
        "will",
        "with",
        "within",
        "without",
        "would",
        "yet",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
    ]
)


def tokens(text: str) -> list[str]:
    """Lower-cased alphanumeric runs, stop words dropped."""
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOP_WORDS]


# ── the cache ────────────────────────────────────────────────────────────────


@dataclass
class Cache:
    texts: list[str]
    chunk_docs: np.ndarray  # doc id (str) per chunk
    vecs: np.ndarray  # [chunks, dim]
    claims: list[dict[str, Any]]
    claim_vecs: np.ndarray  # [claims, dim], same order as ``claims``
    scheme: str = PROTOTYPE_SCHEME  # the text the chunks were cut from


def cache_scheme(path: Path) -> str:
    """The cache's text scheme (``meta.json``); the prototype's without one."""
    meta = Path(path) / META_FILE
    if not meta.is_file():
        return PROTOTYPE_SCHEME
    return str(json.loads(meta.read_text()).get("text_scheme") or PROTOTYPE_SCHEME)


def cache_present(path: Path) -> bool:
    return all((Path(path) / name).is_file() for name in CACHE_FILES)


def load_cache(path: Path, *, tier: str = "full") -> Cache:
    """The cached replica; ``tier='1k'`` keeps the seeded harness subset
    (dev-cited docs + 717 distractors, seed 20261010), as the live 1k graph."""
    path = Path(path)
    chunks = json.loads((path / "chunks.json").read_text())
    vecs = np.load(path / "chunk_vecs.npy")
    claims = json.loads((path / "claims.json").read_text())
    keep = _tier_rows(chunks, claims, tier)
    return Cache(
        texts=[chunks[i][2] for i in keep],
        chunk_docs=np.array([str(chunks[i][0]) for i in keep]),
        vecs=vecs[keep],
        claims=claims,
        claim_vecs=np.load(path / "claim_vecs.npy"),
        scheme=cache_scheme(path),
    )


def _tier_rows(chunks: list, claims: list[dict], tier: str) -> list[int]:
    if tier == "full":
        return list(range(len(chunks)))
    if tier != "1k":
        raise ValueError(f"unknown tier {tier!r}; full or 1k")
    every = {str(c[0]) for c in chunks}
    cited = [c.get("cited_doc_ids") or [] for c in claims if c.get("split") == "dev"]
    chosen = scifact.subset_ids(every, cited, distractors=717)
    return [i for i, c in enumerate(chunks) if str(c[0]) in chosen]


# ── lanes ────────────────────────────────────────────────────────────────────


class LexicalIndex:
    """An inverted index over chunk texts: BM25 (k1 0.9, b 0.4, Lucene idf)
    and the IDF-lexical lane (sum of ln(N/df) over the distinct query terms a
    chunk holds: the shape of the platform's OR-mode FTS lane)."""

    def __init__(self, texts: Sequence[str]) -> None:
        postings: dict[str, tuple[list[int], list[int]]] = defaultdict(lambda: ([], []))
        lengths = []
        for i, text in enumerate(texts):
            counts: dict[str, int] = defaultdict(int)
            for term in tokens(text):
                counts[term] += 1
            lengths.append(sum(counts.values()))
            for term, n in counts.items():
                postings[term][0].append(i)
                postings[term][1].append(n)
        self.n = len(texts)
        self.postings = {
            t: (np.array(ids, dtype=np.int64), np.array(tf, dtype=np.float32))
            for t, (ids, tf) in postings.items()
        }
        self.lengths = np.array(lengths, dtype=np.float32)
        self.avgdl = self.lengths.mean() if self.n else np.float32(1.0)

    def _terms(self, query: str) -> list[str]:
        return sorted({t for t in tokens(query) if t in self.postings})

    def bm25(self, query: str, *, k1: float = BM25_K1, b: float = BM25_B) -> np.ndarray:
        scores = np.zeros(self.n, dtype=np.float32)
        for term in self._terms(query):
            ids, tf = self.postings[term]
            idf = math.log(1 + (self.n - len(ids) + 0.5) / (len(ids) + 0.5))
            norm = tf + k1 * (1 - b + b * self.lengths[ids] / self.avgdl)
            scores[ids] += idf * tf * (k1 + 1) / norm
        return scores

    def idf_lexical(self, query: str) -> np.ndarray:
        scores = np.zeros(self.n, dtype=np.float32)
        for term in self._terms(query):
            ids, _tf = self.postings[term]
            scores[ids] += math.log(self.n / len(ids))
        return scores


def doc_ranking(
    scores: np.ndarray, chunk_docs: np.ndarray, *, k: int = DEPTH, positive_only: bool = False
) -> list[str]:
    """Chunk scores max-pooled to docs, best first (a lexical lane stops at
    the first chunk that matched nothing)."""
    ranked: list[str] = []
    seen: set[str] = set()
    for i in np.argsort(-scores):
        if positive_only and scores[i] <= 0:
            break
        doc = str(chunk_docs[i])
        if doc not in seen:
            seen.add(doc)
            ranked.append(doc)
            if len(ranked) >= k:
                break
    return ranked


def rrf(
    rankings: Sequence[Sequence[str]],
    *,
    k: int = RRF_K,
    depth: int = DEPTH,
    weights: Sequence[float] | None = None,
) -> list[str]:
    """Reciprocal-rank fusion; ties keep first-seen order (lane order)."""
    fused: dict[str, float] = defaultdict(float)
    for lane, ranking in enumerate(rankings):
        weight = 1.0 if weights is None else weights[lane]
        for rank, doc in enumerate(ranking[:depth], start=1):
            fused[doc] += weight / (k + rank)
    return [doc for doc, _ in sorted(fused.items(), key=lambda item: -item[1])]


# ── evaluation ───────────────────────────────────────────────────────────────

LANES = ("dense", "bm25", "lexical", "rrf", "rrf_lexical")


def _lane_rankings(cache: Cache, index: LexicalIndex, qi: int) -> dict[str, list[str]]:
    query = cache.claims[qi]["claim"]
    dense = doc_ranking(cache.vecs @ cache.claim_vecs[qi], cache.chunk_docs)
    bm25 = doc_ranking(index.bm25(query), cache.chunk_docs, positive_only=True)
    lexical = doc_ranking(index.idf_lexical(query), cache.chunk_docs, positive_only=True)
    return {
        "dense": dense,
        "bm25": bm25,
        "lexical": lexical,
        "rrf": rrf([dense, bm25]),
        "rrf_lexical": rrf([dense, lexical]),
    }


def _with_evidence(cache: Cache, split: str) -> list[int]:
    name = "train" if split == "tune" else split
    return [i for i, c in enumerate(cache.claims) if c.get("split") == name and c.get("evidence")]


def eval_claims(cache: Cache, split: str) -> list[int]:
    """Indices of the split's claims (``tune`` = train) whose evidence docs
    are ALL in the cache's corpus: a claim with evidence outside the tier
    (train claims at the 1k tier) would score 0 by construction (C0)."""
    corpus = set(cache.chunk_docs.tolist())
    return [
        i
        for i in _with_evidence(cache, split)
        if {str(d) for d in cache.claims[i]["evidence"]} <= corpus
    ]


def evaluate(cache: Cache, *, split: str = "dev") -> dict[str, Any]:
    """Mean retrieval scores per lane over the split's evidence claims."""
    index = LexicalIndex(cache.texts)
    per_lane: dict[str, list[dict[str, float]]] = defaultdict(list)
    selected = eval_claims(cache, split)
    for qi in selected:
        relevant = {str(d) for d in cache.claims[qi]["evidence"]}
        for lane, ranking in _lane_rankings(cache, index, qi).items():
            per_lane[lane].append(metrics.retrieval_scores(ranking, relevant))
    lanes = {lane: _means(rows) for lane, rows in per_lane.items()}
    return {
        "claims": len(selected),
        "dropped_evidence_outside_corpus": len(_with_evidence(cache, split)) - len(selected),
        "chunks": len(cache.texts),
        "docs": len(set(cache.chunk_docs.tolist())),
        "scheme": cache.scheme,
        "lanes": lanes,
    }


def _means(rows: list[dict[str, float]]) -> dict[str, float]:
    return {key: round(metrics.mean([r[key] for r in rows]), 4) for key in rows[0]}


# ── build (the staged model; never a download) ───────────────────────────────


def platform_chunks(text: str, offsets: Sequence[tuple[int, int]], window: int = WINDOW):
    """``(chunk_index, text)`` windows of ``window`` tokens over ``text``, from
    the tokenizer's character offsets; blank windows are dropped."""
    out = []
    for index, start in enumerate(range(0, len(offsets), window)):
        span = offsets[start : start + window]
        piece = text[span[0][0] : span[-1][1]]
        if piece.strip():
            out.append((index, piece))
    return out


def _load_model(model_dir: Path, device: str) -> Any:
    if not Path(model_dir).is_dir():
        raise SystemExit(f"--model-dir {model_dir} is not a staged model directory")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(str(model_dir), device=device)


def _encode(model: Any, texts: list[str]) -> np.ndarray:
    vecs = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


def source_texts(data_dir: Path) -> dict[str, str]:
    """doc id -> the exact text the live graph ingested for it: what
    ``scifact.write_docs`` writes, so the replica replays the live chunks."""
    docs = scifact.read_corpus(Path(data_dir) / "corpus.jsonl")
    return {doc_id: scifact.doc_text(doc) for doc_id, doc in docs.items()}


def chunk_corpus(texts: dict[str, str], tokenizer: Callable[..., Any]) -> list[list[Any]]:
    """``[[doc_id, chunk_index, text], ...]`` by the platform chunker's rule."""
    chunks: list[list[Any]] = []
    for doc_id, text in texts.items():
        enc = tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)
        chunks += [[doc_id, i, t] for i, t in platform_chunks(text, enc["offset_mapping"])]
    return chunks


def build(data_dir: Path, model_dir: Path, out: Path, *, device: str = "cpu") -> dict[str, int]:
    """Chunk and embed the full corpus and every train/dev claim into ``out``."""
    model = _load_model(model_dir, device)
    chunks = chunk_corpus(source_texts(data_dir), model.tokenizer)
    claims = [
        {**json.loads(line), "split": split}
        for split in ("train", "dev")
        for line in (Path(data_dir) / f"claims_{split}.jsonl").read_text().splitlines()
    ]
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "chunk_vecs.npy", _encode(model, ["passage: " + c[2] for c in chunks]))
    np.save(out / "claim_vecs.npy", _encode(model, ["query: " + c["claim"] for c in claims]))
    (out / "chunks.json").write_text(json.dumps(chunks))
    (out / "claims.json").write_text(json.dumps(claims))
    meta = {"text_scheme": TEXT_SCHEME, "window": WINDOW, "model_dir": str(model_dir)}
    (out / META_FILE).write_text(json.dumps({**meta, "chunks": len(chunks)}))
    return {"chunks": len(chunks), "claims": len(claims)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("eval")
    p.add_argument(
        "--cache", type=Path, default=Path(os.environ.get("ADR093_REPLICA_DIR") or DEFAULT_CACHE)
    )
    p.add_argument("--split", default="dev", choices=("dev", "tune", "train"))
    p.add_argument("--tier", default="full", choices=("full", "1k"))
    p = sub.add_parser("build")
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--model-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--device", default="cpu")
    args = parser.parse_args(argv)
    if args.cmd == "build":
        print(json.dumps(build(args.data, args.model_dir, args.out, device=args.device)))
        return 0
    if not cache_present(args.cache):
        print(f"no replica cache at {args.cache} (need {', '.join(CACHE_FILES)})", file=sys.stderr)
        return 2
    report = evaluate(load_cache(args.cache, tier=args.tier), split=args.split)
    reference = REFERENCE.get(report["scheme"], {}).get(args.tier)
    report["reference"] = reference if args.split == "dev" else None
    print(json.dumps({"tier": args.tier, "split": args.split, **report}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
