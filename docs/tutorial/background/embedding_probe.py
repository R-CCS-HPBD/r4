"""Measure retrieval over the tutorial knowledge pack with a real embedder.

For the background slide "embeddings: similar meaning, nearby vectors": the
Lab 1.4 question is embedded, every section of the knowledge pack is
embedded, and the cosine similarities (what pgvector ranks by) are recorded,
plus a 2-D projection (PCA) of all vectors for a picture.

It also checks the English-only default embedder on the Japanese pack:

    python embedding_probe.py [--out embedding_probe.json]

Needs sentence-transformers + numpy and the models in the local Hugging Face
cache. The committed JSON is what the deck builder reads.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK = HERE.parent / "data" / "knowledge-pack"
QUESTIONS = {
    "en": "Which reference model, Moho depth and mantle velocity do we use for a Kii Channel "
    "event, and when is a pick rejected?",
    "ja": "紀伊水道の地震にはどの参照モデル・モホ面の深さ・マントル速度を使い、どの場合に読み取り値を棄却しますか?",
}
MODELS = ("intfloat/e5-base-v2", "intfloat/multilingual-e5-base")

# Review 2026-11-05: one short question against four single sentences — the
# answer, the same fact in other words, the same topic but another question,
# and something unrelated — so the ranking reads without knowing the pack.
SENTENCE_DEMO = {
    "en": {
        "question": "How deep is the Moho under the Kii Channel?",
        "sentences": [
            ("answer", "In our reference model the Moho lies at 28 km depth."),
            ("paraphrase", "The crust ends and the mantle begins about 28 km down."),
            ("same topic", "Pn picks closer than 140 km are rejected."),
            ("unrelated", "The lunch break starts at 12:30 in room 101."),
        ],
    },
    "ja": {
        "question": "紀伊水道の下のモホ面の深さは?",
        "sentences": [
            ("answer", "私たちの参照モデルでは、モホ面の深さは 28 km です。"),
            ("paraphrase", "地殻が終わりマントルが始まるのは、地下約 28 km です。"),
            ("same topic", "震央距離 140 km より近い Pn の読み取りは棄却します。"),
            ("unrelated", "昼休みは 12:30 から 101 号室で始まります。"),
        ],
    },
}


def sections(lang: str) -> list[dict]:
    """One chunk per '## ' section (the file's preamble is its own chunk)."""
    chunks = []
    for path in sorted((PACK / lang).glob("*.md")):
        title, body = path.stem, []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                chunks.append({"file": path.name, "section": title, "text": "\n".join(body)})
                title, body = line[3:].strip(), []
            body.append(line)
        chunks.append({"file": path.name, "section": title, "text": "\n".join(body)})
    return [c for c in chunks if c["text"].strip()]


def score(model, question: str, chunks: list[dict]) -> list[float]:
    import numpy as np

    q = model.encode(["query: " + question], normalize_embeddings=True)
    p = model.encode(["passage: " + c["text"] for c in chunks], normalize_embeddings=True)
    return [round(float(x), 4) for x in (p @ q.T).ravel()], np.vstack([q, p])


def pca_2d(vectors) -> list[list[float]]:
    import numpy as np

    centred = vectors - vectors.mean(axis=0)
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    xy = centred @ vt[:2].T
    return [[round(float(a), 4), round(float(b), 4)] for a, b in xy]


def probe(model_id: str, lang: str) -> dict:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_id)
    chunks = sections(lang)
    sims, vectors = score(model, QUESTIONS[lang], chunks)
    ranked = sorted(zip(sims, chunks, strict=True), key=lambda t: -t[0])
    return {
        "model": model_id,
        "dimension": int(vectors.shape[1]),
        "question": QUESTIONS[lang],
        "ranked": [{"score": s, "file": c["file"], "section": c["section"]} for s, c in ranked],
        "xy": {
            "question": pca_2d(vectors)[0],
            "chunks": [
                {"file": c["file"], "section": c["section"], "xy": xy}
                for c, xy in zip(chunks, pca_2d(vectors)[1:], strict=True)
            ],
        },
    }


def sentence_demo(model_id: str, lang: str) -> dict:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_id)
    demo = SENTENCE_DEMO[lang]
    q = model.encode(["query: " + demo["question"]], normalize_embeddings=True)
    p = model.encode(["passage: " + t for _, t in demo["sentences"]], normalize_embeddings=True)
    sims = (p @ q.T).ravel()
    return {
        "model": model_id,
        "question": demo["question"],
        "sentences": [
            {"role": role, "text": text, "score": round(float(x), 4)}
            for (role, text), x in zip(demo["sentences"], sims, strict=True)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Record retrieval scores for the slides")
    parser.add_argument("--out", type=Path, default=HERE / "embedding_probe.json")
    args = parser.parse_args()
    result = {f"{m.split('/')[1]}:{lang}": probe(m, lang) for m in MODELS for lang in QUESTIONS}
    result["sentence_demo"] = {
        f"{m.split('/')[1]}:{lang}": sentence_demo(m, lang)
        for m in MODELS
        for lang in SENTENCE_DEMO
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for key, r in result["sentence_demo"].items():
        print(key, [(x["role"], x["score"]) for x in r["sentences"]])
    for key, r in result.items():
        if key == "sentence_demo":
            continue
        top = r["ranked"][0]
        print(f"{key}: dim {r['dimension']}, top {top['score']} {top['file']} / {top['section']}")


if __name__ == "__main__":
    main()
