#!/usr/bin/env python3
"""
Benchmark report generator.

Reads raw JSON results from the results/ directory and produces:
- Consolidated comparison tables
- Per-category breakdowns
- Statistical summaries

Usage:
    python report.py                      # Read all results in results/
    python report.py --input results/     # Specify input directory
    python report.py --output report.md   # Write Markdown report
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_results(results_dir: Path) -> dict[str, dict[str, Any]]:
    """Load all benchmark result files from a directory.

    Returns dict of model_name -> latest result data.
    """
    results: dict[str, dict[str, Any]] = {}

    for f in sorted(results_dir.glob("*.json"), reverse=True):
        if f.name.startswith("summary_"):
            continue

        try:
            data = json.loads(f.read_text())
            model = data.get("model", f.stem)
            if model not in results:
                results[model] = data
        except (json.JSONDecodeError, KeyError):
            continue

    return results


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Format a markdown table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    lines = []
    header_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
    sep_line = "|" + "|".join("-" * (w + 2) for w in col_widths) + "|"

    lines.append(header_line)
    lines.append(sep_line)
    for row in rows:
        lines.append(
            "| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"
        )

    return "\n".join(lines)


# ── Bilingual label table (D3 — EN+JA dual-authored reports) ──────────────

STRINGS: dict[str, dict[str, str]] = {
    "title": {
        "en": "LLM Code Generation Benchmark Report",
        "ja": "LLM コード生成ベンチマークレポート",
    },
    "date": {"en": "Date", "ja": "実行日時"},
    "config": {"en": "Config", "ja": "設定"},
    "overall": {"en": "Overall Summary", "ja": "全体サマリ"},
    "per_category": {"en": "Per-Category Breakdown", "ja": "カテゴリ別内訳"},
    "token_efficiency": {"en": "Token Efficiency", "ja": "トークン効率"},
    "per_prompt": {"en": "Per-Prompt Details", "ja": "プロンプト別詳細"},
    "notes": {"en": "Notes", "ja": "注意事項"},
    "gate": {"en": "Bilingual Gate", "ja": "日英ゲート"},
    "model": {"en": "Model", "ja": "モデル"},
    "pass_rate": {"en": "Pass Rate", "ja": "合格率"},
    "avg_latency": {"en": "Avg Latency", "ja": "平均レイテンシ"},
    "avg_tps": {"en": "Avg TPS", "ja": "平均TPS"},
    "avg_sandbox": {"en": "Avg Sandbox", "ja": "平均サンドボックス"},
    "timeouts": {"en": "Timeouts", "ja": "タイムアウト"},
    "passed_total": {"en": "Passed/Total", "ja": "合格/全体"},
    "total_prompts": {"en": "Total Prompts", "ja": "総プロンプト数"},
    "total_out_tok": {"en": "Total Output Tokens", "ja": "総出力トークン"},
    "avg_tok_prompt": {"en": "Avg Tokens/Prompt", "ja": "トークン/プロンプト"},
    "passed": {"en": "Passed", "ja": "合格"},
    "latency": {"en": "Latency", "ja": "レイテンシ"},
    "tps": {"en": "TPS", "ja": "TPS"},
    "error": {"en": "Error", "ja": "エラー"},
    "verdict": {"en": "Verdict", "ja": "判定"},
    "gate_rule": {
        "en": "Ship rule: a model that fails the `japanese` category must not be the default — it is marked AVAILABLE (bilingual limited).",
        "ja": "出荷ルール: `japanese` カテゴリに不合格のモデルはデフォルトにできません（利用可・日英限定と表示）。",
    },
    "v_recommended": {"en": "RECOMMENDED", "ja": "推奨"},
    "v_limited": {"en": "AVAILABLE (bilingual limited)", "ja": "利用可（日英限定）"},
    "v_not": {"en": "NOT RECOMMENDED", "ja": "非推奨"},
    "v_no_ja": {"en": "NO JA ROW", "ja": "JA行なし"},
    "note_k": {
        "en": "**pass@k**: Using k=1 means single-shot deterministic evaluation.",
        "ja": "**pass@k**: k=1 は1-shot決定的評価を意味します。",
    },
    "note_passk": {
        "en": "For pass@k > 1, re-run with `--k 5 --temperature 0.7`.",
        "ja": "pass@k > 1 の場合は `--k 5 --temperature 0.7` で再実行。",
    },
    "note_parallel": {
        "en": "**Parallel/HPC prompts**: C/MPI/CUDA prompts are skipped unless `--categories ...,parallel` is specified and compilers are available.",
        "ja": "**並列/HPC プロンプト**: `--categories ...,parallel` 指定かつコンパイラ利用時のみ C/MPI/CUDA を実行。",
    },
    "note_opus": {
        "en": "**Claude Opus**: Requires a public provider endpoint configured in r4.",
        "ja": "**Claude Opus**: r4 に公開プロバイダエンドポイントの設定が必要です。",
    },
}


def tr(key: str, lang: str) -> str:
    return STRINGS[key][lang]


def _gate_verdicts(results: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Apply the JA bilingual ship rule to each model's aggregate stats."""
    verdicts = {}
    for name, data in results.items():
        agg = data.get("aggregate", {})
        cats = agg.get("per_category", {})
        ja = cats.get("japanese")
        overall = agg.get("pass_rate", 0.0) or 0.0
        if ja is None:
            verdicts[name] = {"key": "v_no_ja", "kind": "no_ja"}
            continue
        ja_rate = ja["passed"] / ja["total"] * 100 if ja["total"] else 0.0
        if overall < 50.0:
            verdicts[name] = {"key": "v_not", "kind": "not_recommended"}
        elif ja_rate < 50.0:
            verdicts[name] = {"key": "v_limited", "kind": "bilingual_limited"}
        else:
            verdicts[name] = {"key": "v_recommended", "kind": "recommended"}
    return verdicts


def _sec_header(results: dict[str, dict[str, Any]], lang: str) -> list[str]:
    """Title + run metadata block."""
    lines = [f"# {tr('title', lang)}", ""]
    if not results:
        return lines
    first = next(iter(results.values()))
    lines.append(f"**{tr('date', lang)}**: {first.get('timestamp', 'unknown')}")
    lines.append(
        f"**{tr('config', lang)}**: temp={first.get('config', {}).get('temperature', 0)}, "
        f"k={first.get('config', {}).get('k', 1)}, "
        f"repeats={first.get('config', {}).get('repeats', 1)}"
    )
    lines.append("")
    return lines


def _sec_gate(results: dict[str, dict[str, Any]], lang: str) -> list[str]:
    """Bilingual gate verdict table."""
    verdicts = _gate_verdicts(results)
    lines = [f"## {tr('gate', lang)}", "", f"_{tr('gate_rule', lang)}_", ""]
    headers = [tr("model", lang), tr("verdict", lang)]
    rows = [[n, tr(v["key"], lang)] for n, v in sorted(verdicts.items())]
    lines.append(format_table(headers, rows))
    lines.append("")
    return lines


def _sec_overall(results: dict[str, dict[str, Any]], lang: str) -> list[str]:
    """Cross-model summary table."""
    lines = [f"## {tr('overall', lang)}", ""]
    headers = [
        tr("model", lang),
        tr("pass_rate", lang),
        tr("avg_latency", lang),
        tr("avg_tps", lang),
        tr("avg_sandbox", lang),
        tr("timeouts", lang),
    ]
    rows = []
    for model_name in sorted(results.keys()):
        agg = results[model_name].get("aggregate", {})
        rows.append(
            [
                model_name,
                f"{agg.get('pass_rate', 0):.1f}%",
                f"{agg.get('avg_latency_ms', 0):.0f}ms",
                f"{agg.get('avg_tokens_per_second', 0):.1f} tok/s",
                f"{agg.get('avg_sandbox_ms', 0):.0f}ms",
                str(agg.get("timeouts", 0)),
            ]
        )
    lines.append(format_table(headers, rows))
    lines.append("")
    return lines


def _sec_categories(results: dict[str, dict[str, Any]], lang: str) -> list[str]:
    """Per-category breakdown, one table/section per category."""
    lines = [f"## {tr('per_category', lang)}", ""]
    all_categories: set[str] = set()
    for data in results.values():
        all_categories.update(data.get("aggregate", {}).get("per_category", {}).keys())

    headers = [tr("model", lang), tr("passed_total", lang), tr("pass_rate", lang)]
    for cat in sorted(all_categories):
        lines.append(f"### {cat}")
        lines.append("")
        rows = []
        for model_name in sorted(results.keys()):
            cat_stats = (
                results[model_name].get("aggregate", {}).get("per_category", {}).get(cat, {})
            )
            total = cat_stats.get("total", 0)
            passed = cat_stats.get("passed", 0)
            rate = passed / total * 100 if total > 0 else 0
            rows.append([model_name, f"{passed}/{total}", f"{rate:.0f}%"])
        lines.append(format_table(headers, rows))
        lines.append("")
    return lines


def _sec_token_efficiency(results: dict[str, dict[str, Any]], lang: str) -> list[str]:
    """Token usage summary table."""
    lines = [f"## {tr('token_efficiency', lang)}", ""]
    headers = [
        tr("model", lang),
        tr("total_prompts", lang),
        tr("total_out_tok", lang),
        tr("avg_tok_prompt", lang),
    ]
    rows = []
    for model_name in sorted(results.keys()):
        agg = results[model_name].get("aggregate", {})
        total = agg.get("total_prompts", 0)
        out_tok = agg.get("total_output_tokens", 0)
        rows.append(
            [
                model_name,
                str(total),
                str(out_tok),
                f"{out_tok / total:.0f}" if total > 0 else "0",
            ]
        )
    lines.append(format_table(headers, rows))
    lines.append("")
    return lines


def _collect_prompt_ids(results: dict[str, dict[str, Any]]) -> list[str]:
    """All unique prompt ids in deterministic first-seen order."""
    seen: list[str] = []
    for data in results.values():
        for r in data.get("per_prompt", []):
            pid = r.get("prompt_id", "")
            if pid and pid not in seen:
                seen.append(pid)
    return seen


def _prompt_rows(results: dict[str, dict[str, Any]], pid: str) -> list[list[str]]:
    """Rows for one prompt id — first result per model."""
    rows = []
    for model_name in sorted(results.keys()):
        prompt_results = [
            r for r in results[model_name].get("per_prompt", []) if r.get("prompt_id") == pid
        ]
        if not prompt_results:
            continue
        r = prompt_results[0]
        error = r.get("error", "") or ""
        if len(error) > 60:
            error = error[:57] + "..."
        rows.append(
            [
                model_name,
                "✓" if r.get("passed") else "✗",
                f"{r.get('latency_ms', 0):.0f}ms",
                f"{r.get('tokens_per_second', 0):.1f}",
                error,
            ]
        )
    return rows


def _sec_per_prompt(results: dict[str, dict[str, Any]], lang: str) -> list[str]:
    """One table per prompt id, with per-model row roll-ups."""
    lines = [f"## {tr('per_prompt', lang)}", ""]
    headers = [
        tr("model", lang),
        tr("passed", lang),
        tr("latency", lang),
        tr("tps", lang),
        tr("error", lang),
    ]
    for pid in _collect_prompt_ids(results):
        lines.append(f"### {pid}")
        lines.append("")
        lines.append(format_table(headers, _prompt_rows(results, pid)))
        lines.append("")
    return lines


def _sec_notes(lang: str) -> list[str]:
    """Static notes block."""
    return [
        f"## {tr('notes', lang)}",
        "",
        f"- {tr('note_k', lang)}",
        f"  {tr('note_passk', lang)}",
        f"- {tr('note_parallel', lang)}",
        f"- {tr('note_opus', lang)}",
        "",
    ]


def generate_report(results: dict[str, dict[str, Any]], lang: str = "en") -> str:
    """Generate markdown report from benchmark results (EN or JA)."""
    lines = _sec_header(results, lang)
    lines += _sec_gate(results, lang)
    lines += _sec_overall(results, lang)
    lines += _sec_categories(results, lang)
    lines += _sec_token_efficiency(results, lang)
    lines += _sec_per_prompt(results, lang)
    lines += _sec_notes(lang)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Benchmark Report Generator (EN+JA)")
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Input directory with JSON result files (default: results/)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file for Markdown report (prints to stdout if not set)",
    )
    parser.add_argument(
        "--lang",
        choices=("en", "ja", "both"),
        default="en",
        help="Report language: en, ja, or both (writes .md and .ja.md siblings)",
    )
    args = parser.parse_args()

    results_dir = Path(args.input or str(Path(__file__).parent / "results"))
    if not results_dir.exists():
        print(f"[ERROR] Results directory not found: {results_dir}", file=sys.stderr)
        sys.exit(1)

    results = load_results(results_dir)
    if not results:
        print(f"[ERROR] No result files found in {results_dir}", file=sys.stderr)
        sys.exit(1)

    if args.lang == "both":
        base = Path(args.output) if args.output else results_dir / "report.md"
        base.parent.mkdir(parents=True, exist_ok=True)
        en_path = base.with_suffix(".md")
        ja_path = base.with_suffix(".ja.md")
        en_path.write_text(generate_report(results, "en"), encoding="utf-8")
        ja_path.write_text(generate_report(results, "ja"), encoding="utf-8")
        print(f"✓ EN report: {en_path}")
        print(f"✓ JA report: {ja_path}")
        return

    report = generate_report(results, args.lang)
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"✓ Report written to {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
