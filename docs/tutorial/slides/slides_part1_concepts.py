"""Part 1 frame: the divider and model / agent / harness / knowledge (slides 8
and 9). The deployment patterns and the harness map follow right after
(slides_part1_overview, review 2026-11-01)."""

from __future__ import annotations

from dataclasses import dataclass

import slides_part1_harness
import slides_part1_model
import slides_part1_overview
from deck_kit import (
    BODY_TOP,
    CONTENT_W,
    INK,
    MARGIN,
    OCEAN,
    SCOPE_H,
    TINT,
    TINT_AMBER,
    Deck,
    Style,
    content,
    divider,
    notes,
    rect,
    ring,
    scope_legend,
    scope_tag,
    text_box,
)
from pptx.enum.text import MSO_ANCHOR
from pptx.util import Inches

KICK = ("Part 1 · Agentic work", "第 1 部 · エージェンティックワーク")


def _corner_tag(slide, right, top_edge, kind: str):
    """Scope pill right-aligned at ``right``, straddling the edge at ``top_edge``."""
    pill = scope_tag(slide, right, top_edge - SCOPE_H / 2, kind)
    pill.left = int(right - pill.width)
    return pill


# ── New 8: Part 1 divider ────────────────────────────────────────────────────


def part1_divider(deck: Deck) -> None:
    slide = divider(
        deck,
        ("Part 1 · ~2 h", "第 1 部 · 約 2 時間"),
        ("Agentic work", "エージェンティックワーク"),
        (
            "Agentic work = delegate a task, watch the tools, judge the result; then make the "
            "agent better on your domain.",
            "エージェンティックワーク = タスクを任せ、ツールの動きを見て、結果を判断する働き方。"
            "そのうえで、エージェントを自分の分野に強くしていく。",
        ),
        [
            (
                "Under the hood: model · agent · harness · knowledge",
                "中身を見る:モデル・エージェント・ハーネス(外側の仕組み)・ナレッジ",
            ),
            (
                "Labs 1.1–1.3: fetch a real earthquake, plot travel times, speed up a solver",
                "Lab 1.1–1.3:地震データの取得・走時曲線・ソルバ(計算プログラム)の高速化",
            ),
            (
                "Labs 1.4–1.5: RAG off/on (stretch: skill, MCP), your solver vs the real event",
                "Lab 1.4–1.5:RAG オフ/オン(発展:スキル・MCP)・自分のソルバと実際の地震",
            ),
        ],
    )
    deck.mark("part1_divider")
    notes(
        slide,
        (
            "Hyogo lecture cut: run the hands-on blocks as guided demos on the projector; "
            "attendees follow along if their stack is up.",
            "兵庫講義版:ハンズオン部分はプロジェクタ上のガイド付きデモとして実施し、"
            "環境が動いている参加者は一緒に操作する。",
        ),
    )


# ── New 9: model, agent, harness and knowledge ───────────────────────────────


@dataclass(frozen=True)
class FrameCard:
    """One card of the four-layer frame; ``rings`` = nesting depth (0 = none)."""

    name: tuple[str, str]
    tag: tuple[str, str]
    body: tuple[str, str]
    question: tuple[str, str]
    kind: str
    rings: int
    fill: object = TINT


FRAME_CARDS = [
    FrameCard(
        ("Model", "モデル"),
        ("generates text — the innermost piece", "テキストを生成する中核"),
        (
            "Qwen, Llama, GPT, Claude … Swappable. Knows nothing about your research.",
            "Qwen・Llama・GPT・Claude など。別のモデルに差し替えられる。ユーザの研究については何も知らない。",
        ),
        (
            "What does it compute — and why does it make things up?",
            "何を計算しているのか? なぜ作り話をするのか?",
        ),
        "general",
        1,
    ),
    FrameCard(
        ("Agent", "エージェント"),
        ("the model + a loop with tools", "モデルと、ツールを使うループ"),
        (
            "Plans, calls a tool, reads the result, repeats toward your goal. "
            "The model sits inside it.",
            "目標に向かって、計画 → ツール呼び出し → 観察を繰り返す。モデルを内側に組み込んで動く。",
        ),
        (
            "How does the model's text turn into actions on your files?",
            "モデルが出した文章が、どうやってファイル操作になるのか?",
        ),
        "general",
        2,
    ),
    FrameCard(
        ("Harness", "ハーネス"),
        ("everything around them", "エージェントを包む外側のすべて"),
        (
            "Context assembly, permissions, memory, logs — and here, the agent loop's "
            "implementation too. **R4 is this harness.**",
            "コンテキスト(1 回の呼び出しでモデルに渡す入力全体)の組み立て・実行の許可・記憶・"
            "ログ。エージェントのループ自体もここで実装される。**R4 はこのハーネスにあたる。**",
        ),
        (
            "What is sent to the model each turn, and who decides what runs?",
            "依頼のたびに、何がモデルに送られ、何を実行するかを誰が決めるのか?",
        ),
        "general",
        3,
        TINT_AMBER,
    ),
    FrameCard(
        ("Knowledge (RAG)", "ナレッジ(RAG)"),
        ("your documents, found on demand", "必要なときに探し出す自分の文書"),
        (
            "Retrieval puts the right passage of your documents in front of the model. "
            "In R4 the search runs on UMRS, the server side.",
            "自分の文書から必要な箇所を検索で探し、モデルへの入力(コンテキスト)に加える。"
            "R4 では、この検索をサーバ側の UMRS が行う。",
        ),
        (
            "How does RAG find the right page in your documents?",
            "RAG はどうやって文書から正しい箇所を見つけるのか?",
        ),
        "r4tuned",
        0,
    ),
]

_CARD_GAP = Inches(0.3)
_CARD_TOP = BODY_TOP + Inches(0.45)
_CARD_H = Inches(3.65)
_CARD_PAD = Inches(0.2)


def _card_rings(slide, x, depth: int) -> None:
    for r in range(depth):
        ring(slide, x + Inches(0.45), _CARD_TOP + Inches(0.5), Inches(0.1 + 0.07 * r), OCEAN, 2)


def _frame_card(slide, x, w, spec: FrameCard) -> None:
    rect(slide, x, _CARD_TOP, w, _CARD_H, fill=spec.fill, rounded=True)
    _corner_tag(slide, x + w - Inches(0.15), _CARD_TOP, spec.kind)
    _card_rings(slide, x, spec.rings)
    name_x = x + (Inches(0.85) if spec.rings else _CARD_PAD)
    inner_w = w - 2 * _CARD_PAD
    top = _CARD_TOP
    text_box(
        slide,
        name_x,
        top + Inches(0.28),
        x + w - name_x - Inches(0.1),
        Inches(0.45),
        spec.name,
        Style(20, INK, True),
    )
    text_box(
        slide,
        x + _CARD_PAD,
        top + Inches(0.92),
        inner_w,
        Inches(0.5),
        spec.tag,
        Style(14, OCEAN, True),
    )
    text_box(
        slide,
        x + _CARD_PAD,
        top + Inches(1.45),
        inner_w,
        _CARD_H - Inches(2.3),
        spec.body,
        Style(13, INK),
    )
    text_box(
        slide,
        x + _CARD_PAD,
        top + _CARD_H - Inches(0.82),
        inner_w,
        Inches(0.7),
        spec.question,
        Style(13, OCEAN, italic=True),
        anchor=MSO_ANCHOR.BOTTOM,
    )


THREE_THINGS_NOTES = (
    "4 min. The lesson starts here (adviser review 2026-10-26): most confusion about 'AI' "
    "comes from mixing these layers up. Definitions follow the common industry framing "
    "(Agent = Model + Harness, per LangChain 'The Anatomy of an Agent Harness', Databricks, "
    "Microsoft Learn, Anthropic's long-running-agent posts): the model is the text "
    "generator; the agent is the model plus the tool-using loop that acts; the harness is "
    "everything around them, and in R4 that ALSO includes the loop's own implementation. "
    "The rings encode the nesting: model ⊂ agent ⊂ harness.\n"
    "Badges (first seen on slide 3; the legend sits under this slide's title): model, agent "
    "and harness are GENERAL patterns. Claude Code / the Claude Agent SDK, the OpenAI Agents "
    "SDK, opencode and LangChain agents have the same three layers. Knowledge is a general "
    "pattern (LangChain retrievers, OpenAI file_search); that UMRS runs the search, and how "
    "much it hands over, are R4's settings (badge GENERAL · R4 SETTINGS). Claude Code has no "
    "built-in document index and searches files with grep instead.\n"
    "Where each layer lives in R4's code:\n"
    "- Model: R4 never runs it. Each round is one POST to an OpenAI-compatible "
    "/v1/chat/completions (r4/src/r4/client.py:1435); on the stand-alone stack that is "
    "Ollama with a 32,768-token window (deploy/standalone/env/base.env:36).\n"
    "- Agent: _run_agentic_turn (r4/src/r4/companion/agent.py:3131-3242). Each round fits "
    "the messages, calls the model once and reads tool_calls. No call: that text is the "
    "answer. Otherwise PreToolUse hook, permission gate, run, append the results, next "
    "round. Every 25 rounds it checks for new evidence; the hard roof is 1,000 rounds, then "
    "one wrap-up round without tools (agent.py:79-80).\n"
    "- Harness: send() (agent.py:2695) builds the tools array and the messages; the "
    "session-stable system message is _system_message (agent.py:4609-4633); per-turn parts "
    "ride a <turn_context> block (companion/turn_context.py); the permission ladder is "
    "PermissionManager.decide (companion/permissions.py:823; ADR-104), the one mode in "
    "companion/agent_mode.py; sessions are kept in SQLite "
    "(companion/session_store.py:32).\n"
    "- Knowledge: the search_docs tool asks UMRS (client.rag.retrieve, "
    "companion/tools.py:2556-2562); UMRS ranks and cuts. The pack is sized by the reading "
    "model: 3 passages / 1,200 tokens (small) or 4 / 2,000 (medium) "
    "(r4-contracts/src/r4_contracts/models.py:144-166).\n"
    "Then the 35-min under-the-hood block (slides 12–31): every number was measured on a "
    "real model — Qwen2.5 3B run locally for tokens and probabilities, the stack's own "
    "embedder (e5-base-v2) for retrieval; scripts and data in docs/tutorial/background/.",
    "4 分。講義の出発点(2026-10-26 レビュー)。「AI」への誤解の多くは、これらの層の混同から"
    "生まれる。用語は業界で一般的な整理(Agent = Model + Harness:LangChain『The Anatomy of "
    "an Agent Harness』、Databricks、Microsoft Learn、Anthropic の長時間エージェントの記事)に"
    "揃えた。モデルは文章を生成するもの。エージェントはモデルとツールを使うループを合わせた、"
    "実際に行動する仕組み。ハーネスはその外側のすべてで、R4 ではループの実装もハーネスに含まれる。"
    "カードの同心円はこの内包関係(モデル ⊂ エージェント ⊂ ハーネス)を表す。\n"
    "印について(初出はスライド 3。凡例はこのスライドのタイトルの下):モデル・エージェント・"
    "ハーネスは「一般的な仕組み」。Claude Code / Claude Agent SDK、OpenAI Agents SDK、opencode、"
    "LangChain のエージェントも同じ 3 層を持つ。ナレッジは一般的な仕組み(LangChain の"
    "リトリーバ、OpenAI の file_search)で、検索を UMRS 側で行う点と渡す量が R4 の設定"
    "(印は「一般的な仕組み・R4 の設定」)。Claude Code は文書の索引を持たず、grep でファイルを"
    "検索する。\n"
    "R4 のコードでの置き場所:\n"
    "- モデル:R4 はモデルを動かさない。1 ラウンドごとに、OpenAI 互換の /v1/chat/completions に"
    "1 回 POST する(r4/src/r4/client.py:1435)。stand-alone スタックでは、コンテキストウィンドウ"
    " 32,768 トークンの Ollama が相手(deploy/standalone/env/base.env:36)。\n"
    "- エージェント:_run_agentic_turn(r4/src/r4/companion/agent.py:3131-3242)。各ラウンドで"
    "メッセージを予算内に収め、モデルを 1 回呼び、tool_calls を読む。呼び出しがなければ、その"
    "文章が回答。あれば PreToolUse フック → 許可ゲート → 実行 → 結果を追加 → 次のラウンド。"
    "25 ラウンドごとに新しい根拠が得られたかを確かめ、上限は 1,000 ラウンド。上限に達すると、"
    "ツールなしのまとめのラウンドを 1 回行う(agent.py:79-80)。\n"
    "- ハーネス:send()(agent.py:2695)がツール一覧とメッセージを組み立てる。セッション中は"
    "変わらないシステムメッセージは _system_message(agent.py:4609-4633)。ターンごとに変わる"
    "部分は <turn_context> ブロックに入る(companion/turn_context.py)。許可の判定順は "
    "PermissionManager.decide(companion/permissions.py:823、ADR-104)、モードは "
    "companion/agent_mode.py。セッションは SQLite に保存"
    "(companion/session_store.py:32)。\n"
    "- ナレッジ:search_docs ツールが UMRS に問い合わせる(client.rag.retrieve、"
    "companion/tools.py:2556-2562)。順位付けと絞り込みは UMRS が行う。渡す量は読むモデルの"
    "大きさで決まる:small は 3 件・1,200 トークン、medium は 4 件・2,000 トークン"
    "(r4-contracts/src/r4_contracts/models.py:144-166)。\n"
    "このあと 35 分の「中身を見る」ブロック(スライド 12–31)。数値はすべて実測:トークンと確率は"
    "ローカルの Qwen2.5 3B、検索はスタックの埋め込みモデル e5-base-v2。スクリプトとデータは "
    "docs/tutorial/background/。",
)


def three_things_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Model, agent, harness — and knowledge", "モデル・エージェント・ハーネス、そしてナレッジ"),
        KICK,
        ("0–8 min", "0–8 分"),
    )
    deck.mark("three_things")
    scope_legend(slide, MARGIN, BODY_TOP - Inches(0.08))
    w = (CONTENT_W - 3 * _CARD_GAP) / 4
    for i, spec in enumerate(FRAME_CARDS):
        _frame_card(slide, MARGIN + i * (w + _CARD_GAP), w, spec)
    text_box(
        slide,
        MARGIN,
        _CARD_TOP + _CARD_H + Inches(0.18),
        CONTENT_W,
        Inches(0.9),
        (
            "Common wording in the industry: **Agent = Model + Harness** (LangChain, Databricks, "
            "Microsoft). Claude Code, opencode and the OpenAI Agents SDK are harnesses too; R4 is "
            "one of them. Today you work on the **harness**, not the model: with a well-built "
            "harness, a small free model can do domain work.",
            "業界では **「Agent = Model + Harness」** と整理するのが一般的(LangChain・Databricks・"
            "Microsoft も同じ表現を使う)。Claude Code・opencode・OpenAI Agents SDK もハーネスで、"
            "R4 もその一つ。今日手を入れるのはモデルではなく**ハーネス**。ハーネスを工夫すれば、"
            "小さな無料モデルでも専門の仕事ができる。",
        ),
        Style(14, INK),
    )
    notes(slide, THREE_THINGS_NOTES)


def add(deck: Deck, probe: dict, emb: dict) -> None:
    part1_divider(deck)
    three_things_slide(deck)
    slides_part1_overview.add(deck)
    slides_part1_model.add(deck, probe)
    slides_part1_harness.add(deck, probe, emb)
