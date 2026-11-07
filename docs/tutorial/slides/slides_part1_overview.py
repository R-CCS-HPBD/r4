"""Part 1 frame, after "model, agent, harness": three ways to run an agent
(who sees your data) and the whole picture of one turn (the harness map).

Review 2026-11-01: the four-pattern table (old slide 24) became three
patterns — B (local app + hosted model) and C (own server + cloud GPU) are
one case for the data: the agent is yours, the model is someone else's — and
moved up next to slide 9; the harness map follows and is reused small on
every background slide (harness_map.mini).
"""

from __future__ import annotations

import harness_map
from deck_kit import (
    ALERT,
    AMBER,
    BODY_TOP,
    CONTENT_W,
    INK,
    LINE,
    MARGIN,
    MUTED,
    OCEAN,
    SCOPE_H,
    TINT,
    TINT_AMBER,
    WHITE,
    Deck,
    Style,
    badge,
    bullets,
    content,
    down_arrow,
    notes,
    rect,
    scope_tag,
    text_box,
)
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches

KICK = ("Part 1 · Agentic work", "第 1 部 · エージェンティックワーク")

# ── Three ways to run an agent ───────────────────────────────────────────────

LOCAL = ("your PC / your site", "自分の PC・サイト")
CLOUD = ("provider's servers", "事業者のサーバ")

# name, regions (top, bottom; None = one region for both), facts
PATTERNS = (
    (
        ("All cloud", "全部クラウド"),
        (CLOUD, None),
        (
            (
                "**Sees your data:** the provider — every prompt and every file you upload",
                "**データが見える相手:**事業者(全プロンプトと、アップロードした全ファイル)",
            ),
            ("**Cost:** subscription", "**費用:**月額"),
            ("**You run:** nothing (a web app)", "**自分で動かすもの:**なし(Web アプリ)"),
        ),
    ),
    (
        ("Hybrid: your agent, a hosted model", "ハイブリッド:エージェントは手元、モデルは外部"),
        (LOCAL, CLOUD),
        (
            (
                "**Sees your data:** the model's provider — every prompt, with the file "
                "excerpts and tool output the agent adds",
                "**データが見える相手:**モデルの事業者(全プロンプトと、エージェントが加えた"
                "ファイルの抜粋やツールの出力)",
            ),
            (
                "**Cost:** per token (API) or per hour (cloud GPU)",
                "**費用:**トークン課金(API)か時間課金(クラウド GPU)",
            ),
            (
                "**You run:** the agent (Claude Code, Codex, R4 …)",
                "**自分で動かすもの:**エージェント(Claude Code・Codex・R4 など)",
            ),
        ),
    ),
    (
        ("All local — today", "全部ローカル — 今日"),
        (LOCAL, None),
        (
            (
                "**Sees your data:** only you (on a site: its admins)",
                "**データが見える相手:**自分だけ(サイトなら管理者も)",
            ),
            (
                "**Cost:** hardware you own; model size capped by it",
                "**費用:**手持ちの機材(モデルの大きさもこれで決まる)",
            ),
            (
                "**You run:** the stack (one command)",
                "**自分で動かすもの:**R4・UMRS 一式(1 コマンド)",
            ),
        ),
    ),
)

CHECK_ITEMS = [
    ("files: !r4 files list --bucket …", "ファイル:!r4 files list --bucket …"),
    ("vectors: !r4 knowledge docs list", "ベクトル:!r4 knowledge docs list"),
    ("model calls: Ctrl+P → Settings pane", "モデル呼び出し:Ctrl+P → 設定ペイン"),
    ("session log: /export session.md", "セッション記録:/export session.md"),
]

_GAP = Inches(0.3)
_COL_TOP = BODY_TOP + Inches(0.45)
_DIAG_TOP = _COL_TOP + Inches(0.5)
_DIAG_H = Inches(1.75)
_BOX_H = Inches(0.45)


def _region(slide, x, y, w, h, which, label_top=False) -> None:
    fill = TINT if which is LOCAL else TINT_AMBER
    rect(slide, x, y, w, h, fill=fill, rounded=True)
    text_box(
        slide,
        x + Inches(0.1),
        y + (Inches(0.08) if label_top else h - Inches(0.3)),
        w - Inches(0.2),
        Inches(0.26),
        which,
        Style(10, OCEAN if which is LOCAL else MUTED, True),
        align=PP_ALIGN.RIGHT,
    )


def _actor(slide, x, y, w, label) -> None:
    rect(slide, x, y, w, _BOX_H, fill=WHITE, line=LINE, rounded=True)
    text_box(
        slide,
        x,
        y,
        w,
        _BOX_H,
        label,
        Style(13, INK, True),
        align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE,
    )


def _diagram(slide, x, w, regions) -> None:
    top, bottom = regions
    half = (_DIAG_H - Inches(0.1)) // 2
    if bottom is None:
        _region(slide, x, _DIAG_TOP, w, _DIAG_H, top)
    else:
        _region(slide, x, _DIAG_TOP, w, half, top, label_top=True)
        _region(slide, x, _DIAG_TOP + half + Inches(0.1), w, half, bottom)
    bw = int(w * 0.6)
    bx = x + Inches(0.25)
    _actor(slide, bx, _DIAG_TOP + Inches(0.15), bw, ("Agent (harness)", "エージェント(ハーネス)"))
    model_y = _DIAG_TOP + _DIAG_H - _BOX_H - Inches(0.35)
    _actor(slide, bx, model_y, bw, ("Model", "モデル"))
    arrow_y = _DIAG_TOP + Inches(0.15) + _BOX_H + Inches(0.04)
    down_arrow(
        slide,
        bx + bw // 2 - Inches(0.18),
        arrow_y,
        h=model_y - arrow_y - Inches(0.04),
        fill=AMBER if bottom is not None else OCEAN,
    )
    if bottom is not None:
        lx = bx + bw + Inches(0.1)
        text_box(
            slide,
            lx,
            _DIAG_TOP + half - Inches(0.42),
            x + w - lx - Inches(0.1),
            Inches(0.4),
            ("every prompt leaves here ↓", "ここで全プロンプトが外へ ↓"),
            Style(10, ALERT, True),
            align=PP_ALIGN.RIGHT,
            anchor=MSO_ANCHOR.BOTTOM,
        )


def _column(slide, i: int, x, w, pattern) -> None:
    name, regions, facts = pattern
    badge(
        slide, x, _COL_TOP, str(i + 1), fill=AMBER if i == 2 else OCEAN, size=Inches(0.4), font=14
    )
    text_box(
        slide,
        x + Inches(0.5),
        _COL_TOP,
        w - Inches(0.5),
        Inches(0.4),
        name,
        Style(15, INK, True),
        anchor=MSO_ANCHOR.MIDDLE,
    )
    _diagram(slide, x, w, regions)
    bullets(
        slide,
        x,
        _DIAG_TOP + _DIAG_H + Inches(0.12),
        w,
        Inches(1.4),
        list(facts),
        Style(11, INK),
        gap=2,
    )


def _check_row(slide) -> None:
    y = BODY_TOP + Inches(4.25)
    h = Inches(0.95)
    rect(slide, MARGIN, y, CONTENT_W, h, fill=TINT_AMBER, rounded=True)
    text_box(
        slide,
        MARGIN + Inches(0.2),
        y + Inches(0.1),
        Inches(2.4),
        Inches(0.3),
        ("Check it yourself", "自分で確かめる"),
        Style(14, INK, True),
    )
    text_box(
        slide,
        MARGIN + Inches(0.2),
        y + Inches(0.42),
        Inches(2.6),
        Inches(0.5),
        (
            "What is in the context window is what an external model would see.",
            "外部のモデルを使えば、コンテキストに入ったものはすべて事業者に見える。",
        ),
        Style(10, MUTED, italic=True),
    )
    col_w = (CONTENT_W - Inches(3.2)) // 2
    for j in range(2):
        bullets(
            slide,
            MARGIN + Inches(3.0) + j * col_w,
            y + Inches(0.14),
            col_w,
            h - Inches(0.2),
            CHECK_ITEMS[2 * j : 2 * j + 2],
            Style(12, INK),
            gap=2,
        )


PATTERNS_NOTES = (
    "7 min. Three cases, told by WHERE THE MODEL RUNS, because that decides who sees the "
    "context. Ask the room which one their lab uses (usually 1 or 2). None is wrong; choose "
    "knowingly. Review 2026-11-01: the old table split case 2 into 'local app + vendor API' "
    "and 'own server + cloud GPU'; for the data question they are the same — the agent is "
    "yours, every prompt goes to someone else's model — so they are one column now (the "
    "cost line keeps the difference: per token vs per hour).\n"
    "GENERAL question, the same for every harness: by default Claude Code sends the context "
    "to Anthropic and the OpenAI Agents SDK to OpenAI (case 2); opencode or a LangChain "
    "agent pointed at a local Ollama is case 3. A provider you trust — a site you belong to, "
    "such as Rikyu's endpoint — is a reasonable case 2 that keeps large models usable (an "
    "explicit opt-in in R4, never the default).\n"
    "R4's settings for case 3: the stand-alone stack runs Ollama (models, on an internal "
    "network without outside access), Postgres + pgvector (vectors) and SeaweedFS (an "
    "S3-compatible file store) as containers on your PC (deploy/standalone/compose.yaml); "
    "sessions are a SQLite file, ~/.config/r4/companion/sessions.db "
    "(r4/src/r4/companion/session_store.py:32). The commands in the check row are in the "
    "handbook (01-part1-labs). Commercial services: the optional slide in the appendix.",
    "7 分。「モデルがどこで動くか」で 3 つに分ける。コンテキストを誰が見られるかはそれで決まる。"
    "自分の研究室がどれかを会場に聞く(多くは 1 か 2)。どれも間違いではない。分かった上で選ぶ。"
    "2026-11-01 のレビュー:以前の表は 2 を「ローカルアプリ+事業者の API」と「自前サーバ+"
    "クラウド GPU」に分けていたが、データの観点では同じ(エージェントは手元、全プロンプトは"
    "他者のモデルへ)なので 1 列にまとめた(違いは費用の行:トークン課金か時間課金か)。\n"
    "どのハーネスでも同じ「一般的な」問い:既定では Claude Code は Anthropic に、OpenAI "
    "Agents SDK は OpenAI にコンテキストを送る(2)。opencode や LangChain のエージェントも"
    "ローカルの Ollama につなげば 3。Rikyu のように自分が所属する、信頼できるサイトのモデルなら、"
    "2 でも妥当な選択で、大きなモデルも使える(R4 では明示的に選んだときだけ。既定にはしない)。\n"
    "3 の R4 の設定:stand-alone スタックが Ollama(モデル。外部に出られない内部ネットワーク上)、"
    "Postgres + pgvector(ベクトル)、SeaweedFS(S3 互換のファイル置き場)を PC 上のコンテナとして"
    "動かす(deploy/standalone/compose.yaml)。セッションは SQLite ファイル "
    "~/.config/r4/companion/sessions.db(r4/src/r4/companion/session_store.py:32)。"
    "確認のコマンドはハンドブック(01-part1-labs)にある。商用サービスは付録の任意スライド。",
)


def patterns_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "Three ways to run an agent — who sees your data?",
            "エージェントの 3 つの使い方 — データは誰に見えるか",
        ),
        KICK,
        ("8–15 min", "8–15 分"),
    )
    deck.mark("patterns")
    pill = scope_tag(slide, MARGIN, BODY_TOP - Inches(0.05), "general")
    text_box(
        slide,
        MARGIN + pill.width + Inches(0.15),
        BODY_TOP - Inches(0.05),
        CONTENT_W - pill.width - Inches(0.15),
        SCOPE_H,
        (
            "The question is the same for every harness: where does the model run?",
            "どのハーネスでも同じ問い:モデルはどこで動くか?",
        ),
        Style(12, MUTED),
        anchor=MSO_ANCHOR.MIDDLE,
    )
    w = (CONTENT_W - 2 * _GAP) // 3
    for i, pattern in enumerate(PATTERNS):
        _column(slide, i, MARGIN + i * (w + _GAP), w, pattern)
    _check_row(slide)
    notes(slide, PATTERNS_NOTES)


# ── The whole picture: one turn through the harness ──────────────────────────

TURN_STEPS = [
    (
        "**1** your request joins the context: rules, history, active skills",
        "**1** 依頼文がコンテキストに入る:指示・履歴・有効なスキル",
    ),
    (
        "**2** the context + the tool list go to the model in one request; the model "
        "server turns both into the text the model reads",
        "**2** コンテキストとツール一覧を 1 つのリクエストで送り、モデルサーバがモデルの"
        "読む文章に変える",
    ),
    (
        "**3** the reply is plain text (the answer, done) or a tool call",
        "**3** 返事は普通の文章(回答。終わり)か、ツールの呼び出し",
    ),
    ("**4** the gate decides: run, ask you, or block", "**4** 許可ゲートが判断:実行・確認・拒否"),
    (
        "**5** the tool runs — a local tool, a knowledge search or an MCP tool (the last two "
        "through UMRS); its result is added to the context → back to 2",
        "**5** ツールを実行(手元のツール、ナレッジ検索、MCP のツール。後の 2 つは UMRS 経由)。"
        "結果をコンテキストに足して → 2 に戻る",
    ),
]

OVERVIEW_NOTES = (
    "5 min. The map the rest of the block keeps coming back to (review 2026-11-01: show the "
    "whole picture first, then explain each part on the same figure). Walk one turn with the "
    "amber numbers: 1 the harness assembles the context; 2 one HTTP request carries it and "
    "the tool definitions to the model; 3 the model answers in text — either the final "
    "answer or a request to call a tool; 4 the permission gate decides; 5 the tool runs "
    "(locally, or through MCP / knowledge search on UMRS) and the result goes back in; the "
    "loop repeats until the model answers in plain text.\n"
    "From here on every background slide carries this map, small, top right, with the part "
    "it explains in amber and framed in red (review 2026-11-05), and a full-size 'Next' map "
    "opens each block: the model (next four slides), harness ↔ model (tool calling), the "
    "loop, the context, the gate, skills, MCP, knowledge.\n"
    "Code: the turn is _run_agentic_turn (r4/src/r4/companion/agent.py:3131-3242); the "
    "context is built by send() (agent.py:2695) and _system_message (4609-4633); the gate is "
    "PermissionManager.decide (companion/permissions.py:823); MCP tools are "
    "mcp_first_class.py; knowledge is search_docs (companion/tools.py:2556) asking UMRS.",
    "5 分。このブロックで何度も戻ってくる全体図(2026-11-01 のレビュー:まず全体像を見せ、"
    "同じ図で各部分を説明する)。オレンジの番号で 1 ターンをたどる。1 ハーネスがコンテキストを"
    "組み立てる。2 1 回の HTTP リクエストで、それとツールの定義をモデルへ送る。3 モデルは文章で"
    "答える。最終回答か、ツールを呼びたいという依頼のどちらか。4 許可ゲートが判断する。5 ツールを"
    "実行し(手元で、または MCP やナレッジ検索で UMRS を通して)、結果を戻す。モデルが普通の文章で"
    "答えるまで繰り返す。\n"
    "このあとの「中身を見る」スライドには右上にこの図を小さく置き、説明している部分をオレンジに"
    "して赤い枠で囲む(2026-11-05 のレビュー)。各ブロックの最初には全体図の「次は」スライドを"
    "置く:モデル(次の 4 枚)、ハーネス ↔ モデル(ツール呼び出し)、ループ、コンテキスト、"
    "許可ゲート、スキル、MCP、ナレッジ。\n"
    "コード:1 ターンは _run_agentic_turn(r4/src/r4/companion/agent.py:3131-3242)。"
    "コンテキストは send()(agent.py:2695)と _system_message(4609-4633)が組み立てる。"
    "ゲートは PermissionManager.decide(companion/permissions.py:823)。MCP のツールは "
    "mcp_first_class.py。ナレッジは search_docs(companion/tools.py:2556)が UMRS に問い合わせる。",
)


def overview_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("The whole picture: one turn through the harness", "全体像:ハーネスの中の 1 ターン"),
        KICK,
        ("15–20 min", "15–20 分"),
    )
    deck.mark("overview")
    map_w = Inches(9.0)
    h = harness_map.draw_full(slide, MARGIN, BODY_TOP + Inches(0.15), map_w)
    x = MARGIN + map_w + Inches(0.3)
    w = CONTENT_W - map_w - Inches(0.3)
    rect(slide, x, BODY_TOP, w, h + Inches(0.15), fill=TINT, rounded=True)
    text_box(
        slide,
        x + Inches(0.2),
        BODY_TOP + Inches(0.12),
        w - Inches(0.4),
        Inches(0.35),
        ("One turn", "1 ターンの流れ"),
        Style(15, INK, True),
    )
    text_box(
        slide,
        x + Inches(0.2),
        BODY_TOP + Inches(0.55),
        w - Inches(0.4),
        h - Inches(0.5),
        ("\n".join(e for e, _ in TURN_STEPS), "\n".join(j for _, j in TURN_STEPS)),
        Style(12, INK),
    )
    text_box(
        slide,
        MARGIN,
        BODY_TOP + h + Inches(0.4),
        CONTENT_W,
        Inches(0.6),
        (
            "Only the model box is AI. Everything amber-backed is ordinary software you can "
            "read, configure and test — the harness. The next slides zoom into one box at a "
            "time; the small map top right shows where you are.",
            "AI なのはモデルの箱だけ。オレンジの背景の部分はすべて、読めて・設定できて・試せる"
            "普通のソフトウェア(ハーネス)。次のスライドから箱を一つずつ拡大する。右上の小さな"
            "図が、今どこを見ているかを示す。",
        ),
        Style(14, INK),
    )
    notes(slide, OVERVIEW_NOTES)


# ── Transitions: the full map again, framed on what comes next ─────────────
# Review 2026-11-05: "indicate again the entire map with framing to explain
# what we will be explaining next" (e.g. between the model and the context).

TRANSITIONS = {
    "harness": (
        ("Next: the harness at work", "次は:ハーネスの働き"),
        [
            (
                "The model: tokens, probabilities, why it guesses, where its knowledge lives",
                "モデル:トークン・確率・推測してしまう理由・知識の在りか",
            )
        ],
        [
            (
                "what is sent to the model every turn (the context window)",
                "毎ターン、モデルに何を送るか(コンテキストウィンドウ)",
            ),
            (
                "how its text becomes an action: tool calls and the loop",
                "モデルの文章が操作になる仕組み:ツール呼び出しとループ",
            ),
            ("keeping the window from overflowing", "ウィンドウをあふれさせない工夫"),
            ("the permission gate: run, ask you, or block", "許可ゲート:実行・確認・拒否"),
        ],
    ),
    "extend": (
        ("Next: extending the agent — skills and MCP", "次は:エージェントを広げる — スキルと MCP"),
        [
            ("The model", "モデル"),
            (
                "The harness at work: context, tool calls, loop, gate",
                "ハーネスの働き:コンテキスト・ツール呼び出し・ループ・許可ゲート",
            ),
        ],
        [
            ("skills: how-to notes loaded only when needed", "スキル:必要なときだけ読み込む手順書"),
            ("MCP: tools that live on other servers", "MCP:別のサーバにあるツール"),
        ],
    ),
    "knowledge": (
        ("Next: knowledge — finding the right page (RAG)", "次は:ナレッジ — 正しい箇所を探す(RAG)"),
        [
            ("The model", "モデル"),
            ("The harness at work", "ハーネスの働き"),
            ("Skills and MCP", "スキルと MCP"),
        ],
        [
            ("why RAG: hand the model your documents", "なぜ RAG か:モデルに自分の文書を渡す"),
            ("embeddings: how search finds similar meaning", "埋め込み:意味の近さで探す仕組み"),
            ("the pipeline, and the [S1] labels", "全体の流れと [S1] のラベル"),
            ("does it help? measured", "効くのか?実測"),
        ],
    ),
}


def transition_slide(deck: Deck, focus: str) -> None:
    title, done, upcoming = TRANSITIONS[focus]
    slide = content(deck, title, KICK)
    map_w = Inches(9.0)
    h = harness_map.draw_full(slide, MARGIN, BODY_TOP + Inches(0.15), map_w, focus=focus)
    x = MARGIN + map_w + Inches(0.3)
    w = CONTENT_W - map_w - Inches(0.3)
    rect(slide, x, BODY_TOP, w, h + Inches(0.15), fill=TINT, rounded=True)
    text_box(
        slide,
        x + Inches(0.2),
        BODY_TOP + Inches(0.12),
        w - Inches(0.4),
        Inches(0.3),
        ("So far", "ここまで"),
        Style(13, MUTED, True),
    )
    y = BODY_TOP + Inches(0.45)
    bullets(
        slide,
        x + Inches(0.2),
        y,
        w - Inches(0.4),
        Inches(0.3) * len(done) + Inches(0.3),
        done,
        Style(11, MUTED),
        gap=2,
    )
    y += Inches(0.32) * len(done) + Inches(0.45)
    text_box(
        slide,
        x + Inches(0.2),
        y,
        w - Inches(0.4),
        Inches(0.3),
        ("Next (red frame)", "次(赤い枠)"),
        Style(14, ALERT, True),
    )
    bullets(
        slide,
        x + Inches(0.2),
        y + Inches(0.36),
        w - Inches(0.4),
        BODY_TOP + h - y - Inches(0.3),
        upcoming,
        Style(12, INK),
        gap=4,
    )
    notes(
        slide,
        (
            "1 min. Review 2026-11-05: show the whole map again at each block boundary, the "
            "next part framed in red, so the audience knows where the talk is going. The "
            "same frame appears small at the top right of each following slide.",
            "1 分。2026-11-05 のレビュー:ブロックの切れ目ごとに全体図をもう一度見せ、次に話す"
            "部分を赤い枠で示す。続くスライドの右上にも、同じ枠が小さく出る。",
        ),
    )


def add(deck: Deck) -> None:
    patterns_slide(deck)
    overview_slide(deck)
