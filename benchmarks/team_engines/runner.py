"""Benchmark runner (ADR-020 §13).

Usage:
    python -m benchmarks.team_engines.runner \
        --suite benchmarks/team_engines/suites/sample_research.json \
        --engines native,langgraph [--runs 2] [--out benchmarks/results]

Every case is submitted through the public TeamService API. Metrics come
from the run result (authoritative broker metering), never engine logs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from r4.teams.service import TeamService
from r4_contracts.team_engine.spec import (
    TEAM_SPEC_VERSION,
    AgentSpec,
    BudgetSpec,
    TaskSpec,
    TeamSpec,
    Topology,
    WorkflowStyle,
)

from .graders import Grade, load_graders

RESULTS_ROOT = Path("benchmarks/results")


@dataclass(frozen=True)
class BenchCase:
    case_id: str
    spec: TeamSpec
    repeat: int = 1
    grader_names: tuple[str, ...] = ("status_success", "metrics_sanity")
    expected_outputs: tuple[str, ...] = ()


@dataclass
class CaseOutcome:
    case_id: str
    engine: str
    run_index: int
    status: str
    metrics: dict[str, Any] | None
    grades: list[Grade] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "success" and all(g.passed for g in self.grades)


def spec_from_dict(raw: dict[str, Any]) -> TeamSpec:
    """Suite JSON -> TeamSpec (mirrors CLI --spec shape; json only)."""
    topo = raw.get("topology", {})
    return TeamSpec(
        spec_version=raw.get("spec_version", TEAM_SPEC_VERSION),
        run_name=raw["run_name"],
        project=raw.get("project", "bench"),
        task=TaskSpec(**raw.get("task", {"objective": raw["run_name"]})),
        agents=tuple(AgentSpec(**a) for a in raw.get("agents", ())),
        topology=Topology(
            style=WorkflowStyle(topo.get("style", "sequential")),
            entry_agent=topo.get("entry_agent"),
            max_parallel=topo.get("max_parallel", 1),
        ),
        budgets=BudgetSpec(**raw.get("budgets", {})),
    )


def load_suite(path: Path) -> list[BenchCase]:
    raw = json.loads(Path(path).read_text())
    cases: list[BenchCase] = []
    for entry in raw["cases"]:
        spec = spec_from_dict(entry["spec"])
        cases.append(
            BenchCase(
                case_id=entry["id"],
                spec=spec,
                repeat=int(entry.get("repeat", 1)),
                grader_names=tuple(entry.get("graders", ("status_success", "metrics_sanity"))),
                expected_outputs=tuple(spec.task.expected_outputs),
            )
        )
    return cases


async def run_case(
    service: TeamService, engine: str, case: BenchCase, run_index: int
) -> CaseOutcome:
    outcome = CaseOutcome(case.case_id, engine, run_index, status="failed", metrics=None)
    try:
        handle = await service.submit(case.spec, engine=engine)
    except Exception as exc:  # noqa: BLE001 — bench isolates engine failures
        outcome.error = f"submit: {exc}"
        return outcome
    try:
        _ = [event async for event in handle.events()]
        result = await handle.result()
    except Exception as exc:  # noqa: BLE001
        outcome.error = f"run: {exc}"
        return outcome
    outcome.status = result.status
    outcome.error = result.error
    outcome.metrics = _metrics_dict(result.metrics)
    graders = load_graders(list(case.grader_names))
    outcome.grades = [g.grade(result, case.expected_outputs) for g in graders]
    (handle.run_dir / "bench.json").write_text(json.dumps(_outcome_dict(outcome), indent=2))
    return outcome


def _metrics_dict(metrics) -> dict[str, Any] | None:
    if metrics is None:
        return None
    return {
        "wallclock_sec": metrics.wallclock_sec,
        "total_tokens": metrics.total_tokens,
        "llm_calls": metrics.llm_calls,
        "tool_calls": metrics.tool_calls,
        "agent_steps": metrics.agent_steps,
        "cost_usd": metrics.cost_usd,
    }


def _outcome_dict(outcome: CaseOutcome) -> dict[str, Any]:
    return {
        "case": outcome.case_id,
        "engine": outcome.engine,
        "run_index": outcome.run_index,
        "status": outcome.status,
        "passed": outcome.passed,
        "metrics": outcome.metrics,
        "grades": [{"name": g.name, "passed": g.passed, "note": g.note} for g in outcome.grades],
        "error": outcome.error,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


async def run_suite(
    service: TeamService, cases: list[BenchCase], engines: list[str]
) -> list[CaseOutcome]:
    outcomes: list[CaseOutcome] = []
    for case in cases:
        for engine in engines:
            for run_index in range(case.repeat):
                outcomes.append(await run_case(service, engine, case, run_index))
    return outcomes


def write_outputs(suite_name: str, outcomes: list[CaseOutcome], out_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = out_root / stamp / suite_name
    out_dir.mkdir(parents=True, exist_ok=True)
    for outcome in outcomes:
        (out_dir / f"{outcome.engine}-{outcome.case_id}-{outcome.run_index}.json").write_text(
            json.dumps(_outcome_dict(outcome), indent=2) + "\n"
        )
    (out_dir / "summary.md").write_text(render_summary(outcomes))
    return out_dir


def render_summary(outcomes: list[CaseOutcome]) -> str:
    engines = sorted({o.engine for o in outcomes})
    lines = [
        "| case | engine | status | pass | tokens | llm_calls | steps |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for engine in engines:
        for outcome in (o for o in outcomes if o.engine == engine):
            metrics = outcome.metrics or {}
            lines.append(
                f"| {outcome.case_id} | {engine} | {outcome.status} "
                f"| {'✅' if outcome.passed else '❌'} | {metrics.get('total_tokens', '-')} "
                f"| {metrics.get('llm_calls', '-')} | {metrics.get('agent_steps', '-')} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, *, service: TeamService | None = None) -> int:
    args = _parse_args(argv)
    cases = load_suite(Path(args.suite))
    if service is None:
        from r4.client import Client

        service = TeamService(Client())
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    outcomes = asyncio.run(run_suite(service, cases, engines))
    out_dir = write_outputs(Path(args.suite).stem, outcomes, Path(args.out))
    print(render_summary(outcomes))
    print(f"results: {out_dir}")
    return 0 if all(o.passed for o in outcomes) else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="r4-team-bench")
    parser.add_argument("--suite", required=True, help="Suite JSON path")
    parser.add_argument("--engines", required=True, help="Comma-separated engine names")
    parser.add_argument("--out", default=str(RESULTS_ROOT), help="Results root directory")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
