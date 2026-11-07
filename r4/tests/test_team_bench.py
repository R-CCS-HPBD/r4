"""Benchmark harness tests (ADR-020 §13)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# benchmarks/ is a repo-root tree, not a packaged member of r4 — from
# working-directory=r4 lanes (CI) it needs the parent-repo root on path.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmarks.team_engines.graders import (
    ArtifactPresenceGrader,
    MetricsSanityGrader,
    load_graders,
)
from benchmarks.team_engines.runner import (
    load_suite,
    main,
    run_suite,
    spec_from_dict,
)
from r4_contracts.team_engine.results import ArtifactRef, RunMetrics, TeamResult

from r4.teams.service import TeamService

# Absolute path pinned from the file's location; routinely-run from both the
# repo root and working-directory=r4 lanes (CI).
SUITE_PATH = _REPO_ROOT / "benchmarks/team_engines/suites/sample_research.json"


class FakeChatResponse:
    def __init__(self, content: str) -> None:
        self.content = content
        self.usage = {"prompt_tokens": 5, "completion_tokens": len(content.split())}


class FakeClient:
    def chat(self, message=None, **_kwargs) -> FakeChatResponse:
        return FakeChatResponse("bench ack")

    def list_models(self):
        return ["fake-model"]


def _metrics(**overrides) -> RunMetrics:
    base = {
        "wallclock_sec": 1.0,
        "total_tokens": 100,
        "input_tokens": 40,
        "output_tokens": 60,
        "tool_calls": 0,
        "llm_calls": 2,
        "cost_usd": None,
        "agent_steps": 2,
        "checkpoint_count": 0,
        "human_interventions": 0,
    }
    base.update(overrides)
    return RunMetrics(**base)


class TestGraders:
    def test_metrics_sanity(self) -> None:
        ok = TeamResult(status="success", summary="x", metrics=_metrics())
        assert MetricsSanityGrader().grade(ok, ()).passed
        bad = TeamResult(status="success", summary="x", metrics=_metrics(llm_calls=0))
        assert not MetricsSanityGrader().grade(bad, ()).passed

    def test_artifact_presence(self) -> None:
        art = ArtifactRef(
            artifact_id="a1",
            name="report.md",
            media_type="text/md",
            producer="pm",
            content_hash="x",
        )
        result = TeamResult(status="success", summary="x", artifacts=(art,))
        assert ArtifactPresenceGrader().grade(result, ("report.md",)).passed
        assert not ArtifactPresenceGrader().grade(result, ("missing.md",)).passed

    def test_unknown_grader_fails_closed(self) -> None:
        with pytest.raises(KeyError):
            load_graders(["nope"])


class TestSuiteLoading:
    def test_load_sample_suite(self) -> None:
        cases = load_suite(SUITE_PATH)
        assert [c.case_id for c in cases] == ["triage_01", "dag_chain_01"]
        assert cases[0].spec.run_name == "bench-triage"

    def test_spec_from_dict_defaults(self) -> None:
        spec = spec_from_dict({"run_name": "x"})
        assert spec.project == "bench"
        assert spec.topology.max_parallel == 1


class TestRunSuite:
    async def test_native_succeeds_on_file_based_case(self, tmp_path: Path, monkeypatch) -> None:
        # ADR-092 slice 9: native role turns go through the run's AgentBroker;
        # the bench scripts it (as the plan engine's tests do).
        from r4_teamengine_spi.testing import MemoryAgentBroker

        from r4.teams import service as team_service

        monkeypatch.setattr(
            team_service.TeamService,
            "_agent_broker",
            lambda _self, _spec, _run_id, meter: MemoryAgentBroker({"": "bench ack"}, meter=meter),
        )
        service = TeamService(FakeClient(), run_root=tmp_path)
        cases = [c for c in load_suite(SUITE_PATH) if c.case_id == "triage_01"]
        outcomes = await run_suite(service, cases, ["native"])
        assert len(outcomes) == 1
        assert outcomes[0].status == "success"
        assert outcomes[0].passed
        assert outcomes[0].metrics["llm_calls"] >= 1

    async def test_dag_case_fails_on_native_records_error(self, tmp_path: Path) -> None:
        """Incapable engines surface as failed outcomes, not crashes."""
        service = TeamService(FakeClient(), run_root=tmp_path)
        cases = [c for c in load_suite(SUITE_PATH) if c.case_id == "dag_chain_01"]
        outcomes = await run_suite(service, cases, ["native"])
        assert outcomes[0].status == "failed"
        assert "not expressible" in (outcomes[0].error or "")

    async def test_langgraph_covers_dag_case(self, tmp_path: Path) -> None:
        pytest.importorskip("r4_engine_langgraph")
        service = TeamService(FakeClient(), run_root=tmp_path)
        cases = [c for c in load_suite(SUITE_PATH) if c.case_id == "dag_chain_01"]
        outcomes = await run_suite(service, cases, ["langgraph"])
        assert outcomes[0].status == "success"
        assert outcomes[0].passed

    def test_main_writes_results(self, tmp_path: Path) -> None:
        service = TeamService(FakeClient(), run_root=tmp_path)
        exit_code = main(
            ["--suite", str(SUITE_PATH), "--engines", "native", "--out", str(tmp_path / "out")],
            service=service,
        )
        assert exit_code == 1  # native cannot express dag case — honest signal
        summaries = list((tmp_path / "out").rglob("summary.md"))
        assert summaries, "summary.md not written"
        assert "triage_01" in summaries[0].read_text()
