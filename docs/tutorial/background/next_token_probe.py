"""Measure a real model's next-token probabilities for the background slides.

The tutorial's "a model predicts the next token" slides show measured numbers,
not invented ones. This script loads a small Qwen2.5-family model, feeds a few
prompts as plain text (no chat template) and records the top candidates for
the next token at temperature 1, plus how temperature reshapes them.

    python next_token_probe.py [--model Qwen/Qwen2.5-VL-3B-Instruct] [--out probe.json]

Needs torch + transformers and the model in the local Hugging Face cache
(nothing is uploaded anywhere). The committed ``next_token_probe.json`` is
what the deck builder reads, so building the deck needs neither.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent

PROMPTS = {
    "physics_en": "Seismic waves refracted along the Moho and recorded far from the source are called head",
    "lab_en": "Our observatory's reference model puts the Moho under the Kii Channel at a depth of ",
    "physics_ja": "地震波には P 波と S 波があり、先に到達するのは",
    "lab_ja": "私たちの観測所の参照モデルでは、紀伊水道の下のモホ面の深さは ",
    # The same question with the retrieved model passage in front — what RAG does.
    "lab_rag_en": (
        "KRSO-1D v2 (Kinki Regional Seismic Observatory reference model): upper crust "
        "0-18 km, 5.9 km/s; lower crust 18-28 km, 6.7 km/s; mantle below the Moho at "
        "28 km, 7.9 km/s.\n\n"
        "Our observatory's reference model puts the Moho under the Kii Channel at a depth of "
    ),
    "lab_rag_ja": (
        "KRSO-1D v2(近畿地域地震観測所の参照モデル):上部地殻 0〜18 km は 5.9 km/s、"
        "下部地殻 18〜28 km は 6.7 km/s、深さ 28 km のモホ面より下のマントルは 7.9 km/s。\n\n"
        "私たちの観測所の参照モデルでは、紀伊水道の下のモホ面の深さは "
    ),
    # Review 2026-11-05 ("how do we make RAG answer right?"): the same passage with
    # an instruction to answer from it only — one lever a harness can pull.
    "lab_rag_strict_en": (
        "KRSO-1D v2 (Kinki Regional Seismic Observatory reference model): upper crust "
        "0-18 km, 5.9 km/s; lower crust 18-28 km, 6.7 km/s; mantle below the Moho at "
        "28 km, 7.9 km/s.\n\n"
        "Answer from the reference above only, and copy its numbers exactly.\n\n"
        "Our observatory's reference model puts the Moho under the Kii Channel at a depth of "
    ),
    "lab_rag_strict_ja": (
        "KRSO-1D v2(近畿地域地震観測所の参照モデル):上部地殻 0〜18 km は 5.9 km/s、"
        "下部地殻 18〜28 km は 6.7 km/s、深さ 28 km のモホ面より下のマントルは 7.9 km/s。\n\n"
        "上の資料だけをもとに答え、数値はそのまま写すこと。\n\n"
        "私たちの観測所の参照モデルでは、紀伊水道の下のモホ面の深さは "
    ),
}
TOKEN_DEMO = (
    "The Pn wave arrived 33.5 s after the earthquake.",
    "紀伊水道の地震の深さは 12 km だった。",
    "p_picks.csv",
)
TEMPERATURES = (0.2, 1.0, 1.5)
# The RAG check scores whole answers, not first digits: P("28 km") is
# p("2") * p("8" | "2") * p(no further digit | "28"), at temperatures 1 and 0.4,
# plus the argmax answer.
ANSWER_PROMPTS = (
    "lab_en",
    "lab_rag_en",
    "lab_rag_strict_en",
    "lab_ja",
    "lab_rag_ja",
    "lab_rag_strict_ja",
)
ANSWER = "28"
R4_TEMPERATURE = 0.4  # the R4 agent's default (companion/agent.py _initial_temperature)
TOP_K = 6


def load(model_id: str):
    import torch
    from transformers import AutoModelForImageTextToText, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_id)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    model = AutoModelForImageTextToText.from_pretrained(model_id, torch_dtype=dtype).to(device)
    model.eval()
    return tok, model, device


def next_token_logits(tok, model, device, text: str):
    import torch

    ids = tok(text, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model(**ids)
    return out.logits[0, -1].float().cpu()


def top_at(tok, logits, temperature: float) -> list[dict]:
    import torch

    probs = torch.softmax(logits / temperature, dim=-1)
    values, idx = torch.topk(probs, TOP_K)
    return [
        {"token": tok.decode([int(i)]), "p": round(float(v), 4)}
        for v, i in zip(values, idx, strict=True)
    ]


def entropy_bits(logits) -> float:
    import torch

    p = torch.softmax(logits, dim=-1)
    return round(float(-(p * torch.log2(p.clamp_min(1e-12))).sum()), 2)


def _probs(tok, model, device, text: str, temperature: float):
    import torch

    return torch.softmax(next_token_logits(tok, model, device, text) / temperature, dim=-1)


def answer_probability(tok, model, device, text: str, answer: str, temperature: float) -> float:
    """Probability that the next tokens spell ``answer`` and then stop the number."""
    prob = 1.0
    for ch in answer:
        prob *= float(_probs(tok, model, device, text, temperature)[tok.convert_tokens_to_ids(ch)])
        text += ch
    digits = [tok.convert_tokens_to_ids(str(d)) for d in range(10)]
    return round(
        prob * (1.0 - float(_probs(tok, model, device, text, temperature)[digits].sum())), 4
    )


def greedy_answer(tok, model, device, text: str, steps: int = 4) -> str:
    """Most likely continuation, one argmax token at a time (no repetition penalty)."""
    out = ""
    for _ in range(steps):
        piece = tok.decode([int(next_token_logits(tok, model, device, text + out).argmax())])
        out += piece
        if not piece.strip().isdigit():
            break
    return out.strip()


def answers(tok, model, device, text: str) -> dict:
    row = {
        str(t): answer_probability(tok, model, device, text, ANSWER, t)
        for t in (1.0, R4_TEMPERATURE)
    }
    return {**row, "greedy": greedy_answer(tok, model, device, text)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Record next-token probabilities")
    parser.add_argument("--model", default="Qwen/Qwen2.5-VL-3B-Instruct")
    parser.add_argument("--out", type=Path, default=HERE / "next_token_probe.json")
    args = parser.parse_args()
    tok, model, device = load(args.model)
    result = {"model": args.model, "vocab_size": len(tok), "prompts": {}}
    result["token_demo"] = [
        {"text": t, "chars": len(t), "tokens": [tok.decode([i]) for i in tok.encode(t)]}
        for t in TOKEN_DEMO
    ]
    for key, text in PROMPTS.items():
        logits = next_token_logits(tok, model, device, text)
        result["prompts"][key] = {
            "text": text,
            "tokens": [tok.decode([i]) for i in tok.encode(text)],
            "entropy_bits": entropy_bits(logits),
            "top": {str(t): top_at(tok, logits, t) for t in TEMPERATURES},
        }
    result["answer_28"] = {key: answers(tok, model, device, PROMPTS[key]) for key in ANSWER_PROMPTS}
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {args.out} ({len(PROMPTS)} prompts, log2(vocab) = {math.log2(len(tok)):.1f} bits)"
    )


if __name__ == "__main__":
    main()
