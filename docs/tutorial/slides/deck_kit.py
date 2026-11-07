"""Drawing kit for the R4 + UMRS tutorial deck (python-pptx).

One palette, one set of slide frames, a handful of components (cards,
step badges, code panels, tables, flows). The content lives in
``build_tutorial_deck.py``; every visible string there is an ``(en, ja)``
pair resolved through :func:`set_lang` / :func:`tr`.

Motif: concentric "isobar" rings on dark slides (the subject thread is a
typhoon), teal numbered badges on light slides.
"""

from __future__ import annotations

import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

# ── Palette: deep ocean + storm amber ────────────────────────────────────────
NAVY = RGBColor(0x0B, 0x1F, 0x33)
NAVY2 = RGBColor(0x13, 0x2E, 0x48)
OCEAN = RGBColor(0x0E, 0x7C, 0x86)
SEAFOAM = RGBColor(0x5C, 0xC8, 0xC2)
AMBER = RGBColor(0xF2, 0xA5, 0x41)
ALERT = RGBColor(0xD1, 0x49, 0x5B)
INK = RGBColor(0x1B, 0x2B, 0x3A)
MUTED = RGBColor(0x5B, 0x6B, 0x7A)
TINT = RGBColor(0xEC, 0xF4, 0xF5)
TINT_AMBER = RGBColor(0xFD, 0xF1, 0xDE)
LINE = RGBColor(0xC9, 0xD8, 0xDC)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
CODE_INK = RGBColor(0xD6, 0xF5, 0xF2)

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
MARGIN = Inches(0.6)
CONTENT_W = SLIDE_W - 2 * MARGIN
BODY_TOP = Inches(1.75)

LATIN_FONT = "Calibri"
EA_FONT = "Yu Gothic"
MONO_FONT = "Courier New"

ASSETS = Path(__file__).resolve().parent / "assets"

_LANG = {"code": "en"}


def set_lang(code: str) -> None:
    """Select the language every later :func:`tr` call resolves to."""
    _LANG["code"] = code


def lang() -> str:
    return _LANG["code"]


def tr(text) -> str:
    """Resolve an ``(en, ja)`` pair, or pass a plain string through."""
    if isinstance(text, tuple):
        return text[0] if _LANG["code"] == "en" else text[1]
    return text


# ── Japanese punctuation ─────────────────────────────────────────────────────
# Prose in the JA deck uses full-width ：（）？ next to Japanese text. Applied to
# text runs, chart labels and notes; never to code panels, mono runs or EN.

_CJK = "[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]"
_COLON_AFTER_CJK = re.compile(f"({_CJK}\\**)[ \\t]*:(?!//)[ \\t]*")
_COLON_BEFORE_CJK = re.compile(f"[ \\t]*:(?!//)[ \\t]*(\\**{_CJK})")
_QUESTION = re.compile(f"({_CJK}\\**)\\?")
_INNER_PAREN = re.compile("([ \\t]*)\\(([^()]*)\\)")
_HAS_CJK = re.compile(_CJK)
_LOOKBACK = re.compile(f"{_CJK}[*]*$")
_MASK_OPEN, _MASK_CLOSE = "\x01", "\x02"
_SPACE_AFTER_CLOSE = re.compile(f"）[ \\t]+(?=\\**{_CJK})")


def _paren_repl(match: re.Match, text: str) -> str:
    """Full-width pair when Japanese sits before ``(`` or inside; else mask it."""
    space, inner = match.group(1), match.group(2)
    before = text[: match.start()]
    if _LOOKBACK.search(before) or _HAS_CJK.search(inner):
        return f"（{inner}）"
    return f"{space}{_MASK_OPEN}{inner}{_MASK_CLOSE}"


def _ja_parens(text: str) -> str:
    """Innermost pairs first, repeated until stable; skipped pairs are masked."""
    while True:
        new = _INNER_PAREN.sub(lambda m, t=text: _paren_repl(m, t), text, count=1)
        if new == text:
            break
        text = new
    return text.replace(_MASK_OPEN, "(").replace(_MASK_CLOSE, ")")


def _ja_colon_question(text: str) -> str:
    text = _COLON_AFTER_CJK.sub("\\1：", text)
    text = _COLON_BEFORE_CJK.sub("：\\1", text)
    return _QUESTION.sub("\\1？", text)


def _ja_punct(text: str) -> str:
    """Half-width ``:`` ``( )`` ``?`` beside Japanese become ``：（）？``.

    The colon/question pass runs before the parentheses (so ``：(`` and ``？(``
    can widen the pair) and again after (so ``）:`` and ``）?`` widen too).
    """
    if not _HAS_CJK.search(text):
        return text
    text = _ja_parens(_ja_colon_question(text))
    return _SPACE_AFTER_CLOSE.sub("）", _ja_colon_question(text))


def _ja_label(text) -> str:
    """:func:`tr` plus the JA punctuation pass (chart titles, axes, series)."""
    out = tr(text)
    return _ja_punct(out) if lang() == "ja" else out


# ── Low-level text ───────────────────────────────────────────────────────────


def _set_fonts(run, mono: bool = False) -> None:
    """Latin + East-Asian typeface on one run (python-pptx sets latin only)."""
    run.font.name = MONO_FONT if mono else LATIN_FONT
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        old = rpr.find(qn(tag))
        if old is not None:
            rpr.remove(old)
    ea = etree.SubElement(rpr, qn("a:ea"))
    ea.set("typeface", EA_FONT)


@dataclass
class Style:
    """Text style for one paragraph run."""

    size: float = 16
    color: RGBColor = INK
    bold: bool = False
    italic: bool = False
    mono: bool = False


def _style_run(run, st: Style) -> None:
    run.font.size = Pt(st.size)
    run.font.bold = st.bold
    run.font.italic = st.italic
    run.font.color.rgb = st.color
    _set_fonts(run, st.mono)


def _add_runs(para, text: str, st: Style) -> None:
    """``**bold**`` spans inside ``text`` become bold runs."""
    if lang() == "ja" and not st.mono:
        text = _ja_punct(text)
    parts = text.split("**")
    for i, part in enumerate(parts):
        if not part:
            continue
        run = para.add_run()
        run.text = part
        bold_style = Style(st.size, st.color, True, st.italic, st.mono)
        _style_run(run, bold_style if i % 2 else st)


def text_box(
    slide,
    x,
    y,
    w,
    h,
    text,
    st: Style | None = None,
    align=PP_ALIGN.LEFT,
    anchor=MSO_ANCHOR.TOP,
    name: str | None = None,
):
    """A text box; ``text`` may hold ``\\n`` paragraphs and ``**bold**``."""
    st = st or Style()
    box = slide.shapes.add_textbox(x, y, w, h)
    if name:
        box.name = name
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, m, 0)
    for i, line in enumerate(tr(text).split("\n")):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = align
        _add_runs(para, line, st)
    return box


def bullets(slide, x, y, w, h, items: Sequence, st: Style | None = None, gap: float = 8):
    """Real bulleted paragraphs (buChar), each item an ``(en, ja)`` pair."""
    st = st or Style()
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, m, 0)
    for i, item in enumerate(items):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.space_after = Pt(gap)
        _bullet_ppr(para, st.color)
        _add_runs(para, tr(item), st)
    return box


def _bullet_ppr(para, color: RGBColor) -> None:
    ppr = para._p.get_or_add_pPr()
    ppr.set("marL", str(Emu(Inches(0.28))))
    ppr.set("indent", str(-Emu(Inches(0.28))))
    clr = etree.SubElement(ppr, qn("a:buClr"))
    srgb = etree.SubElement(clr, qn("a:srgbClr"))
    srgb.set("val", str(OCEAN if color == INK else color))
    font = etree.SubElement(ppr, qn("a:buFont"))
    font.set("typeface", "Arial")
    char = etree.SubElement(ppr, qn("a:buChar"))
    char.set("char", "•")


# ── Shapes ───────────────────────────────────────────────────────────────────


def rect(slide, x, y, w, h, fill=None, line=None, rounded=False, name=None):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    shp = slide.shapes.add_shape(kind, x, y, w, h)
    if rounded:
        shp.adjustments[0] = 0.08
    _fill_line(shp, fill, line)
    if name:
        shp.name = name
    return shp


def _fill_line(shp, fill, line, width: float = 1.0) -> None:
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(width)
    shp.shadow.inherit = False


def ring(slide, cx, cy, r, color, width=1.25):
    """One isobar ring (unfilled circle) centred at ``cx, cy``."""
    shp = slide.shapes.add_shape(MSO_SHAPE.OVAL, cx - r, cy - r, 2 * r, 2 * r)
    _fill_line(shp, None, color, width)
    return shp


def isobars(slide, cx, cy, color=NAVY2, count=7, step=Inches(0.55)):
    """Concentric rings slightly off-centre, like a pressure chart."""
    for i in range(count, 0, -1):
        off = Emu(int(step * 0.18 * i))
        ring(slide, cx + off, cy - off // 2, step * i, color, 1.0 + 0.15 * (count - i))


def badge(slide, x, y, label: str, fill=OCEAN, size=Inches(0.48), font=16):
    """Numbered circle — the light-slide motif."""
    shp = slide.shapes.add_shape(MSO_SHAPE.OVAL, x, y, size, size)
    _fill_line(shp, fill, None)
    tf = shp.text_frame
    for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, m, 0)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.CENTER
    _add_runs(para, label, Style(font, WHITE, True))
    return shp


def chip(slide, x, y, label, fill=AMBER, color=INK, w=Inches(1.3), h=Inches(0.34), size=12):
    """Small rounded label (e.g. "LAB 1.3", "STRETCH")."""
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    shp.adjustments[0] = 0.5
    _fill_line(shp, fill, None)
    tf = shp.text_frame
    for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, m, 0)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.CENTER
    _add_runs(para, tr(label), Style(size, color, True))
    return shp


def arrow(slide, x, y, w, h=Inches(0.36), fill=SEAFOAM):
    shp = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, x, y, w, h)
    _fill_line(shp, fill, None)
    return shp


def down_arrow(slide, x, y, w=Inches(0.36), h=Inches(0.3), fill=SEAFOAM):
    shp = slide.shapes.add_shape(MSO_SHAPE.DOWN_ARROW, x, y, w, h)
    _fill_line(shp, fill, None)
    return shp


# ── Pictures ─────────────────────────────────────────────────────────────────

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MAP_HINT = "uv run --script docs/tutorial/slides/render_kii_map.py --lang both"


def _png_size(path: Path) -> tuple[int, int]:
    """Pixel width and height from the PNG IHDR chunk (no Pillow)."""
    head = path.read_bytes()[:24]
    if head[:8] != _PNG_MAGIC:
        raise ValueError(f"{path} is not a PNG file")
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def picture(slide, name, x, y, w, h, alt=None):
    """Insert ``assets/<name>`` fitted inside ``w`` x ``h``, centred, aspect kept.

    ``name`` and ``alt`` may be ``(en, ja)`` pairs; ``alt`` becomes the
    picture's alt text (``descr``).
    """
    path = ASSETS / tr(name)
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; render it with: {_MAP_HINT}")
    px_w, px_h = _png_size(path)
    scale = min(w / px_w, h / px_h)
    pw, ph = int(px_w * scale), int(px_h * scale)
    pic = slide.shapes.add_picture(
        str(path), x + (w - pw) // 2, y + (h - ph) // 2, width=Emu(pw), height=Emu(ph)
    )
    if alt is not None:
        pic._element.nvPicPr.cNvPr.set("descr", tr(alt))
    return pic


# ── Scope badges: general agent-harness pattern vs R4-specific ───────────────

SCOPE_KINDS = {
    "general": (("GENERAL PATTERN", "一般的な仕組み"), OCEAN, WHITE),
    "r4tuned": (("GENERAL · R4 SETTINGS", "一般的な仕組み・R4 の設定"), SEAFOAM, INK),
    "r4": (("R4-SPECIFIC", "R4 独自"), AMBER, INK),
}
SCOPE_CAPTION = ("Badges:", "印の意味：")
# Short gloss drawn after each pill in the legend (slide 9 explains all three).
SCOPE_GLOSS = {
    "general": ("common to mainstream harnesses", "主要なハーネスに共通"),
    "r4tuned": ("same pattern, R4's own values", "共通の仕組みを R4 の値で使う"),
    "r4": ("only in R4", "R4 にしかない"),
}
SCOPE_H = Inches(0.28)
_SCOPE_SIZE = 9
_SCOPE_PAD = Inches(0.12)


def _char_em(ch: str) -> float:
    """Rough advance width of one character, in ems (bold sans)."""
    if ord(ch) >= 0x2E80:
        return 1.0
    if ch.isupper() or ch.isdigit():
        return 0.66
    if ch in " ·.,:;'|!il":
        return 0.3
    return 0.54


def text_width(text: str, size: float, bold: bool = True) -> Emu:
    """Estimated rendered width of one line of ``text`` at ``size`` pt."""
    ems = sum(_char_em(ch) if ord(ch) >= 0x2E80 or bold else 0.85 * _char_em(ch) for ch in text)
    return Emu(int(Pt(size) * ems))


def _pill(shapes, left, top, label: str, fill, color):
    width = text_width(label, _SCOPE_SIZE) + 2 * _SCOPE_PAD
    shp = shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, SCOPE_H)
    shp.adjustments[0] = 0.5
    _fill_line(shp, fill, None)
    tf = shp.text_frame
    tf.word_wrap = False
    for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, m, 0)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.CENTER
    _add_runs(para, label, Style(_SCOPE_SIZE, color, True))
    return shp


def scope_tag(slide, left, top, kind: str):
    """Small pill saying whether a mechanism is a general pattern or R4's own.

    ``kind``: ``"general"`` (shared by mainstream agent harnesses),
    ``"r4tuned"`` (general pattern, R4's own numbers or choices) or ``"r4"``
    (R4-specific). About 0.28 in tall; the width follows the label.
    """
    if kind not in SCOPE_KINDS:
        raise ValueError(f"scope kind {kind!r} not in {sorted(SCOPE_KINDS)}")
    label, fill, color = SCOPE_KINDS[kind]
    return _pill(slide.shapes, left, top, tr(label), fill, color)


def _legend_text(shapes, left, top, text: str) -> Emu:
    """One muted 11 pt line beside the pills; returns its width."""
    # bold-width estimate: the plain estimate is tight enough that LibreOffice wraps it
    width = text_width(text, 11, bold=True) + Inches(0.15)
    box = shapes.add_textbox(left, top, width, SCOPE_H)
    tf = box.text_frame
    tf.word_wrap = False
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, m, 0)
    _add_runs(tf.paragraphs[0], text, Style(11, MUTED))
    return width


def scope_legend(slide, left, top):
    """One line: caption, then each pill with its gloss. Returns the group."""
    group = slide.shapes.add_group_shape()
    x = left + _legend_text(group.shapes, left, top, tr(SCOPE_CAPTION))
    for kind, (label, fill, color) in SCOPE_KINDS.items():
        pill = _pill(group.shapes, x, top, tr(label), fill, color)
        x += pill.width + Inches(0.08)
        x += _legend_text(group.shapes, x, top, tr(SCOPE_GLOSS[kind])) + Inches(0.1)
    return group


# ── Components ───────────────────────────────────────────────────────────────


def card(
    slide,
    x,
    y,
    w,
    h,
    title,
    body=None,
    fill=TINT,
    title_color=INK,
    body_size=14,
    items=None,
    line=None,
):
    """Tinted rounded panel: bold title then a paragraph or bullet list."""
    rect(slide, x, y, w, h, fill=fill, line=line, rounded=True)
    pad = Inches(0.22)
    text_box(
        slide,
        x + pad,
        y + Inches(0.16),
        w - 2 * pad,
        Inches(0.45),
        title,
        Style(17, title_color, True),
    )
    top = y + Inches(0.62)
    inner_h = h - Inches(0.75)
    if body is not None:
        text_box(slide, x + pad, top, w - 2 * pad, inner_h, body, Style(body_size, INK))
    if items is not None:
        bullets(slide, x + pad, top, w - 2 * pad, inner_h, items, Style(body_size, INK), gap=5)


CODE_COMMENTS_JA: dict[str, str] = {}


def _localize_line(line: str) -> str:
    """Swap a ``# comment`` for its Japanese text when the deck is Japanese."""
    if lang() != "ja" or "#" not in line:
        return line
    cut = line.index("#")
    if cut and not line[cut - 1].isspace():
        return line
    comment = line[cut:]
    return line[:cut] + CODE_COMMENTS_JA.get(comment, comment)


def code_panel(slide, x, y, w, h, code, size=13, title=None):
    """Dark terminal-style panel; ``code`` is literal or an (en, ja) pair.

    Comment lines are localized through :data:`CODE_COMMENTS_JA`.
    """
    code = tr(code)
    rect(slide, x, y, w, h, fill=NAVY, rounded=True)
    pad = Inches(0.22)
    top = y + Inches(0.16)
    if title:
        text_box(slide, x + pad, top, w - 2 * pad, Inches(0.3), title, Style(11, SEAFOAM, True))
        top += Inches(0.34)
    box = slide.shapes.add_textbox(x + pad, top, w - 2 * pad, h - (top - y) - Inches(0.12))
    tf = box.text_frame
    tf.word_wrap = True
    for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, m, 0)
    for i, raw in enumerate(code.split("\n")):
        line = _localize_line(raw)
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        color = MUTED if line.lstrip().startswith("#") else CODE_INK
        run = para.add_run()
        run.text = line if line else " "
        _style_run(run, Style(size, SEAFOAM if color == MUTED else color, mono=True))
    return box


def table(
    slide,
    x,
    y,
    w,
    rows: Sequence[Sequence],
    col_w: Sequence[float],
    size=13,
    row_h=Inches(0.42),
    header_fill=OCEAN,
    first_col_bold=True,
):
    """Simple striped table; ``rows[0]`` is the header. Cells are (en, ja) or str."""
    shape = slide.shapes.add_table(len(rows), len(rows[0]), x, y, w, row_h * len(rows))
    tbl = shape.table
    total = sum(col_w)
    for i, cw in enumerate(col_w):
        tbl.columns[i].width = Emu(int(w * cw / total))
    for r, row in enumerate(rows):
        tbl.rows[r].height = row_h
        for c, val in enumerate(row):
            _table_cell(tbl.cell(r, c), val, r, c, size, header_fill, first_col_bold)
    return shape


def _table_cell(cell, val, r, c, size, header_fill, first_col_bold):
    cell.fill.solid()
    cell.fill.fore_color.rgb = header_fill if r == 0 else (TINT if r % 2 else WHITE)
    cell.margin_left = cell.margin_right = Inches(0.08)
    cell.margin_top = cell.margin_bottom = Inches(0.04)
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf = cell.text_frame
    tf.word_wrap = True
    head = r == 0
    st = Style(
        size - (1 if head else 0), WHITE if head else INK, head or (c == 0 and first_col_bold)
    )
    for i, line in enumerate(tr(val).split("\n")):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        _add_runs(para, line, st)


def flow(slide, x, y, w, h, steps: Sequence, fill=TINT, size=14, accent=OCEAN):
    """Left-to-right boxes joined by arrows; each step is (title, sub)."""
    n = len(steps)
    gap = Inches(0.42)
    bw = (w - gap * (n - 1)) / n
    for i, (title, sub) in enumerate(steps):
        bx = x + i * (bw + gap)
        rect(slide, bx, y, bw, h, fill=fill, rounded=True)
        text_box(
            slide,
            bx + Inches(0.12),
            y + Inches(0.14),
            bw - Inches(0.24),
            Inches(0.4),
            title,
            Style(size + 1, accent, True),
            align=PP_ALIGN.CENTER,
        )
        text_box(
            slide,
            bx + Inches(0.12),
            y + Inches(0.6),
            bw - Inches(0.24),
            h - Inches(0.7),
            sub,
            Style(size - 1, INK),
            align=PP_ALIGN.CENTER,
        )
        if i < n - 1:
            arrow(
                slide,
                bx + bw + Inches(0.06),
                y + h / 2 - Inches(0.15),
                gap - Inches(0.12),
                Inches(0.3),
            )


def bar_chart(
    slide,
    x,
    y,
    w,
    h,
    categories,
    series: dict,
    colors,
    title=None,
    number_format="0%",
    horizontal=True,
    max_value=None,
):
    """Native bar chart with value labels; one colour per series."""
    data = CategoryChartData(number_format=number_format)
    data.categories = [_ja_label(c) for c in categories]
    for name, values in series.items():
        data.add_series(_ja_label(name), values)
    kind = XL_CHART_TYPE.BAR_CLUSTERED if horizontal else XL_CHART_TYPE.COLUMN_CLUSTERED
    chart = slide.shapes.add_chart(kind, x, y, w, h, data).chart
    _chart_title(chart, title)
    chart.has_legend = len(series) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(12)
    plot = chart.plots[0]
    plot.gap_width = 60
    plot.has_data_labels = True
    plot.data_labels.font.size = Pt(11)
    plot.data_labels.font.color.rgb = INK
    plot.data_labels.number_format = number_format
    plot.data_labels.number_format_is_linked = False
    for plot_series, color in zip(plot.series, colors, strict=False):
        plot_series.format.fill.solid()
        plot_series.format.fill.fore_color.rgb = color
    _quiet_axes(chart, None)
    if horizontal:
        chart.category_axis.reverse_order = True
        chart.category_axis.tick_labels.font.size = Pt(13)
        chart.category_axis.tick_labels.font.color.rgb = INK
    if max_value is not None:
        chart.value_axis.maximum_scale = max_value
        chart.value_axis.minimum_scale = 0
    return chart


def xy_chart(slide, x, y, w, h, series, title=None, x_title=None, y_title=None):
    """Native XY scatter; ``series`` = [(name, xs, ys, color, "line" | "points")]."""
    from pptx.chart.data import XyChartData
    from pptx.enum.chart import XL_MARKER_STYLE

    data = XyChartData()
    for name, xs, ys, _color, _style in series:
        s = data.add_series(_ja_label(name))
        for a, b in zip(xs, ys, strict=True):
            s.add_data_point(a, b)
    chart = slide.shapes.add_chart(
        XL_CHART_TYPE.XY_SCATTER_LINES_NO_MARKERS, x, y, w, h, data
    ).chart
    _chart_title(chart, title)
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False
    chart.legend.font.size = Pt(12)
    for plot_series, (_n, _xs, _ys, color, style) in zip(
        chart.plots[0].series, series, strict=True
    ):
        plot_series.smooth = False
        if style == "points":
            plot_series.format.line.fill.background()
            plot_series.marker.style = XL_MARKER_STYLE.CIRCLE
            plot_series.marker.size = 8
            plot_series.marker.format.fill.solid()
            plot_series.marker.format.fill.fore_color.rgb = color
            plot_series.marker.format.line.color.rgb = INK
        else:
            plot_series.format.line.color.rgb = color
            plot_series.format.line.width = Pt(2.5)
            plot_series.marker.style = XL_MARKER_STYLE.NONE
    _quiet_axes(chart, y_title)
    if x_title:
        cat = chart.category_axis
        cat.has_title = True
        cat.axis_title.text_frame.text = _ja_label(x_title)
        run = cat.axis_title.text_frame.paragraphs[0].runs[0]
        run.font.size = Pt(11)
        run.font.color.rgb = MUTED
    return chart


def line_chart(slide, x, y, w, h, categories, series: dict, colors, title=None, y_title=None):
    """Native line chart, quiet frame, legend at the bottom."""
    data = CategoryChartData()
    data.categories = [_ja_label(c) for c in categories]
    for name, values in series.items():
        data.add_series(_ja_label(name), values)
    gframe = slide.shapes.add_chart(XL_CHART_TYPE.LINE, x, y, w, h, data)
    chart = gframe.chart
    _chart_title(chart, title)
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False
    chart.legend.font.size = Pt(12)
    for plot_series, color in zip(chart.plots[0].series, colors, strict=False):
        plot_series.format.line.color.rgb = color
        plot_series.format.line.width = Pt(2.25)
        plot_series.smooth = False
    _quiet_axes(chart, y_title)
    return chart


def _chart_title(chart, title):
    chart.has_title = bool(title)
    if title:
        chart.chart_title.text_frame.text = _ja_label(title)
        para = chart.chart_title.text_frame.paragraphs[0]
        para.runs[0].font.size = Pt(14)
        para.runs[0].font.bold = True
        para.runs[0].font.color.rgb = INK


def _quiet_axes(chart, y_title):
    cat, val = chart.category_axis, chart.value_axis
    cat.tick_labels.font.size = Pt(11)
    cat.tick_labels.font.color.rgb = MUTED
    cat.format.line.color.rgb = LINE
    cat.has_major_gridlines = False
    val.tick_labels.font.size = Pt(11)
    val.tick_labels.font.color.rgb = MUTED
    val.format.line.fill.background()
    val.major_gridlines.format.line.color.rgb = LINE
    if y_title:
        val.has_title = True
        val.axis_title.text_frame.text = _ja_label(y_title)
        run = val.axis_title.text_frame.paragraphs[0].runs[0]
        run.font.size = Pt(11)
        run.font.color.rgb = MUTED


# ── Slide frames ─────────────────────────────────────────────────────────────


class Deck:
    """A presentation plus page bookkeeping and the footer label."""

    def __init__(self, footer):
        self.prs = Presentation()
        self.prs.slide_width = SLIDE_W
        self.prs.slide_height = SLIDE_H
        self.footer = footer
        self.count = 0
        self.anchors: dict[str, int] = {}

    def blank(self, dark=False):
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = NAVY if dark else WHITE
        self.count += 1
        return slide

    def mark(self, name: str) -> int:
        """Record the number of the slide just added under ``name``.

        Lets a later slide (the lecture-cut map) cite slide numbers that stay
        right when slides are inserted or removed.
        """
        self.anchors[name] = self.count
        return self.count

    def anchor(self, name: str) -> int:
        """Slide number recorded by :meth:`mark`; KeyError names what is known."""
        if name not in self.anchors:
            raise KeyError(f"no slide marked {name!r}; marked: {sorted(self.anchors)}")
        return self.anchors[name]

    def save(self, path):
        self.prs.save(path)


def notes(slide, text) -> None:
    """Speaker notes; the JA deck gets the full-width punctuation pass."""
    slide.notes_slide.notes_text_frame.text = _ja_label(text)


def _footer(deck: Deck, slide, dark=False):
    color = SEAFOAM if dark else MUTED
    text_box(
        slide, MARGIN, SLIDE_H - Inches(0.45), Inches(8), Inches(0.3), deck.footer, Style(10, color)
    )
    text_box(
        slide,
        SLIDE_W - MARGIN - Inches(1),
        SLIDE_H - Inches(0.45),
        Inches(1),
        Inches(0.3),
        str(deck.count),
        Style(10, color),
        align=PP_ALIGN.RIGHT,
    )


def content(deck: Deck, title, kicker=None, minutes=None, chip_right=None):
    """Light content slide: kicker, title, footer. Returns the slide.

    ``chip_right``: right edge of the time chip (default: the right margin;
    slides with the harness mini-map move it left of the map).
    """
    slide = deck.blank()
    if kicker:
        text_box(
            slide, MARGIN, Inches(0.42), Inches(9.5), Inches(0.35), kicker, Style(13, OCEAN, True)
        )
    if minutes:
        right = SLIDE_W - MARGIN if chip_right is None else chip_right
        chip(
            slide,
            right - Inches(1.5),
            Inches(0.4),
            minutes,
            fill=TINT,
            color=OCEAN,
            w=Inches(1.5),
        )
    text_box(
        slide,
        MARGIN,
        Inches(0.78),
        CONTENT_W,
        Inches(0.8),
        title,
        Style(32, INK, True),
        name="Title",
    )
    _footer(deck, slide)
    return slide


def divider(deck: Deck, part_label, title, sub, meta_items):
    """Dark part divider with isobar rings on the right."""
    slide = deck.blank(dark=True)
    isobars(slide, SLIDE_W - Inches(2.4), Inches(3.6))
    ring(slide, SLIDE_W - Inches(2.4), Inches(3.6), Inches(0.16), AMBER, 3)
    lines = tr(title).count("\n") + 1
    top = Inches(1.5) - Inches(0.4) * (lines - 1)
    text_box(slide, MARGIN, top, Inches(8), Inches(0.5), part_label, Style(18, AMBER, True))
    text_box(
        slide,
        MARGIN,
        top + Inches(0.5),
        Inches(8.2),
        Inches(0.85) * lines,
        title,
        Style(48, WHITE, True),
        name="Title",
    )
    text_box(
        slide,
        MARGIN,
        top + Inches(0.75) + Inches(0.85) * lines,
        Inches(7.6),
        Inches(1.2),
        sub,
        Style(19, CODE_INK),
    )
    for i, item in enumerate(meta_items):
        y = Inches(5.0) + i * Inches(0.42)
        ring(slide, MARGIN + Inches(0.09), y + Inches(0.15), Inches(0.07), SEAFOAM, 2)
        text_box(
            slide, MARGIN + Inches(0.35), y, Inches(7.6), Inches(0.38), item, Style(15, CODE_INK)
        )
    _footer(deck, slide, dark=True)
    return slide
