"""Part 1, minutes 55–120: real data, agentic coding, your inference environment.

Slides 32–43 (Lab 1.1–1.5, good practice, quiz). Commands and harness facts
were checked against the code on 2026-10-29 (file:line in the speaker notes);
numbers are measured (docs/tutorial/data/README.md,
docs/tutorial/tasks/eikonal/README.md). Real ISC picks are never stored in
the repository: the agent downloads them live; the offline fallback is
synthetic.

Scope pills (deck_kit.scope_tag) mark each harness mechanism as a general
agent-harness pattern, a general pattern with R4's own settings, or
R4-specific; the speaker notes name comparable harnesses.
"""

from __future__ import annotations

from deck_kit import (
    AMBER,
    BODY_TOP,
    CONTENT_W,
    INK,
    MARGIN,
    MUTED,
    OCEAN,
    SCOPE_H,
    SCOPE_KINDS,
    TINT,
    TINT_AMBER,
    WHITE,
    Deck,
    Style,
    arrow,
    badge,
    bar_chart,
    card,
    chip,
    code_panel,
    content,
    notes,
    rect,
    scope_tag,
    table,
    text_box,
    text_width,
    tr,
)
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

KICK = ("Part 1 · Agentic work", "第 1 部 · エージェンティックワーク")
KICK_CODE = ("Part 1 · Agentic coding", "第 1 部 · エージェンティックコーディング")
KICK_LAB = ("Part 1 · Your inference environment", "第 1 部 · 自分の推論環境")
CITE = (
    "Picks: International Seismological Centre (2026), On-line Bulletin, doi:10.31905/D808B830",
    "読み取りの出典:International Seismological Centre (2026), On-line Bulletin, doi:10.31905/D808B830",
)

RAG_Q = (
    "Which reference model, Moho depth and mantle velocity do we use for a Kii Channel "
    "event, and when is a pick rejected?",
    "紀伊水道の地震にはどの参照モデル・モホ面の深さ・マントル速度を使い、"
    "どの場合に読み取り(pick)を棄却しますか?",
)


def tag(slide, right, top, kind: str):
    """Scope pill straddling a panel's top edge ``top``, inset from ``right``."""
    width = text_width(tr(SCOPE_KINDS[kind][0]), 9) + Inches(0.24)
    return scope_tag(slide, right - width - Inches(0.2), top - SCOPE_H // 2, kind)


def prompt_card(slide, x, y, w, h, label, text, fill=TINT_AMBER, size=15):
    """A 'type this' prompt, quoted, with a small label chip."""
    rect(slide, x, y, w, h, fill=fill, rounded=True)
    chip(slide, x + Inches(0.2), y + Inches(0.18), label, fill=AMBER, w=Inches(1.6))
    text_box(
        slide,
        x + Inches(0.25),
        y + Inches(0.65),
        w - Inches(0.5),
        h - Inches(0.75),
        text,
        Style(size, INK, italic=True),
    )


def note_strip(slide, x, y, w, h, text, size=14, fill=TINT):
    """Tinted rounded strip with one block of text."""
    rect(slide, x, y, w, h, fill=fill, rounded=True)
    text_box(
        slide,
        x + Inches(0.25),
        y + Inches(0.18),
        w - Inches(0.5),
        h - Inches(0.25),
        text,
        Style(size, INK),
    )


# ── Lab 1.1 — guided task ───────────────────────────────────────────────────

GUIDED_CODE = (
    "# inside the console\n"
    "Ctrl+P → Open pane: Settings\n"
    "/models        # pick the chat model\n"
    "Ctrl+O         # show every tool call\n"
    "F9             # activity tray\n"
    "/web           # web_fetch / web_search only\n"
    "!<command>     # shell; output reaches the agent\n"
    "/term share    # send last 40 shell lines to agent\n"
    "Shift+Tab      # mode: chat → plan → autopilot\n"
    "\n"
    "# approve each tool call\n"
    "y once · a this session\n"
    "n no: the agent is told you said no",
    "# inside the console\n"
    "Ctrl+P → Open pane: Settings（設定ペイン）\n"
    "/models        # pick the chat model\n"
    "Ctrl+O         # show every tool call\n"
    "F9             # activity tray\n"
    "/web           # web_fetch / web_search only\n"
    "!<command>     # shell; output reaches the agent\n"
    "/term share    # send last 40 shell lines to agent\n"
    "Shift+Tab      # mode: chat → plan → autopilot\n"
    "\n"
    "# approve each tool call\n"
    "y 今回だけ · a このセッション中は許可\n"
    "n 拒否：拒否したことがエージェントに伝わる",
)

GUIDED_NOTES = (
    "10 min. Everything happens in the console. /web opens the web-access choice (off / "
    "allowlist / public). The agent should save raw data with python or curl (web_fetch strips "
    "pages to text). /web filters only web_fetch and web_search; python_exec, shell_exec and "
    "MCP tools are not filtered (web_access.py:12), so the python or curl download works "
    "whatever /web says. /settings typed alone does NOT open the pane; use Ctrl+P. Expected: 25 "
    "stations, farthest JHT at ~268 km. The data are never committed.\n"
    "Approval keys (ADR-104, 2026-10-31): y = this call only; the next call of the same tool "
    "asks again. a = this tool for the rest of the session (approve_for_session; the "
    "companion keeps it). There is no project-wide answer (ADR-104 amendment 1, "
    "2026-11-03): a standing rule goes in ~/.config/r4/permissions.json on purpose "
    "(/settings rules lists it). n = reject with feedback, "
    "r or Esc = reject. The companion decides which answers a card offers "
    "(approval_answers.py:37-60); the console only shows the card and never answers by "
    "itself. a is not offered for credential calls, calls that copy text from "
    "documents, MCP results or downloads (tainted), protected paths, ask rules and the "
    "always-ask tools: those count for that call only. n rejects the call; the agent is told "
    "it was your no and the tool stays available (only a deny that holds for the whole tool "
    "hides it: agent.py:5299-5335). Shift+Tab cycles the mode chat → plan → autopilot.\n"
    "Scope: approval prompts are a general pattern (Claude Code: yes / yes, don't ask again / "
    "no; opencode: once / always / reject). !<command> is "
    "like Claude Code's ! bash mode. /term opens a shell pane; /term share [N] sends its last N "
    "lines (default 40) to the agent once, as a turn (wave-intercepts.ts:55-83; never streamed). "
    "That is R4's addition. FDSN = the International Federation of Digital Seismograph "
    "Networks; its standard FDSN Web Services deliver seismic data. " + CITE[0],
    "10 分。すべてコンソールの中で行う。/web で Web アクセス(off / allowlist / public)を選ぶ。"
    "生データは python か curl で保存させる(web_fetch はページをテキストに変換してしまう)。"
    "/web が制限するのは web_fetch と web_search だけ。python_exec・shell_exec・MCP は対象外"
    "(web_access.py:12)なので、python や curl での取得は /web の設定に関係なく動く。"
    "/settings と入力してもペインは開かないので Ctrl+P を使う。想定:25 観測点、最遠は JHT の"
    "約 268 km。データはリポジトリに保存しない。\n"
    "承認キー(ADR-104、2026-10-31):y はこの 1 回だけで、同じツールの次の呼び出しでも"
    "また確認する。a はそのセッション中そのツールを許可(permissions.py:1019 の "
    "approve_for_session。コンパニオンが覚える)。プロジェクト全体の許可という答えはない"
    "(ADR-104 の改訂 1、2026-11-03)。常設のルールは ~/.config/r4/permissions.json に"
    "意図して書く(/settings rules で一覧できる)。n は理由つきで拒否、"
    "r か Esc は拒否。カードに出す答えはコンパニオンが決め(approval_answers.py:37-60)、"
    "コンソールはカードを表示するだけで自分では答えない。認証情報に関わる呼び出し、文書・"
    "MCP の結果・ダウンロードしたデータから写した文字列を含む呼び出し、保護パス、ask ルール、"
    "常に確認が必要なツールには a は出ず、その呼び出しだけに効く。n で拒否すると、"
    "利用者が拒否したことがエージェントに伝わり、ツールはそのまま使える(ツール全体を拒否"
    "するときだけ隠す:agent.py:5299-5335)。Shift+Tab でモードを chat → plan → autopilot "
    "と切り替える。\n"
    "位置づけ:ツール実行前の承認は一般的な仕組み(Claude Code は「はい/今後は確認しない/いいえ」、"
    "opencode は once / always / reject)。!<command> は "
    "Claude Code の ! (bash モード)と同じ考え方。/term で開いたシェルの直近 N 行(既定 40)を "
    "1 回だけエージェントに送る /term share [N] は R4 の追加機能(常時共有はしない)。"
    "FDSN = 国際デジタル地震計ネットワーク連盟。その標準 Web サービス(FDSN Web Services)で"
    "地震データを配信する。" + CITE[1],
)


def guided_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "Lab 1.1 — Guided task: let the agent fetch a real earthquake",
            "Lab 1.1 — ガイド付き課題:実際の地震データを取得させる",
        ),
        KICK,
        ("55–65 min", "55–65 分"),
    )
    code_panel(
        slide,
        MARGIN,
        BODY_TOP,
        Inches(5.9),
        Inches(4.7),
        GUIDED_CODE,
        size=12,
        title=("console", "コンソール"),
    )
    tag(slide, MARGIN + Inches(5.9), BODY_TOP, "r4tuned")
    x = MARGIN + Inches(6.3)
    w = CONTENT_W - Inches(6.3)
    prompt_card(
        slide,
        x,
        BODY_TOP,
        w,
        Inches(2.6),
        ("type this", "入力する"),
        (
            "“Download the first-P arrival times within 2.5° for ISC event 621611300 (Kii "
            "Channel, 2021-12-03) from the ISC FDSN event service. Save event.json and "
            "p_picks.csv in docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03. How many "
            "stations, and which is farthest?”",
            "「ISC の FDSN イベントサービスから、ISC イベント 621611300(紀伊水道、2021-12-03)の"
            "震央距離 2.5° 以内の P\u00a0波初動の到達時刻を取得し、event.json と p_picks.csv を"
            "次のフォルダに保存して:\n"
            "docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03\n"
            "観測点はいくつで、最も遠いのはどれ?」",
        ),
        size=14,
    )
    card(
        slide,
        x,
        BODY_TOP + Inches(2.8),
        w,
        Inches(1.9),
        ("Expect", "期待される結果"),
        body_size=13,
        items=[
            ("25 stations; the farthest about 268 km away.", "25 観測点。最も遠い点は約 268 km。"),
            (
                "No network? Use data/synthetic-kii, and say it is synthetic.",
                "ネットワークにつながらない場合は data/synthetic-kii を使い、合成データだと明記する。",
            ),
            ("Full console cheat sheet: handbook 01.", "コンソールの早見表:ハンドブック 01。"),
        ],
    )
    notes(slide, GUIDED_NOTES)
    deck.mark("lab_1_1")


# ── Reading the tool trace ──────────────────────────────────────────────────

TRACE_ROWS = [
    (
        ("plan + tool call", "計画+ツール呼び出し"),
        (
            "one reply: “Query ISC with arrivals, keep first P ≤ 2.5°” → python_exec urllib … "
            "eventid=621611300",
            "1 回の返答:「到達時刻付きで ISC に問い合わせ、2.5° 以内の最初の P を残す」"
            "→ python_exec urllib … eventid=621611300",
        ),
        AMBER,
        False,
    ),
    (
        ("observe", "観察"),
        (
            "QuakeML parsed in code: origin, 1,000+ arrivals (printed whole, output past 32,000 "
            "chars loses its middle)",
            "QuakeML(地震カタログの XML 形式)をコードで解析:震源・1,000 件超の到達時刻。"
            "そのまま表示すると 32,000 字を超えた分の中ほどが消える",
        ),
        OCEAN,
        False,
    ),
    (
        ("tool call", "ツール呼び出し"),
        "file_write  …/kii-channel-2021-12-03/p_picks.csv",
        AMBER,
        True,
    ),
    (
        ("answer", "回答"),
        ("25 stations; farthest JHT ≈ 268 km", "25 観測点、最遠は JHT の約 268 km"),
        INK,
        False,
    ),
]

TRACE_NOTES = (
    "Illustrative trace of the real task. Ctrl+O starts every call expanded (Enter or a click "
    "shows the full output; chat.tsx:347-354); F9 opens the Activity tray on its Agent tab, and 2 "
    "switches to Tools (lib/activity-tabs.ts:9-16). Each round is one model call: it returns "
    "text plus zero or more tool_calls (concurrency-safe ones, such as read-only commands, run "
    "in parallel, up to 8 workers; agent.py:5097-5102). R4 runs them, appends one role:tool "
    "message per call (the 'observe' row), then calls the model again. "
    "python_exec runs `python -c` as your user: 180 s default timeout (max 1,800 s); output over "
    "32,000 chars keeps the first 20,000 and the last 8,000 (tools.py:131-138, 599-616), so the "
    "agent must parse the QuakeML in code, not print it.\n"
    "Scope: the trace view is general (Claude Code Ctrl+O transcript, opencode's tool parts). "
    "Sandbox off by default: approved python_exec / shell_exec calls run with your rights, as in "
    "opencode or LangChain; /sandbox on (ADR-101) confines them with bubblewrap or Seatbelt, "
    "like Claude Code's optional sandbox (Codex sandboxes by default). The taint fence is "
    "R4-specific (ADR-039): results of search_docs, rag/knowledge, web_*, mcp_invoke and lab "
    "tools are registered, and a later call whose arguments copy ≥ 24 characters of them asks in "
    "every mode (r4-contracts/src/r4_contracts/taint.py:28-75). Also registered (fixed "
    "2026-10-30): mcp.<server>.<tool> results, and the output of a python_exec / "
    "shell_exec call that reads the network, plus the files it saved (companion/taint_exec.py).",
    "トレースは実際の課題の例。Ctrl+O ですべての呼び出しを展開して表示(全文は Enter か"
    "クリック。chat.tsx:347-354)。F9 でアクティビティ表示が Agent タブで開き、2 で Tools タブへ"
    "(lib/activity-tabs.ts:9-16)。1 ラウンド = モデル呼び出し 1 回:テキストと 0 個以上の "
    "tool_calls を返す(読み取り専用のコマンドなど並行実行してよい呼び出しは最大 8 並列。"
    "agent.py:5097-5102)。R4 がそれらを実行し、呼び出しごとに role:tool のメッセージ(「観察」の"
    "行)を足して、もう一度モデルを呼ぶ。"
    "python_exec は自分のユーザ権限で `python -c` を実行する。タイムアウトは既定 180 秒(最大 "
    "1,800 秒)。32,000 字を超える出力は先頭 20,000 字と末尾 8,000 字だけが残る"
    "(tools.py:131-138, 599-616)。だから QuakeML はそのまま表示せず、コードで解析させる。\n"
    "位置づけ:トレース表示は一般的な仕組み(Claude Code の Ctrl+O、opencode のツール表示)。"
    "サンドボックスは既定でオフ:承認した python_exec / shell_exec は自分の権限で動く(opencode "
    "や LangChain も同じ)。/sandbox on(ADR-101)で bubblewrap か Seatbelt の中に閉じ込められる"
    "(Claude Code の選択式サンドボックスと同じ。Codex は既定で使う)。"
    "汚染フェンスは R4 独自(ADR-039):search_docs・RAG/ナレッジ・web_*・mcp_invoke・lab "
    "ツールの結果を記録し、そこから 24 字以上を写した引数の呼び出しはどのモードでも確認を求める"
    "(r4-contracts/src/r4_contracts/taint.py:28-75)。mcp.<server>.<tool> の結果と、"
    "ネットワークから取得する python_exec・shell_exec の出力、その呼び出しが保存したファイル"
    "も記録する(companion/taint_exec.py。2026-10-30 に修正)。",
)


def _trace_rows(slide, w) -> None:
    for i, (label, text, color, mono) in enumerate(TRACE_ROWS):
        y = BODY_TOP + Inches(0.4) + i * Inches(0.95)
        chip(slide, MARGIN, y + Inches(0.2), label, fill=color, color=WHITE, w=Inches(1.9))
        rect(slide, MARGIN + Inches(2.05), y, w, Inches(0.8), fill=TINT, rounded=True)
        text_box(
            slide,
            MARGIN + Inches(2.2),
            y + Inches(0.1),
            w - Inches(0.3),
            Inches(0.65),
            text,
            Style(12, INK, mono=mono),
        )


def trace_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Reading the tool trace", "ツールトレース(ツールの呼び出しと結果の記録)の読み方"),
        KICK,
    )
    row_w = Inches(5.85)
    text_box(
        slide,
        MARGIN,
        BODY_TOP,
        Inches(5.0),
        Inches(0.3),
        ("What Ctrl+O shows for the guided task", "ガイド付き課題で Ctrl+O に出るもの"),
        Style(13, MUTED, True),
    )
    _trace_rows(slide, row_w)
    tag(slide, MARGIN + Inches(2.05) + row_w, BODY_TOP + Inches(0.17), "general")
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(4.3),
        Inches(7.9),
        Inches(0.6),
        (
            "One round = one model call; it can ask for several tool calls, which R4 runs before "
            "the next round. Ctrl+O expands each call: its code or command and the result.",
            "1 ラウンド = モデル呼び出し 1 回。1 回で複数のツール呼び出しを頼むこともあり、R4 は"
            "それを全部実行してから次のラウンドへ進む。Ctrl+O で各呼び出しのコード(またはコマンド)"
            "と結果を展開。",
        ),
        Style(12, MUTED),
    )
    x = MARGIN + Inches(8.2)
    w = CONTENT_W - Inches(8.2)
    card(
        slide,
        x,
        BODY_TOP,
        w,
        Inches(2.45),
        ("Questions to ask of every trace", "トレースで確かめること"),
        body_size=13,
        items=[
            (
                "Did it ask the **right** service, for the right event?",
                "**正しい**サービスに、正しい地震で問い合わせたか?",
            ),
            (
                "Did a tool fail and get silently retried?",
                "失敗したツールを黙って再試行していないか?",
            ),
            (
                "Does the answer follow from the file it wrote?",
                "回答は書き出したファイルから導かれているか?",
            ),
        ],
    )
    note_strip(
        slide,
        x,
        BODY_TOP + Inches(2.7),
        w,
        Inches(1.6),
        (
            "R4 marks what the agent downloads with python or curl as web text: a call that "
            "copies from it asks you, for that call only. Whether the data are right is "
            "still yours to check.",
            "python や curl でダウンロードしたデータは Web 由来として記録される。そこから写した"
            "呼び出しは、その呼び出しに限って確認を求める。データが正しいかを確かめるのは"
            "引き続き自分。",
        ),
        size=13,
        fill=TINT_AMBER,
    )
    tag(slide, x + w, BODY_TOP + Inches(2.7), "r4")
    notes(slide, TRACE_NOTES)
    deck.mark("trace")


# ── Lab 1.2 — own task ──────────────────────────────────────────────────────


def _own_task_expect(slide, x, w) -> None:
    rect(slide, x, BODY_TOP, w, Inches(2.4), fill=TINT, rounded=True)
    text_box(
        slide,
        x + Inches(0.25),
        BODY_TOP + Inches(0.2),
        w - Inches(0.5),
        Inches(2.1),
        (
            "Expect the crossover distance (where Pn starts to arrive first) near **130 km** "
            "(real event) — about **105 km** with the synthetic set. The inverse of the Pn slope "
            "is the uppermost-mantle velocity, about 8 km/s.",
            "クロスオーバー距離(Pn が直接波より先に届き始める距離)は**約 130 km**(実際の地震)、"
            "合成データでは**約 105 km**。Pn の傾きの逆数が最上部マントルの速度で、約 8 km/s。",
        ),
        Style(14, INK),
    )
    chip(slide, x, BODY_TOP + Inches(2.7), ("check", "確認"), fill=OCEAN, color=WHITE)
    text_box(
        slide,
        x,
        BODY_TOP + Inches(3.15),
        w,
        Inches(1.5),
        (
            "Plotting needs matplotlib in your Python (pre-work). Read one point off the CSV "
            "yourself and compare it with the plot.",
            "作図には自分の Python に matplotlib が必要(事前準備)。1 点は CSV を自分で読んで図と"
            "照合する。",
        ),
        Style(13, MUTED),
    )


def own_task_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Lab 1.2 — Your task: the travel-time curve", "Lab 1.2 — 自分の課題:走時曲線"),
        KICK,
        ("65–75 min", "65–75 分"),
    )
    prompt_card(
        slide,
        MARGIN,
        BODY_TOP,
        Inches(7.4),
        Inches(1.85),
        ("type this", "入力する"),
        (
            "“Plot travel time against epicentral distance from p_picks.csv, one marker per "
            "phase (Pg, Pb, Pn). From what epicentral distance does Pn arrive before Pg and Pb? "
            "Save travel_times.png.”",
            "「p_picks.csv から震央距離に対する走時を、相(波の種類:Pg・Pb・Pn)ごとに記号を変えて"
            "描いて。Pn が Pg・Pb より先に届き始めるのは震央距離何 km から? travel_times.png に"
            "保存して。」",
        ),
    )
    text_box(
        slide,
        MARGIN + Inches(0.1),
        BODY_TOP + Inches(1.92),
        Inches(7.3),
        Inches(0.35),
        (
            "Pg: direct wave in the upper crust · Pb: along the lower crust · Pn: refracted just "
            "below the Moho",
            "Pg:上部地殻を通る直接波/Pb:下部地殻を通る波/Pn:モホ面直下を伝わる屈折波",
        ),
        Style(12, MUTED),
    )
    card(
        slide,
        MARGIN,
        BODY_TOP + Inches(2.4),
        Inches(7.4),
        Inches(2.3),
        ("Then iterate", "続けて頼む"),
        items=[
            (
                "“Fit a straight line to the Pn picks. What velocity does the slope give?”",
                "「Pn の読み取りに直線を当てはめて。傾きから速度はいくつ?」",
            ),
            (
                "“Use the crossover formula in the knowledge pack's primer (docs/tutorial/data/"
                "knowledge-pack/en/05-domain-primer-seismic-travel-times.md) to estimate the Moho "
                "depth.”",
                "「ナレッジパックの入門資料(docs/tutorial/data/knowledge-pack/ja/"
                "05-domain-primer-seismic-travel-times.md)にあるクロスオーバー距離の式から、"
                "モホ面の深さを見積もって」",
            ),
        ],
    )
    _own_task_expect(slide, MARGIN + Inches(7.8), CONTENT_W - Inches(7.8))
    notes(
        slide,
        (
            "10 min. Real data: Pg/Pb to 128 km, first Pn at 132 km. A head-wave slope near "
            "8 km/s is a good sanity check. No new harness mechanism here: the same loop as "
            "Lab 1.1, now with python_exec and matplotlib. The primer is file 05 of the knowledge "
            "pack; here the agent reads it as a plain file. Lab 1.4 A starts in a fresh session "
            "(/quit, then r4), so the no-knowledge arm runs without it.",
            "10 分。実データ:Pg/Pb は 128 km まで、最初の Pn は 132 km。Pn(ヘッドウェーブ)の"
            "傾きから約 8 km/s が出れば妥当。ここで新しいハーネスの仕組みは出てこない。Lab 1.1 と"
            "同じループを、python_exec と matplotlib で回す。入門資料はナレッジパックのファイル 05 で、"
            "ここではエージェントが普通のファイルとして読む。Lab 1.4 A は新しいセッション(/quit → r4)"
            "で始めるので、知識なしの条件にこの資料は残らない。",
        ),
    )
    deck.mark("lab_1_2")


# ── Lab 1.3 — agentic coding ────────────────────────────────────────────────

CODING_NOTES = (
    "15 min. Measured on the real event's 2 km grid: pure Python 14.6 s, NumPy (one diagonal "
    "plane per array operation) "
    "1.4 s (~10×), Numba 0.24 s (~60×), identical residuals; on the 1 km grid pure Python 117 s "
    "vs Numba 1.5 s (≈75×; tasks/eikonal/README.md:85). Answer keys in "
    "tasks/eikonal/answer_key/. A 7B laptop model may struggle here; the larger site model is "
    "the demo route.\n"
    "Scope: edit → run the tests → repeat until the acceptance check passes is the general "
    "agentic-coding loop (the core use of Claude Code, opencode and Codex; file_edit, "
    "apply_patch and shell_exec in R4). The rule files are a general idea with R4's own "
    "settings (G14, ADR-104): ~/.config/r4/permissions.json and the per-project "
    "~/.config/r4/projects/<id>/permissions.json (what p on a card writes) honour allow / ask "
    "/ deny; a project .r4/permissions.json only ask / deny, so a checked-in file can never "
    "loosen your guardrails (permission_rules.py:1-27, 245-262). A deny or ask rule and a "
    "protected path are checked before any allow rule, so an allow never writes into .git/ "
    "without asking. Tool names may use wildcards (mcp.*). A prefix rule matches only the first "
    "characters of the command: shell_exec(prefix:EIKONAL_IMPL=my_fast pytest) runs the lab's "
    "acceptance command without asking, but a plain prefix:pytest never fires for it, because "
    "that command starts with EIKONAL_IMPL= (permission_rules.py:94-100). Claude Code's "
    "settings.json has the same allow / ask / deny with Bash(npm test:*), but there a project "
    "file may allow; Claude Code also splits && and ; so its prefix rule never approves "
    "'npm test && rm …', while R4's prefix is a plain startswith, so 'pytest -q; rm -rf data' "
    "passes a prefix:pytest rule.",
    "15 分。実際の地震の 2 km 格子で測定:Python のみ 14.6 秒、NumPy(対角平面ごとの一括更新)"
    "1.4 秒(約 10 倍)、Numba 0.24 秒(約 60 倍)、残差は同一。1 km 格子では Python のみ 117 秒、"
    "Numba 1.5 秒(約 75 倍。"
    "tasks/eikonal/README.md:85)。解答例は tasks/eikonal/answer_key/。PC 上の 7B モデルでは"
    "難しい場合があり、デモはサイトの大きなモデルで行う。\n"
    "位置づけ:編集 → テスト実行 → 合格基準を満たすまで繰り返す、は一般的なエージェント"
    "コーディングのループ(Claude Code・opencode・Codex の中心的な使い方。R4 では file_edit・"
    "apply_patch・shell_exec)。規則ファイルは一般的な考え方に R4 独自の設定を加えたもの(G14):"
    "~/.config/r4/permissions.json と、プロジェクトごとの "
    "~/.config/r4/projects/<id>/permissions.json(カードで p と答えると書かれる)は allow / "
    "ask / deny をすべて使える。プロジェクトの .r4/permissions.json は ask と deny だけなので、"
    "リポジトリに置いたファイルで制限を緩めることはできない(permission_rules.py:1-27, "
    "245-262)。deny・ask のルールと保護パスは allow より先に判定するので、allow があっても "
    ".git/ への書き込みは必ず確認する。ツール名には mcp.* のようなワイルドカードも使える。"
    "prefix の規則はコマンドの先頭の文字列"
    "だけで判定する:shell_exec(prefix:EIKONAL_IMPL=my_fast pytest) なら課題の合格判定コマンドが"
    "確認なしで動くが、prefix:pytest ではこのコマンドは EIKONAL_IMPL= で始まるので一致しない"
    "(permission_rules.py:94-100)。Claude Code の settings.json も allow / ask / deny と "
    "Bash(npm test:*) の形を持つが、プロジェクトのファイルでも allow できる。また Claude Code は "
    "&& や ; でコマンドを分けて判定するので「npm test && rm …」は許可されない。R4 の prefix は"
    "単純な前方一致なので、prefix:pytest の規則で「pytest -q; rm -rf data」も通ってしまう。",
)


def _coding_left(slide) -> None:
    w = Inches(6.3)
    prompt_card(
        slide,
        MARGIN,
        BODY_TOP,
        w,
        Inches(1.8),
        ("type this", "入力する"),
        (
            "“eikonal_slow.py is correct but too slow. Write my_fast.py with the same solve(). It "
            "must pass EIKONAL_IMPL=my_fast pytest test_eikonal.py, and bench.py must show "
            "max |ΔT| below 1e-6. Explain why it is faster.”",
            "「eikonal_slow.py は正しいが遅い。同じ solve() を持つ my_fast.py を書いて。"
            "EIKONAL_IMPL=my_fast pytest test_eikonal.py に合格し、かつ bench.py で "
            "max |ΔT| < 1e-6 となること。なぜ速いかも説明して。」",
        ),
        size=14,
    )
    tag(slide, MARGIN + w, BODY_TOP, "general")
    card(
        slide,
        MARGIN,
        BODY_TOP + Inches(1.95),
        w,
        Inches(1.75),
        ("The acceptance check is in the prompt", "合格基準を依頼文に入れる"),
        body_size=13,
        items=[
            (
                "turn on coding mode (read, edit and run files) first: /code on",
                "先にコーディングモード(ファイルの読み書きと実行を有効にする)をオン:/code on",
            ),
            (
                "check it yourself: !EIKONAL_IMPL=my_fast pytest test_eikonal.py, then "
                "!python bench.py --impl my_fast",
                "自分でも確認:!EIKONAL_IMPL=my_fast pytest test_eikonal.py、次に "
                "!python bench.py --impl my_fast",
            ),
        ],
    )
    y = BODY_TOP + Inches(3.85)
    note_strip(
        slide,
        MARGIN,
        y,
        w,
        Inches(1.1),
        (
            'Tired of approving the tests? In ~/.config/r4/permissions.json: "allow": '
            '["shell_exec(prefix:EIKONAL_IMPL=my_fast pytest)"]. It matches only the first '
            "characters of the command. A checked-in .r4/permissions.json can only ask or deny.",
            "テストの承認が面倒なら ~/.config/r4/permissions.json に "
            '"allow": ["shell_exec(prefix:EIKONAL_IMPL=my_fast pytest)"]。コマンドの先頭の文字列'
            "だけで判定する。リポジトリ内の .r4/permissions.json は ask と deny しか効かない。",
        ),
        size=12,
        fill=TINT_AMBER,
    )
    tag(slide, MARGIN + w, y, "r4tuned")


def coding_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "Lab 1.3 — Agentic coding: a faster 3-D solver, same answers",
            "Lab 1.3 — 3 次元ソルバを高速化:答えは同じまま",
        ),
        KICK_CODE,
        ("75–90 min", "75–90 分"),
    )
    _coding_left(slide)
    bar_chart(
        slide,
        MARGIN + Inches(6.6),
        BODY_TOP + Inches(0.55),
        CONTENT_W - Inches(6.6),
        Inches(4.15),
        [tr(("pure Python", "Python のみ")), "NumPy", "Numba"],
        {
            tr(("seconds per solve (1.26 M grid points)", "1 回の計算時間(格子点 126 万)")): [
                14.6,
                1.40,
                0.24,
            ]
        },
        [AMBER],
        number_format='0.00" s"',
        max_value=16,
        title=("Real event, 2 km grid", "実際の地震、2 km 格子"),
    )
    text_box(
        slide,
        MARGIN + Inches(6.6),
        BODY_TOP,
        CONTENT_W - Inches(6.6),
        Inches(0.5),
        (
            "The solver: the eikonal equation |∇T| = 1/v (v: seismic velocity) gives the travel "
            "time T on a 3-D grid.",
            "ソルバ:アイコナール方程式 |∇T| = 1/v(v は地震波の速度)を解き、3 次元格子上の走時 T を"
            "求める。",
        ),
        Style(12, MUTED),
    )
    notes(slide, CODING_NOTES)
    deck.mark("lab_1_3")


def _speedup_grid(slide) -> None:
    x0, y0 = MARGIN + Inches(7.0), BODY_TOP + Inches(0.2)
    cell = Inches(0.48)
    for i in range(8):
        for j in range(8):
            plane = i + j
            fill = OCEAN if plane == 7 else (TINT if plane < 7 else WHITE)
            rect(
                slide,
                x0 + j * cell,
                y0 + i * cell,
                cell - Inches(0.04),
                cell - Inches(0.04),
                fill=fill,
                line=MUTED,
            )
    text_box(
        slide,
        x0 - Inches(0.3),
        y0 + 8 * cell + Inches(0.15),
        8 * cell + Inches(0.6),
        Inches(0.75),
        (
            "2-D view: the dark diagonal is updated in one array operation; tinted cells are "
            "done, white cells come next.",
            "2 次元で見ると:濃い色の対角線を 1 回の配列演算で更新。薄い色のマスは更新済み、白いマスは"
            "これから。",
        ),
        Style(12, MUTED),
        align=PP_ALIGN.CENTER,
    )


def speedup_why_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Why the fast versions give the same answer", "高速版が同じ答えを出す理由"),
        KICK_CODE,
    )
    card(
        slide,
        MARGIN,
        BODY_TOP,
        Inches(6.2),
        Inches(2.2),
        ("Numba: same loops, compiled", "Numba:同じループをコンパイル"),
        body_size=14,
        items=[
            (
                "Numba is a just-in-time (JIT) compiler for Python.",
                "Numba は Python 用の JIT コンパイラ(実行時に機械語へ変換する)。",
            ),
            (
                "Same algorithm, same update order → bit-identical times.",
                "同じアルゴリズム・同じ更新順 → 完全に同じ走時。",
            ),
            (
                "Lists → NumPy arrays, add @njit. ~60× faster on the 2 km grid, ~75× on the 1 km grid.",
                "リストを NumPy 配列に替え、@njit(コンパイル指定)を付ける。2 km 格子で約 60 倍、"
                "1 km 格子で約 75 倍。",
            ),
        ],
    )
    card(
        slide,
        MARGIN,
        BODY_TOP + Inches(2.45),
        Inches(6.2),
        Inches(2.25),
        ("NumPy: a whole diagonal plane at once", "NumPy:対角平面をまとめて更新"),
        fill=TINT_AMBER,
        body_size=14,
        items=[
            (
                "Within one sweep, nodes on i + j + k = const do not depend on each other.",
                "1 回の掃引(sweep:格子を一方向に順に更新すること)では、i + j + k が等しい"
                "格子点どうしは互いに依存しない。",
            ),
            (
                "So each plane is one array operation — that is how it runs on a GPU (Part 3).",
                "そのため各平面を 1 回の配列演算で更新できる。GPU 化もこれと同じ考え方(第 3 部)。",
            ),
        ],
    )
    _speedup_grid(slide)
    notes(
        slide,
        (
            "Detrixhe, Gibou & Min (2013), parallel fast sweeping. The NumPy key is only ~10× on "
            "a CPU because Python still loops over planes; on a GPU thousands of threads update a "
            "plane's nodes at once. Measured: tasks/eikonal/README.md:81-85.",
            "Detrixhe, Gibou & Min (2013) の並列 fast sweeping。CPU では Python が平面ごとにループ"
            "するため NumPy 版は約 10 倍にとどまる。GPU では各平面の格子点を数千のスレッドで同時に更新する。"
            "測定値:tasks/eikonal/README.md:81-85。",
        ),
    )
    deck.mark("speedup")


# ── Lab 1.4 — knowledge: A ask, B build, C compare, D–E rule, skill, MCP ─────

LAB14_STEPS = [
    ("A ask", "A 質問"),
    ("B build RAG", "B RAG 構築"),
    ("C off/on", "C オフ/オン比較"),
    ("D rule + skill", "D 規則+スキル"),
    ("E MCP tool", "E MCP ツール"),
]


def lab14_strip(slide, current: int) -> None:
    """The five Lab 1.4 steps as chips; ``current`` is highlighted."""
    n = len(LAB14_STEPS)
    gap = Inches(0.35)
    w = (CONTENT_W - gap * (n - 1)) / n
    for i, label in enumerate(LAB14_STEPS):
        x = MARGIN + i * (w + gap)
        on = i == current
        chip(
            slide,
            x,
            BODY_TOP,
            label,
            fill=AMBER if on else TINT,
            color=INK if on else OCEAN,
            w=int(w),
            h=Inches(0.45),
            size=12,
        )
        if i < n - 1:
            arrow(
                slide,
                x + w + Inches(0.06),
                BODY_TOP + Inches(0.1),
                gap - Inches(0.12),
                Inches(0.25),
            )


def ask_first_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Lab 1.4 A — Ask first, with no knowledge", "Lab 1.4 A — まず知識なしで質問する"),
        KICK_LAB,
        ("90–105 min", "90–105 分"),
    )
    lab14_strip(slide, 0)
    left_w = CONTENT_W - Inches(4.3)
    top = BODY_TOP + Inches(0.6)
    prompt_card(slide, MARGIN, top, left_w, Inches(1.3), ("the question", "質問"), RAG_Q, size=14)
    code_panel(
        slide,
        MARGIN,
        BODY_TOP + Inches(2.05),
        left_w,
        Inches(2.65),
        (
            "# BEFORE step B, in a fresh session\n"
            "/quit, then r4\n"
            "<paste the question into chat>\n"
            "\n"
            "/cost        # tokens so far in this session",
            "# BEFORE step B, in a fresh session\n"
            "/quit → r4\n"
            "<質問をチャットに貼り付ける>\n"
            "\n"
            "/cost        # tokens so far in this session",
        ),
        size=14,
        title=("console", "コンソール"),
    )
    tag(slide, MARGIN + left_w, BODY_TOP + Inches(2.05), "general")
    x = MARGIN + CONTENT_W - Inches(4.1)
    card(
        slide,
        x,
        top,
        Inches(4.1),
        Inches(2.05),
        ("An invented observatory", "架空の観測所"),
        body_size=13,
        body=(
            "The knowledge pack describes an invented observatory: its reference model, pick "
            "rules, report format. **No model was trained on it** — so you see exactly what RAG "
            "adds.",
            "ナレッジパックは架空の観測所の資料一式(参照モデル・読み取り規則・レポート書式)。"
            "**どのモデルの学習データにも含まれない**ので、RAG の効果がはっきり見える。",
        ),
    )
    card(
        slide,
        x,
        top + Inches(2.2),
        Inches(4.1),
        Inches(1.9),
        ("Expect", "予想"),
        body_size=13,
        body=(
            "A confident guess (ak135, PREM, “about 30–35 km”). Write down: correct? "
            "tool calls (Ctrl+O)? tokens (/cost)?",
            "自信ありげな推測(全球の標準モデル ak135・PREM、「30〜35 km 程度」など)。記録すること:正解か/"
            "ツール呼び出し数(Ctrl+O)/トークン数(/cost)",
        ),
    )
    notes(
        slide,
        (
            "2 min. Start the ingestion (step B) right after this question: on a CPU laptop the "
            "knowledge-graph extraction takes minutes. Do A BEFORE ingesting so the off arm is "
            "clean: with "
            "nothing ingested the agent's own search finds nothing. Start a fresh session (/quit, then "
            "r4) as the On arm does: /cost totals the whole session, and the history of Labs "
            "1.1–1.3 is re-sent with every request, so an old session inflates the count. Count "
            "tool calls with Ctrl+O; the turn count is +1 per question in every arm and tells "
            "nothing. Correct answer (only in the "
            "pack): KRSO-1D v2 (rule RM-1), Moho 28 km, mantle 7.9 km/s, reject |residual| > 2.5 s "
            "(rule PQ-3).",
            "2 分。この質問の直後に取り込み(手順 B)を開始する(CPU の PC ではナレッジグラフの抽出に数分"
            "かかる)。この質問(手順 A)は必ず取り込み(手順 B)より前に行い、RAG なしの条件を確実にする。"
            "何も取り込んで"
            "いなければ、エージェント自身の検索も何も見つけない。オンの条件と同じく新しいセッションで"
            "始める(/quit → r4)。/cost はセッション全体の合計で、Lab 1.1〜1.3 の履歴は毎回の"
            "リクエストで送り直されるため、古いセッションのままだと数字が膨らむ。ツール呼び出しは "
            "Ctrl+O で数える。ターン数はどの条件でも質問 1 つにつき 1 増えるだけで、差が出ない。"
            "正解(パックにのみ記載):"
            "KRSO-1D v2(規則 RM-1)、モホ面 28 km、マントル 7.9 km/s、|残差| > 2.5 秒で棄却"
            "(規則 PQ-3)。",
        ),
    )
    deck.mark("lab_1_4_a")


BUILD_CODE = (
    "# ask the agent (it ingests a whole folder):\n"
    "Ingest the folder docs/tutorial/data/knowledge-pack/en\n"
    "into this project's knowledge.\n"
    "\n"
    "/rag docs             # wait until the pack is listed",
    "# ask the agent (it ingests a whole folder):\n"
    "docs/tutorial/data/knowledge-pack/ja フォルダを、\n"
    "このプロジェクトのナレッジに取り込んで。\n"
    "\n"
    "/rag docs             # wait until the pack is listed",
)

BUILD_NOTES = (
    "Start this ingestion first; the knowledge-graph extraction takes minutes on CPU, and on "
    "this 3B stack retrieval does not use the graph yet (UMRS_RAG_GRAPH_LANE=auto stays off; "
    "deploy/standalone/env/base.env:85-89). Wait until /rag docs lists the pack before step C. "
    "Alternative: /rag upload <file> "
    "once per file (/rag upload does not take a folder). Japanese attendees use knowledge-pack/ja; "
    "the English-only default embedder ranks it less sharply, so if retrieval misses, fall back "
    "to the EN pack.\n"
    "What runs: the agent calls knowledge_ingest; file contents are uploaded to UMRS and a "
    "durable ingest job is queued there (knowledge_tools.py:139-165, 229-280). Knowledge writes "
    "are open by default (/acquisition off or extend <graph> restricts them; "
    "knowledge_write_gate.py:37); each knowledge_ingest call still asks for approval. "
    "The job chunks, embeds, stores and extracts the graph on UMRS (here the stand-alone "
    "UMRS on your laptop), not inside the console.\n"
    "Scope: chunk → embed → vector store is the general RAG pipeline (LangChain vector stores, "
    "OpenAI file_search); 256-token chunks, e5-base-v2 and pgvector are R4/UMRS's settings. "
    "Knowledge kept per project on UMRS, filled by a server job, is R4-specific: Claude Code and "
    "opencode have no built-in document index; OpenAI vector stores are hosted per account.",
    "この取り込みは最初に開始する。CPU ではナレッジグラフの抽出に数分かかるが、この 3B の構成の"
    "検索ではまだグラフを使わない(UMRS_RAG_GRAPH_LANE=auto がオフのまま。"
    "deploy/standalone/env/base.env:85-89)。手順 C の前に、/rag docs にパックが並ぶまで待つ。別法:/rag upload <file> を 1 ファイルずつ"
    "(フォルダは指定できない)。日本語の参加者は knowledge-pack/ja を使う。既定の英語用埋め込み"
    "モデルでは順位付けが甘いので、検索が外れたら英語パックを使う。\n"
    "裏で起きること:エージェントが knowledge_ingest を呼び、ファイルの中身を UMRS に送り、UMRS 上に"
    "取り込みジョブを登録する(knowledge_tools.py:139-165, 229-280)。ナレッジへの書き込みは"
    "既定で許可されている(/acquisition off や extend <graph> で制限できる)。knowledge_ingest の"
    "呼び出しごとに承認は求められる。分割・埋め込み・保存・グラフ抽出は UMRS(ここでは PC 上"
    "の単体版 UMRS)で動き、コンソールの中では動かない。\n"
    "位置づけ:分割 → 埋め込み → ベクトルストアは一般的な RAG の流れ(LangChain のベクトルストア、"
    "OpenAI の file_search)。256 トークンのチャンク・e5-base-v2・pgvector は R4/UMRS の設定。"
    "プロジェクトごとのナレッジを UMRS に置き、サーバのジョブで作るのは R4 独自(Claude Code と "
    "opencode には文書索引の機能がなく、OpenAI のベクトルストアはアカウント単位のクラウド保存)。",
)


def _build_rag_right(slide) -> None:
    x = MARGIN + Inches(8.0)
    w = CONTENT_W - Inches(8.0)
    note_strip(
        slide,
        x,
        BODY_TOP,
        w,
        Inches(1.8),
        (
            "Same pipeline as the “RAG end to end” slide (256-token chunks → e5-base-v2 → "
            "pgvector), now on your laptop. The ingest job also has the local model extract a "
            "knowledge graph: minutes on CPU, and on this small stack retrieval does not use it yet.",
            "「RAG の全体像」と同じ流れ(256 トークンに分割 → e5-base-v2 で埋め込み → pgvector)を"
            "PC 上で動かす。取り込みジョブはローカルのチャットモデルでナレッジグラフ(概念とその関係)"
            "も抽出する。CPU では数分かかるが、この小さな構成の検索ではまだ使わない。",
        ),
        size=13,
    )
    tag(slide, x + w, BODY_TOP, "r4tuned")
    y = BODY_TOP + Inches(1.95)
    note_strip(
        slide,
        x,
        y,
        w,
        Inches(1.45),
        (
            "The agent calls knowledge_ingest: the files go to UMRS, whose ingest job adds them "
            "to this project's knowledge. search_docs and /rag read it from there.",
            "エージェントが knowledge_ingest を呼ぶと、ファイルは UMRS に送られ、UMRS 上の取り込み"
            "ジョブがその中身をこのプロジェクトのナレッジに加える。search_docs と /rag はそこから読む。",
        ),
        size=13,
    )
    tag(slide, x + w, y, "r4")


def build_rag_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "Lab 1.4 B — Build the RAG from the knowledge pack",
            "Lab 1.4 B — ナレッジパックから RAG を作る",
        ),
        KICK_LAB,
    )
    code_panel(
        slide,
        MARGIN,
        BODY_TOP,
        Inches(7.6),
        Inches(3.4),
        BUILD_CODE,
        size=14,
        title=("console", "コンソール"),
    )
    _build_rag_right(slide)
    note_strip(
        slide,
        MARGIN,
        BODY_TOP + Inches(3.7),
        CONTENT_W,
        Inches(0.95),
        (
            "Nothing in this step leaves your laptop: the embedder and the model run in the local stack.",
            "この手順で PC の外に出るものはない:埋め込みモデルもチャットモデルもローカルで動く。",
        ),
        size=15,
        fill=TINT_AMBER,
    )
    notes(slide, BUILD_NOTES)
    deck.mark("lab_1_4_b")


EFFECT_ROWS = [
    [
        ("Arm", "条件"),
        ("In the console", "コンソールで"),
        ("Correct?", "正解?"),
        ("Tool calls", "ツール呼び出し"),
        ("Tokens", "トークン"),
        ("Cites a source?", "出典あり?"),
    ],
    [
        ("Off — agent", "オフ:エージェント"),
        ("before B: /quit, r4, chat; /cost", "B の前に /quit → r4 → チャット、/cost"),
        "",
        "",
        "",
        "",
    ],
    [("On — RAG answer", "オン:RAG の回答"), ("/rag <question>", "/rag <質問>"), "", "—", "—", ""],
    [
        ("On — agent", "オン:エージェント"),
        (
            "/quit, R4_RAG_ROUTER=on r4, chat; /cost",
            "/quit → R4_RAG_ROUTER=on r4 → チャット、/cost",
        ),
        "",
        "",
        "",
        "",
    ],
]

EFFECT_NOTES = (
    "Fill in the table live; the class compares on the board. R4_RAG_ROUTER=on: before round 1 "
    "of most turns the harness asks UMRS for evidence (GET /gw/rag/retrieve) and puts the context "
    "pack, labelled [S1] [S2] …, into your turn. It skips slash commands, short acknowledgements, "
    "messages with code, paths or tracebacks, and sessions without a project "
    "(retrieval_router.py:1-30). Off (the default): the agent decides whether to call "
    "search_docs, as with OpenAI file_search or a LangChain retriever tool. There is no switch "
    "inside the console, so the last arm relaunches r4. Relaunch with R4_RAG_ROUTER=on only "
    "after /rag docs lists the pack: a question asked against an empty project makes the router "
    "skip that project for 10 minutes (EMPTY_PROJECT_TTL_SEC = 600, retrieval_router.py:138, "
    "528-530). /rag <question> is answered by UMRS (rag.query → POST rag/query, "
    "bridge_server.py:3325-3339); no agent turn runs and /cost does not count it, so its "
    "Tool-calls and Tokens cells stay empty.\n"
    "Scope: model-decided search is the agentic pattern (OpenAI file_search, a LangChain "
    "retriever tool); always retrieving before the model answers is the classic RAG pattern "
    "(LangChain 2-step RAG chains). R4's own part is the deterministic per-turn eligibility "
    "rules, size-profiled packs and the server's sufficiency grade (retrieval_router.py:1-75), "
    "added for small local models that rarely pick a search tool by themselves.",
    "表は実演しながら埋め、全員の結果を板書で比べる。R4_RAG_ROUTER=on:ほとんどのターンで、"
    "1 ラウンド目の前にハーネスが UMRS に根拠を問い合わせ(GET /gw/rag/retrieve)、[S1] [S2] … の"
    "ラベル付きの抜粋をユーザの依頼に添える。スラッシュコマンド・短い相づち・コードやパスや"
    "トレースバックを含む依頼・プロジェクトのないセッションでは行わない(retrieval_router.py:1-30)。"
    "オフ(既定):search_docs を呼ぶかはエージェントが決める(OpenAI の file_search や LangChain の"
    "検索ツールと同じ)。コンソール内に切り替えはないので、最後の条件では r4 を起動し直す。"
    "R4_RAG_ROUTER=on での再起動は、/rag docs にパックが並んでから行う。空のプロジェクトに"
    "質問すると、ルータはそのプロジェクトを 10 分間検索しない(EMPTY_PROJECT_TTL_SEC = 600、"
    "retrieval_router.py:138, 528-530)。/rag <質問> には UMRS が答える(rag.query → "
    "POST rag/query、bridge_server.py:3325-3339)。エージェントのターンは動かず /cost にも"
    "数えられないので、この行のツール呼び出しとトークンの欄は空のままにする。\n"
    "位置づけ:検索するかをモデルが決めるのはエージェント型の一般的な仕組み(OpenAI の "
    "file_search、LangChain の検索ツール)。回答の前に必ず検索するのは従来型の RAG"
    "(LangChain の 2 段階 RAG チェーン)。R4 独自なのは、ターンごとに検索するかを決める固定の"
    "規則、モデルの大きさに合わせた抜粋の量、サーバによる根拠の十分さの判定"
    "(retrieval_router.py:1-75)で、自分から検索ツールを選びにくい小さなローカルモデルのために"
    "加えた。",
)


def _effect_mechanism(slide, y) -> None:
    half = (CONTENT_W - Inches(0.3)) / 2
    lanes = [
        (
            "general",
            (
                "No router (the default): the agent decides whether to call search_docs (agentic "
                "RAG).",
                "ルータなし(既定):search_docs を呼ぶかはエージェントが決める(エージェント型の "
                "RAG)。",
            ),
        ),
        (
            "r4tuned",
            (
                "Router on (R4_RAG_ROUTER=on): before round 1 the harness retrieves and adds "
                "[S1] [S2] … passages to your turn (classic retrieve-then-answer RAG). R4's rules "
                "skip slash commands, code and short replies.",
                "ルータあり(R4_RAG_ROUTER=on):1 ラウンド目の前にハーネスが検索し、[S1] [S2] … と"
                "番号を付けた抜粋を依頼に添える(従来型の RAG)。スラッシュコマンド・コード・短い"
                "返事では検索しないのが R4 の規則。",
            ),
        ),
    ]
    for i, (kind, text) in enumerate(lanes):
        x = MARGIN + i * (half + Inches(0.3))
        note_strip(slide, x, y, int(half), Inches(1.2), text, size=13)
        tag(slide, x + half, y, kind)


def effect_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Lab 1.4 C — Effect check: RAG off vs on", "Lab 1.4 C — 効果の確認:RAG オフ/オン"),
        KICK_LAB,
    )
    table(
        slide,
        MARGIN,
        BODY_TOP,
        CONTENT_W,
        EFFECT_ROWS,
        [2.3, 4.0, 1.1, 1.6, 1.1, 1.5],
        size=14,
        row_h=Inches(0.55),
    )
    _effect_mechanism(slide, BODY_TOP + Inches(2.45))
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(3.75),
        CONTENT_W,
        Inches(0.35),
        (
            "Fill in your own numbers — the class compares them on the board.",
            "自分の数値を記入する。全員の結果を板書で比べる。",
        ),
        Style(14, MUTED, italic=True),
    )
    note_strip(
        slide,
        MARGIN,
        BODY_TOP + Inches(4.15),
        CONTENT_W,
        Inches(0.7),
        (
            "Expected: **KRSO-1D v2, Moho 28 km, 7.9 km/s; reject |residual| > 2.5 s** — evidence: "
            "knowledge-pack files 01 and 02.",
            "想定解:**KRSO-1D v2、モホ面 28 km、7.9 km/s、|残差| > 2.5 秒で棄却**(根拠:"
            "ナレッジパックのファイル 01 と 02)",
        ),
        size=15,
    )
    notes(slide, EFFECT_NOTES)
    deck.mark("lab_1_4_c")


# ── Lab 1.4 D–E — instruction file, skill, MCP tool ─────────────────────────

SKILL_CODE = (
    "!mkdir -p .r4/skills && cp -r \\\n"
    "  docs/tutorial/data/skills/residual-report .r4/skills/\n"
    "/quit      # then start r4 again\n"
    "/skills    # listed, off until you write “residual”"
)

MCP_CODE = (
    "/term     # a shell pane inside the console, then:\n"
    'export HATCH_REGISTRY_URL="$PWD/mcp-servers/hatch-registry"\n'
    "export R4_HATCH_BIND_HOST=0.0.0.0\n"
    "export R4_HATCH_ADVERTISE_HOST=host.containers.internal\n"
    "r4 mcp hatch install platform-tools\n"
    "r4 mcp hatch connect platform-tools\n"
    "\n"
    "/mcp tools   # back in chat: what the agent can call"
)

WHERE_R4_LOOKS = (
    "R4 re-reads R4.md and the other instruction files every turn; skills are read only at "
    "start, hence the restart. The repository's own R4.md, AGENTS.md and CLAUDE.md already use "
    "6.7k of the 8k-character budget: check that the agent adds your rule to R4.md rather than "
    "replacing it.",
    "指示ファイル(エージェントへの常設の指示を書いたファイル。R4.md など)は毎ターン読み直す。"
    "スキルは起動時にしか読まないので再起動する。このリポジトリには R4.md・AGENTS.md・CLAUDE.md "
    "がすでにあり、3 つで計 6,700 字(上限 8,000 字の大半)を使っている。エージェントが R4.md を"
    "置き換えず、規則を追記したかを確かめる。",
)

INSTRUCTIONS_NOTES = (
    "Stretch, facilitator-paced. D: instruction files load in chain order, one per name (the "
    "nearest), each capped at 4,000 chars, 8,000 in total, into the system message as "
    "<repo_briefing>; _system_message re-reads them on every request, so an R4.md edit applies "
    "on the next turn (briefing.py:95-160, agent.py:4622). Skills are scanned once when the "
    "session starts (session.py:355), hence /quit and restart. Instruction files: every R4.md, "
    "AGENTS.md, agent.md, AGENT.md, CLAUDE.md, OPENCODE.md found walking up from the folder. "
    "Skill folders: ./.r4/skills, ./skills, ~/.config/r4/companion/skills, ~/.config/r4/skills "
    "(skills.py:74-76, 484-498). The "
    "repository's own R4.md, AGENTS.md and CLAUDE.md (6.7k chars) are already in every prompt "
    "when r4 runs from the repo folder. The skill activates when a keyword from x-r4-keywords "
    "appears as a whole word in your message (skills.py:159-177); Japanese keywords (残差, 走時, "
    "モホ) match too, so the Japanese prompt works. Or the model calls activate_skill and the body "
    "applies from the next turn. The real data contain a 'Pn' at 132 km (station JKS), which "
    "violates the invented rule PQ-5 (Pn only beyond 140 km): a good check that the skill was "
    "followed.\n"
    "E: Hatch = R4's mechanism that packages and starts MCP servers. The install has no slash "
    "command (/mcp register only points to the CLI), so it runs in the /term pane. UMRS registers "
    "the server with a per-runtime access token; the agent sees its tools as "
    "mcp.<server>.<tool>. Recipe verified 2026-10-09 (deploy/standalone/README.md). A session "
    "loads its MCP tools at start (session.py:266), hence the restart before the agent call; "
    "list_models is a read-only tool of platform-tools. Fixed 2026-10-30: the "
    "handler read result.is_error / .content, fields MCPToolResult lacks, so every agent call "
    "failed; it now reads success / result / error (mcp_first_class.py:84-95).\n"
    "Scope: instruction files are general (Claude Code reads CLAUDE.md; opencode and Codex read "
    "AGENTS.md); the six-name chain and the 4k/8k caps are R4's. Skills (a catalog line, the body "
    "on demand) are general, as in Claude Code Agent Skills; activation by keyword scan is R4's "
    "own. MCP is the shared standard: Claude Code, the OpenAI Agents SDK and opencode support "
    "it, and LangChain has adapters; R4 keeps the server registry on UMRS instead of a local config file.",
    "発展課題。講師のペースで進める。D:指示ファイルは決まった順に、同名ごとに最も近い 1 つを読み、"
    "各 4,000 字、合計 8,000 字までを <repo_briefing> としてシステムメッセージに入れる。"
    "_system_message がモデルへのリクエストのたびに読み直すので、R4.md の変更は次のターンから効く"
    "(briefing.py:95-160、agent.py:4622)。スキルはセッション開始時に 1 回だけ読む"
    "(session.py:355)。だから /quit して再起動する。指示ファイルは R4.md・AGENTS.md・agent.md・"
    "AGENT.md・CLAUDE.md・OPENCODE.md を現在のフォルダから上へ探す。スキルの場所は ./.r4/skills、"
    "./skills、~/.config/r4/companion/skills、~/.config/r4/skills(skills.py:74-76, 484-498)。"
    "リポジトリのフォルダ"
    "で r4 を動かすと、リポジトリ自身の R4.md・AGENTS.md・CLAUDE.md(6,700 字)がすでに毎回の"
    "プロンプトに入っている。スキルは、x-r4-keywords のキーワードが依頼文に単語として現れると"
    "有効になる(skills.py:159-177)。日本語のキーワード(残差・走時・モホ)も一致するので、日本語の"
    "依頼でも動く。モデルが activate_skill を呼んだ場合は、次のターンから本文が効く。実データには "
    "震央距離 132 km の「Pn」(観測点 JKS)があり、架空の規則 PQ-5(Pn と読めるのは 140 km より"
    "遠い場合のみ)に違反する。スキルに従ったかを確かめるのにちょうどよい。\n"
    "E：Hatch(MCP サーバを配布・起動する R4 の仕組み)でサーバを導入するスラッシュコマンドはない"
    "(/mcp register は CLI の使い方を案内するだけ)ので、/term ペインで r4 mcp hatch install を"
    "実行する。UMRS が専用のアクセストークン"
    "付きでサーバを登録し、エージェントにはツールが mcp.<server>.<tool> と見える。手順は "
    "2026-10-09 に確認済み(deploy/standalone/README.md)。セッションは開始時に MCP ツールを"
    "読み込む(session.py:266)ので、エージェントに呼ばせる前に再起動する。list_models は "
    "platform-tools の読み取り専用のツール。2026-10-30 に修正:"
    "以前は MCPToolResult にない result.is_error / .content を読んでいたため、エージェントからの"
    "呼び出しはすべて失敗していた。今は success / result / error を読む"
    "(mcp_first_class.py:84-95)。\n"
    "位置づけ:指示ファイルは一般的な仕組み(Claude Code は CLAUDE.md、opencode と Codex は "
    "AGENTS.md を読む)。6 つの名前の順序と 4,000/8,000 字の上限は R4 の設定。スキル(一覧の 1 行"
    "だけを常に見せ、本文は必要なときに読む)は Claude Code の Agent Skills と同じ一般的な仕組みで、"
    "キーワードでの自動有効化は R4 独自。MCP は共通の標準で、Claude Code・OpenAI Agents SDK・"
    "opencode が対応し、LangChain にもアダプタがある。R4 はサーバの登録簿を手元の設定ファイルではなく UMRS に置く。",
)


def _instructions_left(slide) -> None:
    w = Inches(5.9)
    prompt_card(
        slide,
        MARGIN,
        BODY_TOP,
        w,
        Inches(1.5),
        ("D · type this", "D · 入力する"),
        (
            "“Add one rule at the end of R4.md in the repository folder (create it if missing): "
            "always name the reference model and its version, and the pick source with its "
            "citation.”",
            "「リポジトリのフォルダにある R4.md の末尾に、規則を 1 つ書き足して(なければ作って):"
            "『レポートには参照モデルの名前とバージョン、読み取りデータの出典(引用文献)を必ず書く"
            "こと』」",
        ),
        size=13,
    )
    tag(slide, MARGIN + w, BODY_TOP, "r4tuned")
    y = BODY_TOP + Inches(1.72)
    code_panel(
        slide, MARGIN, y, w, Inches(1.4), SKILL_CODE, size=11, title=("console", "コンソール")
    )
    tag(slide, MARGIN + w, y, "general")
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(3.18),
        w,
        Inches(1.3),
        WHERE_R4_LOOKS,
        Style(12, MUTED),
    )


def _instructions_right(slide) -> None:
    x = MARGIN + Inches(6.2)
    w = CONTENT_W - Inches(6.2)
    code_panel(
        slide,
        x,
        BODY_TOP,
        w,
        Inches(2.1),
        MCP_CODE,
        size=11,
        title=("E · console", "E · コンソール"),
    )
    tag(slide, x + w, BODY_TOP, "r4tuned")
    y = BODY_TOP + Inches(2.25)
    card(
        slide,
        x,
        y,
        w,
        Inches(2.25),
        ("Try it", "試す"),
        body_size=12,
        items=[
            (
                "“Write the residual report for the picks in docs/tutorial/tasks/eikonal/data/"
                "kii-channel-2021-12-03.” “residual” is a keyword of the skill, so R4 turns it on "
                "from your message; Claude Code leaves the choice to the model.",
                "「docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03 の読み取りで残差レポートを"
                "書いて」。「残差」という語でスキルが有効になる(R4 は依頼文からキーワードを探す。"
                "Claude Code ではモデルが選ぶ)。",
            ),
            (
                "Does it flag station JKS, a “Pn” at 132 km, under rule PQ-5 (Pn only beyond "
                "140 km)?",
                "震央距離 132 km の「Pn」(観測点 JKS)を、規則 PQ-5(Pn は 140 km より遠い場合のみ)"
                "の違反として指摘したか?",
            ),
        ],
    )
    tag(slide, x + w, y, "r4")


def instructions_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "Lab 1.4 D–E (stretch) — a rule, a skill, one MCP tool",
            "Lab 1.4 D–E(発展)— 指示ファイル・スキル・MCP",
        ),
        KICK_LAB,
    )
    _instructions_left(slide)
    _instructions_right(slide)
    note_strip(
        slide,
        MARGIN,
        BODY_TOP + Inches(4.6),
        CONTENT_W,
        Inches(0.45),
        (
            "E, then: /quit and r4 (MCP tools load at session start), and ask “Which models are "
            "available on the platform? Use the platform-tools MCP server.”",
            "E のあと:/quit と r4 で再起動し、「プラットフォームで使えるモデルを platform-tools "
            "の MCP サーバで調べて」と依頼する。",
        ),
        size=12,
        fill=TINT,
    )
    notes(slide, INSTRUCTIONS_NOTES)
    deck.mark("lab_1_4_de")


# ── Lab 1.5, good practice, quiz ────────────────────────────────────────────


def _compare_cards(slide) -> None:
    x = MARGIN + Inches(7.4)
    w = CONTENT_W - Inches(7.4)
    card(
        slide,
        x,
        BODY_TOP,
        w,
        Inches(2.6),
        ("Validate first", "まず検証"),
        body_size=14,
        items=[
            (
                "Our ak135 residuals track the ISC's own, station by station.",
                "ak135 の残差は、ISC が公表する残差と観測点ごとにほぼ同じ傾向を示す。",
            ),
            (
                "Our residuals differ from the ISC's by −0.36 s at 2 km and −0.18 s at 1 km: the "
                "gap halves with the grid spacing.",
                "ISC の残差との差は 2 km 格子で −0.36 秒、1 km で −0.18 秒。格子間隔を半分にすると"
                "差も半分になる。",
            ),
        ],
    )
    card(
        slide,
        x,
        BODY_TOP + Inches(2.85),
        w,
        Inches(1.85),
        ("Then interpret", "それから解釈"),
        fill=TINT_AMBER,
        body_size=14,
        items=[
            (
                "ak135 is ~1 s too slow here: a thinner crust.",
                "ak135 の予測はこの地域で約 1 秒遅い。実際の地殻が ak135 より薄いためである。",
            ),
            (
                "The model your RAG retrieved (KRSO-1D v2) fits the real event.",
                "RAG で得たモデル(KRSO-1D v2)は、実際の地震によく合う。",
            ),
        ],
    )


def compare_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Lab 1.5 — Your solver meets the real earthquake", "Lab 1.5 — 自分のソルバと実際の地震"),
        KICK,
        ("105–112 min", "105–112 分"),
    )
    code_panel(
        slide,
        MARGIN,
        BODY_TOP,
        Inches(7.0),
        Inches(2.3),
        "# one line each\n"
        "/code run docs/tutorial/tasks/eikonal/japan_event.py\n"
        "   -- --impl my_fast --model ak135 --data\n"
        "   docs/tutorial/tasks/eikonal/data/kii-channel-2021-12-03\n"
        "# then --model krso, and ask: “Explain residuals.csv”",
        size=12,
        title=("console", "コンソール"),
    )
    rows = [
        [("Model", "モデル"), ("Mean residual", "平均残差"), ("RMS", "RMS")],
        [("ak135 (global)", "ak135(全球)"), "−1.12 s", "1.32 s"],
        [("KRSO-1D v2 (from your RAG)", "KRSO-1D v2(RAG で得たモデル)"), "+0.04 s", "0.46 s"],
    ]
    table(
        slide,
        MARGIN,
        BODY_TOP + Inches(2.6),
        Inches(7.0),
        rows,
        [4.0, 1.6, 1.4],
        size=14,
        row_h=Inches(0.6),
    )
    text_box(
        slide,
        MARGIN,
        BODY_TOP + Inches(4.45),
        Inches(7.0),
        Inches(0.3),
        (
            "RMS = root mean square of the residuals (their typical size).",
            "RMS = 残差の二乗平均平方根(残差の典型的な大きさ)。",
        ),
        Style(12, MUTED),
    )
    _compare_cards(slide)
    notes(
        slide,
        (
            "/code run runs from the repository root with a 60 s default timeout (--timeout N before --). "
            "Measured on the real ISC picks (2 km grid): ak135 −1.12 / 1.32 s; KRSO-1D v2 +0.04 / 0.46 s. "
            "Difference to ISC's ak135 residuals −0.36 s at 2 km, −0.18 s at 1 km. " + CITE[0],
            "/code run はリポジトリのルートから実行し、既定のタイムアウトは 60 秒(-- の前に --timeout N)。"
            "実際の ISC 読み取りで測定(2 km 格子):ak135 −1.12 / 1.32 秒、KRSO-1D v2 +0.04 / 0.46 秒。"
            "ISC の ak135 残差との差は 2 km で −0.36 秒、1 km で −0.18 秒。" + CITE[1],
        ),
    )
    deck.mark("lab_1_5")


PROMPT_PARTS = [
    (
        ("Data", "データ"),
        ("p_picks.csv, ISC event 621611300", "p_picks.csv、ISC イベント 621611300"),
    ),
    (("Goal", "目標"), ("residuals against KRSO-1D v2", "KRSO-1D v2 に対する残差を求める")),
    (
        ("Constraints", "制約"),
        (
            "first P only, drop |residual| > 2.5 s (PQ-3), keep the ISC hypocentre",
            "各観測点の最初の P 波のみ・|残差| > 2.5 秒は除く(規則 PQ-3)・震源は ISC の値に固定",
        ),
    ),
    (
        ("Acceptance check", "合格基準"),
        (
            "one station recomputed by hand matches",
            "1 観測点を手で計算し直し、エージェントの値と一致すること",
        ),
    ),
]

REPRODUCE_CODE = (
    "# in the console\n"
    "/export kii-session.md  # transcript as markdown\n"
    "/retry           # re-send the last prompt\n"
    "/rewind          # back to an earlier turn"
)


def _prompt_parts(slide) -> None:
    w = (CONTENT_W - Inches(0.3) * 3) / 4
    for i, (name, example) in enumerate(PROMPT_PARTS):
        x = MARGIN + i * (w + Inches(0.3))
        rect(slide, x, BODY_TOP, w, Inches(2.0), fill=TINT_AMBER if i == 3 else TINT, rounded=True)
        badge(
            slide, x + Inches(0.2), BODY_TOP + Inches(0.18), str(i + 1), size=Inches(0.42), font=14
        )
        text_box(
            slide,
            x + Inches(0.75),
            BODY_TOP + Inches(0.2),
            w - Inches(0.95),
            Inches(0.45),
            name,
            Style(17, INK, True),
        )
        text_box(
            slide,
            x + Inches(0.2),
            BODY_TOP + Inches(0.8),
            w - Inches(0.4),
            Inches(1.1),
            example,
            Style(13, MUTED),
        )


def prompting_slide(deck: Deck) -> None:
    slide = content(
        deck,
        (
            "Good practice: ask in four parts, verify, keep the session",
            "良い使い方:4 要素で頼み、確かめ、セッションを残す",
        ),
        KICK,
        ("112–116 min", "112–116 分"),
    )
    _prompt_parts(slide)
    top = BODY_TOP + Inches(2.25)
    card(
        slide,
        MARGIN,
        top,
        Inches(6.4),
        Inches(2.0),
        ("Verify the output", "出力を確かめる"),
        body_size=14,
        items=[
            (
                "RAG answers: open the cited source and find the sentence.",
                "RAG の回答:引用元を開き、該当の文を見つける。",
            ),
            (
                "Numbers: recompute one by hand or with a second tool.",
                "数値:1 つは手計算か別の方法で再計算する。",
            ),
        ],
    )
    x = MARGIN + Inches(6.7)
    w = CONTENT_W - Inches(6.7)
    code_panel(
        slide, x, top, w, Inches(1.75), REPRODUCE_CODE, size=12, title=("console", "コンソール")
    )
    tag(slide, x + w, top, "general")
    text_box(
        slide,
        x,
        top + Inches(1.85),
        w,
        Inches(0.5),
        (
            "Answers can vary between runs — so keep the acceptance check with the session.",
            "回答は実行のたびに変わりうる。だから合格基準もセッションと一緒に残しておく。",
        ),
        Style(13, MUTED, italic=True),
    )
    notes(
        slide,
        (
            "4 min. The acceptance check turns 'looks right' into 'is right for this case'. "
            "Same data + same prompt + same model → you can rerun it; the export records what "
            "the agent did, step by step. /export is the R4 core (companion) export, forwarded "
            "by the console. Scope: export, retry and rewind are general session commands "
            "(Claude Code has /export and /rewind; opencode has /share and /undo).",
            "4 分。合格基準があれば、「正しそうに見える」が「この条件では正しい」と言えるようになる。"
            "同じデータ・同じ依頼文・"
            "同じモデルなら再実行できる。エクスポートにはエージェントの行動が順に記録される。"
            "/export は R4 本体(companion)のエクスポート機能をコンソールから呼ぶ。位置づけ:"
            "書き出し・再送・巻き戻しは一般的なセッション操作(Claude Code には /export と /rewind、"
            "opencode には /share と /undo がある)。",
        ),
    )
    deck.mark("good_practice")


QUIZ = [
    (
        (
            "Would a bigger model know the Moho depth of the observatory's reference model?",
            "より大きなモデルなら、観測所の参照モデル(KRSO-1D v2)のモホ面の深さを知っているか?",
        ),
        (
            "No — that knowledge is in your documents, which the harness puts in front of the "
            "model through RAG.",
            "いいえ。その知識はモデルではなく自分の文書にあり、ハーネスが RAG でモデルに渡す。",
        ),
    ),
    (
        (
            "The agent's fast solver passes all tests. Is it right?",
            "エージェントが書いた高速なソルバが全テストに合格した。正しいと言えるか?",
        ),
        (
            "Only as right as the tests: also check the benchmark's max |ΔT| and one real "
            "earthquake.",
            "テストが確かめた範囲でしか正しいと言えない。ベンチマークの max |ΔT| と、実際の地震 1 件"
            "でも確かめる。",
        ),
    ),
    (
        (
            "Observed P arrives 1 s early against ak135. What do you conclude?",
            "観測した P 波の到達が、ak135 の予測より 1 秒早い。何が言える?",
        ),
        (
            "The model is too slow along these paths — or the location / origin time is biased.",
            "この経路ではモデルの速度が遅すぎる。あるいは震源位置・発震時刻がずれている。",
        ),
    ),
]


def quiz_slide(deck: Deck) -> None:
    slide = content(deck, ("Quiz", "クイズ"), KICK, ("116–120 min", "116–120 分"))
    for i, (q, a) in enumerate(QUIZ):
        y = BODY_TOP + i * Inches(1.55)
        badge(slide, MARGIN, y + Inches(0.05), f"Q{i + 1}", size=Inches(0.62), font=15)
        text_box(
            slide, MARGIN + Inches(0.85), y, Inches(11.2), Inches(0.6), q, Style(18, INK, True)
        )
        text_box(
            slide,
            MARGIN + Inches(0.85),
            y + Inches(0.65),
            Inches(11.2),
            Inches(0.6),
            a,
            Style(15, OCEAN),
        )
    notes(
        slide,
        (
            "Reveal answers one at a time (or delete the answer lines for a printed quiz).",
            "解答は 1 つずつ示す(配布用には解答行を消す)。",
        ),
    )
    deck.mark("quiz")


def add(deck: Deck) -> None:
    guided_slide(deck)
    trace_slide(deck)
    own_task_slide(deck)
    coding_slide(deck)
    speedup_why_slide(deck)
    ask_first_slide(deck)
    build_rag_slide(deck)
    effect_slide(deck)
    instructions_slide(deck)
    compare_slide(deck)
    prompting_slide(deck)
    quiz_slide(deck)
