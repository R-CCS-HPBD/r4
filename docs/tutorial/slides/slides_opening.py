"""Opening (30 min; ~10 min in the Hyogo lecture cut): context and the story.

Slides 1-7. Scope pills (deck_kit.scope_tag) mark what is a general
agent-harness pattern and what is R4's own on slides 3, 4 and 5; the legend
itself sits once on the model/agent/harness slide (slides_part1_concepts).
"""

from __future__ import annotations

from deck_kit import (
    AMBER,
    BODY_TOP,
    CODE_INK,
    CONTENT_W,
    INK,
    MARGIN,
    MUTED,
    OCEAN,
    SCOPE_H,
    SEAFOAM,
    SLIDE_W,
    TINT,
    TINT_AMBER,
    WHITE,
    Deck,
    Style,
    arrow,
    badge,
    card,
    code_panel,
    content,
    down_arrow,
    isobars,
    lang,
    notes,
    picture,
    rect,
    ring,
    scope_tag,
    text_box,
    tr,
)
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

KICK = ("Opening", "オープニング")


def _tag_right(slide, right, top, kind: str) -> None:
    """A scope pill whose right edge sits at ``right``."""
    pill = scope_tag(slide, int(right), int(top), kind)
    pill.left = int(right - pill.width)


def _tag_on_edge(slide, x, y, w, kind: str) -> None:
    """A scope pill straddling the top edge of a box, near its right corner."""
    _tag_right(slide, x + w - Inches(0.15), y - SCOPE_H // 2, kind)


# ── 1. Title ─────────────────────────────────────────────────────────────────


def title_slide(deck: Deck) -> None:
    slide = deck.blank(dark=True)
    deck.mark("opening")
    isobars(slide, SLIDE_W - Inches(2.6), Inches(3.4), count=8)
    ring(slide, SLIDE_W - Inches(2.6), Inches(3.4), Inches(0.18), AMBER, 3)
    text_box(
        slide,
        MARGIN,
        Inches(1.35),
        Inches(8.6),
        Inches(0.5),
        ("Hands-on tutorial · R4 + UMRS", "ハンズオン・チュートリアル · R4 + UMRS"),
        Style(18, AMBER, True),
    )
    title = (
        "Nothing sensitive leaves this machine —\nbuild your own AI inference environment",
        "機密データを外部に送らない、\nカスタム AI 推論環境を作る",
    )
    lines = tr(title).count("\n") + 1
    text_box(
        slide,
        MARGIN,
        Inches(1.9),
        Inches(9.4),
        Inches(0.95) * lines,
        title,
        Style(38 if lang() == "en" else 44, WHITE, True),
        name="Title",
    )
    text_box(
        slide,
        MARGIN,
        Inches(2.4) + Inches(0.95) * lines,
        Inches(7.8),
        Inches(1.4),
        (
            "Build a customized agentic workflow around your own research data — "
            "without uploading it to a hosted AI vendor.",
            "自分の研究データに合わせたエージェント・ワークフローを自分で組み立てる。"
            "データを外部の AI 事業者(クラウドの AI サービス)に預ける必要はない。",
        ),
        Style(20, CODE_INK),
    )
    text_box(
        slide,
        MARGIN,
        Inches(5.7),
        Inches(8),
        Inches(0.45),
        ("[event · date · venue]", "[イベント名 · 日付 · 会場]"),
        Style(15, SEAFOAM),
    )
    notes(
        slide,
        (
            "Fill the event line before delivery. Welcome, 1 minute. Say the theme in one sentence: "
            "everything today runs on your own laptop. 'Inference' here means a trained model "
            "generating its answer, not logical inference.",
            "実施前にイベント行を記入。"
            "挨拶 1 分。テーマを一文で:今日の作業はすべて自分のノート PC 上で動く。"
            "「推論」はここでは学習済みモデルで回答を生成すること(inference)で、"
            "論理的な推論の意味ではない。",
        ),
    )


# ── 2. Arc ───────────────────────────────────────────────────────────────────

_ARC_PARTS = [
    (
        "0",
        ("Opening", "オープニング"),
        ("30 min", "30 分"),
        (
            "Why science needs AI agents; what R4 + UMRS is; the earthquake dataset",
            "なぜ科学に AI エージェントが必要か/R4 + UMRS とは何か/題材:紀伊水道の地震データ",
        ),
    ),
    (
        "1",
        ("Agentic work", "エージェンティック\nワーク"),
        ("~2 h", "約 2 時間"),
        (
            "Handing work to an agent. How an agent works inside, and how to give the model "
            "what it does not know: your documents, procedures and tools",
            "エージェントに仕事を任せる働き方。エージェントの中身と、モデルが知らない"
            "文献・手順・ツールを与える方法",
        ),
    ),
    (
        "2",
        ("A small workflow", "小さなワークフロー"),
        ("~3 h", "約 3 時間"),
        (
            "A fan-out that tries many velocity models in parallel, run as a workflow "
            "on your local UMRS (the run engine)",
            "多数の速度構造モデルを並列に試す分岐(ファンアウト:1 つの処理を多数に並列展開"
            "する)型ワークフローを組み、ローカルの UMRS(実行エンジン)で動かす",
        ),
    ),
    (
        "3",
        ("A real workflow", "実践ワークフロー"),
        ("~6 h", "約 6 時間"),
        (
            "Real data at scale, plus a reusable template (content to be decided later)",
            "実データを大規模に処理し、再利用できるテンプレートを持ち帰る(内容は後日決定)",
        ),
    ),
]


def _arc_card(slide, x, w, part) -> None:
    """Badge, name, length, then the outcome (13 pt so the long JA card fits)."""
    num, name, length, outcome = part
    fill = TINT_AMBER if num == "3" else TINT
    rect(slide, x, BODY_TOP + Inches(0.2), w, Inches(3.75), fill=fill, rounded=True)
    badge(slide, x + Inches(0.2), BODY_TOP + Inches(0.38), num, size=Inches(0.52), font=19)
    inner = w - Inches(0.4)
    rows = [
        (Inches(0.98), Inches(0.62), name, Style(18 if lang() == "en" else 16, INK, True)),
        (Inches(1.6), Inches(0.36), length, Style(15, OCEAN, True)),
        (Inches(2.0), Inches(1.9), outcome, Style(13, INK)),
    ]
    for top, height, text, style in rows:
        text_box(slide, x + Inches(0.2), BODY_TOP + top, inner, height, text, style)


def arc_slide(deck: Deck) -> None:
    slide = content(
        deck, ("One storyline, three hands-on parts", "1 つの題材、3 つのハンズオン"), KICK
    )
    w = (CONTENT_W - Inches(0.3) * 3) / 4
    for i, part in enumerate(_ARC_PARTS):
        _arc_card(slide, MARGIN + i * (w + Inches(0.3)), w, part)
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(4.2),
        CONTENT_W,
        Inches(0.8),
        (
            "**What the hands-on parts hand you:** the local stack, your session, your knowledge "
            "set (RAG index + instructions/skill + MCP tool), a working fan-out workflow.",
            "**ハンズオンを通して得られるもの:**ローカル環境、セッション記録、自分の知識セット"
            "(RAG 索引+指示/スキル+MCP ツール)、実際に動く分岐型ワークフロー。",
        ),
        Style(17, INK),
    )
    notes(
        slide,
        (
            "2 min. Parts build on each other but are separable; Part 1 alone is about 2 h; "
            "the Hyogo lecture cut condenses it to 1.5 h (last slide). Part 3 is shown as an "
            "outline today: its case is decided after the platform feature study. RAG, skills "
            "and MCP are defined two slides on.",
            "2 分。各部は積み上げ式だが単独でも成立する。第 1 部のみで約 2 時間。兵庫講義版は"
            "これを 1.5 時間に凝縮する(最終スライド)。第 3 部は今日は概要のみで、題材は"
            "プラットフォーム機能調査の後に確定する。RAG・スキル・MCP は 2 枚後で定義する。",
        ),
    )


# ── 3. What runs where ───────────────────────────────────────────────────────

_PIECES = [
    (
        "💻",
        ("R4 — the client", "R4 — クライアント"),
        (
            "the console you operate (a screen in your terminal), agent, skills, sessions",
            "コンソール(ターミナル上の操作画面)・エージェント・スキル・セッション",
        ),
    ),
    (
        "⚙",
        ("UMRS — the engine", "UMRS — 実行エンジン"),
        (
            "runs jobs and workflows; its gateway fronts the three services below",
            "ジョブ・ワークフローを実行。下の 3 つへの窓口(ゲートウェイ)も兼ねる",
        ),
    ),
    (
        "🗄",
        ("Storage (SeaweedFS, S3)", "ストレージ(SeaweedFS、S3 互換)"),
        (
            "buckets, files, results — where the data sits (same API as Amazon S3)",
            "バケット・ファイル・結果の置き場(Amazon S3 と同じ API で読み書き)",
        ),
    ),
    (
        "📇",
        (
            "Knowledge store (Postgres + pgvector database)",
            "ナレッジストア(Postgres + pgvector のデータベース)",
        ),
        (
            "job records and your RAG index (vectors: text turned into numbers)",
            "ジョブの記録と、RAG(文書検索)用の索引(文章を数値化したベクトル)",
        ),
    ),
    (
        "🧠",
        ("Model (Ollama)", "モデル(Ollama)"),
        (
            "the free, local chat model (RAG embeddings run inside UMRS)",
            "無料のローカルなチャット用モデル(RAG のベクトル化は UMRS 内)",
        ),
    ),
]
_PIECE_TOP = Inches(1.08)
_PIECE_STEP = Inches(0.62)


def _theme_piece(slide, box_x, y, text_w, piece) -> None:
    icon, name, sub = piece
    text_box(
        slide,
        box_x + Inches(0.3),
        y + Inches(0.02),
        Inches(0.55),
        Inches(0.5),
        icon,
        Style(22, INK),
    )
    text_box(slide, box_x + Inches(0.95), y, text_w, Inches(0.28), name, Style(14, INK, True))
    text_box(
        slide,
        box_x + Inches(0.95),
        y + Inches(0.28),
        text_w,
        Inches(0.28),
        sub,
        Style(12 if lang() == "en" else 11, MUTED),
    )


def _theme_links(slide, box_x, box_y) -> None:
    """R4 → UMRS → a bracket over the three services UMRS fronts (not a chain)."""
    for i in (0, 1):
        down_arrow(
            slide,
            box_x + Inches(1.05),
            box_y + _PIECE_TOP + i * _PIECE_STEP + Inches(0.54),
            w=Inches(0.22),
            h=Inches(0.07),
            fill=SEAFOAM,
        )
    top = box_y + _PIECE_TOP + 2 * _PIECE_STEP
    rect(
        slide, box_x + Inches(0.14), top, Inches(0.05), 3 * _PIECE_STEP - Inches(0.1), fill=SEAFOAM
    )


def _theme_local_frame(slide, box_x, box_y, box_w, box_h) -> None:
    rect(slide, box_x, box_y, box_w, box_h, fill=TINT, line=OCEAN, rounded=True)
    text_box(
        slide,
        box_x + Inches(0.25),
        box_y + Inches(0.12),
        Inches(4.0),
        Inches(0.32),
        ("👤 LOCAL — your laptop", "👤 ローカル — 自分のノート PC"),
        Style(16, OCEAN, True),
    )
    text_box(
        slide,
        box_x + Inches(0.25),
        box_y + Inches(0.44),
        box_w - Inches(0.5),
        Inches(0.62),
        (
            "all started by ./r4-local up: UMRS, storage and knowledge in Podman/Docker "
            "containers; R4 in .venv; the model in a container (on a Mac: your Ollama app)",
            "すべて ./r4-local up で起動。UMRS・ストレージ・ナレッジストアはコンテナ"
            "(Podman/Docker による隔離された実行環境)、R4 は .venv、モデルもコンテナ"
            "(Mac では手元の Ollama アプリ)",
        ),
        Style(11, MUTED),
    )
    _tag_right(slide, box_x + box_w - Inches(0.2), box_y + Inches(0.14), "r4tuned")
    for i, piece in enumerate(_PIECES):
        y = box_y + _PIECE_TOP + i * _PIECE_STEP
        _theme_piece(slide, box_x, y, box_w - Inches(1.1), piece)
    _theme_links(slide, box_x, box_y)


_LANES = [
    (
        "①",
        "🖥",
        ("Compute site (e.g. Rikyu's GPU nodes)", "計算サイト(例:Rikyu の GPU ノード)"),
        (
            "Submit a workflow to your institution's site and the job's **input data** goes there.",
            "所属機関のサイトにワークフローを投入すると、**ジョブの入力データ**がそこへ送られる。",
        ),
        ("job inputs", "ジョブの\n入力"),
        "r4",
    ),
    (
        "②",
        "☁",
        (
            "External AI service (only if you enable it)",
            "外部の AI サービス(自分で有効にした場合のみ)",
        ),
        (
            "Ask it a question and it receives the **prompt** (the model's input text): your "
            "messages plus whatever the agent read into it, such as file excerpts and search "
            "results. Nothing else is uploaded.",
            "質問すると、事業者に届くのは**プロンプト(モデルへの入力文)**。自分の発言に加え、"
            "エージェントが読み込んだファイルの抜粋や検索結果も含まれる。それ以外は送られない。",
        ),
        ("the prompt text", "プロンプトの本文"),
        "general",
    ),
    (
        "③",
        "🌐",
        ("The web (only if you turn /web on)", "ウェブ(/web で有効にした場合のみ)"),
        (
            "The agent's **URLs and search queries** go out to the web, and so does any curl "
            "or Python download you approve.",
            "エージェントが開く **URL や検索語**が外に出る。許可した curl や Python による"
            "ダウンロードも同じ。",
        ),
        ("URLs, queries", "URL・\n検索語"),
        "general",
    ),
]
_LANE_H = Inches(1.33)
_LANE_STEP = Inches(1.41)


def _theme_lane_rail(slide, ax, ly, num, tag) -> None:
    """Number, arrow and the 'what leaves' caption left of a lane box."""
    text_box(
        slide,
        ax - Inches(0.06),
        ly + Inches(0.04),
        Inches(0.5),
        Inches(0.4),
        num,
        Style(15, OCEAN, True),
    )
    arrow(slide, ax, ly + Inches(0.42), Inches(0.55), h=Inches(0.3), fill=SEAFOAM)
    text_box(
        slide,
        ax - Inches(0.12),
        ly + Inches(0.76),
        Inches(0.75),
        Inches(0.5),
        tag,
        Style(10, MUTED),
        align=PP_ALIGN.CENTER,
    )


def _theme_lane(slide, ax, ly, lane, first: bool) -> None:
    num, icon, name, body, tag, kind = lane
    _theme_lane_rail(slide, ax, ly, num, tag)
    bx = ax + Inches(0.62)
    bw = SLIDE_W - MARGIN - bx
    rect(slide, bx, ly, bw, _LANE_H, fill=WHITE, line=AMBER if first else MUTED, rounded=True)
    _tag_on_edge(slide, bx, ly, bw, kind)
    text_box(
        slide,
        bx + Inches(0.2),
        ly + Inches(0.12),
        bw - Inches(0.4),
        Inches(0.32),
        icon + " " + tr(name),
        Style(13, INK, True),
    )
    text_box(
        slide,
        bx + Inches(0.2),
        ly + Inches(0.46),
        bw - Inches(0.4),
        Inches(0.82),
        body,
        Style(12 if lang() == "en" else 11, INK),
    )


_THEME_NOTES = (
    "3 min. The stand-alone stack (deploy/standalone, ADR-090) is the whole platform on one "
    "machine, installed in the pre-work; name the five parts so the tool traces later make "
    "sense. ./r4-local up starts UMRS, storage and the knowledge store in containers, installs "
    "R4 into .venv (r4-local:14-17) and runs the model in a container, except on a Mac that "
    "already has Ollama, where it uses that app (deploy/standalone/lib/model.sh:109-112). "
    "R4 reaches every service through the UMRS gateway on localhost:8080, so the pieces are "
    "not a chain; the RAG embeddings (e5-base-v2) run inside UMRS, not in Ollama "
    "(deploy/standalone/env/base.env:50-58). An external AI service is an explicit opt-in, "
    "covered only in the optional slide at the end.\n"
    "Scope pills (first use; the legend comes with the model/agent/harness slide). "
    "Lane ② is a GENERAL PATTERN: in every harness, whatever sits in the context window "
    "reaches the model provider (Claude Code sends it to Anthropic, the OpenAI Agents SDK to "
    "OpenAI, opencode to whichever provider you configure). R4 re-sends the system message "
    "(_system_message, r4/src/r4/companion/agent.py:4609) and the conversation text on every "
    "request; within a turn, every tool result (file excerpts, RAG passages) is re-sent on "
    "each round, older ones shortened once they grow large. Tool traffic from earlier turns "
    "is not replayed (build_history, r4/src/r4/companion/session_turns.py:93-94). "
    "Lane ③ is a GENERAL PATTERN too: Claude Code's WebFetch/WebSearch and the OpenAI Agents "
    "SDK's WebSearchTool also send the URL or query out. In R4, web_fetch and web_search follow "
    "the session's /web choice (off, allowlist or public; r4/src/r4/companion/web_access.py); "
    "shell and Python downloads are not filtered by it, only by the approval card (and, with "
    "/sandbox on, by the sandbox's proxy, which follows the same /web choice; ADR-101). Lab 1.1 "
    "turns /web on to reach www.isc.ac.uk. "
    "Lane ① is R4-SPECIFIC: mainstream harnesses run tools on your machine or in the vendor's "
    "cloud sandbox (Claude Code on the web, Codex cloud, OpenAI Agents SDK hosted tools such "
    "as code interpreter and file search); none hands a durable job to your institution's "
    "scheduler. R4 does, through UMRS (workflow tools, r4/src/r4/companion/workflow_tools.py), "
    "and UMRS stages the inputs to the site. The LOCAL frame is GENERAL · R4 SETTINGS: "
    "pointing a harness at a local model server is common (opencode or LangChain with "
    "Ollama); R4's own choice is the bundle of engine, S3 store and pgvector knowledge store, "
    "started together by ./r4-local up, with a 32,768-token window "
    "(deploy/standalone/env/base.env:36).",
    "3 分。単体版(stand-alone)のスタック、つまり 1 台で動くサービス一式(deploy/standalone、"
    "ADR-090)がプラットフォーム全体で、インストールは事前準備で済んでいる。後のツールトレースが"
    "読めるよう 5 つの構成要素の名前を紹介する。./r4-local up は UMRS・ストレージ・"
    "ナレッジストアをコンテナで起動し、R4 を .venv に入れ(r4-local:14-17)、モデルはコンテナで"
    "動かす。ただし Ollama が入っている Mac ではそのアプリを使う"
    "(deploy/standalone/lib/model.sh:109-112)。R4 はどのサービスにも localhost:8080 の UMRS "
    "ゲートウェイ経由でつながるので、各要素は直列の連鎖ではない。RAG 用のベクトル化"
    "(埋め込み、e5-base-v2)は Ollama ではなく UMRS 内で動く(deploy/standalone/env/base.env:"
    "50-58)。外部の AI サービスは明示的に選んだ場合のみ使い、巻末の任意スライドでだけ扱う。\n"
    "色付きの印はここが初出(凡例はモデル・エージェント・ハーネスのスライドに載せる)。"
    "経路 ② は「一般的な仕組み」:どのハーネスでも、コンテキストウィンドウに入ったものは"
    "モデルの事業者に届く(Claude Code なら Anthropic、OpenAI Agents SDK なら OpenAI、"
    "opencode なら設定した事業者)。R4 はリクエストのたびにシステムメッセージ"
    "(_system_message、r4/src/r4/companion/agent.py:4609)と会話の本文を送り直す。1 ターンの"
    "中では、ツールの結果(ファイルの抜粋・RAG の文章)をラウンドごとに送り直し、大きくなった"
    "古い結果は短縮する。前のターンのツールのやり取りは再送しない(build_history、"
    "r4/src/r4/companion/session_turns.py:93-94)。"
    "経路 ③ も「一般的な仕組み」:Claude Code の WebFetch/WebSearch や OpenAI Agents SDK の "
    "WebSearchTool も URL や検索語を外に送る。R4 の web_fetch・web_search はセッションの /web "
    "の選択(off・allowlist・public、r4/src/r4/companion/web_access.py)に従う。シェルや "
    "Python からのダウンロードはこの設定では絞られず、承認カードが歯止めになる(/sandbox on の"
    "ときは、同じ /web の選択に従うサンドボックスのプロキシも効く。ADR-101)。Lab 1.1 "
    "では /web を有効にして www.isc.ac.uk にアクセスする。"
    "経路 ① は「R4 独自」:主なハーネスはツールを自分のマシンか事業者のクラウドのサンドボックス"
    "(Claude Code on the web、Codex cloud、OpenAI Agents SDK のコードインタプリタや"
    "ファイル検索などのホスト型ツール)で実行する。所属機関のスケジューラに永続的なジョブとして"
    "渡すものはない。R4 は UMRS を通してそれを行い(ワークフロー用ツール、"
    "r4/src/r4/companion/workflow_tools.py)、入力データは UMRS がサイトへ送る。ローカルの枠は"
    "「一般的な仕組み・R4 の設定」:ハーネスをローカルのモデルサーバにつなぐ構成は珍しくない"
    "(opencode や LangChain と Ollama の組み合わせなど)。R4 独自なのは、実行エンジン・"
    "S3 互換ストレージ・pgvector のナレッジストアを一式にし、./r4-local up でまとめて起動する点。"
    "コンテキストウィンドウは 32,768 トークン(deploy/standalone/env/base.env:36)。",
)


def theme_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "What runs where — and when data can leave",
            "全体像:何がどこで動き、データはいつ外に出るか",
        ),
        KICK,
    )
    deck.mark("theme")
    box_x, box_y = MARGIN, BODY_TOP + Inches(0.05)
    box_w, box_h = Inches(6.5), Inches(4.25)
    _theme_local_frame(slide, box_x, box_y, box_w, box_h)
    ax = box_x + box_w + Inches(0.12)
    for i, lane in enumerate(_LANES):
        _theme_lane(slide, ax, box_y + Inches(0.1) + i * _LANE_STEP, lane, i == 0)
    text_box(
        slide,
        box_x,
        box_y + box_h + Inches(0.1),
        CONTENT_W,
        Inches(0.8),
        (
            "🔒 **Three ways out** — job inputs (①), prompt text (②), web requests (③) — and "
            "each happens **only when you choose it**. Everything else stays on your disk. No "
            "accounts, no server, no GPU booking; you keep the setup after the course.",
            "🔒 外に出る経路は ① ジョブの入力、② プロンプト本文、③ ウェブへのリクエストの"
            " **3 つだけ**。どれも**自分で選んだときだけ**使われ、それ以外はすべて自分のディスクに"
            "残る。アカウント発行・サーバ・GPU 予約は不要で、チュートリアルの後も環境は手元に残る。",
        ),
        Style(13, INK),
    )
    notes(slide, _THEME_NOTES)


# ── 4. Why AI for Science ────────────────────────────────────────────────────


def _why_now_card(slide) -> None:
    card(
        slide,
        MARGIN,
        BODY_TOP,
        CONTENT_W,
        Inches(1.85),
        (
            "Why now — four pressures AI answers in science",
            "なぜ今か — AI が解決を助ける 4 つの課題",
        ),
        body_size=13,
        items=[
            (
                "**Volume** — papers and data grow faster than anyone can read: agents triage, "
                "summarise and route them.",
                "**量** — 論文もデータも、人が読み切れる量を超えて増えている:選別・要約・"
                "仕分けをエージェントが担う。",
            ),
            (
                "**Search speed** — simulations and experiments produce candidates faster than "
                "people can check: an agent drives the try → check loop.",
                "**探索速度** — シミュレーションや実験が生む候補の数に人の確認が追いつかない:"
                "試行 → 確認のループをエージェントが回す。",
            ),
            (
                "**Reproducibility** — every tool call and result is logged: you can audit what "
                "the agent did and re-run the workflow it built (the model's wording varies).",
                "**再現性** — ツール呼び出しと結果がすべて記録され、検証できる。作った"
                "ワークフローは再実行できる(モデルの文面は毎回変わる)。",
            ),
            (
                "**Access** — small labs get HPC-grade (high-performance computing) automation "
                "without a dedicated software team.",
                "**利用の裾野** — 専任の開発者がいない研究室でも HPC(高性能計算)級の自動化を"
                "使える。",
            ),
        ],
        line=OCEAN,
        fill=TINT,
    )


def _why_cards(slide) -> None:
    y = BODY_TOP + Inches(2.1)
    card(
        slide,
        MARGIN,
        y,
        Inches(5.9),
        Inches(2.5),
        ("What changes for a working scientist", "研究者の仕事で何が変わるか"),
        body_size=14,
        items=[
            (
                "Chat answers questions; an **agent does work** — reads files, runs code, "
                "checks results.",
                "チャットは質問に答える。**エージェントは仕事をする**:ファイルを読み、"
                "コードを実行し、結果を確かめる。",
            ),
            (
                "Your group's knowledge, tools and compute become usable by the agent.",
                "研究室の知識・ツール・計算資源をエージェントが使えるようになる。",
            ),
            (
                "You stay the judge: you set the goal and the acceptance check.",
                "判断するのは研究者自身:目標と合格基準を決める。",
            ),
        ],
    )
    x = MARGIN + Inches(6.25)
    card(
        slide,
        x,
        y,
        Inches(5.9),
        Inches(2.5),
        ("Why a general model is not enough", "汎用モデルだけでは足りない理由"),
        body_size=13,
        items=[
            (
                "**Domain materials** — papers, lab notes, data → RAG (retrieve your "
                "documents for the model)",
                "**専門資料** — 論文・ノート・データ → RAG(文書を検索してモデルに渡す仕組み)",
            ),
            (
                "**Tacit know-how** — how your group works → skills (how-to notes loaded on "
                "demand), instructions",
                "**暗黙知** — 研究室のやり方 → スキル(必要時に読み込む手順書)・指示",
            ),
            (
                "**Dedicated tools** — instruments, HPC scripts → MCP servers (Model Context "
                "Protocol: a standard plug for tools)",
                "**専用ツール** — 装置・HPC スクリプト → MCP サーバ(MCP = Model Context "
                "Protocol:外部ツールをつなぐ標準規格)",
            ),
        ],
    )
    _tag_on_edge(slide, x, y, Inches(5.9), "general")


def fugaku_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "Why AI for Science matters for your research",
            "AI for Science — なぜ自分の研究に関係するのか",
        ),
        KICK,
        ("10 min", "10 分"),
    )
    deck.mark("why_ai")
    _why_now_card(slide)
    _why_cards(slide)
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(4.68),
        CONTENT_W,
        Inches(0.58),
        (
            "Background at this site: the AI-for-Science programme of RIKEN R-CCS (Center for "
            "Computational Science), such as FugakuNEXT — see the official materials; this "
            "tutorial is about the hands-on method.",
            "背景:理研 計算科学研究センター(R-CCS)の AI for Science の取り組み(「富岳NEXT」"
            "など)は公式資料を参照。このチュートリアルは手法の実習が主題。",
        ),
        Style(13, MUTED, italic=True),
    )
    notes(
        slide,
        (
            "10 min in the full Opening (2 min in the Hyogo cut). The right card is the "
            "design's 'why a general LLM is not enough' table (docs/R4-UMRS-DESIGN.md). "
            "FugakuNEXT is deliberately not front and centre (review 2026-10-27): if the "
            "event wants it, insert the official R-CCS/FugakuNEXT slides before this one.\n"
            "Pill: RAG, skills and MCP are GENERAL PATTERNS. Claude Code has Agent Skills "
            "(SKILL.md loaded when relevant) and MCP servers; LangChain has retrievers and MCP "
            "adapters; the OpenAI Agents SDK has hosted file search and MCP. R4's own versions "
            "(RAG run on the UMRS server, MCP servers reached through the UMRS gateway "
            "registry, keyword skill activation) come in Part 1 with their own pills.",
            "通常 10 分(兵庫講義版は 2 分)。右のカードは設計書(docs/R4-UMRS-DESIGN.md)の"
            "「汎用 LLM(大規模言語モデル)では足りない理由」の表。「富岳NEXT」はあえて前面に"
            "出さない(2026-10-27 レビュー)。必要なら公式スライドをこの前に差し込む。\n"
            "印:RAG・スキル・MCP は「一般的な仕組み」。Claude Code にはスキル(必要なときに "
            "SKILL.md を読み込む)と MCP サーバがあり、LangChain にはリトリーバ(検索器)と "
            "MCP アダプタ、"
            "OpenAI Agents SDK にはファイル検索と MCP がある。R4 独自の実装(RAG を UMRS "
            "サーバ側で実行、MCP サーバは UMRS ゲートウェイの登録簿経由、キーワードによる"
            "スキルの起動)は第 1 部でそれぞれ印を付けて扱う。",
        ),
    )


# ── 5. The platform ──────────────────────────────────────────────────────────

_TILES = [
    (
        "👤",
        ("You", "研究者"),
        (
            "set the goal, own the data, judge the result",
            "目標を決め、データを持ち、結果を判断する",
        ),
        WHITE,
        INK,
        None,
    ),
    (
        "💻",
        ("R4", "R4"),
        (
            "the client you use: the console (TUI: a screen in your terminal) or the CLI, "
            "the agent, skills, sessions",
            "自分が使うクライアント:コンソール(TUI = ターミナル上の画面)または CLI"
            "(コマンドで操作)、エージェント・スキル・セッション",
        ),
        TINT,
        OCEAN,
        "general",
    ),
    (
        "⚙",
        ("UMRS", "UMRS"),
        (
            "the backbone that runs work: jobs, storage, models, MCP registry",
            "処理を実行する基盤:ジョブ・ストレージ・モデル・MCP 登録簿(レジストリ)",
        ),
        TINT,
        OCEAN,
        "r4",
    ),
    (
        "🖥",
        ("Site", "サイト"),
        (
            "where compute happens: your laptop today, Rikyu GPU nodes later",
            "計算する場所:今日は自分の PC、規模を広げるときは Rikyu の GPU ノード",
        ),
        TINT_AMBER,
        INK,
        None,
    ),
]
_TILE_H = Inches(1.95)


def _platform_tile(slide, x, y, w, tile) -> None:
    icon, name, sub, fill, color, kind = tile
    line = OCEAN if fill == WHITE else None
    rect(slide, x, y, w, _TILE_H, fill=fill, line=line, rounded=True)
    if kind:
        _tag_on_edge(slide, x, y, w, kind)
    text_box(
        slide,
        x + Inches(0.15),
        y + Inches(0.12),
        w - Inches(0.3),
        Inches(0.55),
        icon + " " + tr(name),
        Style(24, color, True),
        align=PP_ALIGN.CENTER,
    )
    text_box(
        slide,
        x + Inches(0.15),
        y + Inches(0.72),
        w - Inches(0.3),
        Inches(1.15),
        sub,
        Style(12, INK),
        align=PP_ALIGN.CENTER,
    )


def _platform_cards(slide, y) -> None:
    cw, ch = Inches(5.9), Inches(2.8)
    card(
        slide,
        MARGIN,
        y,
        cw,
        ch,
        ("Scientific Catalog — knowledge as data", "Scientific Catalog — 知識をデータとして持つ"),
        body_size=13,
        items=[
            (
                "A portable bundle of what a project knows: skills, MCP definitions, the RAG "
                "index and vectors, a knowledge graph (concepts and their links). Data, not a "
                "service — export it and share it.",
                "プロジェクトの知識をまとめた持ち運べるデータ一式:スキル・MCP 定義・RAG 索引と"
                "ベクトル・ナレッジグラフ(概念と関係のネットワーク)。サービスではなくデータなので、"
                "書き出して共有できる。",
            ),
            (
                "**Others have this too:** Claude Code plugins bundle skills and MCP servers; a "
                "Claude project keeps knowledge files and searches them when they grow large.",
                "**同様の仕組みは他にもある:**Claude Code のプラグインはスキルや MCP サーバを"
                "まとめ、Claude のプロジェクトは知識ファイルを持ち、大きくなると検索で使う。",
            ),
            (
                "**R4 adds:** the vectors and knowledge graph travel with it, and it is served "
                "per project by UMRS on your site.",
                "**R4 が加える点:**ベクトルとナレッジグラフも一緒に持ち運べ、自分のサイトの UMRS "
                "がプロジェクトごとに提供する。",
            ),
        ],
    )
    _tag_on_edge(slide, MARGIN, y, cw, "r4tuned")
    x = MARGIN + Inches(6.25)
    card(
        slide,
        x,
        y,
        cw,
        ch,
        ("Where credentials come in", "認証情報が登場するのはどこか"),
        body_size=13,
        items=[
            (
                "Some connections — a hosted model API, a remote site — need an **API key or "
                "access token (credentials)**.",
                "接続先(外部の AI サービスの API、リモートの計算サイトなど)によっては"
                "**API キーやアクセストークン(認証情報)**が必要になる。",
            ),
            (
                "As in any AI client (Claude Code included), R4 attaches tokens to each "
                "request itself — they are never put into the prompt.",
                "Claude Code を含む一般的な AI ツールと同じく、トークンは R4 自身がリクエストに"
                "付けるので、プロンプトには入らない。",
            ),
            (
                "**R4 adds a default fence:** a tool call that touches a credential file "
                "(~/.ssh, ~/.aws, ~/.config/r4 …) asks you every time, in every mode — say no. "
                "In Claude Code you get this by writing deny rules yourself.",
                "**R4 が加える既定の防御:**認証情報のファイル(~/.ssh、~/.aws、~/.config/r4 "
                "など)に触れるツール呼び出しは、どのモードでも毎回確認を求める。求められたら拒否"
                "する。Claude Code では自分で deny ルールを書いて同じことをする。",
            ),
        ],
    )
    _tag_on_edge(slide, x, y, cw, "r4tuned")


_PLATFORM_NOTES = (
    "15 min (3 min in the Hyogo cut). Source: docs/R4-UMRS-DESIGN.md, system architecture. "
    "Rikyu is the compute site with GPU nodes; today the 'site' is the attendee's laptop "
    "running the stand-alone UMRS.\n"
    "Pills. R4 is a GENERAL PATTERN: it is the harness, the same kind of program as Claude "
    "Code or opencode (console, agent loop, skills, sessions); Part 1 opens it up. UMRS is "
    "R4-SPECIFIC: mainstream harnesses run tools on your machine or in the vendor's cloud "
    "sandbox (Claude Code on the web, Codex cloud, OpenAI Agents SDK hosted tools such as code "
    "interpreter and file search); none hands a durable job to your institution's scheduler. "
    "R4 does, through UMRS, which adds durable jobs, S3 buckets, a model registry and the MCP "
    "registry the agent reaches through the gateway "
    "(r4/src/r4/companion/mcp_first_class.py:126-205). The Catalog: bundling skills and MCP "
    "server definitions into a shareable package is general (Claude Code plugins carry "
    "skills, agents, hooks and MCP servers); R4-SPECIFIC is that the bundle also carries the "
    "RAG index, vectors and knowledge graph (r4 knowledge export, "
    "r4/src/r4/_cli/knowledge.py:400-430) and that it lives on UMRS: search_docs calls "
    "GET /gw/rag/retrieve (r4/src/r4/rag.py:846-869), with no chat model involved. "
    "Credentials: putting keys in HTTP headers and not in the prompt is what every SDK does "
    "(Anthropic, OpenAI, opencode providers; R4: r4/src/r4/client.py:1099-1130), hence the "
    "'as in any AI client' bullet. The R4-SPECIFIC part, and the reason for the pill, is the "
    "credential-path class: any file or shell tool that touches ~/.ssh, ~/.aws/credentials, "
    "~/.config/r4/*.json, *.pem, .netrc ... asks every time, in every mode, autopilot "
    "included, and no earlier answer or rule settles it (r4/src/r4/companion/"
    "permissions.py:365-397, 417-431, 917-926; ADR-104 ladder rung 5). It asks; it does not "
    "block: approving puts the file into the context, so the answer is no. Review "
    "2026-11-05 ('doesn't Claude have the same?'): yes in part, hence the cards now carry "
    "GENERAL · R4 SETTINGS. Claude Code keeps credentials out of the prompt too, protects "
    "files only through deny rules you write (e.g. Read(./.env)), and its optional sandbox "
    "write-protects some paths but still lets commands read ~/.ssh and ~/.aws "
    "(code.claude.com/docs/en/permissions, /sandboxing, checked 2026-11-05). Claude Code "
    "plugins bundle skills, agents, hooks and MCP servers, listed in marketplaces "
    "(/plugins); Claude projects keep knowledge files and switch to a search tool when they "
    "outgrow the window.",
    "15 分(兵庫講義版は 3 分)。出典:docs/R4-UMRS-DESIGN.md のシステム構成。Rikyu は "
    "GPU ノードを持つ計算サイト。今日の「サイト」は単体版 UMRS(stand-alone)を動かす"
    "各自のノート PC。\n"
    "印について。R4 は「一般的な仕組み」:ハーネスそのもので、Claude Code や opencode と"
    "同じ種類のプログラム(コンソール・エージェントループ・スキル・セッション)。第 1 部で"
    "中身を見る。UMRS は「R4 独自」:主なハーネスはツールを自分のマシンか事業者のクラウドの"
    "サンドボックス(Claude Code on the web、Codex cloud、OpenAI Agents SDK のコード"
    "インタプリタやファイル検索などのホスト型ツール)で実行する。所属機関のスケジューラに"
    "永続的なジョブとして渡すものはない。R4 は UMRS を通してそれを行い、UMRS は永続的な"
    "ジョブ・S3 互換のバケット・モデル登録簿、さらにエージェントがゲートウェイ経由で使う MCP "
    "登録簿を加える(r4/src/r4/companion/mcp_first_class.py:126-205)。Catalog について:"
    "スキルや MCP サーバの定義を共有できるパッケージにまとめるのは一般的な仕組み(Claude Code "
    "のプラグインはスキル・エージェント・フック・MCP サーバを含む)。「R4 独自」なのは、"
    "その一式に RAG 索引・ベクトル・ナレッジグラフも入ること(r4 knowledge export、"
    "r4/src/r4/_cli/knowledge.py:400-430)と、それが UMRS 上にあること。search_docs は "
    "GET /gw/rag/retrieve を呼ぶ(r4/src/r4/rag.py:846-869)。チャット用モデルは使わない。"
    "認証情報について:キーを HTTP ヘッダに載せてプロンプトに入れないのは、どの SDK でも"
    "同じ(Anthropic・OpenAI の SDK、opencode の接続設定。R4 では r4/src/r4/client.py:"
    "1099-1130)。だからカードでは「一般的な AI ツールと同じく」と書いている。「R4 独自」の印の"
    "理由は認証情報パスの区分で、~/.ssh、~/.aws/credentials、~/.config/r4/*.json、*.pem、"
    ".netrc などに触れるファイル操作・シェルのツールは、autopilot を含むどのモードでも毎回"
    "確認を求め、以前の答えやルールでは決まらない(r4/src/r4/companion/permissions.py:"
    "365-397、417-431、917-926。ADR-104 の判定の 5 段目)。確認を求めるだけで"
    "遮断はしない。許可するとファイルの中身がコンテキストに入るので、答えは「拒否」。"
    "2026-11-05 のレビュー(「Claude にも同じ機能があるのでは」):一部はその通りなので、"
    "カードの印を「一般的な仕組み・R4 の設定」にした。Claude Code もトークンをプロンプトに"
    "入れない。ファイルの保護は自分で書く deny ルール(例:Read(./.env))で行い、任意の"
    "サンドボックスは一部のパスへの書き込みを防ぐが、~/.ssh や ~/.aws の読み取りは許す"
    "(code.claude.com/docs/en/permissions、/sandboxing、2026-11-05 確認)。Claude Code の"
    "プラグインはスキル・エージェント・フック・MCP サーバをまとめ、マーケットプレイスで"
    "配布する。Claude のプロジェクトは知識ファイルを持ち、ウィンドウに収まらなくなると検索"
    "ツールに切り替える。",
)


def platform_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "The platform: R4, UMRS, Scientific Catalog, Rikyu",
            "プラットフォーム:R4・UMRS・Scientific Catalog・Rikyu",
        ),
        KICK,
        ("15 min", "15 分"),
    )
    deck.mark("platform")
    y = BODY_TOP + Inches(0.15)
    w = Inches(2.65)
    gap = (CONTENT_W - 4 * w) / 3
    for i, tile in enumerate(_TILES):
        x = MARGIN + i * (w + gap)
        _platform_tile(slide, x, y, w, tile)
        if i < 3:
            rect(
                slide,
                x + w + Inches(0.08),
                y + Inches(0.85),
                gap - Inches(0.16),
                Inches(0.08),
                fill=SEAFOAM,
            )
    _platform_cards(slide, y + _TILE_H + Inches(0.25))
    notes(slide, _PLATFORM_NOTES)


# ── 6. The case ──────────────────────────────────────────────────────────────

_STORY_NOTES = (
    "3 min. REAL DATA (review 2026-11-05: 'use the station positions and arrival times of "
    "real data'): ISC event 621611300, 2021-12-03 00:28:28.6 UTC, Kii Channel, epicentre "
    "33.815 N 135.142 E, 16 km deep; 25 stations within 2.5 degrees. Station positions are "
    "rebuilt from ISC's epicentral distance and azimuth (fetch_isc_picks.py; about ±1 km). "
    "Both figures are rendered by render_kii_map.py, which downloads the picks into the "
    "gitignored tasks/eikonal/data/kii-2021-12-03; only the PNGs are committed, so the "
    "repository holds no table of ISC values (decision 2026-10-27). The chart is plain "
    "travel time against distance. The slope changes near ~110 km: beyond it the first "
    "arrival is Pn, the head wave refracted along just below the Moho (the crust–mantle "
    "boundary), which travels faster. Against these picks the global model ak135 (35 km Moho) "
    "is 0.8 s too late on average (RMS 1.0 s) and 1.0 s beyond 130 km (0.5–1.9 s, 16 "
    "stations); KRSO-1D v2, the tutorial's regional model, is within 0.2 s on average (RMS "
    "0.5 s) (first_arrival in docs/tutorial/tasks/eikonal/make_synthetic_picks.py, the "
    "analytic layered-model formula). Dashed rings: KRSO-1D v2 first-arrival isochrons at "
    "10/20/30 s for the 16 km depth. Lab 1.1 has the agent download the same event; the "
    "synthetic set (data/synthetic-kii) stays as the offline fallback. Map relief: ETOPO1 1 "
    "arc-minute global relief, Amante & Eakins 2009, NOAA NCEI, doi:10.7289/V5C8276M (public "
    "domain); coastlines: Natural Earth 1:10m (public domain). Cite: International "
    "Seismological Centre (2026), On-line Bulletin, doi:10.31905/D808B830.",
    "3 分。【実データ】(2026-11-05 のレビュー:「実データの観測点の位置と到達時刻を使えないか」):"
    "ISC イベント 621611300、2021-12-03 00:28:28.6 UTC、紀伊水道、震央 北緯 33.815 度・東経 "
    "135.142 度、深さ 16 km。震央から 2.5 度以内の 25 観測点。観測点の位置は ISC の震央距離と"
    "方位角から復元したもの(fetch_isc_picks.py。誤差は約 1 km)。2 つの図は "
    "render_kii_map.py が描き、読み取り値は git の管理対象外の tasks/eikonal/data/kii-2021-12-03 "
    "にダウンロードする。コミットするのは PNG だけなので、リポジトリに ISC の値の表は置かない"
    "(2026-10-27 の決定)。グラフは走時と震央距離をそのまま描いたもの。約 110 km で傾きが"
    "変わる。それより遠くでは、初動はモホ面(地殻とマントルの境界)直下を伝わる、より速い"
    "屈折波 Pn(ヘッドウェーブ)になる。この読み取りに対して、全球モデル ak135(モホ 35 km)は"
    "平均 0.8 秒遅く(RMS 1.0 秒)、130 km より遠くでは平均 1.0 秒遅い(0.5〜1.9 秒、16 観測点)。"
    "チュートリアルの地域モデル KRSO-1D v2 は平均 0.2 秒以内(RMS 0.5 秒)"
    "(docs/tutorial/tasks/eikonal/make_synthetic_picks.py の first_arrival、層構造の解析式)。"
    "破線の円は、深さ 16 km での KRSO-1D v2 の 10・20・30 秒の等走時線。Lab 1.1 ではエージェント"
    "が同じ地震を取得する。合成データ(data/synthetic-kii)はオフライン用の代わりとして残す。"
    "地形:ETOPO1 1 分角全球地形(Amante & Eakins 2009、NOAA NCEI、doi:10.7289/V5C8276M、"
    "パブリックドメイン)。海岸線:Natural Earth 1:10m(パブリックドメイン)。引用:"
    "International Seismological Centre (2026), On-line Bulletin, doi:10.31905/D808B830。",
)


def story_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "The case: a Kii Channel earthquake — real shape of the problem",
            "題材:紀伊水道の地震 — 実際の問題設定",
        ),
        KICK,
    )
    deck.mark("story")
    text_box(
        slide,
        MARGIN,
        BODY_TOP,
        Inches(6.2),
        Inches(0.32),
        (
            "Real stations (ISC), coloured by observed first P-wave arrival",
            "実際の観測点(ISC)— 観測された P 波初動の到達時刻で色分け",
        ),
        Style(14 if lang() == "ja" else 13, INK, True),
    )
    picture(
        slide,
        ("kii-station-map.en.png", "kii-station-map.ja.png"),
        MARGIN,
        BODY_TOP + Inches(0.34),
        Inches(6.2),
        Inches(3.45),
        alt=(
            "Relief map of the Kii Channel region: the 25 ISC stations of the 2021-12-03 event "
            "coloured by observed first-arrival time, epicentre star (16 km deep), predicted "
            "first-arrival isochrons",
            "紀伊水道周辺の地形図:2021-12-03 の地震の ISC 観測点 25 点を観測された初動の到達時刻で"
            "色分けし、震央(深さ 16 km)と予測の等走時線を表示",
        ),
    )
    picture(
        slide,
        ("kii-traveltime.en.png", "kii-traveltime.ja.png"),
        MARGIN + Inches(6.45),
        BODY_TOP,
        Inches(5.7),
        Inches(3.8),
        alt=(
            "Travel time against epicentral distance: 25 ISC picks, the ak135 and KRSO-1D v2 "
            "first-arrival curves",
            "走時と震央距離:ISC の読み取り 25 点と、ak135・KRSO-1D v2 の初動の曲線",
        ),
    )
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(3.87),
        CONTENT_W,
        Inches(0.62),
        (
            "**Pick** = the first P-wave arrival time read from each station's seismogram — "
            "here the real ones, from the ISC Bulletin. Right: seconds from the earthquake to "
            "that arrival, against distance. Beyond ~110 km the wave that arrives first runs "
            "along the top of the mantle, so the slope changes; the global model (grey) is "
            "about 1 s late there.",
            "**読み取り(pick)**= 各観測点の地震記録から読み取った P 波初動の到達時刻。ここでは "
            "ISC の公開カタログにある実際の値。右図は、地震発生からその到達までの秒数(走時)と"
            "距離。約 110 km より遠くでは、マントルの最上部を伝わる波が先に着くので傾きが変わる。"
            "全球モデル(灰色)はそこで約 1 秒遅い。",
        ),
        Style(13, INK),
    )
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(4.55),
        CONTENT_W,
        Inches(0.45),
        (
            "Real event: 2021-12-03, Kii Channel, M~5 — picks downloaded live in Lab 1.1 from "
            "the ISC (International Seismological Centre) Bulletin. Reference: International "
            "Seismological Centre (2026), On-line Bulletin, doi:10.31905/D808B830.",
            "実際の地震:2021-12-03、紀伊水道、M5 程度。読み取りは ISC(国際地震センター)の"
            "公開カタログから、Lab 1.1 でその場で取得する。出典:International Seismological Centre (2026), "
            "On-line Bulletin, doi:10.31905/D808B830。",
        ),
        Style(11, MUTED),
    )
    notes(slide, _STORY_NOTES)


# ── 7. Environment check ─────────────────────────────────────────────────────

_ENV_TAIL = (
    "cd r4\n"
    "./r4-local up       # installs, pulls the model, registers\n"
    "\n"
    "# today — the 5-minute check, in a terminal (repository folder)\n"
    "./r4-local doctor\n"
    "source .venv/bin/activate && r4\n"
    "\n"
    "# then everything inside the console\n"
    "/source           # pick: standalone\n"
    "/health           # gateway, endpoints, platform\n"
    "/models           # the local model\n"
)
ENV_CHECK = (
    "# pre-work — already done at home\n"
    "git clone --recurse-submodules https://github.com/R-CCS-HPBD/r4.git\n"
    + _ENV_TAIL
    + "Say hello in one sentence.\n"
    "/rag docs         # the knowledge store answers",
    "# pre-work — already done at home\n"
    "git clone --recurse-submodules https://github.com/R-CCS-HPBD/r4.git\n"
    + _ENV_TAIL
    + "1 文で挨拶して。\n"
    "/rag docs         # the knowledge store answers",
)

_CHECKS = [
    ("Stack healthy", "スタック(サービス一式)が正常"),
    ("r4 reaches the local UMRS", "r4 がローカル UMRS に接続"),
    ("The model answers", "モデルが応答する"),
    ("The knowledge store answers", "ナレッジストアが応答する"),
    (
        "No login, no /connect — the pre-work register already bound the local stack",
        "ログインも /connect も不要。事前準備の登録(./r4-local up がアクセストークンを発行)"
        "で接続済み",
    ),
]


def env_check_slide(deck: Deck) -> None:
    slide = content(
        deck, ("Environment check — 5 minutes", "環境チェック — 5 分"), KICK, ("5 min", "5 分")
    )
    deck.mark("env_check")
    code_panel(
        slide,
        MARGIN,
        BODY_TOP,
        Inches(7.4),
        Inches(4.7),
        ENV_CHECK,
        size=12,
        title=("terminal", "ターミナル"),
    )
    x = MARGIN + Inches(7.8)
    w = CONTENT_W - Inches(7.8)
    for i, text in enumerate(_CHECKS):
        y = BODY_TOP + Inches(0.05) + i * Inches(0.68)
        badge(slide, x, y, "✓", fill=OCEAN, size=Inches(0.42), font=14)
        text_box(slide, x + Inches(0.6), y, w - Inches(0.6), Inches(0.62), text, Style(15, INK))
    rect(slide, x, BODY_TOP + Inches(3.8), w, Inches(0.95), fill=TINT_AMBER, rounded=True)
    text_box(
        slide,
        x + Inches(0.2),
        BODY_TOP + Inches(3.92),
        w - Inches(0.4),
        Inches(0.78),
        (
            "Red light? Raise your hand — staff fix it now, not in the middle of Part 1.",
            "赤ランプが出たら手を挙げてください。第 1 部の途中ではなく、今のうちにスタッフが"
            "直します。",
        ),
        Style(14, INK),
    )
    notes(
        slide,
        (
            "5 min. Real installs happen in the pre-work (handbook 00). ./r4-local up registers "
            "the stack as the r4 source 'standalone' with a 30-day token (./r4-local register "
            "issues a fresh one) but leaves your active source unchanged (r4-local:16-17), so "
            "/source comes before /health: /health reports on the active source. ./r4-local "
            "doctor runs the health probes (status only lists the containers). Staff walk the "
            "room; anyone still red pairs with a neighbour for Part 1.",
            "5 分。インストール本体は事前準備(ハンドブック 00)で実施済み。./r4-local up は"
            "スタックを r4 の接続先 'standalone' として登録し、30 日間有効のトークンを発行する"
            "(./r4-local register で再発行できる)。ただし現在使っている接続先は切り替えない"
            "(r4-local:16-17)。/health は現在の接続先を調べるので、先に /source で選ぶ。"
            "./r4-local doctor は稼働確認を行う(status はコンテナの一覧を出すだけ)。スタッフが巡回し、まだ赤い人は第 1 部を"
            "隣の人とペアで進める。",
        ),
    )


def add(deck: Deck) -> None:
    title_slide(deck)
    arc_slide(deck)
    theme_slide(deck)
    fugaku_slide(deck)
    platform_slide(deck)
    story_slide(deck)
    env_check_slide(deck)
