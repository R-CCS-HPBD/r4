"""Integration checks for the tutorial deck (run after every merge of slide lanes).

    python check_deck.py [--slides 55] [--no-pptx]

1. Code-panel comments: every ``# comment`` written in a slides_*.py string
   has a Japanese entry in code_comments_ja.COMMENTS, and every entry is used
   (same rule as deck_kit._localize_line: ``#`` at column 0 or after a space).
2. deck_kit._ja_punct on fixed samples, including the cases it must leave
   alone (times, URLs, doi:, ``run(ctx)``, English), and mono runs untouched.
3. kii_story_data.isochron_radius_km within 0.5 km of 26.9 / 69.8 / 124.1 /
   203.1 km.
4. Slide count of both built decks (skipped with --no-pptx).

Exit status 1 when any check fails.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_CJK = re.compile("[　-ヿ㐀-䶿一-鿿＀-￯]")

PUNCT_SAMPLES = [
    ("題材:紀伊水道の地震", "題材：紀伊水道の地震"),
    ("出典:docs/R4-UMRS-DESIGN.md", "出典：docs/R4-UMRS-DESIGN.md"),
    ("**目的**: 走時を比べる", "**目的**：走時を比べる"),
    ("読み取り (pick) は計測値", "読み取り（pick）は計測値"),
    ("**用語** (term)", "**用語**（term）"),
    ("KRSO-1D v2 (地域モデル)", "KRSO-1D v2（地域モデル）"),
    ("(see run(ctx) を参照)", "（see run(ctx) を参照）"),
    ("何を確かめますか?", "何を確かめますか？"),
    ("12:30 に開始", "12:30 に開始"),
    ("https://www.isc.ac.uk を参照", "https://www.isc.ac.uk を参照"),
    ("出典:https://www.isc.ac.uk", "出典：https://www.isc.ac.uk"),
    ("doi:10.7289/V5C8276M の地形", "doi:10.7289/V5C8276M の地形"),
    ("International Seismological Centre (2026) のデータ", None),
    ("run(ctx) を呼ぶ", None),
    ("Note: (a) is it?", None),
    # colon / question mark right after a closing parenthesis (fixed 2026-10-29)
    ("モード切替(TUI 上):Shift+Tab", "モード切替（TUI 上）：Shift+Tab"),
    ("書き込み(~/.ssh など)?", "書き込み（~/.ssh など）？"),
    ("日本:(x)", "日本：（x）"),
]
ISOCHRON_EXPECT = {5.0: 26.9, 12.0: 69.8, 20.0: 124.1, 30.0: 203.1}


def _comment_of(line: str) -> str | None:
    if "#" not in line:
        return None
    cut = line.index("#")
    if cut and not line[cut - 1].isspace():
        return None
    return line[cut:]


def _string_constants(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def written_comments() -> set[str]:
    """Every English ``# comment`` line inside slides_*.py string constants."""
    found = set()
    for path in sorted(HERE.glob("slides_*.py")):
        for text in _string_constants(path):
            lines = (_comment_of(line) for line in text.split("\n"))
            found.update(c for c in lines if c and not _CJK.search(c) and c != "#")
    return found


def check_comments() -> list[str]:
    from code_comments_ja import COMMENTS

    written = written_comments()
    missing = sorted(written - COMMENTS.keys())
    unused = sorted(COMMENTS.keys() - written)
    return [f"comment without JA entry: {c!r}" for c in missing] + [
        f"COMMENTS key never used: {c!r}" for c in unused
    ]


def check_punct() -> list[str]:
    import deck_kit

    errors = []
    for text, want in PUNCT_SAMPLES:
        got = deck_kit._ja_punct(text)
        if got != (text if want is None else want):
            errors.append(f"_ja_punct({text!r}) = {got!r}, want {want or text!r}")
    return errors + _check_mono_untouched(deck_kit)


def _check_mono_untouched(deck_kit) -> list[str]:
    deck_kit.set_lang("ja")
    try:
        deck = deck_kit.Deck(("f", "f"))
        slide = deck.blank()
        text = "a: 日本 (x)"
        box = deck_kit.text_box(slide, 0, 0, 100, 100, text, deck_kit.Style(mono=True))
        got = box.text_frame.paragraphs[0].runs[0].text
    finally:
        deck_kit.set_lang("en")
    return [] if got == text else [f"mono run changed: {got!r}"]


def check_isochrons() -> list[str]:
    import kii_story_data

    errors = []
    for t_s, want in ISOCHRON_EXPECT.items():
        got = kii_story_data.isochron_radius_km(t_s)
        if abs(got - want) > 0.5:
            errors.append(f"isochron {t_s} s: {got:.1f} km, want {want} ± 0.5")
    return errors


def check_slides(expect: int) -> list[str]:
    from pptx import Presentation

    errors = []
    for code in ("en", "ja"):
        path = HERE / f"tutorial-r4-umrs.{code}.pptx"
        count = len(Presentation(str(path)).slides) if path.exists() else -1
        if count != expect:
            errors.append(f"{path.name}: {count} slides, want {expect}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Tutorial deck integration checks")
    parser.add_argument("--slides", type=int, default=55)
    parser.add_argument("--no-pptx", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(HERE))
    checks = [check_comments, check_punct, check_isochrons]
    if not args.no_pptx:
        checks.append(lambda: check_slides(args.slides))
    errors = [err for check in checks for err in check()]
    for err in errors:
        print("FAIL", err)
    print("ALL DECK CHECKS PASSED" if not errors else f"{len(errors)} deck check(s) failed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
