"""Part 3 outline, closing, the optional slide and the facilitator's lecture cut.

New slides 52–55 (plan slide map): the Part 3 day shape, what you take home,
one optional slide (when data leaves) and the 1.5 h lecture cut. The lecture
cut reads its slide numbers from Deck.mark anchors when the other slide
modules set them, so inserted slides do not leave stale numbers behind.
"""

from __future__ import annotations

import sys

from deck_kit import (
    BODY_TOP,
    CONTENT_W,
    INK,
    MARGIN,
    MUTED,
    OCEAN,
    SLIDE_W,
    TINT,
    TINT_AMBER,
    Deck,
    Style,
    badge,
    card,
    chip,
    content,
    notes,
    rect,
    scope_tag,
    table,
    text_box,
)
from pptx.util import Inches

KICK3 = (
    "Part 3 · ~6 h · outline — details set after the platform feature study",
    "第 3 部 · 約 6 時間 · 概要 — 詳細はプラットフォーム機能調査の後に決定",
)
KICK_APX = ("Appendix", "付録")

# Slide numbers of the lecture-cut map (plan slide map, 55 slides since 2026-11-05). A module
# that calls deck.mark(<name>) right after adding that slide overrides the
# default, so the map follows the deck when slides move.
CUT_DEFAULTS = {
    "opening_end": 6,
    "hood_start": 9,
    "patterns": 10,
    "hood_end": 31,
    "lab11": 32,
    "lab12": 34,
    "lab13": 35,
    "lab13_why": 36,
    "lab14a": 37,
    "lab14c": 39,
    "lab14de": 40,
    "lab15": 41,
    "practice": 42,
    "quiz": 43,
    "bridge": 44,
}
# The deck.mark names the other slide modules set, and the offset from that
# slide to the one the map needs (the background block ends at "recap", the
# Part 2 divider just after "quiz"). Used when no module marks the key itself.
CUT_ANCHORS = {
    "opening_end": ("story", 0),
    "hood_start": ("three_things", 0),
    "patterns": ("patterns", 0),
    "hood_end": ("recap", 0),
    "lab11": ("lab_1_1", 0),
    "lab12": ("lab_1_2", 0),
    "lab13": ("lab_1_3", 0),
    "lab13_why": ("speedup", 0),
    "lab14a": ("lab_1_4_a", 0),
    "lab14c": ("lab_1_4_c", 0),
    "lab14de": ("lab_1_4_de", 0),
    "lab15": ("lab_1_5", 0),
    "practice": ("good_practice", 0),
    "quiz": ("quiz", 0),
    "bridge": ("quiz", 1),
}
LECTURE_CUT_SLIDE = 55

# Slides the optional slide (54) cites, by deck.mark name; the plan's numbers
# where a module does not mark them.
REF_DEFAULTS = {"theme": 3, "platform": 5, "mcp": 24, "lab_1_4_de": 40}


def _ref(deck: Deck, name: str) -> int:
    """Number of the slide marked ``name``; the plan's number when unmarked."""
    return deck.anchors.get(name, REF_DEFAULTS[name])


# ── 52 · Part 3: the shape of the day ───────────────────────────────────────


def _part3_rows() -> list:
    return [
        [("Time", "時間"), ("Block", "ブロック"), ("Content", "内容")],
        [
            "0:00–0:45",
            ("Design", "設計"),
            (
                "Science question, inputs, outputs, acceptance check; "
                "draw the pipeline with the agent",
                "研究課題・入力・出力・合格基準を決め、エージェントと一緒に処理の流れを設計する",
            ),
        ],
        [
            "0:45–1:45",
            "Lab 3.1",
            (
                "Stage real data; sanity checks; a small subset first",
                "実データの配置と確認。まず小さな部分集合で",
            ),
        ],
        [
            "1:45–2:45",
            "Lab 3.2",
            (
                "Build the steps and a container image; ask for GPU nodes with routing_hints",
                "ステップとコンテナイメージを作り、routing_hints で GPU ノードを指定する",
            ),
        ],
        ["2:45–3:30", ("Lunch", "昼食"), "—"],
        [
            "3:30–4:30",
            "Lab 3.3",
            (
                "Run on GPU nodes; monitor; debug with logs + agent",
                "GPU 実行環境で実行・監視、ログとエージェントで修正",
            ),
        ],
        [
            "4:30–5:15",
            "Lab 3.4",
            (
                "Subset → full data; retrieve, aggregate, plot",
                "部分集合 → 全データ。結果の取得・集計・作図",
            ),
        ],
        [
            "5:15–5:45",
            "Lab 3.5",
            (
                "Reproducibility: config, session export, rerun in a fresh environment",
                "再現性:設定・セッション書き出し・まっさらな環境での再実行",
            ),
        ],
    ]


def part3_shape_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("Part 3 — a real workflow: the shape of the day", "第 3 部 — 実践ワークフロー:1 日の流れ"),
        KICK3,
    )
    table(
        slide,
        MARGIN,
        BODY_TOP,
        Inches(8.4),
        _part3_rows(),
        [1.4, 1.3, 5.7],
        size=13,
        row_h=Inches(0.55),
    )
    card(
        slide,
        MARGIN + Inches(8.8),
        BODY_TOP,
        CONTENT_W - Inches(8.8),
        Inches(4.4),
        ("Open decisions", "未決事項"),
        fill=TINT_AMBER,
        body_size=13,
        items=[
            (
                "Planned case: 3-D structure (subducting slab, sedimentary basin), many "
                "events, GPU — Part 2's solver and workflow, scaled up",
                "予定:3 次元構造(スラブ=沈み込むプレート、堆積盆地)、多数の地震、GPU。"
                "第 2 部のソルバとワークフローの規模を広げる",
            ),
            (
                "Candidate topics: a 3-D velocity model, tomography (structure from many "
                "travel times), or relocation (locating the events again)",
                "題材の候補:3 次元速度モデル、トモグラフィ(多数の走時から地下構造を推定)、"
                "震源再決定(震源の位置を求め直す)のどれか",
            ),
            (
                "Where: laptop subset, local UMRS → Rikyu, or a shared UMRS server",
                "実行場所:手元の PC で部分集合、ローカル UMRS → Rikyu、または共用の UMRS サーバ",
            ),
            ("GPU capacity for N attendees", "参加人数分の GPU 容量"),
        ],
    )
    notes(
        slide,
        (
            "Shown as an outline only. Long-course strategy waits for the platform feature "
            "study; until the case is fixed the timings stay generic. "
            "The planned case keeps Part 2's solver and workflow and scales them up: 3-D "
            "structure, many events, the solver on GPU nodes through routing_hints. "
            "Lunch is 2:45–3:30.",
            "概要のみ示す。長期コースの方針はプラットフォーム機能調査の完了を待つ。"
            "題材が決まるまで時間配分は仮のまま。"
            "予定の題材は第 2 部のソルバとワークフローをそのまま使い、規模を広げる。"
            "3 次元構造、多数の地震、routing_hints で GPU ノードに送るソルバ。昼食は 2:45–3:30。",
        ),
    )


# ── 53 · What you take home ──────────────────────────────────────────────────

TAKE_HOME = [
    (
        "1",
        ("A local AI stack you control", "自分で管理するローカル AI 環境"),
        ("R4 + stand-alone UMRS + a free model", "R4 + PC 上の単体版 UMRS + 無料モデル"),
    ),
    (
        "2",
        ("Your knowledge set", "自分の知識セット"),
        ("RAG index + instructions/skill + MCP tool", "RAG 索引+指示/スキル+MCP ツール"),
    ),
    (
        "3",
        ("A working fan-out workflow", "動く分岐(ファンアウト)ワークフロー"),
        ("163 solves → a ranked residual report", "163 回の計算 → 順位付きの残差レポート"),
    ),
    (
        "4",
        ("A habit", "習慣"),
        ("State the acceptance check before you ask", "依頼の前に合格基準を書く"),
    ),
]

NEXT_STEPS = [
    (
        "Bring **your own data**: another event, your own picks or velocity model.",
        "**自分のデータ**で:別の地震、自分の読み取りや速度モデル。",
    ),
    (
        "Homework: rerun the workflow on the real picks and compare with KRSO-1D v2; "
        "add a step that relocates the event by grid search.",
        "宿題:実際の読み取りでワークフローを実行し KRSO-1D v2 と比べる。"
        "グリッドサーチで震源を再決定するステップを追加する。",
    ),
    ("Handbook: docs/tutorial/ (EN / JA).", "ハンドブック:docs/tutorial/(英語/日本語)。"),
    (
        "Support channel and consultation: [to be announced].",
        "サポート窓口・個別相談:[後日案内]。",
    ),
]


def _take_home_item(slide, i: int, num: str, head, sub) -> None:
    y = BODY_TOP + Inches(0.05) + i * Inches(1.05)
    badge(slide, MARGIN, y + Inches(0.05), num, size=Inches(0.6), font=20)
    left = MARGIN + Inches(0.85)
    text_box(slide, left, y, Inches(6.2), Inches(0.45), head, Style(19, INK, True))
    text_box(slide, left, y + Inches(0.45), Inches(6.2), Inches(0.45), sub, Style(15, MUTED))


def closing_slide(deck: Deck) -> None:
    slide = content(
        deck,
        ("What you take home", "持ち帰るもの"),
        ("Closing", "まとめ"),
        ("170–180 min", "170–180 分"),
    )
    for i, (num, head, sub) in enumerate(TAKE_HOME):
        _take_home_item(slide, i, num, head, sub)
    card(
        slide,
        MARGIN + Inches(7.4),
        BODY_TOP + Inches(0.05),
        CONTENT_W - Inches(7.4),
        Inches(3.6),
        ("Next steps", "次の一歩"),
        body_size=15,
        items=NEXT_STEPS,
    )
    notes(
        slide,
        (
            "10 min, Part 2 wrap-up and closing together. Item 2 is the set each attendee "
            "built (RAG index, R4.md and skill, one MCP tool), not the observatory's knowledge "
            "pack they indexed. Fill the support channel before delivery; give the survey "
            "link here. After a Part-1-only session skip the homework line.",
            "10 分。第 2 部のまとめと締めくくりを合わせて行う。2 番は各自が作ったもの"
            "(RAG 索引、R4.md とスキル、MCP ツール 1 つ)で、取り込んだ観測所のナレッジパック"
            "とは別物。実施前にサポート窓口を記入し、アンケートのリンクをここで案内する。"
            "第 1 部のみの回では宿題の行は飛ばす。",
        ),
    )


# ── 54 · Optional: when data leaves ──────────────────────────────────────────

OPT_LEFT_W = Inches(5.9)
OPT_RIGHT_X = MARGIN + Inches(6.13)
OPT_RIGHT_W = Inches(6.0)
OPT_TOP = BODY_TOP + Inches(0.2)
OPT_UPPER_H = Inches(2.15)
OPT_LOWER_TOP = OPT_TOP + OPT_UPPER_H + Inches(0.12)
OPT_BOTTOM = Inches(6.85)
OPT_NOTE_TOP = OPT_TOP + Inches(3.05)
OPT_RULE_TOP = OPT_NOTE_TOP + Inches(0.85)


def _tag_right(slide, right, top, kind: str) -> None:
    """Scope pill whose right edge sits at right."""
    pill = scope_tag(slide, right, top, kind)
    pill.left = int(right - pill.width)


def _when_external_items(theme: int) -> list:
    return [
        ("Public or tutorial-class data only.", "公開データ・チュートリアル用データのみ。"),
        (
            "A task the local model cannot do: long reasoning, a big code base.",
            "手元のモデルでは難しい作業(長い推論・大きなコード)。",
        ),
        (
            "Your institution's data policy allows it; check first.",
            "所属機関のデータポリシーが認めている(先に確認する)。",
        ),
        (
            f"As lane ② on slide {theme}: the provider sees the whole context window.",
            f"スライド {theme} の経路 ② のとおり、事業者にはコンテキストの中身がすべて見える。",
        ),
    ]


def _r4_enforce_items() -> list:
    return [
        (
            "An external model is a separate model source you add explicitly "
            "(r4 source add --kind public).",
            "外部のモデルは、自分で明示的に追加する別のモデル接続先(r4 source add --kind public)。",
        ),
        (
            "The project's data class decides: in a project set to sensitive, R4 refuses "
            "external routes (local models and Rikyu stay allowed).",
            "使えるかはプロジェクトのデータ区分(機密度の設定)で決まる。"
            "sensitive(機微)なら R4 が外部への経路を拒否する"
            "(手元のモデルと Rikyu は使える)。",
        ),
        (
            "Today's stand-alone stack starts new projects as non_sensitive; to refuse, run "
            "r4 project data-class set <project> sensitive",
            "単体版 UMRS の新しいプロジェクトは non_sensitive(機微でない)。"
            "拒否させるには r4 project data-class set <project> sensitive",
        ),
    ]


def _external_model_cards(slide, theme: int) -> None:
    edge = MARGIN + OPT_LEFT_W - Inches(0.15)
    card(
        slide,
        MARGIN,
        OPT_TOP,
        OPT_LEFT_W,
        OPT_UPPER_H,
        ("When an external model fits", "外部のモデルを使ってよい場面"),
        body_size=13,
        items=_when_external_items(theme),
    )
    _tag_right(slide, edge, OPT_TOP + Inches(0.18), "general")
    card(
        slide,
        MARGIN,
        OPT_LOWER_TOP,
        OPT_LEFT_W,
        OPT_BOTTOM - OPT_LOWER_TOP,
        ("How R4 enforces it", "R4 での扱い"),
        fill=TINT_AMBER,
        body_size=13,
        items=_r4_enforce_items(),
    )
    _tag_right(slide, edge, OPT_LOWER_TOP + Inches(0.18), "r4")


def _mcp_rows() -> list:
    return [
        [("Pattern", "パターン"), ("Example", "例"), ("Data leaves?", "外に出るか")],
        [
            ("Local tool server", "手元のツールサーバ"),
            ("a script wrapping your analysis code", "自分の解析コードを包むスクリプト"),
            ("no", "出ない"),
        ],
        [
            ("Site-registered server", "サイト登録のサーバ"),
            ("an instrument API on the institute network", "所内ネットワークの装置 API"),
            ("stays in the site", "サイト内に留まる"),
        ],
        [
            ("Public remote server", "公開のリモートサーバ"),
            ("a literature-search service", "文献検索サービス"),
            ("**the query does**", "**検索語は出る**"),
        ],
    ]


def _mcp_note(skills: int) -> tuple[str, str]:
    return (
        f"How R4 wires MCP (each tool as mcp.<server>.<tool>, through the UMRS registry): "
        f"slide {skills}. The results count as MCP output for the taint fence.",
        f"R4 での MCP のつながり方(各ツールは mcp.<server>.<tool>、UMRS の登録簿経由)は"
        f"スライド {skills} を参照。その結果は汚染フェンスで MCP の出力として扱われる。",
    )


def _mcp_panel(slide, skills: int) -> None:
    right = OPT_RIGHT_X + OPT_RIGHT_W
    text_box(
        slide,
        OPT_RIGHT_X,
        OPT_TOP + Inches(0.02),
        OPT_RIGHT_W - Inches(2.1),
        Inches(0.4),
        ("Tool servers (MCP)", "ツールサーバ(MCP)"),
        Style(17, INK, True),
    )
    _tag_right(slide, right, OPT_TOP + Inches(0.06), "r4tuned")
    table(
        slide,
        OPT_RIGHT_X,
        OPT_TOP + Inches(0.5),
        OPT_RIGHT_W,
        _mcp_rows(),
        [2.3, 2.1, 1.6],
        size=13,
        row_h=Inches(0.6),
    )
    text_box(
        slide,
        OPT_RIGHT_X,
        OPT_NOTE_TOP,
        OPT_RIGHT_W,
        OPT_RULE_TOP - OPT_NOTE_TOP - Inches(0.05),
        _mcp_note(skills),
        Style(13, INK),
    )


def _rule_line(platform: int) -> tuple[str, str]:
    return (
        "**Rule:** register servers in UMRS's MCP registry; credentials go in by a person, "
        f"never through the agent (the same rule as slide {platform}, 'Where credentials come in').",
        "**原則:**サーバは UMRS の MCP 登録簿に登録する。認証情報は人が設定し、"
        f"エージェントを通さない(スライド {platform}「認証情報が登場するのはどこか」と同じ原則)。",
    )


def commercial_slide(deck: Deck) -> None:
    refs = {name: _ref(deck, name) for name in REF_DEFAULTS}
    slide = content(
        deck,
        (
            "When data leaves: external models, remote tool servers",
            "データが外に出るとき:外部のモデルとツールサーバ",
        ),
        KICK_APX,
    )
    chip(
        slide,
        SLIDE_W - MARGIN - Inches(1.3),
        Inches(0.4),
        ("optional", "任意"),
        fill=TINT,
        color=OCEAN,
    )
    _external_model_cards(slide, refs["theme"])
    _mcp_panel(slide, refs["mcp"])
    text_box(
        slide,
        OPT_RIGHT_X,
        OPT_RULE_TOP,
        OPT_RIGHT_W,
        OPT_BOTTOM - OPT_RULE_TOP,
        _rule_line(refs["platform"]),
        Style(13, INK),
    )
    notes(slide, _commercial_notes(refs))


def _commercial_notes(refs: dict[str, int]) -> tuple[str, str]:
    theme, skills, lab = refs["theme"], refs["mcp"], refs["lab_1_4_de"]
    return (
        "Optional; use it only when Part 1 has slack. Keep vendor names neutral: the point "
        "is the decision rule, not a product comparison. Left, upper card (general): "
        "whatever is in the context window reaches the model's provider, in every harness "
        "(Claude Code sends it to Anthropic, the OpenAI Agents SDK to OpenAI, opencode or "
        f"LangChain to the provider you configure); lane ② on slide {theme} said the same. "
        "Left, lower card (R4): an external model is a source added with r4 source add "
        "--kind public (r4/src/r4/_cli/source.py:504-509); for a project whose data class "
        "is sensitive (r4 project data-class set, _cli/project_data_class.py:29) the client "
        "refuses any route that is neither loopback nor on the site's institutional list, "
        "Rikyu by default (client.py:1300-1325, source_locality.py:45-60); the venue and "
        "lease gates on the server repeat the check. A site creates projects as sensitive, "
        "but the stand-alone stack sets UMRS_PROJECT_DEFAULT_DATA_CLASS=non_sensitive "
        "(umrs shared/project_data_class.py:206-222, deploy/standalone/compose.yaml:175), "
        "and a non-sensitive or unknown class means no client gate: on today's stack nothing "
        "is refused until the attendee sets the project to sensitive (narrowing needs no "
        "confirmation). Right (general pattern, R4 settings): MCP is the shared standard "
        "(Claude Code reads .mcp.json, the OpenAI Agents SDK has MCPServerStdio); R4 lists "
        "the tools from the UMRS registry and registers each as mcp.<server>.<tool> with a "
        f"300 s cache (companion/mcp_first_class.py:39, 126-153); slide {skills} has the "
        f"detail. Tie back to Lab 1.4 E (slide {lab}), where attendees registered one local "
        "MCP tool and had the agent call it. Their results taint as mcp_output "
        "(r4_contracts.taint.taint_class_for_tool). Fixed 2026-10-30: agent "
        "calls of MCP tools used to fail (mcp_first_class.py read result.is_error / "
        ".content, which MCPToolResult lacks).",
        "任意。第 1 部に余裕があるときだけ使う。製品名は出さず、製品比較ではなく判断基準を伝える。"
        "左上(一般的な仕組み)。コンテキストに入ったものはモデルの事業者に届く。これはどの実装でも"
        "同じ(Claude Code なら Anthropic、OpenAI Agents SDK なら OpenAI、opencode や LangChain "
        f"なら設定した事業者)。スライド {theme} の経路 ② と同じ話。"
        "左下(R4 独自)。外部のモデルは r4 source add --kind public で追加する接続先"
        "(r4/src/r4/_cli/source.py:504-509)。データ区分が sensitive のプロジェクト"
        "(r4 project data-class set、_cli/project_data_class.py:29)では、手元(ループバック)でも"
        "サイトの所内リスト(既定は Rikyu)でもない経路をクライアントが拒否する"
        "(client.py:1300-1325、source_locality.py:45-60)。サーバ側のゲートも同じ確認をする。"
        "サイトでは新しいプロジェクトは sensitive で作られるが、単体版は "
        "UMRS_PROJECT_DEFAULT_DATA_CLASS=non_sensitive を設定している"
        "(umrs の shared/project_data_class.py:206-222、deploy/standalone/compose.yaml:175)。"
        "データ区分が sensitive 以外または不明ならクライアント側のゲートは働かない。"
        "つまり今日の環境では、参加者が sensitive に設定するまで何も拒否されない"
        "(狭める変更に確認は不要)。"
        "右(一般的な仕組み・R4 の設定)。MCP は共通の規格(Claude Code は .mcp.json、"
        "OpenAI Agents SDK は MCPServerStdio)。R4 は UMRS の登録簿からツールを取得し、"
        "mcp.<server>.<tool> として登録する。一覧は 300 秒キャッシュ"
        f"(companion/mcp_first_class.py:39、126-153)。詳細はスライド {skills}。"
        f"Lab 1.4 E(スライド {lab})でローカルの MCP ツールを 1 つ登録し、エージェントに呼び出させた"
        "ことと結びつける。その結果は mcp_output として汚染に記録される"
        "(r4_contracts.taint.taint_class_for_tool)。2026-10-30 に修正:"
        "以前は mcp_first_class.py が MCPToolResult にない result.is_error / .content を"
        "読んでいたため、エージェントからの MCP ツールの呼び出しは失敗していた。",
    )


# ── 55 · Facilitator: the 1.5 h lecture cut ──────────────────────────────────


def _span(a: int, b: int) -> str:
    return str(a) if a == b else f"{a}–{b}"


def _cut_number(deck: Deck, key: str) -> int | None:
    """A key marked by name, else its CUT_ANCHORS mark plus offset, else None."""
    if key in deck.anchors:
        return deck.anchors[key]
    name, offset = CUT_ANCHORS[key]
    return deck.anchors[name] + offset if name in deck.anchors else None


def _cut_numbers(deck: Deck) -> dict[str, int]:
    """Anchor numbers from deck.mark; the plan's numbers where unmarked."""
    found = {key: _cut_number(deck, key) for key in CUT_DEFAULTS}
    nums = {key: CUT_DEFAULTS[key] if n is None else n for key, n in found.items()}
    nums["self"] = deck.count + 1
    marked = any(n is not None for n in found.values())
    if nums["self"] != LECTURE_CUT_SLIDE and not marked:
        print(
            f"slides_tail: lecture cut is slide {nums['self']}, map assumes "
            f"{LECTURE_CUT_SLIDE}; mark the anchors (CUT_DEFAULTS) to fix the numbers",
            file=sys.stderr,
        )
    return nums


def _span_or_none(a: int, b: int) -> str | None:
    """``_span`` for a non-empty range, else None."""
    return None if a > b else _span(a, b)


def _hood(n: dict[str, int]) -> str:
    """The background block without the patterns slide (the lecture skips it)."""
    before = _span_or_none(n["hood_start"], n["patterns"] - 1)
    after = _span_or_none(n["patterns"] + 1, n["hood_end"])
    return ", ".join(part for part in (before, after) if part)


def _cut_rows(n: dict[str, int]) -> list:
    hood = _hood(n)
    lab11 = _span(n["lab11"], n["lab12"])
    rag = _span(n["lab14a"], n["lab14c"])
    fast = _span(n["lab13"], n["lab13_why"])
    wrap = _span(n["practice"], n["quiz"])
    opening = _span(1, n["opening_end"])
    lecture = ("lecture", "講義")
    demo = ("live demo", "実演")
    return [
        [("Min", "分"), ("Slides", "スライド"), ("Mode", "形式")],
        [
            "0–10",
            (
                f"{opening} Opening: title, arc, what runs where, why AI for Science, "
                "platform, the case",
                f"{opening} オープニング:表紙・全体像・データの経路・AI for Science・"
                "プラットフォーム・題材",
            ),
            lecture,
        ],
        [
            "10–45",
            (
                f"{hood} Under the hood: model, agent loop, harness, RAG (measured)",
                f"{hood} 中身を見る:モデル・エージェントループ・ハーネス・RAG(実測)",
            ),
            lecture,
        ],
        [
            "45–60",
            (
                f"{lab11} Download the real event, read the trace, travel-time curve",
                f"{lab11} 実際の地震の取得・トレースの読み方・走時曲線",
            ),
            demo,
        ],
        [
            "60–80",
            (
                f"{rag} RAG off/on (start ingestion first) · {n['lab15']} compare with the "
                f"real event · {fast} the solver speed-up as measured numbers",
                f"{rag} RAG オフ/オン(取り込みを先に開始)・{n['lab15']} 実際の地震との比較・"
                f"{fast} ソルバ高速化は実測値で紹介",
            ),
            demo,
        ],
        ["80–85", (f"{wrap} Good practice + quiz", f"{wrap} 良い使い方+クイズ"), lecture],
        [
            "85–90",
            (f"{n['bridge']} Bridge to Part 2; Q&A", f"{n['bridge']} 第 2 部への橋渡し、質疑"),
            lecture,
        ],
    ]


def _skipped_line(n: dict[str, int]) -> tuple[str, str]:
    spans = (
        _span_or_none(n["opening_end"] + 1, n["hood_start"] - 1),
        str(n["patterns"]),
        _span_or_none(n["hood_end"] + 1, n["lab11"] - 1),
        str(n["lab14de"]),
        _span_or_none(n["bridge"] + 1, n["self"] - 1),
    )
    parts = [part for part in spans if part]
    return (
        f"Skipped in the lecture: {', '.join(parts)}.",
        f"講義では省略:{'、'.join(parts)}。",
    )


def lecture_cut_slide(deck: Deck) -> None:
    nums = _cut_numbers(deck)
    slide = content(deck, ("Facilitator: the 1.5 h lecture cut", "進行用:1.5 時間講義版"), KICK_APX)
    rows = _cut_rows(nums)
    row_h = Inches(0.55)
    table(slide, MARGIN, BODY_TOP, Inches(8.6), rows, [1.0, 5.9, 1.1], size=13, row_h=row_h)
    text_box(
        slide,
        MARGIN,
        BODY_TOP + row_h * len(rows) + Inches(0.3),
        Inches(8.6),
        Inches(0.35),
        _skipped_line(nums),
        Style(12, MUTED),
    )
    rect(
        slide,
        MARGIN + Inches(9.0),
        BODY_TOP,
        CONTENT_W - Inches(9.0),
        Inches(2.2),
        fill=TINT,
        rounded=True,
    )
    text_box(
        slide,
        MARGIN + Inches(9.25),
        BODY_TOP + Inches(0.2),
        CONTENT_W - Inches(9.5),
        Inches(1.8),
        (
            "**Have ready:** a recorded run of every demo, in case the venue network or the "
            "laptop model is slow.",
            "**準備:**会場ネットワークや PC 上のモデルが遅い場合に備え、全デモの録画を用意する。",
        ),
        Style(15, INK),
    )
    notes(
        slide,
        (
            "2026-12-03, University of Hyogo, computational-science students, "
            "lecture format. Carry the Part 2 slides to show where the course goes. Slide "
            "numbers and minutes mirror the facilitator guide's Timings table; change both "
            "together. The numbers are filled in at build time from the marks (anchors) the "
            "slide modules set on their slides, so inserted slides renumber this map too.",
            "2026-12-03、兵庫県立大学、計算科学系の学生、講義形式。"
            "第 2 部のスライドも持参し、コースの先を見せる。"
            "スライド番号と時間は講師ガイド(ハンドブック 03)の時間配分表と同じ。変えるときは両方を直す。"
            "番号はデッキ生成時に、各スライドに付けた目印(アンカー)から自動で埋め込まれる。"
            "スライドを挿入してもこの表の番号は追従する。",
        ),
    )


def add(deck: Deck) -> None:
    part3_shape_slide(deck)
    deck.mark("part3")
    closing_slide(deck)
    deck.mark("closing")
    commercial_slide(deck)
    deck.mark("optional")
    lecture_cut_slide(deck)
