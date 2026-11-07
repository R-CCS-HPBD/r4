"""Shared R4 terminal branding helpers."""

from __future__ import annotations

import html
from functools import lru_cache
from pathlib import Path
from typing import Literal

_FALLBACK_LOGO = """██████████████
                      ███████████
         ████████████  ████████████
        █████         ██████████████
       ████    ██████████████████████
      █████  █   █████████████████████
     ██████  ███   ████████████████████
     ██████  █████   ██████████████████
    ███████  ███████   █████████████████
    ███████  ███████████████████████████
     ██████  ██████████████████████████
      █████  ██████████████████████████
      █████  █████████████████████████
       ████  ████████████████████████
         ██  ███████████████████████
          █  █████████████████████
             ███████████████████
                ████████████"""

_SURFACE_COPY: dict[str, tuple[str, str]] = {
    "chat": (
        "R4 Chat",
        "Ask R4 about models, data, and workflows.",
    ),
    "companion": (
        "R4 Companion",
        "Research with R4. Type /help for commands.",
    ),
    "tui": (
        "R4 Platform",
        "Your AI research companion in the terminal.",
    ),
}


@lru_cache(maxsize=1)
def ascii_logo() -> str:
    """Return the workspace logo when present, with an embedded fallback."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        logo_path = parent / "logo.txt"
        if not logo_path.exists():
            continue
        try:
            logo = logo_path.read_text(encoding="utf-8").rstrip()
        except OSError:
            continue
        if logo:
            return logo
    return _FALLBACK_LOGO.rstrip()


def brand_banner_text(surface: Literal["chat", "companion", "tui"]) -> str:
    """Return the shared logo plus the surface-specific wordmark/copy."""
    title, subtitle = _SURFACE_COPY[surface]
    return f"{ascii_logo()}\n\n{title}\n{subtitle}"


def brand_html(surface: Literal["chat", "companion", "tui"]) -> str:
    """Return notebook-friendly HTML for the shared R4 brand block."""
    title, subtitle = _SURFACE_COPY[surface]
    logo = html.escape(ascii_logo())
    return f"""
    <section style="
        margin: 0 0 12px 0;
        padding: 16px 18px;
        border: 1px solid #d7e3f4;
        border-radius: 16px;
        background: linear-gradient(135deg, #f8fbff 0%, #eef4ff 100%);
        box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
    ">
        <div style="
            display: grid;
            grid-template-columns: minmax(180px, 1fr) minmax(220px, 1.2fr);
            gap: 16px;
            align-items: center;
        ">
            <pre style="
                margin: 0;
                color: #0f3b73;
                font: 11px/1.05 Menlo, Consolas, 'SFMono-Regular', monospace;
                white-space: pre;
                overflow-x: auto;
            ">{logo}</pre>
            <div>
                <div style="
                    margin-bottom: 6px;
                    color: #486581;
                    font: 600 12px/1.4 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                    letter-spacing: 0.18em;
                    text-transform: uppercase;
                ">R4</div>
                <h3 style="
                    margin: 0 0 8px 0;
                    color: #102a43;
                    font: 700 24px/1.1 Georgia, 'Times New Roman', serif;
                ">{html.escape(title)}</h3>
                <p style="
                    margin: 0;
                    color: #334e68;
                    font: 400 14px/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                ">{html.escape(subtitle)}</p>
            </div>
        </div>
    </section>
    """.strip()
