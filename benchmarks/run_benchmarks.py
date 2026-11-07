#!/usr/bin/env python3
"""
Main benchmark runner.

Executes code generation benchmarks against R4 Gateway endpoints and
collects correctness, performance, and quality metrics.

Usage:
    python run_benchmarks.py                          # All models, all categories
    python run_benchmarks.py --models DeepSeek-V4-Pro,GLM-5.1
    python run_benchmarks.py --categories unit_test,algorithm
    python run_benchmarks.py --single-shot --repeats 3
    python run_benchmarks.py --k 3 --temperature 0.7
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure we can import from benchmarks package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import benchmarks modules
sys.path.insert(0, str(Path(__file__).parent))
from evaluator import (
    AggregateResult,
    EvalResult,
    aggregate_results,
    evaluate_c_prompt,
    evaluate_nlp_prompt,
    evaluate_python_prompt,
)
from gateway_client import GatewayClient, GatewayError
from prompts.algorithm import ALGORITHM_PROMPTS
from prompts.code_synthesis import CODE_SYNTHESIS_PROMPTS
from prompts.japanese import JAPANESE_PROMPTS
from prompts.numerical import NUMERICAL_PROMPTS
from prompts.optimization import OPTIMIZATION_PROMPTS
from prompts.parallel import PARALLEL_PROMPTS
from prompts.profiling import PROFILING_PROMPTS

# Import prompt datasets
from prompts.unit_test import UNIT_TEST_PROMPTS

# ── Model configuration ──────────────────────────────────────────

MODELS = {
    "DeepSeek-V4-Pro": {
        "name": "DeepSeek-V4-Pro",
        "max_tokens": 4096,
        "temperature": 0.0,
    },
    "GLM-5.1": {
        "name": "GLM-5.1",
        "max_tokens": 4096,
        "temperature": 0.0,
    },
    "GLM-5.2": {
        "name": "GLM-5.2",
        "max_tokens": 4096,
        "temperature": 0.0,
    },
    "Kimi-K2.6": {
        "name": "Kimi-K2.6",
        "max_tokens": 4096,
        "temperature": 0.0,
    },
    "gemma-4-31B-it": {
        "name": "gemma-4-31B-it",
        "max_tokens": 4096,
        "temperature": 0.0,
    },
    "Claude-Opus": {
        "name": "Claude-Opus",
        "max_tokens": 4096,
        "temperature": 0.0,
        "provider": "public",  # not yet configured
        "not_ready": True,
    },
}

CATEGORY_MAP = {
    "unit_test": UNIT_TEST_PROMPTS,
    "algorithm": ALGORITHM_PROMPTS,
    "code_synthesis": CODE_SYNTHESIS_PROMPTS,
    "optimization": OPTIMIZATION_PROMPTS,
    "parallel": PARALLEL_PROMPTS,
    "numerical": NUMERICAL_PROMPTS,
    "profiling": PROFILING_PROMPTS,
    "japanese": JAPANESE_PROMPTS,
}

PYTHON_CATEGORIES = {
    "unit_test",
    "algorithm",
    "code_synthesis",
    "optimization",
    "numerical",
    "profiling",
}
C_CATEGORIES = {"parallel"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LLM Code Generation Benchmark Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_benchmarks.py
  python run_benchmarks.py --models DeepSeek-V4-Pro,GLM-5.1
  python run_benchmarks.py --categories unit_test,algorithm
  python run_benchmarks.py --single-shot --repeats 3
  python run_benchmarks.py --k 5 --temperature 0.8
""",
    )
    parser.add_argument(
        "--models",
        type=str,
        default="DeepSeek-V4-Pro,GLM-5.1,Kimi-K2.6,gemma-4-31B-it",
        help="Comma-separated model names to benchmark",
    )
    parser.add_argument(
        "--categories",
        type=str,
        default="unit_test,algorithm,code_synthesis,optimization,numerical,profiling",
        help="Comma-separated categories (python-only by default; add 'parallel' for C/HPC)",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=1,
        help="Number of samples per prompt for pass@k (default: 1 = single-shot)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature (default: 0.0 = deterministic)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="Max output tokens per generation (default: 4096)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of times to repeat each prompt (for statistical rigor)",
    )
    parser.add_argument(
        "--single-shot",
        action="store_true",
        help="Shortcut for --k 1 --temperature 0.0",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory for results (default: results/)",
    )
    parser.add_argument(
        "--gateway-url",
        type=str,
        default=None,
        help="Gateway URL (default: from env R4_GATEWAY_URL or r4 config)",
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Skip parallel/HPC prompts (C/C++/MPI/CUDA)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which prompts would run without actually running them",
    )
    return parser


def _resolve_models(model_names: list[str]) -> list[dict]:
    """Map model names to run configs; unknown names pass through verbatim."""
    models_to_run = []
    for name in model_names:
        if name in MODELS:
            cfg = MODELS[name]
            if cfg.get("not_ready"):
                print(f"[SKIP] {name}: not ready (needs provider config)")
                continue
            models_to_run.append(cfg)
        else:
            # Unknown name → pass through as-is (platform-served model id).
            # Lets staged models (swallow-7b, plamo-8b, Qwen3.6-…) run without
            # hard-coding them in MODELS.
            models_to_run.append({"name": name, "max_tokens": 4096, "temperature": 0.0})
    if not models_to_run:
        print("[ERROR] No models to benchmark")
        sys.exit(1)
    return models_to_run


def _resolve_prompts(cat_names: list[str]) -> list[dict]:
    """Expand category names into the flat prompt list."""
    all_prompts = []
    for cat in cat_names:
        if cat in CATEGORY_MAP:
            all_prompts.extend(CATEGORY_MAP[cat])
        else:
            print(f"[WARN] Unknown category '{cat}', skipping")
    if not all_prompts:
        print("[ERROR] No prompts to benchmark")
        sys.exit(1)
    return all_prompts


def _evaluate_one(
    client, model_name: str, prompt_def: dict, temperature: float, max_tokens: int
) -> "EvalResult":
    """Run one sample of one prompt and evaluate it."""
    response, timing = client.chat(
        prompt=prompt_def["prompt"],
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    lang = prompt_def.get("language", "python")
    if lang in ("python", "py"):
        return evaluate_python_prompt(prompt_def, response, timing, model_name)
    if lang in ("c", "cpp", "cuda"):
        return evaluate_c_prompt(prompt_def, response, timing, model_name)
    if lang == "plain":
        return evaluate_nlp_prompt(prompt_def, response, timing, model_name)
    print("⚠ unsupported language")
    return EvalResult(
        prompt_id=prompt_def["id"],
        model=model_name,
        category=prompt_def["category"],
        error="unsupported language",
    )


def _print_outcome(result: "EvalResult") -> None:
    if result.passed:
        print(f"✓ PASS ({result.latency_ms:.0f}ms, {result.tokens_per_second:.1f} tok/s)")
    elif result.error:
        print(f"✗ FAIL: {result.error[:80]}")
    else:
        print("✗ FAIL (tests failed)")


def _run_one_sample(client, model_name: str, prompt_def: dict, args) -> tuple["EvalResult", bool]:
    """Run+evaluate one sample; returns (result, interrupted)."""
    pid, cat = prompt_def["id"], prompt_def["category"]
    try:
        result = _evaluate_one(
            client,
            model_name,
            prompt_def,
            temperature=(0.0 if args.k == 1 else args.temperature),
            max_tokens=args.max_tokens,
        )
        _print_outcome(result)
        return result, False
    except GatewayError as e:
        print(f"✗ GATEWAY ERROR: {e}")
        return EvalResult(
            prompt_id=pid, model=model_name, category=cat, error=str(e), latency_ms=e.elapsed_ms
        ), False
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED] Saving partial results...")
        return EvalResult(prompt_id=pid, model=model_name, category=cat, error="interrupted"), True
    except Exception as e:
        print(f"✗ UNEXPECTED: {e}")
        return EvalResult(prompt_id=pid, model=model_name, category=cat, error=str(e)), False


def _run_model_benchmark(
    client, model_cfg: dict, all_prompts: list[dict], args
) -> list["EvalResult"]:
    """Run every (prompt, repeat, sample) triple for one model."""
    model_name = model_cfg["name"]
    results: list[EvalResult] = []
    total = len(all_prompts) * args.repeats * args.k
    progress = 0
    interrupted = False

    for repeat in range(args.repeats):
        for prompt_def in all_prompts:
            for sample_idx in range(args.k):
                progress += 1
                pid, cat = prompt_def["id"], prompt_def["category"]
                print(
                    f"  [{progress}/{total}] {pid} ({cat}, sample {sample_idx + 1}/{args.k})",
                    end=" ",
                    flush=True,
                )
                result, interrupted = _run_one_sample(client, model_name, prompt_def, args)
                if result.error != "interrupted":
                    results.append(result)
                if interrupted:
                    break
            if interrupted:
                break
        if interrupted:
            break
    return results


def _save_model_results(output_dir: Path, model_name: str, agg, args, timestamp: str) -> None:
    """Persist one model's raw results JSON."""
    model_file = output_dir / f"{model_name}_{timestamp}.json"
    with open(model_file, "w") as f:
        json.dump(
            {
                "model": model_name,
                "timestamp": timestamp,
                "config": {
                    "temperature": args.temperature,
                    "k": args.k,
                    "repeats": args.repeats,
                    "max_tokens": args.max_tokens,
                },
                "aggregate": {
                    "total_prompts": agg.total_prompts,
                    "passed_prompts": agg.passed_prompts,
                    "pass_rate": round(agg.pass_rate, 2),
                    "avg_latency_ms": round(agg.avg_latency_ms, 2),
                    "avg_tokens_per_second": round(agg.avg_tokens_per_second, 2),
                    "avg_sandbox_ms": round(agg.avg_sandbox_ms, 2),
                    "total_output_tokens": agg.total_output_tokens,
                    "total_prompt_tokens": agg.total_prompt_tokens,
                    "compile_failures": agg.compile_failures,
                    "timeouts": agg.timeouts,
                    "per_category": agg.per_category,
                },
                "per_prompt": agg.per_prompt,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\n  ✓ Results saved: {model_file}")


def _print_summary(
    all_model_results: dict, output_dir: Path, args, cat_names: list[str], timestamp: str
) -> None:
    """Print cross-model summary + per-category breakdown; save summary JSON."""
    print(f"\n{'=' * 60}")
    print("  BENCHMARK SUMMARY")
    print(f"{'=' * 60}")
    print(f"{'Model':<25} {'Pass Rate':>10} {'Avg Lat':>10} {'Avg TPS':>10} {'Timeouts':>10}")
    print(f"{'─' * 65}")
    for name, agg in all_model_results.items():
        print(
            f"{name:<25} {agg.pass_rate:>8.1f}% {agg.avg_latency_ms:>8.0f}ms {agg.avg_tokens_per_second:>8.1f} {agg.timeouts:>10}"
        )
    print(f"{'=' * 60}")

    print(f"\n{'─' * 60}")
    print("  PER-CATEGORY BREAKDOWN")
    print(f"{'─' * 60}")
    for name, agg in all_model_results.items():
        print(f"\n  {name}:")
        for cat, stats in agg.per_category.items():
            cat_pass = stats["passed"] / stats["total"] * 100 if stats["total"] > 0 else 0
            print(f"    {cat:<20} {stats['passed']}/{stats['total']} ({cat_pass:.0f}%)")

    summary_file = output_dir / f"summary_{timestamp}.json"
    with open(summary_file, "w") as f:
        json.dump(
            {
                "timestamp": timestamp,
                "config": {
                    "temperature": args.temperature,
                    "k": args.k,
                    "repeats": args.repeats,
                    "max_tokens": args.max_tokens,
                    "categories": cat_names,
                },
                "models": {
                    name: {
                        "total_prompts": agg.total_prompts,
                        "passed_prompts": agg.passed_prompts,
                        "pass_rate": round(agg.pass_rate, 2),
                        "avg_latency_ms": round(agg.avg_latency_ms, 2),
                        "avg_tokens_per_second": round(agg.avg_tokens_per_second, 2),
                        "avg_sandbox_ms": round(agg.avg_sandbox_ms, 2),
                        "per_category": agg.per_category,
                    }
                    for name, agg in all_model_results.items()
                },
            },
            f,
            indent=2,
        )
    print(f"\n✓ Summary saved: {summary_file}")


def _normalize_args(args) -> None:
    """Apply --single-shot shorthand in-place."""
    if args.single_shot:
        args.k = 1
        args.temperature = 0.0


def _resolve_categories(args) -> list[str]:
    cat_names = [c.strip() for c in args.categories.split(",") if c.strip()]
    if args.no_parallel and "parallel" in cat_names:
        cat_names.remove("parallel")
    return cat_names


def _print_banner(models_to_run: list[dict], cat_names: list[str], prompt_count: int, args) -> None:
    print(f"\n{'=' * 60}")
    print("  LLM Code Generation Benchmark")
    print(f"{'=' * 60}")
    print(f"  Models:    {', '.join(m['name'] for m in models_to_run)}")
    print(f"  Categories: {', '.join(cat_names)}")
    print(f"  Prompts:   {prompt_count}")
    print(f"  Samples:   {args.k} per prompt")
    print(f"  Repeats:   {args.repeats}")
    print(f"  Temp:      {args.temperature}")
    print(f"  Date:      {datetime.now(timezone.utc).isoformat()}")
    print(f"{'=' * 60}\n")


def _open_client(args) -> "GatewayClient":
    """Build the gateway client or exit with a fatal message."""
    try:
        client = GatewayClient(gateway_url=args.gateway_url, timeout=600)
        print(f"✓ Gateway client initialized: {client.base_url}")
        return client
    except Exception as e:
        print(f"[FATAL] Failed to initialize gateway client: {e}")
        sys.exit(1)


def _run_all_models(
    client,
    models_to_run: list[dict],
    all_prompts: list[dict],
    args,
    output_dir: Path,
    timestamp: str,
) -> dict:
    """Run the benchmark across models; return {name: AggregateResult}."""
    all_model_results: dict[str, AggregateResult] = {}
    for model_cfg in models_to_run:
        model_name = model_cfg["name"]
        print(f"\n{'─' * 60}")
        print(f"  Model: {model_name}")
        print(f"{'─' * 60}")
        model_eval_results = _run_model_benchmark(client, model_cfg, all_prompts, args)
        agg = aggregate_results(model_eval_results, model_name)
        all_model_results[model_name] = agg
        _save_model_results(output_dir, model_name, agg, args, timestamp)
    return all_model_results


def main():
    args = _build_parser().parse_args()
    _normalize_args(args)

    model_names = [m.strip() for m in args.models.split(",") if m.strip()]
    models_to_run = _resolve_models(model_names)
    cat_names = _resolve_categories(args)
    all_prompts = _resolve_prompts(cat_names)

    _print_banner(models_to_run, cat_names, len(all_prompts), args)

    if args.dry_run:
        print("[DRY RUN] Prompt list:")
        for p in all_prompts:
            print(f"  [{p['category']}] {p['id']} ({p['language']})")
        return

    client = _open_client(args)

    output_dir = Path(args.output_dir or str(Path(__file__).parent / "results"))
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    all_model_results = _run_all_models(
        client, models_to_run, all_prompts, args, output_dir, timestamp
    )
    _print_summary(all_model_results, output_dir, args, cat_names, timestamp)


if __name__ == "__main__":
    main()
