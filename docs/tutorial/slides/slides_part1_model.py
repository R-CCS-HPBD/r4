"""Part 1 background, first half: what a language model actually does.

Every number on these slides is measured, not illustrative: tokens and
next-token probabilities come from ``docs/tutorial/background/next_token_probe.json``
(a Qwen2.5-family model run locally). R4's own settings (temperature, the
context layout, the window size) are cited from the code in the speaker notes.

Scope pills (deck_kit.scope_tag) mark each mechanism as a general pattern of
language models and agent harnesses, or as R4's own setting or choice.
"""

from __future__ import annotations

import context_bar
import harness_map
import slides_part1_overview
from deck_kit import (
    AMBER,
    BODY_TOP,
    CONTENT_W,
    INK,
    MARGIN,
    MUTED,
    NAVY,
    OCEAN,
    SCOPE_H,
    SEAFOAM,
    SLIDE_W,
    TINT,
    TINT_AMBER,
    WHITE,
    Deck,
    Style,
    bar_chart,
    bullets,
    card,
    content,
    lang,
    notes,
    rect,
    scope_tag,
    table,
    text_box,
)
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches

KICK = ("Part 1 · Under the hood — the model", "第 1 部 · 中身を見る — モデル")


def _show(token: str) -> str:
    """Make a token's leading space visible."""
    return token.replace(" ", "␣") if token.strip() else "␣"


def _tag_on_edge(slide, right, top_edge, kind: str) -> None:
    """Scope pill straddling a panel's top edge, its right end inset from ``right``."""
    pill = scope_tag(slide, right, top_edge - SCOPE_H // 2, kind)
    pill.left = int(right - pill.width - Inches(0.15))


def _panel(slide, x, y, w, h, title, body, fill=TINT, size=12) -> None:
    """Rounded panel: 14 pt bold title, then a paragraph or a bullet list."""
    rect(slide, x, y, w, h, fill=fill, rounded=True)
    pad = Inches(0.18)
    text_box(
        slide, x + pad, y + Inches(0.17), w - 2 * pad, Inches(0.3), title, Style(14, INK, True)
    )
    top, inner_w, inner_h = y + Inches(0.5), w - 2 * pad, h - Inches(0.6)
    if isinstance(body, list):
        bullets(slide, x + pad, top, inner_w, inner_h, body, Style(size, INK), gap=4)
    else:
        text_box(slide, x + pad, top, inner_w, inner_h, body, Style(size, INK))


def _prompt_bar(slide, text: str, w, size: float) -> None:
    rect(slide, MARGIN, BODY_TOP, w, Inches(0.75), fill=NAVY, rounded=True)
    text_box(
        slide,
        MARGIN + Inches(0.25),
        BODY_TOP + Inches(0.04),
        w - Inches(0.4),
        Inches(0.67),
        f"“{text.strip()} ___”",
        Style(size, WHITE),
        anchor=MSO_ANCHOR.MIDDLE,
    )


def token_row(slide, x, y, w, tokens, fill=TINT, size=14):
    """Tokens as chips, wrapped onto new lines inside width ``w``."""
    cx, cy = x, y
    for tok in tokens:
        label = _show(tok)
        cw = Inches(0.22 + 0.13 * max(len(label), 1))
        if cx + cw > x + w:
            cx, cy = x, cy + Inches(0.5)
        rect(slide, cx, cy, cw, Inches(0.4), fill=fill, line=SEAFOAM, rounded=True)
        text_box(
            slide,
            cx,
            cy + Inches(0.07),
            cw,
            Inches(0.3),
            label,
            Style(size, INK, mono=True),
            align=PP_ALIGN.CENTER,
        )
        cx += cw + Inches(0.08)
    return cy + Inches(0.5)


# ── 10 · tokens ──────────────────────────────────────────────────────────────


def _token_demos(slide, probe: dict) -> None:
    y = BODY_TOP
    for demo in probe["token_demo"]:
        text_box(
            slide,
            MARGIN,
            y,
            Inches(8.4),
            Inches(0.35),
            f"“{demo['text']}” — {demo['chars']} "
            + ("chars" if lang() == "en" else "文字")
            + f" → {len(demo['tokens'])} "
            + ("tokens" if lang() == "en" else "トークン"),
            Style(14, MUTED),
        )
        y = token_row(slide, MARGIN, y + Inches(0.42), Inches(8.4), demo["tokens"]) + Inches(0.12)


def tokens_slide(deck: Deck, probe: dict) -> None:
    slide = content(
        deck,
        ("Text becomes tokens", "文章はトークン(モデルが読む単位)になる"),
        KICK,
        ("20–55 min", "20–55 分"),
        chip_right=SLIDE_W - MARGIN - harness_map.MINI_W - Inches(0.15),
    )
    harness_map.mini(slide, "model")
    _token_demos(slide, probe)
    vocab = probe["vocab_size"]
    x, w = MARGIN + Inches(8.8), CONTENT_W - Inches(8.8)
    card(
        slide,
        x,
        BODY_TOP,
        w,
        Inches(4.75),
        ("What to notice", "注目点"),
        body_size=13,
        items=[
            (
                f"The model only ever sees token numbers. How many kinds? The vocabulary size "
                f"— **{vocab:,} for this model** — is settled by the model's "
                "**tokenizer** (its text↔number dictionary); other models differ (tens of "
                "thousands to a few hundred thousand).",
                f"モデルが見るのはトークンの番号だけ。トークンの種類の数を**語彙サイズ**と呼び、"
                f"このモデル(Qwen2.5 系)では **{vocab:,}**。語彙サイズはモデルごとの"
                "**トークナイザ**(文章↔番号の変換辞書)で決まり、数万〜数十万とモデルによって"
                "異なる。",
            ),
            (
                "Japanese costs more tokens per character than English.",
                "日本語は英語より 1 文字あたりのトークン数が多い。",
            ),
            (
                "**33.5 is four tokens: 3 · 3 · . · 5.** The model never sees the number — "
                "so arithmetic belongs in a tool.",
                "**33.5 は 3・3・.・5 の 4 トークン。**モデルは数を数値として見ていない。"
                "計算はツールに任せる。",
            ),
        ],
    )
    _tag_on_edge(slide, x + w, BODY_TOP, "general")
    notes(
        slide,
        (
            "Tokens measured with the Qwen2.5 tokenizer (same family as the stack's default "
            "qwen2.5:7b). ␣ marks a leading space. Token counts matter twice: the context "
            "window is measured in tokens, and hosted APIs bill per token.\n"
            "Scope: general. Every model works this way (Claude, GPT with tiktoken, Qwen, "
            "Llama), and harnesses such as Claude Code or the OpenAI Agents SDK only send "
            "text. R4 is no different: it never runs a tokenizer itself; the model server "
            "(Ollama on your laptop, vLLM on the cluster) splits the text. R4 only estimates "
            "counts: characters ÷ a ratio that starts at 3.5 and is re-fitted from every "
            "reply's usage.prompt_tokens (moving average, kept within 1.5–8; "
            "r4/src/r4/companion/context.py:44-57). The harness slides come back to this.",
            "Qwen2.5 のトークナイザ(スタック既定の qwen2.5:7b と同系統)で測定。␣ は先頭の"
            "空白。トークン数は 2 つの意味で重要:コンテキストウィンドウの大きさはトークンで数え、"
            "外部の AI サービスはトークン単位で課金する。\n"
            "区分:一般的な仕組み。どのモデルも同じ(Claude、tiktoken を使う GPT、Qwen、Llama)"
            "で、Claude Code や OpenAI Agents SDK などのハーネスは文章を送るだけ。R4 も同じで、"
            "トークナイザを自分では動かさず、分割はモデルサーバ(手元では Ollama、クラスタでは "
            "vLLM)が行う。R4 はトークン数を推定するだけ:文字数 ÷ 比率。比率は 3.5 から始まり、応答ごとの "
            "usage.prompt_tokens で補正される(移動平均、1.5〜8 の範囲。"
            "r4/src/r4/companion/context.py:44-57)。ハーネスのスライドで再び扱う。",
        ),
    )


# ── 11 · next-token probabilities and temperature ────────────────────────────

_LEFT_W = Inches(6.9)


def _temperature_chart(slide, entry: dict) -> None:
    """Grouped bars: the top-4 tokens at T = 1.0, measured at three temperatures."""
    cats = [_show(t["token"]) for t in entry["top"]["1.0"][:4]]
    prefix = "T = " if lang() == "en" else "温度 T = "
    series = {}
    for temp in ("0.2", "1.0", "1.5"):
        by_tok = {_show(t["token"]): t["p"] for t in entry["top"][temp]}
        series[prefix + temp] = [by_tok.get(c, 0.0) for c in cats]
    bar_chart(
        slide,
        MARGIN,
        BODY_TOP + Inches(0.9),
        _LEFT_W,
        Inches(3.8),
        cats,
        series,
        [NAVY, OCEAN, AMBER],
        horizontal=False,
        max_value=1.0,
    )


def _entropy_text(entry: dict) -> tuple[str, str]:
    bits = entry["entropy_bits"]
    top = entry["top"]["1.0"][0]["p"]
    sure_en = "The model is sure." if bits < 2 else f"Fairly sure (top token {top:.0%} at T = 1.0)."
    sure_ja = (
        "モデルは確信している。"
        if bits < 2
        else f"モデルはかなり確信している(T = 1.0 で最上位が {top:.0%})。"
    )
    return (
        f"Uncertainty = entropy H = −Σ p · log₂ p (bits): here (T = 1.0) **{bits}**. 0 means "
        f"certain, 17.2 means every candidate equally likely. {sure_en}",
        f"不確かさ = エントロピー H = −Σ p · log₂ p(ビット):今回(T = 1.0)は **{bits}**。"
        f"0 は確実、17.2 は全候補が等確率(まったく分からない)。{sure_ja}",
    )


def _process_column(slide, x, w, entry: dict) -> None:
    text_box(
        slide,
        x,
        BODY_TOP,
        w,
        Inches(0.62),
        (
            "Read all tokens so far → score every possible next token (sum = 1) → pick one "
            "→ append → repeat: that is “writing”.",
            "ここまでの全トークンを読む → 次に来うる全トークンに確率を付ける(合計 1)→ "
            "1 つ選んで追加 → 繰り返す。これが「文章を書く」ということ。",
        ),
        Style(12, INK, True),
    )
    text_box(
        slide,
        x,
        BODY_TOP + Inches(0.7),
        w,
        Inches(0.62),
        (
            "Where the probabilities come from: every candidate gets a raw score (a logit, z_i); "
            "softmax turns scores into probabilities — p_i = exp(z_i) / Σ_j exp(z_j) "
            "(so they always sum to 1).",
            "確率の出し方:全候補に生の点数(ロジット z_i)を付け、softmax で確率に変換する。"
            "p_i = exp(z_i) / Σ_j exp(z_j)(合計は必ず 1)。",
        ),
        Style(12, MUTED),
    )
    rect(slide, x, BODY_TOP + Inches(1.4), w, Inches(0.92), fill=TINT, rounded=True)
    text_box(
        slide,
        x + Inches(0.18),
        BODY_TOP + Inches(1.5),
        w - Inches(0.36),
        Inches(0.8),
        _entropy_text(entry),
        Style(12, INK),
    )


def _temperature_panel(slide, x, w) -> None:
    y = BODY_TOP + Inches(2.52)
    _panel(
        slide,
        x,
        y,
        w,
        Inches(2.73),
        ("Temperature T", "温度 T"),
        [
            (
                "**p_i ∝ exp(z_i / T)**: the logits are divided by T before the softmax. "
                "Low T sharpens, high T flattens.",
                "**p_i ∝ exp(z_i / T)**:ロジットを T で割ってから softmax。T が小さいほど"
                "分布は鋭く、大きいほど平らになる。",
            ),
            (
                "**Low T** (0–0.3): nearly always the top token, repeatable: analysis. "
                "**High T** (>1): varied: brainstorming, not numbers.",
                "**低温**(0–0.3):ほぼ常に最上位を選ぶ。再現性が高く解析向き。**高温**(>1):"
                "答えが多様になり、発想向きだが数値には不向き。",
            ),
            (
                "Same prompt, different answer? Often just sampling (picking a token at "
                "random by its probability).",
                "同じ依頼でも答えが毎回違う? 多くの場合、確率に従ってトークンを選ぶ仕組み"
                "(サンプリング)が原因。",
            ),
            (
                "**In R4:** T = 0.4 by default; for repeatable analysis start r4 with the "
                "environment variable R4_COMPANION_TEMPERATURE=0.",
                "**R4 では:**既定は T = 0.4。再現性を重視する解析では、環境変数 "
                "R4_COMPANION_TEMPERATURE=0 を設定して r4 を起動する。",
            ),
        ],
        fill=TINT_AMBER,
    )
    _tag_on_edge(slide, x + w, y, "r4tuned")


def next_token_slide(deck: Deck, probe: dict) -> None:
    slide = content(
        deck,
        ("Next-token probabilities — and temperature", "次のトークンの確率と温度"),
        KICK,
    )
    harness_map.mini(slide, "model")
    entry = probe["prompts"]["physics_en" if lang() == "en" else "physics_ja"]
    _prompt_bar(slide, entry["text"], _LEFT_W, 15)
    _tag_on_edge(slide, MARGIN + _LEFT_W, BODY_TOP, "general")
    _temperature_chart(slide, entry)
    x = MARGIN + Inches(7.2)
    w = CONTENT_W - Inches(7.2)
    _process_column(slide, x, w, entry)
    _temperature_panel(slide, x, w)
    _next_token_notes(slide, entry["top"]["1.0"][0]["p"])


def _next_token_notes(slide, top: float) -> None:
    notes(
        slide,
        (
            "Measured: Qwen2.5 3B, plain text; bars = top-token probabilities at three "
            "temperatures. Temperature never adds knowledge. Physics seen often in training "
            f"already gives a sharp distribution (top token {top:.0%} even at T = 1.0).\n"
            "Scope: next-token scoring is the model's own work, the same behind every "
            "harness. Temperature is a general sampling knob every API exposes; R4's "
            "settings are the value and the switch (pill: GENERAL · R4 SETTINGS). "
            "R4 sends T = 0.4 on every request "
            "(_DEFAULT_TEMPERATURE, r4/src/r4/companion/agent.py:248); "
            "R4_COMPANION_TEMPERATURE (0–2) overrides it (_initial_temperature, "
            "agent.py:260-265); top_p is sent only when R4_COMPANION_TOP_P is set "
            "(agent.py:2518-2521, _sampling 4737-4742). Every agent reads the same setting, "
            "and a spawned sub-agent gets its parent's temperature (agent.py:4445, 4538). "
            "For comparison: Claude Code does not expose temperature (the API default "
            "applies); the OpenAI Agents SDK sets it per agent with "
            "ModelSettings(temperature=…).",
            "測定値:Qwen2.5 3B、プレーンテキスト。棒は 3 つの温度での上位トークンの確率。"
            "温度は知識を増やさない。学習で何度も見た物理の知識なら、分布はもともと鋭い"
            f"(T = 1.0 でも最上位が {top:.0%})。\n"
            "区分:次のトークンの確率はモデル自身の計算で、どのハーネスでも同じ。温度はどの API "
            "にもある一般的な設定で、R4 の設定は値と切り替え方(印は「一般的な仕組み・R4 の設定」)。"
            "R4 は毎回 T = 0.4 を送る"
            "(_DEFAULT_TEMPERATURE、r4/src/r4/companion/agent.py:248)。"
            "R4_COMPANION_TEMPERATURE(0〜2)で変更できる(_initial_temperature、"
            "agent.py:260-265)。top_p は R4_COMPANION_TOP_P を設定したときだけ送る"
            "(agent.py:2518-2521、_sampling 4737-4742)。サブエージェントも同じ設定を読み、"
            "起動時に親の温度を受け取る(agent.py:4445、4538)。比較:Claude Code は温度を"
            "設定させない(API の既定値)。OpenAI Agents SDK はエージェントごとに "
            "ModelSettings(temperature=…) で指定する。",
        ),
    )


# ── 12 · hallucination ───────────────────────────────────────────────────────


def _probs(entry: dict, temp: str = "1.0", n: int = 6):
    top = entry["top"][temp][:n]
    return [_show(t["token"]) for t in top], [t["p"] for t in top]


def _hallucination_cards(slide, entry: dict, ref_bits: float) -> None:
    x = MARGIN + Inches(7.8)
    w = CONTENT_W - Inches(7.8)
    low = entry["top"]["0.2"][0]
    bits = entry["entropy_bits"]
    card(
        slide,
        x,
        BODY_TOP,
        w,
        Inches(2.6),
        ("Read the numbers", "数値を読む"),
        body_size=14,
        items=[
            (
                f"Spread over several digits: **{bits} bits** of uncertainty (entropy; the "
                f"physics prompt on the last slide: {ref_bits}).",
                f"確率が複数の数字に散らばる:不確かさ(エントロピー)は **{bits} ビット**"
                f"(前のスライドの例は {ref_bits})。",
            ),
            (
                f"At low temperature it says “{low['token']}” with {low['p']:.0%} — "
                "fluent, confident, invented.",
                f"低温では「{low['token']}」を {low['p']:.0%} で選ぶ。流暢で自信ありげだが、作り話。",
            ),
        ],
    )
    box_y = BODY_TOP + Inches(2.85)
    rect(slide, x, box_y, w, Inches(1.7), fill=TINT_AMBER, rounded=True)
    _tag_on_edge(slide, x + w, box_y, "general")
    text_box(
        slide,
        x + Inches(0.2),
        BODY_TOP + Inches(3.05),
        w - Inches(0.4),
        Inches(1.4),
        (
            "**This is a “hallucination”:** not a bug, the model picking tokens as usual "
            "with nothing to go on. Fluency is not knowledge.",
            "**これが「ハルシネーション」(もっともらしい作り話):**バグではなく、根拠がない"
            "ときにもモデルがいつもどおりトークンを選んだ結果。流暢さは知識ではない。",
        ),
        Style(14, INK),
    )
    text_box(
        slide,
        x,
        BODY_TOP + Inches(4.7),
        w,
        Inches(0.45),
        (
            "Measured on a real model: Qwen2.5 3B run locally (docs/tutorial/background/).",
            "実測値:ローカルで動かした実際の Qwen2.5 3B モデル(docs/tutorial/background/)。",
        ),
        Style(11, MUTED, italic=True),
    )


def _hallucination_notes(slide, entry: dict) -> None:
    toks = [t["token"] for t in entry["top"]["1.0"]]
    rank = toks.index("2") + 1
    p2 = entry["top"]["1.0"][rank - 1]["p"]
    notes(
        slide,
        (
            "Measured. The invented observatory's Moho depth appears nowhere in training data, "
            "so the distribution spreads over digits (the reference model's value that RAG "
            f"supplies later, 28 km, starts with 2: choice #{rank} here, {p2:.0%}). "
            "The RAG slide comes back to this exact prompt.\n"
            "Scope: general. This is model behaviour, the same behind every harness; "
            "harnesses differ only in counter-measures (retrieval, citations, verifiers). "
            "R4's: RAG passages placed in the context (later slides) and evidence rules in "
            "its base prompt ('If you cannot verify a claim, say \"I cannot verify this\"', "
            "r4/src/r4/companion/agent.py:581-592).",
            "測定値。架空の観測所のモホ面の深さは学習データにないため、確率は数字全体に散らばる"
            f"(RAG で渡す参照モデルの値 28 km の先頭「2」は、ここでは {rank} 番目、{p2:.0%})。RAG のスライドで同じ"
            "プロンプトに戻る。\n"
            "区分:一般的な仕組み。モデルの振る舞いなので、どのハーネスでも同じ。ハーネスごとに"
            "違うのは対策だけ(検索、出典の明示、検証)。R4 の対策は、RAG の検索結果をコンテキスト"
            "に入れること(後のスライド)と、基本プロンプトの根拠ルール(「検証できない主張は"
            "検証できないと言う」、r4/src/r4/companion/agent.py:581-592)。",
        ),
    )


def hallucination_slide(deck: Deck, probe: dict) -> None:
    slide = content(
        deck,
        (
            "When the model does not know, it still picks a token",
            "知らなくても、モデルはトークンを選ぶ",
        ),
        KICK,
    )
    harness_map.mini(slide, "model")
    entry = probe["prompts"]["lab_en" if lang() == "en" else "lab_ja"]
    _prompt_bar(slide, entry["text"], Inches(7.4), 15)
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(0.8),
        Inches(7.4),
        Inches(0.28),
        (
            "Moho = the crust–mantle boundary · bars: probabilities at T = 1.0",
            "モホ面 = 地殻とマントルの境界 · 棒は T = 1.0 での確率",
        ),
        Style(11, MUTED),
    )
    cats, vals = _probs(entry)
    bar_chart(
        slide,
        MARGIN,
        BODY_TOP + Inches(1.1),
        Inches(7.4),
        Inches(3.6),
        cats,
        {"p": vals},
        [AMBER],
        max_value=1.0,
    )
    physics = probe["prompts"]["physics_en" if lang() == "en" else "physics_ja"]
    _hallucination_cards(slide, entry, physics["entropy_bits"])
    _hallucination_notes(slide, entry)


# ── 13 · parameters, cutoff, quantization ────────────────────────────────────


def _training_cards(slide) -> None:
    card(
        slide,
        MARGIN,
        BODY_TOP,
        Inches(5.6),
        Inches(2.2),
        ("Training (once, by the maker)", "学習(モデル開発者が 1 回だけ)"),
        body_size=14,
        items=[
            ("trillions of tokens of public text", "数兆トークンの公開テキスト"),
            ("adjusts billions of numbers (parameters)", "数十億個の数値(パラメータ)を調整"),
            ("ends at a **knowledge cutoff** date", "知識はある時点(**カットオフ**)で止まる"),
        ],
    )
    _tag_on_edge(slide, MARGIN + Inches(5.6), BODY_TOP, "general")
    card(
        slide,
        MARGIN,
        BODY_TOP + Inches(2.45),
        Inches(5.6),
        Inches(2.3),
        (
            "Inference (every question, laptop or cluster)",
            "推論(質問のたび、PC またはクラスタで)",
        ),
        fill=TINT_AMBER,
        body_size=13,
        items=[
            (
                "inference = the trained model producing an answer",
                "推論 = 学習済みモデルで回答を作ること(論理的な推論とは別)",
            ),
            ("parameters are read, **never changed**", "パラメータは読むだけで**変わらない**"),
            ("your chat does not teach the model", "会話でモデルは学習しない"),
            (
                "new knowledge must come in through the context",
                "新しい知識は入力(コンテキスト)で与える",
            ),
        ],
    )


def _size_table(slide) -> None:
    x, y = MARGIN + Inches(6.0), BODY_TOP + Inches(0.15)
    rows = [
        [
            ("Model (stack picks by memory)", "モデル(PC のメモリ量で自動選択)"),
            ("Parameters\n(B = billion)", "パラメータ数\n(B = 10 億)"),
            ("16-bit", "16 ビット"),
            ("4-bit quantized\n(what you download)", "4 ビット量子化版\n(通常はこの形)"),
        ],
        ["llama3.2:3b", "3 B", "≈ 6 GB", "≈ 2 GB"],
        ["qwen2.5:7b", "7 B", "≈ 14 GB", "≈ 4.7 GB"],
        ["qwen2.5:14b", "14 B", "≈ 28 GB", "≈ 9 GB"],
        [
            "qwen3:30b (MoE)",
            ("30 B\n(≈3 B per token)", "30 B\n(1 トークンで ≈3 B を使う)"),
            "≈ 60 GB",
            "≈ 18.6 GB",
        ],
    ]
    table(
        slide,
        x,
        y,
        CONTENT_W - Inches(6.0),
        rows,
        [2.0, 1.75, 1.0, 1.85],
        size=13,
        row_h=Inches(0.55),
    )
    pill = scope_tag(slide, x, y - SCOPE_H - Inches(0.04), "r4tuned")
    pill.left = int(MARGIN + CONTENT_W - pill.width)
    text_box(
        slide,
        x,
        BODY_TOP + Inches(3.1),
        CONTENT_W - Inches(6.0),
        Inches(1.6),
        (
            "Memory ≈ parameters × (bytes per parameter). **Quantizing** (量子化) = storing "
            "each number in fewer bits to shrink the model. You normally download the 4-bit "
            "version: a 7-billion (7 B) parameter model then fits on a laptop in ≈ 4.7 GB, at "
            "some cost in answer quality. **MoE** (mixture of experts) = a model that uses "
            "only part of its parameters for each token.",
            "メモリ ≈ パラメータ数 ×(1 個あたりのバイト数)。**量子化(quantization)**= "
            "各数値のビット数を減らして容量を減らすこと。通常ダウンロードするのは 4 ビット版で、"
            "70 億(7 B)パラメータのモデルでも約 4.7 GB と PC に収まる(その分、回答の質は少し"
            "落ちる)。**MoE(混合エキスパート)**= 1 トークンごとに一部のパラメータだけで計算する"
            "モデル。",
        ),
        Style(13, MUTED),
    )


def training_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "Where the knowledge lives: frozen parameters",
            "知識の在りか:学習後に固定されるパラメータ",
        ),
        KICK,
    )
    harness_map.mini(slide, "model")
    _training_cards(slide)
    _size_table(slide)
    notes(
        slide,
        (
            "Sizes are approximate (weights only; the context needs extra memory). Key "
            "message for scientists: the model cannot read your lab notebook by itself — the "
            "harness must put it into the context.\n"
            "qwen3:30b is a mixture-of-experts model (MoE): 30 B stored, ≈3 B active per "
            "token. It is picked only at ≥ 48 GB because its weights take 18.6 GB (+3 GiB "
            "per 32k slot), and R4 sizes its RAG pack as for a medium model "
            "(deploy/standalone/lib/model.sh:4-10, 36-39; r4_contracts.models.model_size_class).\n"
            "Scope: parameters, cutoff and quantization are general (every hosted or local "
            "model). R4's setting: the stand-alone stack picks the model from the host's "
            "memory (pick_model, deploy/standalone/lib/model.sh:23-32).",
            "サイズは概算(重みのみ。コンテキスト分のメモリが別途必要)。研究者への要点:モデルは"
            "研究ノートを自分では読めない。ハーネスがコンテキストに入れて渡す必要がある。\n"
            "qwen3:30b は MoE(混合エキスパート)モデル:保存は 30 B、1 トークンあたり実際に動く"
            "のは約 3 B。重みだけで 18.6 GB(さらに 32k の枠ごとに 3 GiB)要るため 48 GB 以上で"
            "のみ選ばれ、R4 は RAG の検索結果の量を中規模モデル向けに決める"
            "(deploy/standalone/lib/model.sh:4-10, 36-39、r4_contracts.models.model_size_class)。\n"
            "区分:パラメータ・カットオフ・量子化は一般的な仕組み(外部・手元を問わずどのモデル"
            "でも同じ)。R4 の設定は、単体構成が PC のメモリ量からモデルを選ぶ点"
            "(pick_model、deploy/standalone/lib/model.sh:23-32)。",
        ),
    )


# ── 14 · the context window ──────────────────────────────────────────────────

_LEFT_COL_W = Inches(7.55)
_BAR_TOP = BODY_TOP + Inches(1.12)
_BAR_H = Inches(0.55)

# One example turn on qwen2.5:7b (window 32,768). Tools ≈ 10.5k is documented
# (deploy/standalone/env/base.env:32-34); the system part is measured (base prompt
# 2,493 chars ≈ 0.7k tokens + skill catalog ≈ 0.5k + instruction files ≤ 8,000
# chars ≈ 2.3k); history, message and this turn's rounds are illustrative.
_FILL = [
    ("system", 2_500, ("1", "1")),
    ("tools", 10_500, ("2 tool definitions ≈ 10.5k", "2 ツール定義 約 1.05 万")),
    ("history", 5_000, ("3 earlier turns", "3 過去のターン")),
    ("message", 1_500, ("4", "4")),
    ("rounds", 4_000, ("5 this turn", "5 このターン")),
    ("reply", 4_096, ("reply", "応答用")),
    ("free", 32_768 - 27_596, ("free", "空き")),
]
_FILL_LEGEND = [
    (
        "system",
        (
            "1 System rules: R4's base prompt, workspace, skill catalog, your R4.md / "
            "AGENTS.md (≤ 8,000 chars) — the same every turn",
            "1 システムの指示:R4 の基本指示・作業フォルダ・スキル一覧・R4.md / AGENTS.md"
            "(計 8,000 字まで)。毎ターン同じ",
        ),
    ),
    (
        "tools",
        (
            "2 Tool definitions: every tool's name, description and arguments, sent in a "
            "separate tools field — Qwen's chat template writes them right after the rules",
            "2 ツール定義:全ツールの名前・説明・引数。別の tools 欄で送るが、Qwen のチャット"
            "テンプレートはシステムの指示の直後に書き込む",
        ),
    ),
    (
        "history",
        (
            "3 Earlier turns: questions and answers as text only (their tool calls are not "
            "re-sent)",
            "3 過去のターン:質問と回答のテキストだけ(ツールのやり取りは送り直さない)",
        ),
    ),
    (
        "message",
        (
            "4 Your message + a <turn_context> block: active skill bodies, memory, project state",
            "4 依頼文+ <turn_context> ブロック:有効なスキルの本文・記憶・プロジェクトの状態",
        ),
    ),
    (
        "rounds",
        (
            "5 This turn's tool calls and results — grows with every round",
            "5 このターンのツール呼び出しと結果。ラウンドごとに増える",
        ),
    ),
    (
        "reply",
        ("Kept free for the model's reply (4,096 tokens)", "モデルの返答のために空けておく(4,096)"),
    ),
]
_ORIGIN = [
    (0, 2_500, ("system message", "system メッセージ")),
    (2_500, 13_000, ("tools field (placed by the template)", "tools 欄(テンプレートが配置)")),
    (
        13_000,
        23_500,
        ("messages: history · your message · this turn", "messages:履歴・依頼文・このターン"),
    ),
]


def _origin_row(slide) -> None:
    """Which part of the JSON request each stretch of the bar comes from."""
    y = _BAR_TOP - Inches(0.3)
    for start, end, label in _ORIGIN:
        x0 = MARGIN + int(_LEFT_COL_W * start / 32_768)
        x1 = MARGIN + int(_LEFT_COL_W * end / 32_768)
        rect(
            slide,
            x0 + Inches(0.02),
            y + Inches(0.24),
            x1 - x0 - Inches(0.04),
            Inches(0.03),
            fill=MUTED,
        )
        text_box(
            slide,
            x0 - Inches(0.3),
            y,
            x1 - x0 + Inches(0.6),
            Inches(0.22),
            label,
            Style(9, MUTED, True),
            align=PP_ALIGN.CENTER,
        )


def _context_bands(slide) -> None:
    text_box(
        slide,
        MARGIN,
        BODY_TOP,
        _LEFT_COL_W,
        Inches(0.5),
        (
            "Context window = the most the model can read in one call (32,768 tokens on the "
            "stand-alone stack). The harness refills it for every call; the model reads it "
            "as one text, left to right (one example turn, to scale):",
            "コンテキストウィンドウ = 1 回の呼び出しでモデルが読める入力の上限(単体構成では "
            "32,768 トークン)。ハーネスは呼び出しのたびに中身を詰め直し、モデルはそれを 1 つの"
            "文章として左から右へ読む(ある 1 ターンの例。長さは実際の比率):",
        ),
        Style(12, MUTED),
    )
    _origin_row(slide)
    context_bar.bar(slide, MARGIN, _BAR_TOP, _LEFT_COL_W, _BAR_H, _FILL)
    context_bar.scale(slide, MARGIN, _BAR_TOP + _BAR_H + Inches(0.02), _LEFT_COL_W)
    col_w = (_LEFT_COL_W - Inches(0.2)) // 2
    top = _BAR_TOP + _BAR_H + Inches(0.36)
    for j in range(2):
        context_bar.swatch_legend(
            slide,
            MARGIN + j * (col_w + Inches(0.2)),
            top,
            col_w,
            _FILL_LEGEND[3 * j : 3 * j + 3],
            size=10,
            row_h=Inches(0.52),
        )
    _tag_on_edge(slide, MARGIN + _LEFT_COL_W, _BAR_TOP, "r4tuned")


_BANDS_END = _BAR_TOP + _BAR_H + Inches(0.36) + 3 * Inches(0.52)


def _context_footers(slide) -> None:
    text_box(
        slide,
        MARGIN,
        _BANDS_END + Inches(0.08),
        _LEFT_COL_W,
        Inches(0.62),
        (
            "R4 sends **one JSON document** (OpenAI-compatible chat/completions): messages + a "
            "separate tools field. The model server's **chat template** turns it into the "
            "single text above; where the tools land depends on that template (Llama 3.2's "
            "puts them next to your last message instead).",
            "R4 が送るのは **1 通の JSON 文書**(OpenAI 互換の chat/completions):messages と、"
            "別の tools 欄。モデルサーバの**チャットテンプレート**がこれを上の 1 つの文章に"
            "変える。ツール定義の位置はテンプレート次第(Llama 3.2 では最後の依頼文の隣に置く)。",
        ),
        Style(12, INK),
    )
    text_box(
        slide,
        MARGIN,
        _BANDS_END + Inches(0.74),
        _LEFT_COL_W,
        Inches(0.3),
        (
            "Even a 1M-token window (≈ 750,000 English words) does not swallow a whole "
            "dataset: compute, don't read.",
            "100 万トークンのウィンドウでも英語で約 75 万語。データセット全体は入らない。"
            "「読ませるより計算させる」。",
        ),
        Style(11, MUTED, italic=True),
    )


_PANELS = [
    (
        ("Stateless", "ステートレス(状態を持たない)"),
        [
            (
                "The model remembers nothing from one call to the next: the harness "
                "re-sends all of it on every call.",
                "モデルは呼び出しと呼び出しの間で何も覚えていない。ハーネスが呼び出しのたびに"
                "全部を送り直す。",
            ),
            (
                "Not in the window = does not exist for the model. An external AI "
                "service would receive exactly this.",
                "ウィンドウにないものは、モデルには存在しない。外部の AI サービスには、"
                "この中身がそのまま届く。",
            ),
        ],
        TINT,
        "general",
        1.55,
    ),
    (
        ("Why this order: the prefix cache", "この順番の理由:先頭部分のキャッシュ"),
        (
            "The model server reuses its computed state (the KV cache) for a start it has "
            "already read. R4 keeps system + tools the same across turns (a denied tool or "
            "an edited R4.md changes them once) and puts what changes at the end of your "
            "message. Within a turn, each call computes only the newly added tool results.",
            "モデルサーバは、前回と同じ先頭部分の計算結果(KV キャッシュと呼ぶ)を再利用できる。"
            "R4 は system と tools をターン間で変えず(ツールの拒否や R4.md の編集で 1 回だけ"
            "変わる)、変わる部分は依頼文の末尾に置く。1 ターン内の呼び出しは、新しいツール"
            "結果だけを計算すればよい。",
        ),
        TINT_AMBER,
        "r4tuned",
        1.8,
    ),
    (
        ("Size costs memory", "ウィンドウにもメモリが要る"),
        (
            "32,768 is Ollama's setting OLLAMA_CONTEXT_LENGTH. qwen2.5:7b needs ≈ 1.75 GiB "
            "per parallel conversation (one 32,768-token slot) on top of its 4.7 GB of "
            "parameters. Your counts: /context.",
            "32,768 は Ollama の設定 OLLAMA_CONTEXT_LENGTH の値。qwen2.5:7b では、同時に扱う"
            "会話 1 本(32,768 トークン分の枠)ごとに、パラメータ本体 4.7 GB とは別に約 "
            "1.75 GiB 要る。確認:/context",
        ),
        TINT,
        "r4tuned",
        1.5,
    ),
]


def _context_panels(slide) -> None:
    x = MARGIN + Inches(7.85)
    w = CONTENT_W - Inches(7.85)
    y = BODY_TOP
    for title, body, fill, kind, height in _PANELS:
        h = Inches(height)
        _panel(slide, x, y, w, h, title, body, fill=fill, size=11)
        _tag_on_edge(slide, x + w, y, kind)
        y += h + Inches(0.15)


_CONTEXT_NOTES = (
    "Scope: a context window, and a stateless model that is re-sent everything on every "
    "call, are general: Claude Code, the OpenAI Agents SDK Runner, opencode and LangChain "
    "agents all re-send the conversation (OpenAI's Responses API can keep it server-side, "
    "which hides the re-send but does not remove it). R4's choices are the order and what "
    "goes where (pill: GENERAL · R4 SETTINGS). "
    "Claude Code also puts volatile state in user messages (<system-reminder> "
    "blocks) but replays tool calls and results across turns until compaction; R4 replays "
    "text only. Prompt caching itself is general (Anthropic cache_control, OpenAI automatic "
    "caching, vLLM automatic prefix caching); R4 orders the request for a self-hosted "
    "server with no cache breakpoints.\n"
    "Review 2026-11-05: the bands became one bar, to scale, in the order the model reads "
    "it. Where the tools go: R4 sends them in the separate tools field "
    "(companion/agent.py:640-654, sorted by name); the model server's chat template "
    "decides where they appear in the text. Ollama's Qwen-family template writes them into "
    "the system turn after the system content ('# Tools … <tools>…</tools>'), before the "
    "history (checked with ollama show --template on a local Qwen-family model, "
    "2026-11-05); Llama 3.2's template renders them only with the last user message "
    "(docs/adr/093-retrieval-for-small-local-models.md:33). Sizes: tools ≈ 10.5k tokens "
    "(deploy/standalone/env/base.env:32-34); base prompt 2,493 chars; history, message and "
    "this turn's rounds are an illustrative example.\n"
    "Layout (wave 80): r4/src/r4/companion/agent.py:4580-4607 (_build_messages: [system, "
    "*history, user + <turn_context>]); agent.py:4609-4633 (_system_message: base prompt, "
    "workspace + skill catalog, R4.md briefing, HPC sites); the block: turn_context.py:69-74. "
    "Turn context parts (skill bodies, memory, session facts, endpoints, UserPromptSubmit "
    "hook output, build-contract orientation): session.py:1137-1151; project state: "
    "agent.py:4596-4604. RAG passages: by default the model calls search_docs and they come "
    "back as a tool result in the history (companion/tools.py:2556-2562); only the opt-in "
    "pre-retrieval router (R4_RAG_ROUTER, off by default, retrieval_router.py:188-192) puts "
    "the pack before the question (retrieval_router.py:968-995). Instruction files and caps: "
    "r4/src/r4/briefing.py:66-81. History is text only: session_turns.py:92-93; tools sorted "
    "by name: session_turns.py:112.\n"
    "Prefix: the tools array stays whole; a skill limit turns calls outside it into a "
    "refusal ('refused this turn') before any hook or approval card (session.py:970-997, "
    "agent.py:5204-5212). What changes the prefix: a tool the policy denies whatever its "
    "arguments (a deny rule for the whole tool, or nobody to ask) is hidden while that deny "
    "holds — a person's no never hides it (ADR-104; agent.py:2787, 2955-2966, 5299-5335) — "
    "a new MCP server, an edited R4.md "
    "(re-read on every request); a hard fit may drop tool definitions that do not fit "
    "(context.py:760-780). Background sub-agent notes (<subagent-notification> blocks, "
    "async_agents.py:57-67) are appended only when one has finished (agent.py:3242-3253).\n"
    "Across turns: within a turn each call adds only the new tool results to a cached "
    "prefix. At the next turn the cache holds through system, tools and older history; "
    "from the previous question on, the server re-reads, because that question is stored "
    "without its <turn_context> and its tool calls are not replayed (session_flow.py:27, "
    "session_turns.py:93).\n"
    "Window: 32,768 = OLLAMA_CONTEXT_LENGTH (deploy/standalone/env/base.env:36). The stack "
    "registers it as the endpoint's max_model_len (deploy/standalone/standalone.sh:697-704) "
    "and R4 uses that published value before its name table (context_budget.py:173-185); "
    "the name table (131,072 for llama3.2 and qwen3) is used only when the endpoint listing "
    "is unreachable. R4's working budget = 0.90 × 32,768 − 4,096 = 25,395 tokens "
    "(context_budget.py:16-17, 82-87). Memory per slot: deploy/standalone/lib/model.sh:34-39.",
    "区分:コンテキストウィンドウと、呼び出しのたびにすべてを送り直すステートレスなモデルは"
    "一般的な仕組み。Claude Code、OpenAI Agents SDK の Runner、opencode、LangChain の"
    "エージェントはどれも会話を毎回送り直す(OpenAI の Responses API はサーバ側に保持できるが、"
    "送り直しが見えなくなるだけで、なくなるわけではない)。R4 の設定は、順番と何をどこに置くか"
    "(印は「一般的な仕組み・R4 の設定」)。"
    "Claude Code も変化する状態を依頼文側(<system-reminder> ブロック)に入れるが、ツールの"
    "呼び出しと結果は要約(コンパクション)までターンをまたいで再送する。R4 はテキストだけを"
    "再送する。プロンプトのキャッシュ自体は一般的(Anthropic の cache_control、OpenAI の自動"
    "キャッシュ、vLLM の自動プレフィックスキャッシュ)。R4 はキャッシュ位置を指定できない自前の"
    "サーバ向けに並べ方を決めている。\n"
    "2026-11-05 のレビュー:帯を、モデルが読む順の 1 本の棒(長さは実際の比率)に変えた。"
    "ツール定義の位置:R4 は別の tools 欄で送り(companion/agent.py:640-654、名前順)、文章の"
    "どこに置くかはモデルサーバのチャットテンプレートが決める。Ollama の Qwen 系のテンプレートは"
    "システムの内容の後(「# Tools … <tools>…</tools>」)、履歴より前に書き込む(手元の Qwen 系"
    "モデルで ollama show --template により 2026-11-05 確認)。Llama 3.2 のテンプレートは"
    "最後の依頼文と一緒にだけ書く(docs/adr/093-retrieval-for-small-local-models.md:33)。"
    "大きさ:ツール定義は約 1.05 万トークン(deploy/standalone/env/base.env:32-34)、基本指示は "
    "2,493 字。履歴・依頼文・このターンの量は説明用の例。\n"
    "並べ方(wave 80):r4/src/r4/companion/agent.py:4580-4607(_build_messages:[system, "
    "*history, user + <turn_context>])。agent.py:4609-4633(_system_message:基本プロンプト、"
    "作業ディレクトリとスキル一覧、R4.md、HPC サイト)。ブロック本体:turn_context.py:69-74。"
    "ターンごとの部品(スキル本文、記憶、セッション情報、エンドポイント、UserPromptSubmit "
    "フックの出力、ビルド契約の案内):session.py:1137-1151。プロジェクト状態:"
    "agent.py:4596-4604。RAG の検索結果:既定ではモデルが search_docs を呼び、ツールの結果"
    "として履歴に入る(companion/tools.py:2556-2562)。既定はオフの事前検索ルータ"
    "(R4_RAG_ROUTER、retrieval_router.py:188-192)を有効にしたときだけ、質問の前に置く"
    "(retrieval_router.py:968-995)。指示ファイルと上限:r4/src/r4/briefing.py:66-81。"
    "履歴はテキストのみ:session_turns.py:92-93。ツールは名前順:session_turns.py:112。\n"
    "先頭部分:tools 配列は常に全部を送る。スキルの制限は、対象外の呼び出しをフックや承認の"
    "前に「このターンは拒否」として返す(session.py:970-997、agent.py:5204-5212)。先頭部分が"
    "変わるのは、引数に関係なくツール全体が拒否されている間(ツール全体を拒否するルールか、"
    "確認する相手がいないとき)そのツールを隠すとき。利用者の拒否では隠さない(ADR-104。"
    "agent.py:2787、2955-2966、5299-5335)。ほかに、MCP サーバを追加したとき、R4.md を編集"
    "したとき(リクエストのたびに読み直す)。"
    "収まらないときはツール定義を削ることもある(context.py:760-780)。サブエージェントの"
    "終了通知(<subagent-notification> ブロック、async_agents.py:57-67)は、終了したときだけ"
    "追加する(agent.py:3242-3253)。\n"
    "ターンをまたぐと:1 ターンの中では、各呼び出しはキャッシュ済みの先頭部分に新しいツール"
    "結果を足すだけ。次のターンでは system・tools・古い履歴まではキャッシュが効き、前回の"
    "質問から先は読み直しになる。前回の質問は <turn_context> なしで保存され、そのツールの"
    "やり取りは再送しないため(session_flow.py:27、session_turns.py:93)。\n"
    "ウィンドウ:32,768 = OLLAMA_CONTEXT_LENGTH(deploy/standalone/env/base.env:36)。"
    "スタックはこれをエンドポイントの max_model_len として登録し"
    "(deploy/standalone/standalone.sh:697-704)、R4 は名前表より先にこの公開値を使う"
    "(context_budget.py:173-185)。名前表の値(llama3.2 と qwen3 は 131,072)は、"
    "エンドポイント一覧に届かないときだけ使う。R4 の作業予算 = 0.90 × 32,768 − 4,096 = "
    "25,395 トークン(context_budget.py:16-17, 82-87)。枠ごとのメモリ:"
    "deploy/standalone/lib/model.sh:34-39。",
)


def context_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "The context window: what the model sees, every turn",
            "コンテキストウィンドウ:モデルが見るもの",
        ),
        KICK,
    )
    harness_map.mini(slide, "context")
    _context_bands(slide)
    _context_footers(slide)
    _context_panels(slide)
    notes(slide, _CONTEXT_NOTES)


def add(deck: Deck, probe: dict) -> None:
    tokens_slide(deck, probe)
    next_token_slide(deck, probe)
    hallucination_slide(deck, probe)
    training_slide(deck)
    slides_part1_overview.transition_slide(deck, "harness")
    context_slide(deck)
