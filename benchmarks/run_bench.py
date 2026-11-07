#!/usr/bin/env python3
"""Benchmark throughput for GLM-5.2 and DeepSeek-V4-Pro endpoints.

Tests prefill (TTFT), decode (tokens/s), and end-to-end latency at
batch sizes 1, 4, 8, 16, 32 for each ready endpoint.

Usage:
    R4_GATEWAY_URL=http://localhost:8002 python benchmarks/run_bench.py
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BenchResult:
    endpoint_id: str
    model: str
    variant: str
    batch_size: int
    input_tokens: int
    output_tokens: int
    total_latency_s: float
    prefill_latency_s: float
    decode_tokens_s: float
    end_to_end_tokens_s: float
    errors: list[str] = field(default_factory=list)


def run_r4(args: list[str], timeout: int = 120) -> dict[str, Any]:
    """Run r4 CLI command and return parsed JSON."""
    cmd = ["r4"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or result.stdout.strip()}
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s"}
    except json.JSONDecodeError as e:
        return {"error": f"JSON decode error: {e}"}
    except Exception as e:
        return {"error": str(e)}


def get_ready_endpoints() -> list[dict[str, Any]]:
    """Get all ready endpoints with their variants."""
    data = run_r4(["endpoint", "list", "--json"])
    if "error" in data:
        print(f"Error getting endpoints: {data['error']}", file=sys.stderr)
        return []
    endpoints = data.get("endpoints", data) if isinstance(data, dict) else data
    if isinstance(endpoints, dict):
        endpoints = endpoints.get("endpoints", [])

    ready = []
    for ep in endpoints:
        if ep.get("status") == "ready":
            ready.append(
                {
                    "instance_id": ep.get("instance_id") or ep.get("job_id"),
                    "model": ep.get("model", "unknown"),
                    "variant": ep.get("variant") or "baseline",
                    "url": ep.get("endpoint_url") or ep.get("service_url"),
                }
            )
    return ready


def benchmark_single_request(
    endpoint_url: str,
    prompt: str,
    max_tokens: int,
    api_key: str,
    timeout: int = 120,
) -> dict[str, Any]:
    """Send a single chat completion request and measure timing."""
    # Use curl to have fine-grained timing control
    import urllib.request

    payload = {
        "model": "test",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": False,
        "temperature": 0.7,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    start_time = time.monotonic()
    try:
        req = urllib.request.Request(
            endpoint_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            end_time = time.monotonic()

            result = json.loads(body)
            usage = result.get("usage", {})

            return {
                "success": True,
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "total_latency_s": end_time - start_time,
                "response_preview": result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")[:100],
            }
    except Exception as e:
        end_time = time.monotonic()
        return {
            "success": False,
            "error": str(e),
            "total_latency_s": end_time - start_time,
        }


def benchmark_batch(
    endpoint: dict[str, Any],
    prompt: str,
    batch_size: int,
    max_tokens: int,
    api_key: str,
) -> BenchResult:
    """Benchmark a batch of concurrent requests."""
    endpoint_url = endpoint["url"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
        futures = [
            executor.submit(
                benchmark_single_request,
                endpoint_url,
                prompt,
                max_tokens,
                api_key,
            )
            for _ in range(batch_size)
        ]
        results = [
            future.result() for future in concurrent.futures.as_completed(futures)
        ]

    successful = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    if not successful:
        return BenchResult(
            endpoint_id=endpoint["instance_id"],
            model=endpoint["model"],
            variant=endpoint["variant"],
            batch_size=batch_size,
            input_tokens=0,
            output_tokens=0,
            total_latency_s=0,
            prefill_latency_s=0,
            decode_tokens_s=0,
            end_to_end_tokens_s=0,
            errors=[r.get("error", "unknown") for r in failed],
        )

    # Aggregate metrics
    total_output_tokens = sum(r["output_tokens"] for r in successful)
    total_input_tokens = sum(r["input_tokens"] for r in successful)
    latencies = [r["total_latency_s"] for r in successful]
    max_latency = max(latencies)

    # Decode throughput = total output tokens / max latency (batch completes
    # when slowest request finishes)
    decode_tokens_s = total_output_tokens / max_latency if max_latency > 0 else 0

    # End-to-end = total tokens / max latency
    total_tokens = total_output_tokens + total_input_tokens
    end_to_end_tokens_s = total_tokens / max_latency if max_latency > 0 else 0

    # Prefill latency approximation: time to first token.
    # Since we use non-streaming, we can't measure TTFT directly.
    # Use min latency as a rough proxy for prefill.
    prefill_latency_s = min(latencies)

    return BenchResult(
        endpoint_id=endpoint["instance_id"],
        model=endpoint["model"],
        variant=endpoint["variant"],
        batch_size=batch_size,
        input_tokens=total_input_tokens // len(successful),
        output_tokens=total_output_tokens // len(successful),
        total_latency_s=statistics.mean(latencies),
        prefill_latency_s=prefill_latency_s,
        decode_tokens_s=decode_tokens_s,
        end_to_end_tokens_s=end_to_end_tokens_s,
        errors=[r.get("error", "unknown") for r in failed],
    )


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark GLM-5.2 and DeepSeek-V4-Pro endpoints"
    )
    parser.add_argument(
        "--batch-sizes",
        type=int,
        nargs="+",
        default=[1, 4, 8, 16, 32],
        help="Batch sizes to test (default: 1 4 8 16 32)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=256,
        help="Max output tokens per request (default: 256)",
    )
    parser.add_argument(
        "--models",
        type=str,
        nargs="+",
        default=None,
        help="Filter by model name (default: all ready endpoints)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="benchmarks/results/bench_report.json",
        help="Output JSON file (default: benchmarks/results/bench_report.json)",
    )
    args = parser.parse_args()

    # Get API key from r4 config
    api_key = os.environ.get("R4_API_KEY") or os.environ.get("LITELLM_MASTER_KEY")
    if not api_key:
        # Try to extract from r4 config
        try:
            import pathlib

            config_path = pathlib.Path.home() / ".config" / "r4" / "config.json"
            if config_path.exists():
                with open(config_path) as f:
                    config = json.load(f)
                api_key = config.get("api_key")
        except Exception:
            pass

    if not api_key:
        print("Error: No API key found. Set R4_API_KEY or LITELLM_MASTER_KEY.", file=sys.stderr)
        sys.exit(1)

    print("=" * 80)
    print("PERFORMANCE BENCHMARK: GLM-5.2 vs DeepSeek-V4-Pro (baseline vs v2)")
    print("=" * 80)
    print()

    # Get ready endpoints
    endpoints = get_ready_endpoints()
    if not endpoints:
        print("No ready endpoints found.", file=sys.stderr)
        sys.exit(1)

    # Filter by models if specified
    if args.models:
        endpoints = [ep for ep in endpoints if ep["model"] in args.models]

    # Filter to only GLM-5.2 and DeepSeek-V4-Pro
    target_models = {"GLM-5.2", "DeepSeek-V4-Pro"}
    endpoints = [ep for ep in endpoints if ep["model"] in target_models]

    if not endpoints:
        print("No ready GLM-5.2 or DeepSeek-V4-Pro endpoints found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(endpoints)} ready endpoint(s):")
    for ep in endpoints:
        print(f"  - {ep['instance_id']:>6} | {ep['model']:20s} | variant={ep['variant']:20s} | {ep['url']}")
    print()

    # Test prompt - moderate length to exercise prefill + decode
    prompt = (
        "Write a detailed technical explanation of how transformer attention "
        "mechanisms work. Include the mathematical formulation of scaled "
        "dot-product attention, multi-head attention, and explain why attention "
        "is O(n^2) in sequence length. Also discuss how FlashAttention and "
        "PagedAttention optimize memory usage. Provide code examples in Python "
        "using PyTorch where appropriate. Be thorough and technical."
    )

    all_results: list[BenchResult] = []

    for endpoint in endpoints:
        print(f"\n{'─' * 80}")
        print(f"Benchmarking: {endpoint['model']} (variant={endpoint['variant']})")
        print(f"  Endpoint: {endpoint['url']}")
        print(f"  Instance: {endpoint['instance_id']}")
        print(f"{'─' * 80}")

        for batch_size in args.batch_sizes:
            print(f"\n  Batch size = {batch_size}")
            print(f"  Max tokens = {args.max_tokens}")
            print(f"  Prompt length = {len(prompt)} chars")

            result = benchmark_batch(
                endpoint=endpoint,
                prompt=prompt,
                batch_size=batch_size,
                max_tokens=args.max_tokens,
                api_key=api_key,
            )

            all_results.append(result)

            print(f"  ┌─────────────────────────────────────────────┐")
            print(f"  │ Input tokens/request:  {result.input_tokens:>8}           │")
            print(f"  │ Output tokens/request: {result.output_tokens:>8}           │")
            print(f"  │ Avg latency:           {result.total_latency_s:>8.2f}s          │")
            print(f"  │ Prefill (min):         {result.prefill_latency_s:>8.2f}s          │")
            print(f"  │ Decode throughput:     {result.decode_tokens_s:>8.1f} tok/s      │")
            print(f"  │ End-to-end throughput: {result.end_to_end_tokens_s:>8.1f} tok/s      │")
            if result.errors:
                print(f"  │ Errors: {len(result.errors)}/{batch_size} requests failed       │")
            print(f"  └─────────────────────────────────────────────┘")

    # Summary table
    print(f"\n\n{'=' * 80}")
    print("SUMMARY: Throughput comparison (decode tok/s)")
    print(f"{'=' * 80}\n")

    # Group by model+variant
    groups: dict[str, list[BenchResult]] = {}
    for r in all_results:
        key = f"{r.model} [{r.variant}]"
        groups.setdefault(key, []).append(r)

    # Print header
    header = f"{'Endpoint':<40s}"
    for bs in args.batch_sizes:
        header += f" | BS={bs:<3d}"
    print(header)
    print("-" * len(header))

    for group_name, group_results in groups.items():
        row = f"{group_name:<40s}"
        for bs in args.batch_sizes:
            matching = [r for r in group_results if r.batch_size == bs]
            if matching and matching[0].decode_tokens_s > 0:
                row += f" | {matching[0].decode_tokens_s:>6.1f}"
            else:
                row += f" | {'N/A':>6s}"
        print(row)

    print()

    # Save results
    output_data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "batch_sizes": args.batch_sizes,
        "max_tokens": args.max_tokens,
        "endpoints": [
            {
                "instance_id": ep["instance_id"],
                "model": ep["model"],
                "variant": ep["variant"],
                "url": ep["url"],
            }
            for ep in endpoints
        ],
        "results": [
            {
                "endpoint_id": r.endpoint_id,
                "model": r.model,
                "variant": r.variant,
                "batch_size": r.batch_size,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "total_latency_s": r.total_latency_s,
                "prefill_latency_s": r.prefill_latency_s,
                "decode_tokens_s": r.decode_tokens_s,
                "end_to_end_tokens_s": r.end_to_end_tokens_s,
                "errors": r.errors,
            }
            for r in all_results
        ],
    }

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"Results saved to: {args.output}")


if __name__ == "__main__":
    main()
