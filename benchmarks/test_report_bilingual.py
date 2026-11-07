"""D3 — bilingual verification report tests.

Covers: string-table parallel coverage (EN and JA for every label),
gate-verdict semantics from JAPANESE_BENCHMARKS.md, and structure parity
between the EN and JA rendered reports.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from report import STRINGS, generate_report, _gate_verdicts  # noqa: E402


def _results(overall: float, ja: tuple[int, int] | None):
    """Build minimal results payload: one model, optional japanese row."""
    cats: dict[str, dict] = {"unit_test": {"total": 4, "passed": 4}}
    if ja is not None:
        cats["japanese"] = {"total": ja[1], "passed": ja[0]}
    return {
        "demo-model": {
            "timestamp": "t",
            "config": {"temperature": 0, "k": 1, "repeats": 1},
            "aggregate": {
                "pass_rate": overall,
                "avg_latency_ms": 10.0,
                "avg_tokens_per_second": 5.0,
                "avg_sandbox_ms": 0.0,
                "total_prompts": 4,
                "total_output_tokens": 100,
                "timeouts": 0,
                "per_category": cats,
            },
            "per_prompt": [],
        }
    }


class TestStringTable:
    def test_every_key_has_both_languages(self):
        for key, pair in STRINGS.items():
            assert set(pair) == {"en", "ja"}, f"missing lang for {key}"
            assert pair["en"].strip(), f"empty EN for {key}"
            assert pair["ja"].strip(), f"empty JA for {key}"

    def test_ja_is_not_english_echo(self):
        # A few labels must be real translations, not copy-pasted EN.
        assert STRINGS["overall"]["ja"] != STRINGS["overall"]["en"]
        assert STRINGS["gate"]["ja"] != STRINGS["gate"]["en"]


class TestGateVerdicts:
    def test_recommended_when_both_strong(self):
        v = _gate_verdicts(_results(100.0, (6, 6)))["demo-model"]
        assert v["kind"] == "recommended"

    def test_bilingual_limited_when_ja_fails(self):
        v = _gate_verdicts(_results(100.0, (0, 6)))["demo-model"]
        assert v["kind"] == "bilingual_limited"

    def test_not_recommended_when_overall_fails(self):
        v = _gate_verdicts(_results(10.0, (6, 6)))["demo-model"]
        assert v["kind"] == "not_recommended"

    def test_no_ja_row_fails_gate(self):
        v = _gate_verdicts(_results(100.0, None))["demo-model"]
        assert v["kind"] == "no_ja"


class TestRenderParity:
    def test_ja_renders_with_ja_headers(self):
        out = generate_report(_results(100.0, (6, 6)), "ja")
        assert "# LLM コード生成ベンチマークレポート" in out
        assert "## 日英ゲート" in out
        assert "推奨" in out

    def test_en_renders_with_en_headers(self):
        out = generate_report(_results(100.0, (6, 6)), "en")
        assert "# LLM Code Generation Benchmark Report" in out
        assert "## Bilingual Gate" in out
        assert "RECOMMENDED" in out

    def test_section_structure_parity(self):
        r = _results(100.0, (6, 6))
        en_heads = [l for l in generate_report(r, "en").splitlines() if l.startswith("## ")]
        ja_heads = [l for l in generate_report(r, "ja").splitlines() if l.startswith("## ")]
        # Same number of sections, same order depth (## headers).
        assert len(en_heads) == len(ja_heads)
        assert all(h.startswith("## ") for h in en_heads + ja_heads)
