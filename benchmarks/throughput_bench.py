#!/usr/bin/env python3
"""
Decode throughput benchmark for GLM-5.2 canary endpoints.

Measures streaming TTFT, TPOT, and output tokens/s with reasoning disabled.
Optional tool-calling correctness sweep for canary validation.

Usage:
    python throughput_bench.py \\
        --models glm-5.2,glm-5.2-graphs,glm-5.2-tep-bf16,glm-5.2-tep,glm-5.2-tep-4n \\
        --concurrency 1,4,16 \\
        --max-tokens 512 \\
        --reasoning-effort none \\
        --compare glm-5.2

    python throughput_bench.py --validate-canaries --compare glm-5.2
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from gateway_client import GatewayClient, GatewayError, StreamTiming

DEFAULT_PROMPT = (
    "Write a detailed technical explanation of how a B-tree index supports "
    "range queries in a relational database. Include insertion, deletion, "
    "and rebalancing. Use clear paragraphs."
)

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "Search for a pattern in files",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal",
            "description": "Run a shell command",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
]

TOOL_PROMPTS = [
    "Read src/main.py and summarize what it does.",
    "Search the repo for 'DeploymentSpec' and list the file paths.",
    "Run `python -m pytest tests/ -q` and report failures.",
    "Find all TODO comments under umrs/ and group them by module.",
    "Read umrs/deploy/ai4s/serve.slurm and list vLLM flags used.",
    "Grep for 'enforce-eager' in the umrs tree.",
    "Read pool-ai4s.yaml and summarize GLM-5.2 endpoints.",
    "Search for 'reasoning_parser' in umrs/src.",
    "Run `git status --short` in the project root.",
    "Read deployment_spec.py and explain generate_sbatch_env.",
    "Find references to VLLM_CUDAGRAPH_MODE in the repo.",
    "Grep for 'FlashInfer' under deploy/.",
    "Read gateway_client.py and list public methods.",
    "Search benchmarks/ for throughput scripts.",
    "Run `python -c \"print(2**20)\"` and return the output.",
    "Read serve.slurm GLM MoE block and summarize the fix.",
    "Find all pool-ai4s.yaml entries with parallel_strategy: tep.",
    "Grep for glm45 in the vscode-extension.",
    "Read test_llm_usage_glm52.py and summarize billing rules.",
    "List files matching *glm* under umrs/deploy/.",
]

PROMOTE_THRESHOLD = 1.10  # 10% improvement over baseline


@dataclass
class RunSample:
    model: str
    concurrency: int
    repeat: int
    ok: bool
    error: str = ""
    timing: dict[str, float] = field(default_factory=dict)


@dataclass
class ModelSummary:
    model: str
    concurrency: int
    samples: int
    failures: int
    avg_ttft_ms: float
    avg_tpot_ms: float
    avg_output_tps: float
    aggregate_tps: float


@dataclass
class ToolCallResult:
    model: str
    prompt_id: int
    ok: bool
    garbage: bool
    has_tool_call: bool
    content_preview: str
    error: str = ""


def _is_garbage_output(content: str) -> bool:
    stripped = content.strip()
    if not stripped:
        return False
    if stripped.count("!") >= 20 and len(set(stripped)) <= 3:
        return True
    if len(stripped) > 50 and stripped.count("!") / len(stripped) > 0.5:
        return True
    return False


def _run_stream_sample(
    client: GatewayClient,
    model: str,
    prompt: str,
    max_tokens: int,
    reasoning_effort: str | None,
    temperature: float,
) -> RunSample:
    try:
        timing, _usage = client.chat_stream(
            prompt=prompt,
            model=model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
        )
        return RunSample(
            model=model,
            concurrency=1,
            repeat=0,
            ok=True,
            timing={
                "ttft_ms": timing.time_to_first_token_ms,
                "tpot_ms": timing.time_per_output_token_ms,
                "output_tps": timing.tokens_per_second,
                "output_tokens": float(timing.output_tokens),
                "total_ms": timing.total_time_ms,
            },
        )
    except GatewayError as exc:
        return RunSample(
            model=model,
            concurrency=1,
            repeat=0,
            ok=False,
            error=str(exc),
        )


def run_concurrency_level(
    client: GatewayClient,
    model: str,
    concurrency: int,
    repeats: int,
    prompt: str,
    max_tokens: int,
    reasoning_effort: str | None,
    temperature: float,
) -> list[RunSample]:
    samples: list[RunSample] = []
    for repeat in range(repeats):
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(
                    _run_stream_sample,
                    client,
                    model,
                    prompt,
                    max_tokens,
                    reasoning_effort,
                    temperature,
                )
                for _ in range(concurrency)
            ]
            batch: list[RunSample] = []
            for fut in as_completed(futures):
                sample = fut.result()
                sample.concurrency = concurrency
                sample.repeat = repeat
                batch.append(sample)
        elapsed = time.perf_counter() - start
        total_tokens = sum(
            int(s.timing.get("output_tokens", 0)) for s in batch if s.ok
        )
        aggregate_tps = total_tokens / elapsed if elapsed > 0 else 0.0
        for s in batch:
            if s.ok:
                s.timing["aggregate_tps"] = aggregate_tps
            samples.append(s)
    return samples


def summarize(samples: list[RunSample], model: str, concurrency: int) -> ModelSummary:
    ok_samples = [s for s in samples if s.ok]
    failures = len(samples) - len(ok_samples)
    if not ok_samples:
        return ModelSummary(
            model=model,
            concurrency=concurrency,
            samples=len(samples),
            failures=failures,
            avg_ttft_ms=0.0,
            avg_tpot_ms=0.0,
            avg_output_tps=0.0,
            aggregate_tps=0.0,
        )
    return ModelSummary(
        model=model,
        concurrency=concurrency,
        samples=len(samples),
        failures=failures,
        avg_ttft_ms=statistics.mean(s.timing["ttft_ms"] for s in ok_samples),
        avg_tpot_ms=statistics.mean(s.timing["tpot_ms"] for s in ok_samples),
        avg_output_tps=statistics.mean(s.timing["output_tps"] for s in ok_samples),
        aggregate_tps=statistics.mean(
            s.timing.get("aggregate_tps", s.timing["output_tps"]) for s in ok_samples
        ),
    )


def run_tool_calling(
    client: GatewayClient,
    model: str,
    max_tokens: int,
) -> list[ToolCallResult]:
    results: list[ToolCallResult] = []
    for idx, prompt in enumerate(TOOL_PROMPTS):
        try:
            response, _timing = client.chat_completion(
                prompt=prompt,
                model=model,
                max_tokens=max_tokens,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
            )
            content = response.content or ""
            raw = response.raw_json
            tool_calls = []
            if response.choices:
                msg = raw.get("choices", [{}])[0].get("message", {})
                tool_calls = msg.get("tool_calls") or []
            garbage = _is_garbage_output(content)
            results.append(
                ToolCallResult(
                    model=model,
                    prompt_id=idx,
                    ok=not garbage,
                    garbage=garbage,
                    has_tool_call=bool(tool_calls),
                    content_preview=content[:120],
                )
            )
        except GatewayError as exc:
            results.append(
                ToolCallResult(
                    model=model,
                    prompt_id=idx,
                    ok=False,
                    garbage=False,
                    has_tool_call=False,
                    content_preview="",
                    error=str(exc),
                )
            )
    return results


def print_compare(
    summaries: dict[str, ModelSummary],
    baseline: str,
    concurrency: int = 1,
) -> None:
    base = summaries.get(f"{baseline}@{concurrency}")
    if not base or base.avg_output_tps <= 0:
        print(f"[compare] baseline {baseline}@c{concurrency} missing or zero tok/s")
        return
    print(f"\nCompare vs {baseline} (concurrency={concurrency}, output tok/s):")
    print(f"{'Model':<22} {'tok/s':>10} {'Δ%':>8} {'TTFT ms':>10} {'TPOT ms':>10}")
    print("-" * 64)
    for key, summary in sorted(summaries.items()):
        if not key.endswith(f"@{concurrency}"):
            continue
        model = summary.model
        delta = (
            (summary.avg_output_tps / base.avg_output_tps - 1.0) * 100.0
            if base.avg_output_tps > 0
            else 0.0
        )
        print(
            f"{model:<22} "
            f"{summary.avg_output_tps:>10.1f} "
            f"{delta:>+7.1f}% "
            f"{summary.avg_ttft_ms:>10.0f} "
            f"{summary.avg_tpot_ms:>10.1f}"
        )


def check_promote(
    summaries: dict[str, ModelSummary],
    tool_results: dict[str, list[ToolCallResult]],
    baseline: str,
) -> dict[str, Any]:
    base_key = f"{baseline}@1"
    base = summaries.get(base_key)
    report: dict[str, Any] = {
        "baseline": baseline,
        "threshold": PROMOTE_THRESHOLD,
        "candidates": [],
    }
    if not base or base.avg_output_tps <= 0:
        report["error"] = "baseline throughput unavailable"
        return report

    for model in summaries:
        if model == baseline or "@" in model:
            continue
        key = f"{model}@1"
        summary = summaries.get(key)
        if not summary:
            continue
        tools = tool_results.get(model, [])
        garbage = sum(1 for t in tools if t.garbage)
        tool_errors = sum(1 for t in tools if not t.ok)
        speed_ratio = summary.avg_output_tps / base.avg_output_tps
        passed = speed_ratio >= PROMOTE_THRESHOLD and garbage == 0 and tool_errors == 0
        report["candidates"].append(
            {
                "model": model,
                "speed_ratio": round(speed_ratio, 3),
                "garbage_responses": garbage,
                "tool_failures": tool_errors,
                "passed": passed,
            }
        )
    return report


def print_promote_yaml(candidate: str) -> None:
    """Print env_vars to merge into production glm-5.2 after a passing canary."""
    mapping = {
        "glm-5.2-graphs": {
            "VLLM_CUDAGRAPH_MODE": "FULL_DECODE_ONLY",
        },
        "glm-5.2-tep-bf16": {
            "node_count": "4",
            "parallel_strategy": "tep",
            "max_model_len": "1048576",
            "partition": "4n4gpu-p",
            "VLLM_MAX_NUM_SEQS": "32",
            "VLLM_MAX_NUM_BATCHED_TOKENS": "32768",
        },
        "glm-5.2-tep": {
            "node_count": "2",
            "parallel_strategy": "tep",
            "max_model_len": "1048576",
            "partition": "2n4gpu",
            "VLLM_QUANTIZATION": "fp8",
            "VLLM_FORCE_FLASHINFER_MOE": "1",
            "VLLM_CUDAGRAPH_MODE": "FULL_DECODE_ONLY",
            "VLLM_MAX_NUM_SEQS": "32",
            "VLLM_MAX_NUM_BATCHED_TOKENS": "32768",
        },
        "glm-5.2-tep-4n": {
            "node_count": "4",
            "parallel_strategy": "tep",
            "max_model_len": "1048576",
            "partition": "4n4gpu-p",
            "VLLM_QUANTIZATION": "fp8",
            "VLLM_FORCE_FLASHINFER_MOE": "1",
            "VLLM_CUDAGRAPH_MODE": "FULL_DECODE_ONLY",
            "VLLM_MAX_NUM_SEQS": "32",
            "VLLM_MAX_NUM_BATCHED_TOKENS": "32768",
        },
    }
    changes = mapping.get(candidate)
    if not changes:
        print(f"No promote mapping for {candidate}")
        return
    print(f"\n# Merge into production glm-5.2 after validation ({candidate} won):\n")
    for k, v in changes.items():
        if k in ("node_count", "parallel_strategy", "max_model_len", "partition"):
            print(f"  {k}: {v}")
        else:
            print(f"  env_vars.{k}: {v}")


def main() -> None:
    parser = argparse.ArgumentParser(description="GLM-5.2 decode throughput benchmark")
    parser.add_argument(
        "--models",
        default="glm-5.2,glm-5.2-graphs,glm-5.2-tep-bf16,glm-5.2-tep,glm-5.2-tep-4n",
        help="Comma-separated endpoint/model names",
    )
    parser.add_argument(
        "--concurrency",
        default="1,4,16",
        help="Comma-separated concurrency levels",
    )
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--reasoning-effort", default="none")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--compare", default="glm-5.2")
    parser.add_argument(
        "--validate-canaries",
        action="store_true",
        help="Run throughput + 20 tool-calling prompts and check promotion gates",
    )
    parser.add_argument(
        "--tool-calling-only",
        action="store_true",
        help="Skip throughput; only run tool-calling correctness sweep",
    )
    parser.add_argument("--gateway-url", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    concurrencies = [int(c.strip()) for c in args.concurrency.split(",") if c.strip()]
    reasoning = args.reasoning_effort if args.reasoning_effort.lower() != "none" else "none"
    reasoning_opt: str | None = reasoning if reasoning != "none" else "none"

    client = GatewayClient(gateway_url=args.gateway_url, timeout=args.timeout)
    output_dir = Path(args.output_dir or Path(__file__).parent / "results")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    all_samples: list[RunSample] = []
    summaries: dict[str, ModelSummary] = {}
    tool_results: dict[str, list[ToolCallResult]] = {}

    if not args.tool_calling_only:
        print(f"Gateway: {client.base_url}")
        print(f"Models: {', '.join(models)}")
        print(f"Concurrency: {concurrencies}, repeats: {args.repeats}\n")

        for model in models:
            for conc in concurrencies:
                print(f"  {model} @ concurrency={conc} ...", flush=True)
                samples = run_concurrency_level(
                    client,
                    model,
                    conc,
                    args.repeats,
                    args.prompt,
                    args.max_tokens,
                    reasoning_opt,
                    args.temperature,
                )
                all_samples.extend(samples)
                summary = summarize(samples, model, conc)
                summaries[f"{model}@{conc}"] = summary
                print(
                    f"    tok/s={summary.avg_output_tps:.1f} "
                    f"agg={summary.aggregate_tps:.1f} "
                    f"failures={summary.failures}/{summary.samples}"
                )

        for conc in concurrencies:
            print_compare(summaries, args.compare, concurrency=conc)

    if args.validate_canaries or args.tool_calling_only:
        print("\nTool-calling correctness sweep (20 prompts):")
        for model in models:
            results = run_tool_calling(client, model, max_tokens=1024)
            tool_results[model] = results
            garbage = sum(1 for r in results if r.garbage)
            failures = sum(1 for r in results if not r.ok)
            print(f"  {model}: garbage={garbage}, failures={failures}/20")

    promote_report = None
    if args.validate_canaries:
        promote_report = check_promote(summaries, tool_results, args.compare)
        print("\nPromotion gate (≥10% tok/s @ c=1, zero garbage, zero tool errors):")
        for cand in promote_report.get("candidates", []):
            status = "PASS" if cand["passed"] else "FAIL"
            print(
                f"  [{status}] {cand['model']}: "
                f"speed={cand['speed_ratio']:.2f}x, "
                f"garbage={cand['garbage_responses']}, "
                f"tool_failures={cand['tool_failures']}"
            )
            if cand["passed"]:
                print_promote_yaml(cand["model"])

    out_file = output_dir / f"throughput_{timestamp}.json"
    payload = {
        "timestamp": timestamp,
        "config": {
            "models": models,
            "concurrency": concurrencies,
            "max_tokens": args.max_tokens,
            "repeats": args.repeats,
            "reasoning_effort": reasoning_opt,
        },
        "summaries": {k: asdict(v) for k, v in summaries.items()},
        "samples": [asdict(s) for s in all_samples],
        "tool_results": {
            m: [asdict(r) for r in rs] for m, rs in tool_results.items()
        },
        "promote_report": promote_report,
    }
    with open(out_file, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nResults saved: {out_file}")


if __name__ == "__main__":
    main()
