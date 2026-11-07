#!/usr/bin/env python3
"""Benchmark throughput for GLM-5.2 and DeepSeek-V4-Pro endpoints.

Tests streaming TTFT, TPOT, decode tokens/s, and aggregate batch throughput
at concurrency levels 1, 4, 8, 16, 32 for each ready endpoint.

Usage:
    # From repo root, with R4 profile configured for ai4s
    python benchmarks/bench_endpoints.py

    # Custom concurrency levels
    python benchmarks/bench_endpoints.py --concurrency 1,4,16

    # Only test specific models
    python benchmarks/bench_endpoints.py --models GLM-5.2,DeepSeek-V4-Pro
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from gateway_client import GatewayClient, GatewayError, StreamTiming

# Standardized test prompt - moderate length to exercise prefill + decode
BENCH_PROMPT = (
    "Write a detailed technical explanation of how transformer attention "
    "mechanisms work. Include the mathematical formulation of scaled "
    "dot-product attention, multi-head attention, and explain why attention "
    "is O(n^2) in sequence length. Discuss FlashAttention and PagedAttention "
    "optimizations. Provide a code example in Python using PyTorch. Be thorough."
)


@dataclass
class BenchSample:
    """Single benchmark request result."""

    model: str
    endpoint_id: str
    concurrency: int
    repeat: int
    ok: bool
    error: str = ""
    ttft_ms: float = 0.0
    total_ms: float = 0.0
    output_tokens: int = 0
    prompt_tokens: int = 0
    decode_tps: float = 0.0
    tpot_ms: float = 0.0
    content_preview: str = ""


@dataclass
class ConcurrencySummary:
    """Aggregated results for one model at one concurrency level."""

    model: str
    endpoint_id: str
    concurrency: int
    samples: int
    failures: int
    avg_ttft_ms: float
    avg_tpot_ms: float
    avg_decode_tps: float
    aggregate_tps: float
    avg_output_tokens: float


def get_ready_endpoints(client: GatewayClient) -> list[dict[str, Any]]:
    """Get all ready GLM-5.2 and DeepSeek-V4-Pro endpoints."""
    # Use r4 CLI to get endpoint list with variants
    import subprocess

    try:
        result = subprocess.run(
            ["r4", "endpoint", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            print(f"Error: {result.stderr}", file=sys.stderr)
            return []
        data = json.loads(result.stdout)
    except Exception as e:
        print(f"Failed to get endpoints: {e}", file=sys.stderr)
        return []

    endpoints = data.get("endpoints", data) if isinstance(data, dict) else data
    if isinstance(endpoints, dict):
        endpoints = endpoints.get("endpoints", [])

    target_models = {"GLM-5.2", "DeepSeek-V4-Pro"}
    ready = []
    for ep in endpoints:
        if ep.get("status") != "ready":
            continue
        if ep.get("model") not in target_models:
            continue
        ready.append(
            {
                "instance_id": ep.get("instance_id") or ep.get("job_id"),
                "model": ep.get("model", "unknown"),
                "variant": ep.get("variant") or "baseline",
                "url": ep.get("endpoint_url") or ep.get("service_url"),
            }
        )
    return ready


def run_stream_sample(
    client: GatewayClient,
    model: str,
    endpoint_id: str,
    concurrency: int,
    repeat: int,
    max_tokens: int,
    temperature: float,
) -> BenchSample:
    """Run a single streaming chat completion and measure timing."""
    try:
        timing, _usage = client.chat_stream(
            prompt=BENCH_PROMPT,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return BenchSample(
            model=model,
            endpoint_id=endpoint_id,
            concurrency=concurrency,
            repeat=repeat,
            ok=True,
            ttft_ms=timing.time_to_first_token_ms,
            total_ms=timing.total_time_ms,
            output_tokens=timing.output_tokens,
            prompt_tokens=timing.prompt_tokens,
            decode_tps=timing.tokens_per_second,
            tpot_ms=timing.time_per_output_token_ms,
            content_preview=timing.content[:100],
        )
    except GatewayError as exc:
        return BenchSample(
            model=model,
            endpoint_id=endpoint_id,
            concurrency=concurrency,
            repeat=repeat,
            ok=False,
            error=str(exc),
        )


def run_concurrency_level(
    client: GatewayClient,
    model: str,
    endpoint_id: str,
    concurrency: int,
    repeats: int,
    max_tokens: int,
    temperature: float,
) -> list[BenchSample]:
    """Run multiple batches at the given concurrency level."""
    all_samples: list[BenchSample] = []

    for repeat in range(repeats):
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(
                    run_stream_sample,
                    client,
                    model,
                    endpoint_id,
                    concurrency,
                    repeat,
                    max_tokens,
                    temperature,
                )
                for _ in range(concurrency)
            ]
            samples = [f.result() for f in as_completed(futures)]

        # Calculate aggregate throughput for this batch
        ok_samples = [s for s in samples if s.ok]
        if ok_samples:
            batch_start = min(s.ttft_ms for s in ok_samples)
            batch_end = max(s.total_ms for s in ok_samples)
            batch_duration_s = (batch_end - batch_start) / 1000
            total_tokens = sum(s.output_tokens for s in ok_samples)
            agg_tps = total_tokens / batch_duration_s if batch_duration_s > 0 else 0
        else:
            agg_tps = 0

        for s in samples:
            s._aggregate_tps = agg_tps  # type: ignore[attr-defined]
        all_samples.extend(samples)

    return all_samples


def summarize_concurrency(
    samples: list[BenchSample],
    model: str,
    endpoint_id: str,
    concurrency: int,
) -> ConcurrencySummary:
    """Aggregate samples into a summary."""
    ok_samples = [s for s in samples if s.ok]
    failures = len(samples) - len(ok_samples)

    if not ok_samples:
        return ConcurrencySummary(
            model=model,
            endpoint_id=endpoint_id,
            concurrency=concurrency,
            samples=len(samples),
            failures=failures,
            avg_ttft_ms=0,
            avg_tpot_ms=0,
            avg_decode_tps=0,
            aggregate_tps=0,
            avg_output_tokens=0,
        )

    # Aggregate throughput: sum of all output tokens / wall-clock time
    total_tokens = sum(s.output_tokens for s in ok_samples)
    max_latency_s = max(s.total_ms for s in ok_samples) / 1000
    aggregate_tps = total_tokens / max_latency_s if max_latency_s > 0 else 0

    return ConcurrencySummary(
        model=model,
        endpoint_id=endpoint_id,
        concurrency=concurrency,
        samples=len(samples),
        failures=failures,
        avg_ttft_ms=statistics.mean(s.ttft_ms for s in ok_samples),
        avg_tpot_ms=statistics.mean(s.tpot_ms for s in ok_samples),
        avg_decode_tps=statistics.mean(s.decode_tps for s in ok_samples),
        aggregate_tps=aggregate_tps,
        avg_output_tokens=statistics.mean(s.output_tokens for s in ok_samples),
    )


def print_results_table(
    summaries: list[ConcurrencySummary],
    concurrencies: list[int],
) -> None:
    """Print a formatted comparison table."""
    print(f"\n{'=' * 100}")
    print("BENCHMARK RESULTS: Decode Throughput (tokens/s)")
    print(f"{'=' * 100}\n")

    # Header
    header = f"{'Model':<18s} {'Variant':<20s} {'EP ID':<8s}"
    for c in concurrencies:
        header += f" | c={c:<3d}"
    print(header)
    print("-" * len(header))

    # Group by model
    by_model: dict[str, list[ConcurrencySummary]] = {}
    for s in summaries:
        by_model.setdefault(s.model, []).append(s)

    for model_name in sorted(by_model.keys()):
        model_summaries = by_model[model_name]
        # Sort by variant
        model_summaries.sort(key=lambda s: s.endpoint_id)
        for summary in model_summaries:
            row = f"{summary.model:<18s} {'':20s} {summary.endpoint_id:<8s}"
            for c in concurrencies:
                if summary.concurrency == c:
                    row += f" | {summary.avg_decode_tps:>5.1f}"
                # We print each concurrency separately
            print(row)

    # Print detailed table with all metrics
    print(f"\n{'=' * 100}")
    print("DETAILED METRICS")
    print(f"{'=' * 100}\n")

    for summary in summaries:
        print(f"  Model: {summary.model} (EP {summary.endpoint_id})")
        print(f"  Concurrency: {summary.concurrency}")
        print(f"  Samples: {summary.samples} (failures: {summary.failures})")
        print(f"  Avg TTFT: {summary.avg_ttft_ms:.0f}ms")
        print(f"  Avg TPOT: {summary.avg_tpot_ms:.1f}ms")
        print(f"  Avg Decode tps: {summary.avg_decode_tps:.1f} tok/s")
        print(f"  Aggregate tps: {summary.aggregate_tps:.1f} tok/s")
        print(f"  Avg Output tokens: {summary.avg_output_tokens:.0f}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark GLM-5.2 and DeepSeek-V4-Pro endpoints"
    )
    parser.add_argument(
        "--concurrency",
        default="1,4,8,16,32",
        help="Comma-separated concurrency levels (default: 1,4,8,16,32)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=256,
        help="Max output tokens per request (default: 256)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Number of batches per concurrency level (default: 3)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="Sampling temperature (default: 0.7)",
    )
    parser.add_argument(
        "--models",
        default="GLM-5.2,DeepSeek-V4-Pro",
        help="Comma-separated model names to test",
    )
    parser.add_argument(
        "--output",
        default="benchmarks/results/bench_report.json",
        help="Output JSON file",
    )
    parser.add_argument(
        "--gateway-url",
        default=None,
        help="Gateway URL (default: from R4 profile)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="HTTP timeout in seconds (default: 600)",
    )
    args = parser.parse_args()

    concurrencies = [int(c.strip()) for c in args.concurrency.split(",") if c.strip()]
    target_models = {m.strip() for m in args.models.split(",") if m.strip()}

    # Initialize gateway client
    client = GatewayClient(
        gateway_url=args.gateway_url,
        timeout=args.timeout,
    )

    print(f"Gateway: {client.base_url}")
    print(f"Models: {', '.join(sorted(target_models))}")
    print(f"Concurrency: {concurrencies}")
    print(f"Max tokens: {args.max_tokens}")
    print(f"Repeats: {args.repeats}")
    print(f"Temperature: {args.temperature}")
    print()

    # Get ready endpoints
    endpoints = get_ready_endpoints(client)
    if not endpoints:
        print("No ready GLM-5.2 or DeepSeek-V4-Pro endpoints found.", file=sys.stderr)
        sys.exit(1)

    # Filter by target models
    endpoints = [ep for ep in endpoints if ep["model"] in target_models]
    if not endpoints:
        print(f"No ready endpoints found for models: {target_models}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(endpoints)} ready endpoint(s):")
    for ep in endpoints:
        print(
            f"  - ID={ep['instance_id']:>6} | {ep['model']:<18s} | "
            f"variant={ep['variant']:<20s} | url={ep['url']}"
        )
    print()

    # Run benchmarks
    all_samples: list[BenchSample] = []
    all_summaries: list[ConcurrencySummary] = []

    for endpoint in endpoints:
        model = endpoint["model"]
        endpoint_id = endpoint["instance_id"]

        print(f"\n{'─' * 80}")
        print(f"Benchmarking: {model} (EP {endpoint_id})")
        print(f"{'─' * 80}")

        for concurrency in concurrencies:
            print(
                f"\n  c={concurrency}, max_tokens={args.max_tokens}, "
                f"repeats={args.repeats}..."
            )

            samples = run_concurrency_level(
                client=client,
                model=model,
                endpoint_id=endpoint_id,
                concurrency=concurrency,
                repeats=args.repeats,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
            )

            summary = summarize_concurrency(
                samples=samples,
                model=model,
                endpoint_id=endpoint_id,
                concurrency=concurrency,
            )

            all_samples.extend(samples)
            all_summaries.append(summary)

            print(f"  ┌──────────────────────────────────────────────────┐")
            print(f"  │ Avg TTFT:           {summary.avg_ttft_ms:>8.0f}ms               │")
            print(f"  │ Avg TPOT:           {summary.avg_tpot_ms:>8.1f}ms               │")
            print(f"  │ Avg Decode tps:     {summary.avg_decode_tps:>8.1f} tok/s          │")
            print(f"  │ Aggregate tps:      {summary.aggregate_tps:>8.1f} tok/s          │")
            print(f"  │ Avg Output tokens:  {summary.avg_output_tokens:>8.0f}               │")
            print(f"  │ Failures:           {summary.failures:>8d}/{summary.samples:<7d}│")
            print(f"  └──────────────────────────────────────────────────┘")

    # Print results table
    print_results_table(all_summaries, concurrencies)

    # Save results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_data = {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": {
            "models": sorted(target_models),
            "concurrency": concurrencies,
            "max_tokens": args.max_tokens,
            "repeats": args.repeats,
            "temperature": args.temperature,
        },
        "endpoints": [
            {
                "instance_id": ep["instance_id"],
                "model": ep["model"],
                "variant": ep["variant"],
                "url": ep["url"],
            }
            for ep in endpoints
        ],
        "summaries": [asdict(s) for s in all_summaries],
        "samples": [asdict(s) for s in all_samples],
    }

    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
