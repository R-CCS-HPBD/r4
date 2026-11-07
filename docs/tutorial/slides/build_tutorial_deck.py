"""Build the R4 + UMRS tutorial deck, English and Japanese (55 slides each).

Plan: the maintainers' internal tutorial plan (2026-10-26 revision).
Covers the Opening, Part 1 (short, ~2 h) and Part 2 (middle, ~3 h) in
full, Part 3 as the deferred outline, and the optional commercial-services
+ MCP set as an appendix. Timing and talk track are in the speaker notes.

Every command on a slide was checked against the code on 2026-10-26; the
handbook pages (docs/tutorial/{en,ja}/) carry the full copy-paste steps.

Maps: uv run --script docs/tutorial/slides/render_kii_map.py --lang both
(needs network once; caches ETOPO1 + Natural Earth under
~/.cache/r4-tutorial-maps), then python build_tutorial_deck.py. The build
itself never needs cartopy: it reads the committed PNGs in assets/.

Usage:  python build_tutorial_deck.py [--lang en|ja|both] [--out DIR]
Output: tutorial-r4-umrs.en.pptx, tutorial-r4-umrs.ja.pptx
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import deck_kit
import slides_opening
import slides_part1_concepts
import slides_part1_labs
import slides_part2
import slides_tail
from code_comments_ja import COMMENTS
from deck_kit import Deck, set_lang

HERE = Path(__file__).resolve().parent
deck_kit.CODE_COMMENTS_JA.update(COMMENTS)
DATA = HERE.parent / "data"
BACKGROUND = HERE.parent / "background"
TASK = HERE.parent / "tasks" / "eikonal"

FOOTER = (
    "R4 + UMRS tutorial · your data, your agent",
    "R4 + UMRS チュートリアル · データもエージェントも手元に",
)
TITLE = {"en": "R4 + UMRS tutorial", "ja": "R4 + UMRS チュートリアル"}
# Fixed, so a rebuild with unchanged slides gives the same metadata.
DECK_DATE = datetime(2026, 11, 7)


def set_core_properties(prs, code: str) -> None:
    """Neutral document metadata.

    python-pptx's default template carries its author's name in
    lastModifiedBy and a 2013 date; neither belongs in a published deck.
    """
    props = prs.core_properties
    props.title = TITLE[code]
    props.author = "R4 + UMRS tutorial"
    props.last_modified_by = "R4 + UMRS tutorial"
    props.created = DECK_DATE
    props.modified = DECK_DATE
    props.revision = 1


def build(code: str, out: Path) -> Path:
    set_lang(code)
    deck = Deck(FOOTER)
    slides_opening.add(deck)
    probe = json.loads((BACKGROUND / "next_token_probe.json").read_text(encoding="utf-8"))
    emb = json.loads((BACKGROUND / "embedding_probe.json").read_text(encoding="utf-8"))
    slides_part1_concepts.add(deck, probe, emb)
    slides_part1_labs.add(deck)
    slides_part2.add(deck)
    slides_tail.add(deck)
    set_core_properties(deck.prs, code)
    path = out / f"tutorial-r4-umrs.{code}.pptx"
    deck.save(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the R4 + UMRS tutorial deck")
    parser.add_argument("--lang", choices=("en", "ja", "both"), default="both")
    parser.add_argument("--out", type=Path, default=HERE)
    args = parser.parse_args()
    codes = ("en", "ja") if args.lang == "both" else (args.lang,)
    for code in codes:
        print("wrote", build(code, args.out))


if __name__ == "__main__":
    main()
