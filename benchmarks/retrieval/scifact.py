"""SciFact as a retrieval + inference probe for the stand-alone stack (ADR-093 S0).

SciFact (Wadden et al., 2020; allenai release, CC BY-NC 2.0) pairs 300 dev
claims with the abstracts that support or contradict them. That gives both
halves of the eval:

- retrieval qrels: a claim's evidence documents (188 claims have some);
- an inference task: SUPPORT / CONTRADICT / NOT_ENOUGH_INFO with the evidence
  cited. The 112 claims with no evidence in the corpus test abstention: the
  right answer is "not enough info", and a 3B model that guesses is wrong.

The corpus is a fixed, seeded subset: every document a dev claim cites, plus
distractors drawn from the rest, so that ingest stays under an hour on a
laptop. Get the data once (it is not vendored)::

    curl -sSLO https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz
    tar -xzf data.tar.gz          # -> data/corpus.jsonl, data/claims_dev.jsonl
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

LABELS = ("SUPPORT", "CONTRADICT", "NOT_ENOUGH_INFO")
DOC_PREFIX = "scifact-"
SEED = 20261010


@dataclass(frozen=True)
class Claim:
    id: int
    text: str
    label: str  # one of LABELS
    evidence: frozenset[str]  # doc ids holding rationale sentences
    cited: frozenset[str]  # doc ids the claim's source cites (a superset)
    # (doc id, sentence indices) of the rationale sentences in each evidence doc
    rationale: tuple[tuple[str, tuple[int, ...]], ...] = ()


@dataclass
class Corpus:
    docs: dict[str, dict] = field(default_factory=dict)  # doc id -> {title, abstract, sentences}
    claims: list[Claim] = field(default_factory=list)


def _claim_label(evidence: dict) -> str:
    """One label per claim; SciFact dev claims never mix SUPPORT and CONTRADICT."""
    labels = {entry["label"] for entries in evidence.values() for entry in entries}
    if not labels:
        return "NOT_ENOUGH_INFO"
    if len(labels) > 1:
        raise ValueError(f"claim mixes labels {sorted(labels)}")
    return labels.pop()


# Out-of-KB probes (NONSENSE_EVAL_V1): 20 seeded word salads and 10 off-domain
# questions. Gold is NOT_ENOUGH_INFO; a verdict on one is a hallucination. They
# are disjoint from the probes UMRS calibrates its null on.
NONSENSE_EVAL_V1 = (
    "violet harbor ledger quietly folds the seventh tuesday of copper",
    "granite melody sorts invoices beneath a sleeping lighthouse",
    "the cardboard orbit negotiates with marmalade every second winter",
    "seventeen umbrellas audit the velvet staircase at noon",
    "a pocket of thunder rents the attic to polite turnips",
    "lantern grammar dissolves when the walrus files its taxes",
    "the ceramic tide forgets which spoon governs the meadow",
    "obsidian pancakes vote on the harbor's tangled syntax",
    "a quiet bicycle annotates the moon's unpaid parking tickets",
    "the hollow violin rehearses customs forms for migrating kettles",
    "teal accordions mortgage the riverbank to a sleepy comet",
    "every ninth pebble subscribes to the library of fog",
    "the marble weather sells cinnamon to reluctant galaxies",
    "a cautious kite indexes the furniture of distant echoes",
    "postcards of gravel referee the duel between two oatmeal clocks",
    "the woolen algorithm naps beside an anxious lighthouse keeper",
    "brass hedgehogs translate tuesday into fluent wallpaper",
    "the velvet compass invoices a cloud for borrowed shadows",
    "an origami glacier counts the vowels of a sleeping fern",
    "the paper volcano apologizes to the punctual raspberry",
    "Which team won the 1998 FIFA World Cup final?",
    "What is the best pizza restaurant in Naples?",
    "How do I change a flat tire on a mountain bike?",
    "Who wrote the novel Pride and Prejudice?",
    "What is the capital city of Australia?",
    "How many strings does a standard violin have?",
    "What year did the Berlin Wall fall?",
    "Which planet has the largest number of known moons?",
    "How long should I boil an egg for a soft yolk?",
    "What programming language was created by Guido van Rossum?",
)

CLAIM_SETS = ("dev", "tune", "nonsense", "multi")
NONSENSE_ID_BASE = 900001


def is_nonsense(claim_id: int) -> bool:
    """Whether a claim id is one of the NONSENSE_EVAL_V1 probes."""
    return int(claim_id) >= NONSENSE_ID_BASE


def _rationale(evidence: dict) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """(doc id, sorted rationale sentence indices) per evidence doc."""
    return tuple(
        (str(doc), tuple(sorted({i for entry in entries for i in entry.get("sentences", ())})))
        for doc, entries in sorted(evidence.items())
    )


def _read_claims(path: Path) -> list[Claim]:
    claims = []
    with path.open() as fh:
        for line in fh:
            raw = json.loads(line)
            claims.append(
                Claim(
                    id=int(raw["id"]),
                    text=raw["claim"],
                    label=_claim_label(raw["evidence"]),
                    evidence=frozenset(raw["evidence"]),
                    cited=frozenset(str(d) for d in raw["cited_doc_ids"]),
                    rationale=_rationale(raw["evidence"]),
                )
            )
    return claims


def claims_for(data_dir: Path, which: str) -> list[Claim]:
    """``dev`` (reported), ``tune`` (SciFact train: every constant is tuned
    here and only here) or ``nonsense`` (out-of-KB probes, ids from 900001)."""
    if which == "dev":
        return _read_claims(data_dir / "claims_dev.jsonl")
    if which == "tune":
        return _read_claims(data_dir / "claims_train.jsonl")
    if which == "nonsense":
        return [
            Claim(NONSENSE_ID_BASE + i, text, "NOT_ENOUGH_INFO", frozenset(), frozenset())
            for i, text in enumerate(NONSENSE_EVAL_V1)
        ]
    if which == "multi":
        return multi_evidence_claims(data_dir)
    raise ValueError(f"unknown claim set {which!r}; one of {CLAIM_SETS}")


def multi_evidence_claims(data_dir: Path) -> list[Claim]:
    """``multi``: dev and train claims whose evidence spans two or more
    abstracts (47 in the allenai release), the non-circular probe for a
    graph lane (ADR-093 D-14): answering needs every evidence abstract."""
    seen: dict[int, Claim] = {}
    for name in ("claims_dev.jsonl", "claims_train.jsonl"):
        for claim in _read_claims(data_dir / name):
            if len(claim.evidence) >= 2:
                seen.setdefault(claim.id, claim)
    return list(seen.values())


def load(
    data_dir: Path, *, distractors: int | None = 717, seed: int = SEED, claims: str = "dev"
) -> Corpus:
    """A claim set and the seeded corpus subset (dev-cited docs + distractors).

    ``distractors=None`` is the full corpus (all 5,183 abstracts), the tier the
    ADR-093 gates read.
    """
    claim_list = claims_for(data_dir, claims)
    dev = claim_list if claims == "dev" else claims_for(data_dir, "dev")
    every = read_corpus(data_dir / "corpus.jsonl")
    if distractors is None:
        return Corpus(docs=every, claims=claim_list)
    chosen = subset_ids(every, (c.cited for c in dev), distractors=distractors, seed=seed)
    return Corpus(docs={d: every[d] for d in sorted(chosen)}, claims=claim_list)


def read_corpus(path: Path) -> dict[str, dict]:
    """doc id -> {title, abstract, sentences}: stripped sentences, the abstract
    their space-joined text (what ``doc_text`` writes and the graph ingests)."""
    every = {}
    with path.open() as fh:
        for line in fh:
            raw = json.loads(line)
            sentences = [s.strip() for s in raw["abstract"]]
            every[str(raw["doc_id"])] = {
                "title": raw["title"].strip(),
                "abstract": " ".join(sentences),
                "sentences": sentences,
            }
    return every


def subset_ids(
    every: Iterable[str], cited: Iterable[Iterable[str]], *, distractors: int, seed: int = SEED
) -> set[str]:
    """The seeded corpus subset: every cited doc that exists, plus ``distractors``
    drawn from the rest (the 1k tier; the offline replica cuts the same set)."""
    pool = {str(d) for d in every}
    needed = set().union(*(set(map(str, c)) for c in cited)) & pool
    rest = sorted(pool - needed)
    return needed | set(random.Random(seed).sample(rest, min(distractors, len(rest))))


def rationale_sentences(corpus: Corpus, claim: Claim) -> list[str]:
    """The text of a claim's rationale sentences, for the docs the corpus holds."""
    out = []
    for doc, indices in claim.rationale:
        sentences = (corpus.docs.get(doc) or {}).get("sentences") or []
        out.extend(sentences[i] for i in indices if i < len(sentences))
    return out


def doc_filename(doc_id: str) -> str:
    return f"{DOC_PREFIX}{doc_id}.md"


def doc_id_of(name: str) -> str | None:
    """The SciFact doc id in an artifact or source name, or None."""
    base = Path(str(name)).name
    if not base.startswith(DOC_PREFIX):
        return None
    stem = base[len(DOC_PREFIX) :].split(".", 1)[0]
    return stem if stem.isdigit() else None


def doc_text(doc: dict) -> str:
    """The exact text one ingested artifact holds: the title as a markdown
    heading, then the abstract. ``write_docs`` writes it and the offline
    replica chunks it, so the replica replays the live graph's chunks."""
    return f"# {doc['title']}\n\n{doc['abstract']}\n"


def write_docs(corpus: Corpus, out_dir: Path) -> list[Path]:
    """One markdown file per abstract (``doc_text``), for graph ingest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for doc_id, doc in corpus.docs.items():
        path = out_dir / doc_filename(doc_id)
        path.write_text(doc_text(doc), encoding="utf-8")
        paths.append(path)
    return paths
