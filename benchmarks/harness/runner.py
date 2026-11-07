"""Harness benchmark runner.

Usage:
    python -m benchmarks.harness.runner \
        --suite benchmarks/harness/suites/sample_coding.json \
        --harnesses r4_companion,claude_code [--runs 1] [--model MODEL] \
        [--out benchmarks/results/harness] [--keep-workdirs]

Each (case x harness x run) gets an isolated workdir; graders evaluate the
workspace state left behind plus the adapter result. Results land in
<out>/<timestamp>/<suite>/ as per-run JSON plus an aggregated summary.md.

Hold the model constant across harnesses (--model) to measure the harness,
not the LLM.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.harness.adapters import HarnessAdapter, load_adapters
from benchmarks.harness.adapters.base import ABLATION_LABELS
from benchmarks.harness.cases import HarnessCase, load_suite
from benchmarks.harness.graders import load_graders

RESULTS_ROOT = Path("benchmarks/results/harness")


@dataclass
class CaseOutcome:
    case_id: str
    harness: str
    run_index: int
    status: str
    ablation: str = "full"
    metrics: dict[str, Any] = field(default_factory=dict)
    grades: list = field(default_factory=list)
    error: str | None = None
    workdir: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "success" and all(g.passed for g in self.grades)


def _prepare_workdir(
    work_root: Path, harness: str, case: HarnessCase, run_index: int, ablation: str = "full"
) -> Path:
    cell = harness if ablation == "full" else f"{harness}-{ablation}"
    workdir = work_root / cell / f"{case.case_id}-run{run_index}"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    for rel, body in case.setup_files.items():
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return workdir


def run_case(
    adapter: HarnessAdapter,
    case: HarnessCase,
    work_root: Path,
    run_index: int,
    ablation: str = "full",
) -> CaseOutcome:
    """Run one case against one harness cell and grade the resulting workspace."""
    outcome = CaseOutcome(
        case_id=case.case_id,
        harness=adapter.name,
        run_index=run_index,
        status="failed",
        ablation=ablation,
    )
    workdir = _prepare_workdir(work_root, adapter.name, case, run_index, ablation)
    outcome.workdir = str(workdir)
    adapter.ablation = ablation
    result = adapter.run_case(case, workdir, run_index)
    outcome.status = result.status
    outcome.error = result.error
    outcome.metrics = {**result.metrics, "model": adapter.model, "ablation": ablation}
    graders = load_graders(list(case.grader_names))
    outcome.grades = [grader.grade(case, workdir, result) for grader in graders]
    (workdir / "bench.json").write_text(json.dumps(_outcome_dict(outcome), indent=2) + "\n")
    (workdir / "response.txt").write_text(result.response_text)
    return outcome


def _outcome_dict(outcome: CaseOutcome) -> dict[str, Any]:
    return {
        "case": outcome.case_id,
        "harness": outcome.harness,
        "run_index": outcome.run_index,
        "ablation": outcome.ablation,
        "status": outcome.status,
        "passed": outcome.passed,
        "metrics": outcome.metrics,
        "grades": [{"name": g.name, "passed": g.passed, "note": g.note} for g in outcome.grades],
        "error": outcome.error,
        "workdir": outcome.workdir,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def run_suite(
    adapters: list[HarnessAdapter],
    cases: list[HarnessCase],
    work_root: Path,
    global_runs: int | None = None,
    ablations: tuple[str, ...] = ("full",),
) -> list[CaseOutcome]:
    """Run the case x harness x ablation-cell grid.

    Cells a harness does not support are skipped by the caller (main);
    run_suite assumes every (adapter, label) pair it receives is runnable.
    """
    outcomes: list[CaseOutcome] = []
    for ablation in ablations:
        for case in cases:
            repeats = global_runs if global_runs is not None else case.repeat
            for adapter in adapters:
                if not adapter.supports_ablation(ablation):
                    continue
                for run_index in range(repeats):
                    outcomes.append(run_case(adapter, case, work_root, run_index, ablation))
    return outcomes


def _summary_row(outcome: CaseOutcome) -> str:
    metrics = outcome.metrics
    failed_grades = [g.name for g in outcome.grades if not g.passed]
    notes = ",".join(failed_grades) if failed_grades else (outcome.error or "")
    return (
        f"| {outcome.case_id} | {outcome.harness} | {outcome.ablation} | {outcome.run_index} "
        f"| {outcome.status} | {'OK' if outcome.passed else 'FAIL'} "
        f"| {metrics.get('total_tokens', '-')} | {metrics.get('llm_calls', '-')} "
        f"| {metrics.get('wallclock_sec', '-')} | {notes[:60]} |"
    )


def render_summary(outcomes: list[CaseOutcome]) -> str:
    cells = sorted(outcomes, key=lambda o: (o.harness, o.case_id, o.ablation, o.run_index))
    lines = [
        "| case | harness | ablation | run | status | pass | tokens | llm_calls | wallclock_s | notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *(_summary_row(outcome) for outcome in cells),
    ]
    passed = sum(1 for o in outcomes if o.passed)
    lines.append(f"\n{passed}/{len(outcomes)} runs passed.")
    return "\n".join(lines) + "\n"


def write_outputs(suite_name: str, outcomes: list[CaseOutcome], out_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = out_root / stamp / suite_name
    out_dir.mkdir(parents=True, exist_ok=True)
    for outcome in outcomes:
        cell = (
            outcome.harness
            if outcome.ablation == "full"
            else f"{outcome.harness}-{outcome.ablation}"
        )
        filename = f"{cell}-{outcome.case_id}-{outcome.run_index}.json"
        (out_dir / filename).write_text(json.dumps(_outcome_dict(outcome), indent=2) + "\n")
    (out_dir / "summary.md").write_text(render_summary(outcomes))
    return out_dir


def _filter_cases(cases: list[HarnessCase], case_arg: str | None) -> list[HarnessCase] | None:
    """Apply --cases id filter; None return = unknown id (caller exits 2)."""
    if not case_arg:
        return cases
    wanted = {c.strip() for c in case_arg.split(",") if c.strip()}
    missing = wanted - {c.case_id for c in cases}
    if missing:
        print(f"unknown case ids: {sorted(missing)}")
        return None
    return [c for c in cases if c.case_id in wanted]


def _apply_model_map(adapters: list[HarnessAdapter], map_arg: str | None) -> bool:
    """Apply --model-map; False = unknown harness name (caller exits 2)."""
    if not map_arg:
        return True
    model_map = dict(pair.strip().split("=", 1) for pair in map_arg.split(",") if "=" in pair)
    unknown = set(model_map) - {a.name for a in adapters}
    if unknown:
        print(f"model-map names not in --harnesses: {sorted(unknown)}")
        return False
    for adapter in adapters:
        if adapter.name in model_map:
            adapter.model = model_map[adapter.name]
    return True


def _resolve_ablations(ablation_arg: str | None) -> tuple[str, ...] | None:
    """Parse --ablations; None return = unknown label (caller exits 2)."""
    ablations = tuple(a.strip() for a in (ablation_arg or "").split(",") if a.strip()) or ("full",)
    unknown = [a for a in ablations if a not in ABLATION_LABELS]
    if unknown:
        print(f"unknown ablation cells: {unknown} (known: {list(ABLATION_LABELS)})")
        return None
    return ablations


def _prepare_cases(args: argparse.Namespace) -> list[HarnessCase] | None:
    """Suite load + id filter + grader preflight; None = caller exits 2."""
    cases = _filter_cases(load_suite(Path(args.suite)), args.cases)
    if cases is None:
        return None
    for case in cases:
        load_graders(list(case.grader_names))  # fail closed on typos before any LLM call
    return cases


def _prepare_adapters(args: argparse.Namespace) -> list[HarnessAdapter] | None:
    """Build adapters + availability + model map; None = caller exits 2."""
    harnesses = [name.strip() for name in args.harnesses.split(",") if name.strip()]
    adapters = load_adapters(harnesses, model=args.model, timeout_sec=args.timeout)
    unavailable = {a.name: reason for a in adapters if (reason := a.available())}
    for name, reason in unavailable.items():
        print(f"unavailable: {name}: {reason}")
    if unavailable or not _apply_model_map(adapters, args.model_map):
        return None
    return adapters


def _prepare_run_args(
    args: argparse.Namespace,
) -> tuple[list[HarnessCase], list[HarnessAdapter], tuple[str, ...]] | None:
    """Validate args into cases/adapters/ablations; None = caller exits 2."""
    cases = _prepare_cases(args)
    adapters = _prepare_adapters(args) if cases is not None else None
    ablations = _resolve_ablations(args.ablations) if adapters is not None else None
    if cases is None or adapters is None or ablations is None:
        return None
    return cases, adapters, ablations


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    prepared = _prepare_run_args(args)
    if prepared is None:
        return 2
    cases, adapters, ablations = prepared
    # Absolute paths: adapter drivers run with cwd=workdir AND may chdir again,
    # so relative paths would resolve against the wrong base.
    work_root = Path(args.out).resolve() / "workdirs"
    for label in ablations:
        for adapter in adapters:
            if not adapter.supports_ablation(label):
                print(f"note: {adapter.name} does not support ablation '{label}' — cell skipped")
    outcomes = run_suite(adapters, cases, work_root, global_runs=args.runs, ablations=ablations)
    out_dir = write_outputs(Path(args.suite).stem, outcomes, Path(args.out))
    if not args.keep_workdirs:
        shutil.rmtree(work_root, ignore_errors=True)
    print(render_summary(outcomes))
    print(f"results: {out_dir}")
    return 0 if all(o.passed for o in outcomes) else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="r4-harness-bench")
    parser.add_argument("--suite", required=True, help="Suite JSON path")
    parser.add_argument("--harnesses", required=True, help="Comma-separated harness names")
    parser.add_argument(
        "--cases", default=None, help="Comma-separated case-id filter (default: all)"
    )
    parser.add_argument("--runs", type=int, default=None, help="Override per-case repeat count")
    parser.add_argument(
        "--model", default=None, help="Pin model across harnesses (harness, not LLM, comparison)"
    )
    parser.add_argument(
        "--model-map",
        default=None,
        help='Per-harness model override, e.g. "r4_companion=kimi-k3@rikyu,opencode=rikyu/kimi-k3"',
    )
    parser.add_argument("--timeout", type=int, default=900, help="Per-case timeout in seconds")
    parser.add_argument(
        "--ablations",
        default="full",
        help='Comma-separated ablation cells, e.g. "full,no_steering,no_guides,no_memory". '
        "Unsupported harnesses are skipped per cell with a note.",
    )
    parser.add_argument("--out", default=str(RESULTS_ROOT), help="Results root directory")
    parser.add_argument(
        "--keep-workdirs", action="store_true", help="Keep per-run case workdirs for inspection"
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
