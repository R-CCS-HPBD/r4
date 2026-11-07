# Bilingual Quality Gate (JA⇄EN parity)

> Ship rule: a **recommended model must never regress in either language.**
> EN is the language of papers/code/APIs; JA is the language of comfort, trust,
> and adoption for researchers in Japan.

## Categories

| Category | What it measures | Tracks |
|---|---|---|
| `japanese` | JA task comprehension, translation fidelity (EN⇄JA), HPC/technical terminology extraction, formal register, error comprehension | marker-keyword hit rate on `expected_ja` keywords (≥2/3 pass) |
| Existing code benches | Function-level correctness under test code | unchanged |

## Prompt set (`benchmarks/prompts/japanese.py`)

| ID | Task | Language surface | Notes |
|---|---|---|---|
| `ja_01_slurm_exp` | Explain Slurm backfill | JA | Terminology retention |
| `ja_02_translate_tech_en2ja` | EN→JA translation of loop semantics | Mixed | Term quality |
| `ja_03_translate_tech_ja2en` | JA→EN translation of offline-inference rules | Mixed | Inverted |
| `ja_04_hpc_term_explain_en` | EN-only explanation of JA-coined concept | EN-side JA concept | Cross-bridge |
| `ja_05_formalize_email` | Register formalization | JA | Politeness, organization |
| `ja_06_error_code_hint` | Error message comprehension + hint | JSON | Pipeline-critical |

## Scoring

`evaluate_nlp_prompt` in `benchmarks/evaluator.py`:
- `passed` when ≥ 2/3 of a prompt's `expected_ja` **keyword groups** appear
- Each group is a list of synonyms (variants); a group scores when *any*
  variant appears — this prevents false-fails from natural JA phrasing
  (e.g. バックフィル vs literal "backfill")
- This is a smoke-quality gate, not a bench of deep semantics; background
  LLM-as-judge eval will replace it in later waves (already in findings lane)

## Usage

```bash
# Run just the JA category for one model
cd benchmarks
python run_benchmarks.py --models GLM-5.2 --categories japanese

# Bilingual-parity pair: run both languages' categories together
python run_benchmarks.py --models GLM-5.2 --categories unit_test,japanese

# Compare recommended candidates on both surfaces
python run_benchmarks.py --models GLM-5.2,DeepSeek-V4-Pro,Kimi-K2.6 --categories unit_test,japanese
```

## Gate semantics

1. **Don't register a recommendation that only passes EN.** If the same model
   also scores below threshold on `japanese`, it must not be the default —
   downgrade to "available, bilingual limited".
2. **Keep the marker keywords updated.** Review the corpus every quarter.
3. **llm-jp staging checklist** lives in `umrs/deploy/*/site*.yaml`
   (`llm-jp-4` entries); this gate confirms any new staging keeps EN quality.

## Runbook notes (2026-07-30, validated against a deployed gateway)

- **Token budget matters.** Reasoning models may consume most of the
  completion budget before answering; with `max_tokens` too low the first
  measured output can be empty. Verified: `ja_04_hpc_term_explain_en`
  returned 0 content at 4096 but passed cleanly at 8192
  (`Qwen3.6-35B-A3B-NVFP4`: 6/6 = 100%).
- **Run loop bug (fixed).** `if KeyboardInterrupt` referenced the exception
  class and aborted runs after the first prompt; now a real `interrupted`
  flag drives the break chain.
- **Profile resolution.** The bench client now prefers the *active* r4
  profile before the `ai4s`/`default` fallbacks, and unknown model names
  pass through verbatim so staged models work without a `MODELS` entry.
- **First green run:** 2026-07-30, `Qwen3.6-35B-A3B-NVFP4`, with its
  per-run and summary JSON written under `benchmarks/results/` (EN row
  pending once a DeepSeek/GLM candidate is re-staged). Result files are
  local run output and are not distributed with the repository.
