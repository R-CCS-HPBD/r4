"""The context window as one horizontal bar, drawn to scale (review 2026-11-05).

Used by the context-window slide (what fills the window, in the order the
model reads it) and the overflow slide (what each trimming step does to the
same bar). Sizes are tokens; the bar is ``total`` tokens wide.
"""

from __future__ import annotations

from deck_kit import (
    ALERT,
    AMBER,
    INK,
    LINE,
    MUTED,
    NAVY,
    NAVY2,
    OCEAN,
    SEAFOAM,
    TINT_AMBER,
    WHITE,
    Style,
    rect,
    text_box,
)
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

WINDOW = 32_768  # qwen2.5:7b on the stand-alone stack (OLLAMA_CONTEXT_LENGTH)
# Review 2026-11-07: draw the axis a little past the window so a red limit line
# shows where the window ends (and what would overflow it).
AXIS = 36_000

# kind → (fill, text colour)
KINDS = {
    "system": (NAVY, WHITE),
    "tools": (SEAFOAM, INK),
    "history": (OCEAN, WHITE),
    "message": (AMBER, INK),
    "rounds": (MUTED, WHITE),
    "cut": (TINT_AMBER, INK),
    "summary": (NAVY2, WHITE),
    "reply": (LINE, INK),
    "over": (ALERT, WHITE),
    "free": (WHITE, MUTED),
}


def bar(slide, x, y, w, h, segments, total=WINDOW, size=9) -> None:
    """``segments``: (kind, tokens, label or None), drawn left to right."""
    left = x
    for kind, tokens, label in segments:
        fill, ink = KINDS[kind]
        sw = int(w * tokens / total)
        shp = rect(slide, left, y, sw, h, fill=fill, line=WHITE if kind != "free" else LINE)
        shp.line.width = Pt(0.75)
        if label and sw >= Inches(0.3):
            text_box(
                slide,
                left,
                y,
                sw,
                h,
                label,
                Style(size, ink, True),
                align=PP_ALIGN.CENTER,
                anchor=MSO_ANCHOR.MIDDLE,
            )
        left += sw


def scale(slide, x, y, w, total=WINDOW, step=8_192, size=8) -> None:
    """Tick labels under a bar: 0, 8k, 16k … up to ``total``."""
    for t in range(0, min(total, WINDOW) + 1, step):
        tx = x + int(w * t / total)
        rect(slide, tx, y, Inches(0.01), Inches(0.06), fill=MUTED)
        text_box(
            slide,
            tx - Inches(0.3),
            y + Inches(0.06),
            Inches(0.6),
            Inches(0.18),
            f"{t // 1024}k" if t else "0",
            Style(size, MUTED),
            align=PP_ALIGN.CENTER,
        )


def marker(slide, x, y, w, h, at, label, total=WINDOW, color=ALERT) -> None:
    """A thin vertical line across a bar at ``at`` tokens, labelled above it."""
    mx = x + int(w * at / total)
    rect(slide, mx, y - Inches(0.05), Inches(0.02), h + Inches(0.1), fill=color)
    text_box(
        slide,
        mx - Inches(0.8),
        y - Inches(0.25),
        Inches(1.6),
        Inches(0.2),
        label,
        Style(8, color, True),
        align=PP_ALIGN.CENTER,
    )


def limit_line(slide, x, y_top, y_bottom, w, total=AXIS, label=None) -> None:
    """The window limit: a red vertical line at WINDOW tokens, labelled above."""
    lx = x + int(w * WINDOW / total)
    rect(slide, lx - Inches(0.015), y_top, Inches(0.03), y_bottom - y_top, fill=ALERT)
    text = label or ("window limit 32,768", "ウィンドウの上限 32,768")
    text_box(
        slide,
        lx - Inches(1.6),
        y_top - Inches(0.22),
        Inches(1.6) + Inches(0.9),
        Inches(0.2),
        text,
        Style(9, ALERT, True),
        align=PP_ALIGN.RIGHT,
    )


def swatch_legend(slide, x, y, w, items, size=10, row_h=Inches(0.27)) -> int:
    """Colour swatch + text per (kind, (en, ja)); returns the y below."""
    for kind, text in items:
        fill, _ = KINDS[kind]
        rect(
            slide,
            x,
            y + Inches(0.04),
            Inches(0.22),
            Inches(0.16),
            fill=fill,
            line=LINE if kind == "free" else None,
        )
        text_box(slide, x + Inches(0.3), y, w - Inches(0.3), row_h, text, Style(size, INK))
        y += row_h
    return int(y)
