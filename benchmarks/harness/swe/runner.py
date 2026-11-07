"""SWE-bench lane runner: real instances, same prompt, in-container harness.

Usage:
    python -m benchmarks.harness.swe.runner \
        --harnesses r4_companion,opencode \
        --model-map "r4_companion=kimi-k3@rikyu,opencode=rikyu/kimi-k3" \
        --count 3 [--ids id1,id2] [--engine docker] [--out benchmarks/results/harness/swe]

Lifecycle per (instance x harness):
  prepare container @ base_commit, strip future commits (anti-leak)
    -> adapter runs INSIDE the container with the identical prompt
    -> runner collects `git diff` (never agent-reported)
    -> runner runs FAIL_TO_PASS pytest -> resolved verdict
    -> artifacts + bench metrics to results dir

NOTE: measure on a native x86_64 host; qemu/Rosetta emulation is enough to
validate the lane (r4 lane proven live 2026-08-21) but invalid as measured
benchmark data, and too slow for node-runtime harnesses.

Host requirements: linux x86_64 with docker/podman and prebuilt SWE-bench
images (`sweb.eval.x86_64.<id>`), or docker desktop with emulation (slow).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.harness.adapters import load_adapters
from benchmarks.harness.swe.containers import (
    ContainerRuntime,
    DockerRuntime,
    engine_available,
)
from benchmarks.harness.swe.dataset import SweInstance, load_verified, select
from benchmarks.harness.swe.fairness import render_prompt
from benchmarks.harness.swe.patches import collect_patch, evaluate_resolved, strip_setup_churn

RESULTS_ROOT = Path("benchmarks/results/harness/swe")


@dataclass
class SweOutcome:
    instance_id: str
    harness: str
    status: str
    resolved: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    eval_info: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.resolved


def _outcome_dict(outcome: SweOutcome) -> dict[str, Any]:
    return {**asdict(outcome), "passed": outcome.passed}


def run_instance(
    adapter,
    runtime: ContainerRuntime,
    instance: SweInstance,
    work_dir: Path,
) -> SweOutcome:
    """One full instance lifecycle against one harness."""
    outcome = SweOutcome(instance.instance_id, adapter.name, status="failed", resolved=False)
    work_dir.mkdir(parents=True, exist_ok=True)
    container: str | None = None
    try:
        mounts = adapter.swe_mounts() if hasattr(adapter, "swe_mounts") else []
        container = runtime.prepare(instance, mounts=tuple(mounts))
        prompt = render_prompt(instance)
        (work_dir / "prompt.txt").write_text(prompt)
        runtime.write_file(container, "/tmp/r4bench_prompt.txt", prompt)
        result = adapter.run_swe_case(instance, container, runtime)
        outcome.status = result.status
        outcome.metrics = result.metrics
        outcome.error = result.error
        (work_dir / "response.txt").write_text(result.response_text)

        raw_patch = collect_patch(runtime, container)
        clean_patch = strip_setup_churn(raw_patch)
        (work_dir / "git.patch").write_text(raw_patch)
        (work_dir / "git.clean.patch").write_text(clean_patch)

        eval_info = evaluate_resolved(runtime, container, instance)
        outcome.resolved = bool(eval_info.get("resolved"))
        outcome.eval_info = eval_info
    except Exception as exc:  # noqa: BLE001 — lane isolates per-instance failures
        outcome.status = outcome.status or "error"
        outcome.error = outcome.error or str(exc)[:500]
    finally:
        if container:
            runtime.destroy(container)
    (work_dir / "bench.json").write_text(json.dumps(_outcome_dict(outcome), indent=2) + "\n")
    return outcome


def render_summary(outcomes: list[SweOutcome]) -> str:
    harnesses = sorted({o.harness for o in outcomes})
    lines = [
        "| instance | harness | status | resolved | llm_calls | tokens | wallclock_s |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for harness in harnesses:
        for outcome in (o for o in outcomes if o.harness == harness):
            metrics = outcome.metrics
            lines.append(
                f"| {outcome.instance_id} | {harness} | {outcome.status} "
                f"| {'resolved' if outcome.resolved else 'no'} "
                f"| {metrics.get('llm_calls', '-')} | {metrics.get('total_tokens', '-')} "
                f"| {metrics.get('wallclock_sec', '-')} |"
            )
    resolved = sum(1 for o in outcomes if o.resolved)
    lines.append(f"\n{resolved}/{len(outcomes)} instances resolved.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, *, runtime: ContainerRuntime | None = None) -> int:
    args = _parse_args(argv)
    harnesses = [name.strip() for name in args.harnesses.split(",") if name.strip()]
    adapters = load_adapters(harnesses, model=args.model, timeout_sec=args.timeout)
    if args.model_map:
        model_map = dict(pair.split("=", 1) for pair in args.model_map.split(",") if "=" in pair)
        for adapter in adapters:
            if adapter.name in model_map:
                adapter.model = model_map[adapter.name]
    from benchmarks.harness.adapters.base import supports_swe

    unsupported = {a.name: reason for a in adapters if (reason := supports_swe(a))}
    if unsupported:
        for name, reason in unsupported.items():
            print(f"swe lane unsupported: {name}: {reason}")
        return 2
    if runtime is None:
        reason = engine_available(args.engine)
        if reason:
            print(f"unavailable: {reason}")
            return 2
        runtime = DockerRuntime(engine=args.engine)
    instances = select(
        load_verified(cache=Path(args.cache)),
        ids=[item.strip() for item in args.ids.split(",")] if args.ids else None,
        count=args.count,
        seed=args.seed,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_root = Path(args.out).resolve() / stamp
    outcomes: list[SweOutcome] = []
    for instance in instances:
        for adapter in adapters:
            work = out_root / adapter.name / instance.instance_id
            outcomes.append(run_instance(adapter, runtime, instance, work))
            print(render_summary(outcomes))  # progressive visibility
    (out_root / "summary.md").write_text(render_summary(outcomes))
    print(f"results: {out_root}")
    return 0 if all(o.resolved for o in outcomes) else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="r4-harness-swe")
    parser.add_argument("--harnesses", required=True, help="Comma-separated harness names")
    parser.add_argument("--ids", default=None, help="Explicit instance ids (comma-separated)")
    parser.add_argument("--count", type=int, default=None, help="Sample N instances (with --seed)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--engine", default="docker", choices=["docker", "podman"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--model-map", default=None)
    parser.add_argument("--timeout", type=int, default=3600, help="Per-instance agent budget (sec)")
    parser.add_argument("--cache", default="benchmarks/harness/swe/.cache/verified.json")
    parser.add_argument("--out", default=str(RESULTS_ROOT))
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
