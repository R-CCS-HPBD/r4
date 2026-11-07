"""The harness map: one picture of a turn, reused small on every background slide.

Review 2026-11-01: after "model, agent, harness", show the
whole picture once, then explain the model exchange, the loop, the gate,
skills, MCP and knowledge on the same figure. :func:`draw_full` draws the
map with labels and the step badges of one turn; :func:`mini` draws the
same geometry small in the top-right corner, with the part a slide explains
filled amber ("you are here").

Geometry is in map inches (12.1 × 4.2) and scaled to the target width.
"""

from __future__ import annotations

from dataclasses import dataclass

from deck_kit import (
    ALERT,
    AMBER,
    INK,
    LINE,
    MARGIN,
    OCEAN,
    SLIDE_W,
    TINT,
    TINT_AMBER,
    WHITE,
    Style,
    badge,
    lang,
    rect,
    text_box,
    text_width,
    tr,
)
from lxml import etree
from pptx.enum.shapes import MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

MAP_W, MAP_H = 12.1, 4.7
ROW1, ROW2, ROW3, ROW_H = 0.5, 1.55, 2.85, 0.8
BUS_Y = ROW2 + ROW_H + 0.22
MINI_W = Inches(2.9)
MINI_TOP = Inches(0.14)


@dataclass(frozen=True)
class Node:
    x: float
    y: float
    w: float
    h: float
    label: tuple[str, str]
    sub: tuple[str, str]
    inside: bool = True


NODES = {
    "you": Node(
        0.0,
        ROW1,
        1.6,
        ROW_H,
        ("You", "あなた"),
        ("a request in the console", "コンソールで依頼"),
        False,
    ),
    "files": Node(
        0.0,
        ROW3,
        1.6,
        ROW_H,
        ("Your files", "自分のファイル"),
        ("disk, shell, Python", "ディスク・シェル・Python"),
        False,
    ),
    "context": Node(
        2.35,
        ROW1,
        3.15,
        ROW_H,
        ("Context builder", "コンテキストの組み立て"),
        ("rules · history · your request", "指示・履歴・依頼文"),
    ),
    "loop": Node(
        5.7,
        ROW1,
        3.15,
        ROW_H,
        ("Agent loop", "エージェントループ"),
        ("call the model, run tools, repeat", "モデルを呼ぶ → ツール → 繰り返す"),
    ),
    "skills": Node(
        2.35,
        ROW2,
        3.15,
        ROW_H,
        ("Skills", "スキル"),
        ("how-to notes, added to the context", "手順書。必要なときコンテキストへ"),
    ),
    "gate": Node(
        5.7,
        ROW2,
        3.15,
        ROW_H,
        ("Permission gate", "許可ゲート"),
        ("run · ask you · block", "実行・確認・拒否"),
    ),
    "tools": Node(
        2.35,
        ROW3,
        2.0,
        ROW_H,
        ("Local tools", "手元のツール"),
        ("python · shell · files", "python・シェル・ファイル"),
    ),
    "knowledge": Node(
        4.5,
        ROW3,
        2.05,
        ROW_H,
        ("Knowledge", "ナレッジ"),
        ("search_docs tool (RAG)", "search_docs ツール(RAG)"),
    ),
    "mcp": Node(
        6.7,
        ROW3,
        2.15,
        ROW_H,
        ("MCP tools", "MCP のツール"),
        ("mcp.* — run on servers", "mcp.* — サーバで実行"),
    ),
    "model": Node(
        9.6,
        ROW1,
        2.5,
        ROW2 + ROW_H - ROW1,
        ("Model (LLM)", "モデル(LLM)"),
        ("Ollama on your PC today — or a hosted API", "今日は PC 上の Ollama。外部の API も可"),
        False,
    ),
    "umrs": Node(
        4.5,
        4.05,
        4.35,
        0.65,
        ("UMRS", "UMRS"),
        ("document search · MCP registry → servers", "文書検索・MCP の登録簿 → サーバ"),
        False,
    ),
}
HARNESS = (2.1, 0.0, 7.0, 3.82)

# Arrows (x1, y1, x2, y2) in map inches. Review 2026-11-05: every box joins
# the flow — skills feed the context, the gate fans out to the three kinds of
# tool (local, knowledge search, MCP), and the last two reach UMRS.
ARROWS = (
    (1.6, 0.9, 2.35, 0.9),  # you → context
    (5.5, 0.9, 5.7, 0.9),  # context → loop
    (8.85, 0.72, 9.6, 0.72),  # loop → model
    (9.6, 1.08, 8.85, 1.08),  # model → loop
    (7.27, ROW1 + ROW_H, 7.27, ROW2),  # loop → gate
    (3.92, ROW2, 3.92, ROW1 + ROW_H),  # skills → context
    (3.35, BUS_Y, 3.35, ROW3),  # tool call → local tools
    (5.52, BUS_Y, 5.52, ROW3),  # tool call → knowledge
    (7.77, BUS_Y, 7.77, ROW3),  # tool call → MCP
    (2.35, ROW3 + 0.4, 1.6, ROW3 + 0.4),  # local tools → your files
    (5.52, ROW3 + ROW_H, 5.52, 4.05),  # knowledge → UMRS
    (7.77, ROW3 + ROW_H, 7.77, 4.05),  # MCP → UMRS
)
# The tool-call bus from the gate (plain lines, no heads).
LINES = (
    (7.27, ROW2 + ROW_H, 7.27, BUS_Y),
    (3.35, BUS_Y, 7.77, BUS_Y),
)
# One turn, in order: the badge sits on the top-left corner of the box;
# step 5 (a tool runs) sits on the tool-call bus.
STEPS = {"context": "1", "model": "2", "loop": "3", "gate": "4"}
BUS_STEP = (2.95, BUS_Y)
EXCHANGE = (2, 3)  # the loop ⇄ model arrows

# What each focus highlights: node keys, and whether the exchange arrows glow.
FOCUS = {
    "model": (("model",), False),
    "context": (("context",), False),
    "exchange": (("loop", "model"), True),
    "loop": (("loop",), False),
    "gate": (("gate",), False),
    "skills": (("skills",), False),
    "mcp": (("mcp", "umrs"), False),
    "knowledge": (("knowledge", "umrs"), False),
}
# Section transitions (review 2026-11-05): the full map, framed, before a block.
FOCUS.update(
    {
        "harness": (("context", "loop", "gate"), True),
        "extend": (("skills", "mcp"), False),
    }
)
# One-word labels for the mini map.
SHORT = {
    "you": ("You", "あなた"),
    "files": ("Files", "ファイル"),
    "context": ("Context", "コンテキスト"),
    "loop": ("Loop", "ループ"),
    "tools": ("Local", "手元"),
    "gate": ("Gate", "ゲート"),
    "skills": ("Skills", "スキル"),
    "knowledge": ("RAG", "RAG"),
    "mcp": ("MCP", "MCP"),
    "model": ("Model", "モデル"),
    "umrs": ("UMRS", "UMRS"),
}
FOCUS_CAPTION = {
    "model": ("the model", "モデル"),
    "context": ("the context", "コンテキスト"),
    "exchange": ("harness ↔ model", "ハーネス ↔ モデル"),
    "loop": ("the agent loop", "エージェントループ"),
    "gate": ("the permission gate", "許可ゲート"),
    "skills": ("skills", "スキル"),
    "mcp": ("MCP", "MCP"),
    "knowledge": ("knowledge (RAG)", "ナレッジ(RAG)"),
    "harness": ("the harness at work", "ハーネスの働き"),
    "extend": ("skills and MCP", "スキルと MCP"),
}


def _line(slide, x1, y1, x2, y2, color, width: float) -> None:
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x1, y1, x2, y2)
    conn.line.color.rgb = color
    conn.line.width = Pt(width)


def _arrow(slide, x1, y1, x2, y2, color, width: float) -> None:
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x1, y1, x2, y2)
    conn.line.color.rgb = color
    conn.line.width = Pt(width)
    ln = conn.line._get_or_add_ln()
    tail = etree.SubElement(ln, qn("a:tailEnd"))
    tail.set("type", "triangle")
    tail.set("w", "med")
    tail.set("len", "med")


class _Scale:
    def __init__(self, x, y, w):
        self.x, self.y, self.k = int(x), int(y), int(w) / MAP_W

    def font(self) -> float:
        """Node text size: 12 pt at full width, never below 9 pt."""
        return max(9.0, min(12.0, round(12 * self.k / Inches(1))))

    def __call__(self, v: float, axis: str = "") -> Emu:
        base = {"x": self.x, "y": self.y}.get(axis, 0)
        return Emu(int(base + v * self.k))


def _node_fill(key: str, node: Node, lit: tuple) -> object:
    if key in lit:
        return AMBER
    return WHITE if node.inside else TINT


def _full_node(slide, s: _Scale, key: str, node: Node, lit: tuple) -> None:
    x, y, w, h = s(node.x, "x"), s(node.y, "y"), s(node.w), s(node.h)
    rect(slide, x, y, w, h, fill=_node_fill(key, node, lit), line=LINE, rounded=True)
    pad = Inches(0.1)
    text_box(
        slide,
        x + pad,
        y + Inches(0.06),
        w - 2 * pad,
        h - Inches(0.1),
        (f"**{node.label[0]}**\n{node.sub[0]}", f"**{node.label[1]}**\n{node.sub[1]}"),
        Style(s.font(), INK),
        align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE,
    )


def _frame(slide, s: _Scale, keys, width: float, pad: float) -> None:
    """A red outline around the bounding box of ``keys`` (the part being explained)."""
    nodes = [NODES[k] for k in keys]
    x0 = min(n.x for n in nodes) - pad
    y0 = min(n.y for n in nodes) - pad
    x1 = max(n.x + n.w for n in nodes) + pad
    y1 = max(n.y + n.h for n in nodes) + pad
    shp = rect(slide, s(x0, "x"), s(y0, "y"), s(x1 - x0), s(y1 - y0), line=ALERT, rounded=True)
    shp.line.width = Pt(width)


def _frames(slide, s: _Scale, focus: str, width: float, pad: float) -> None:
    """One red frame around each highlighted box (a group frame swept in others)."""
    for key in FOCUS[focus][0]:
        _frame(slide, s, [key], width, pad)


def draw_full(slide, x, y, w, focus: str | None = None) -> int:
    """The labelled map at (x, y), ``w`` wide; returns its height in EMU."""
    s = _Scale(x, y, w)
    hx, hy, hw, hh = HARNESS
    rect(slide, s(hx, "x"), s(hy, "y"), s(hw), s(hh), fill=TINT_AMBER, rounded=True)
    text_box(
        slide,
        s(hx + 0.25, "x"),
        s(hy + 0.08, "y"),
        s(hw - 0.5),
        Inches(0.3),
        ("Harness — R4, running on your PC", "ハーネス — R4(自分の PC で動く)"),
        Style(13, OCEAN, True),
    )
    lit = FOCUS[focus][0] if focus else ()
    for key, node in NODES.items():
        _full_node(slide, s, key, node, lit)
    for x1, y1, x2, y2 in ARROWS:
        _arrow(slide, s(x1, "x"), s(y1, "y"), s(x2, "x"), s(y2, "y"), OCEAN, 2.25)
    for x1, y1, x2, y2 in LINES:
        _line(slide, s(x1, "x"), s(y1, "y"), s(x2, "x"), s(y2, "y"), OCEAN, 2.25)
    _bus_label(slide, s)
    size = Inches(0.32)
    for key, step in STEPS.items():
        node = NODES[key]
        badge(slide, s(node.x, "x") - size // 3, s(node.y, "y") - size // 3, step, AMBER, size, 12)
    badge(
        slide,
        s(BUS_STEP[0], "x") - size // 2,
        s(BUS_STEP[1], "y") - size // 2,
        "5",
        AMBER,
        size,
        12,
    )
    if focus:
        _frames(slide, s, focus, 3.0, 0.1)
    return int(s(MAP_H))


def _bus_label(slide, s: _Scale) -> None:
    text_box(
        slide,
        s(3.45, "x"),
        s(BUS_Y - 0.21, "y"),
        s(3.7),
        s(0.2),
        ("a tool call: one of three kinds", "ツール呼び出し:3 種類のどれか"),
        Style(max(7.0, s.font() - 2), OCEAN, True),
    )


def _fit_title(slide, right) -> None:
    """Keep the slide title left of the mini map: narrow its box, shrink only if needed."""
    title = next((shp for shp in slide.shapes if shp.name == "Title"), None)
    if title is None:
        return
    title.width = int(right - title.left)
    text = title.text_frame.text
    slack = 0.9 if lang() == "en" else 1.0  # the width estimate runs wide for Latin text
    size = next((pt for pt in (32, 28, 24) if text_width(text, pt) * slack <= title.width), 24)
    for para in title.text_frame.paragraphs:
        for run in para.runs:
            run.font.size = Pt(size)


def _mini_node(slide, s: _Scale, key: str, node: Node, lit: tuple) -> None:
    fill = AMBER if key in lit else (WHITE if node.inside else TINT)
    rect(slide, s(node.x, "x"), s(node.y, "y"), s(node.w), s(node.h), fill=fill, line=LINE)
    text_box(
        slide,
        s(node.x - 0.3, "x"),
        s(node.y, "y"),
        s(node.w + 0.6),
        s(node.h),
        SHORT[key],
        Style(7, INK, key in lit),
        align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE,
    )


def mini(slide, focus: str) -> None:
    """The same map, small and labelled, top right; ``focus`` parts amber and framed red."""
    left = SLIDE_W - MARGIN - MINI_W
    s = _Scale(left, MINI_TOP, MINI_W)
    lit, glow = FOCUS[focus]
    hx, hy, hw, hh = HARNESS
    rect(slide, s(hx, "x"), s(hy, "y"), s(hw), s(hh), fill=TINT_AMBER, rounded=True)
    for key, node in NODES.items():
        _mini_node(slide, s, key, node, lit)
    if glow:
        for i in EXCHANGE:
            x1, y1, x2, y2 = ARROWS[i]
            _arrow(slide, s(x1, "x"), s(y1, "y"), s(x2, "x"), s(y2, "y"), ALERT, 1.25)
    _frames(slide, s, focus, 2.0, 0.12)
    cap = FOCUS_CAPTION[focus]
    text_box(
        slide,
        left - Inches(1.0),
        s(MAP_H, "y") + Inches(0.02),
        MINI_W + Inches(1.0),
        Inches(0.24),
        (f"Now: {cap[0]}", f"いまの話:{cap[1]}"),
        Style(10, ALERT, True),
        align=PP_ALIGN.RIGHT,
    )
    _fit_title(slide, left - Inches(0.15))


def caption(focus: str) -> str:
    return tr(FOCUS_CAPTION[focus])
