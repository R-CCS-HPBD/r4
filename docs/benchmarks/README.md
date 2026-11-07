# Benchmarks — public surface | ベンチマーク公開面

> **対日第一言語**: 生成されるレポートは英語版 `report.md` と日本語版 `report.ja.md` の二つです。

## Reports | レポート

`benchmarks/report.py` writes two reports with the same content, one in each
language: `docs/benchmarks/report.md` (English) and
`docs/benchmarks/report.ja.md` (日本語). It reads the raw JSON that a bench
run writes into `benchmarks/results/`.

The measured reports and the raw run data of the maintainers' own runs are
kept in the development repository only; they are not part of the public R4
repository (2026-11-07: they name internal endpoints and run files). To get a
report, run the bench against your own endpoints (see
[benchmarks/README.md](../../benchmarks/README.md)) and regenerate it as shown
below.

The Bilingual Gate verdicts in the report follow the ship rule in
[JAPANESE_BENCHMARKS.md](../../benchmarks/JAPANESE_BENCHMARKS.md): a model
that fails the `japanese` category must not be the default.

## Regenerate after a bench run | 再生成方法

```bash
# From repo root, latest summary_<ts>.json in benchmarks/results/
uv run benchmarks/report.py --lang both --output docs/benchmarks/report

# Or pick a specific summary file
uv run benchmarks/report.py benchmarks/results/summary_<ts>.json \
  --lang both --output docs/benchmarks/report
```

## Guardrails | ガードレール

- **Data stays verbatim.** Metrics, model names, prompt ids, and error
  snippets in both reports are identical — only headings and labels
  translate. Do not hand-edit numbers out of one report into the other.
- **Datestamps matter.** Re-generation overwrites `report*.md`; keep the raw
  JSON of earlier runs if you need their history.
