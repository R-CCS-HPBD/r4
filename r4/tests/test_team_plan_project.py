"""Team launches carry the real project; plan runs resume as attempt N+1.

ADR-092 slice-1 fixes: the CLI scoped every launch to a hard-coded ``"cli"``
project, and every run was ``attempt-1`` whatever happened before it. LLM-free:
the service runs over a fake registry and a fake plan engine module.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import types
from argparse import ArgumentParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from r4_contracts.team_engine.capabilities import (
    EngineCapabilities,
    EngineDescriptor,
    TrustTier,
)
from r4_contracts.team_engine.spec import (
    TEAM_SPEC_VERSION,
    AgentSpec,
    BudgetSpec,
    TaskSpec,
    TeamSpec,
    Topology,
    WorkflowStyle,
)

from r4._cli import teamup as teamup_cli
from r4.teams.agent_broker import RoleTurnAgentBroker
from r4.teams.service import TeamService

# ---------------------------------------------------------------------------
# CLI: the launch project
# ---------------------------------------------------------------------------


class _ProjectManager:
    def __init__(self, current: str | None) -> None:
        self._current = current

    def get_current(self) -> str | None:
        return self._current


def _client(project_id: str = "") -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(project_id=project_id))


@pytest.fixture
def active_project(monkeypatch):
    """Set what ``get_project_manager().get_current()`` reports."""

    def _set(value: str | None) -> None:
        import r4.project

        monkeypatch.setattr(r4.project, "get_project_manager", lambda: _ProjectManager(value))

    return _set


def _launch_args(*extra: str):
    parser = ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    teamup_cli.build_teamup_parser(sub)
    return parser.parse_args(["teamup", "launch", "demo", "--dry-run", *extra])


def _dry_run_project(capsys, client, *extra: str) -> str:
    teamup_cli._cmd_team_launch(_launch_args(*extra), client)
    out = capsys.readouterr().out
    line = next(row for row in out.splitlines() if row.strip().startswith("project:"))
    return line.split("project:", 1)[1].strip()


class TestLaunchProject:
    def test_launch_project_is_the_active_project_not_cli(self, capsys, active_project) -> None:
        active_project("proj-active")
        assert _dry_run_project(capsys, _client("ident")) == "proj-active"

    def test_research_team_launch_uses_the_active_project(self, active_project) -> None:
        active_project("proj-active")
        args = _launch_args("--team", "research")
        spec = teamup_cli._team_launch_spec(args, teamup_cli._launch_project(args, _client()))
        assert spec.project == "proj-active"

    def test_identity_project_when_none_is_active(self, capsys, active_project) -> None:
        active_project(None)
        assert _dry_run_project(capsys, _client("ident-proj")) == "ident-proj"

    def test_project_flag_naming_the_active_project_is_used(self, capsys, active_project) -> None:
        active_project("proj-active")
        assert _dry_run_project(capsys, _client(), "--project", "proj-active") == "proj-active"

    def test_project_flag_naming_another_project_is_refused(self, capsys, active_project) -> None:
        """The ADR-062 gate checks the ACTIVE project, so a run labelled with
        another one would be gated as the wrong project (review finding 39)."""
        active_project("proj-active")
        with pytest.raises(SystemExit) as exit_info:
            _dry_run_project(capsys, _client(), "--project", "explicit")
        assert exit_info.value.code == 2

    def test_no_project_is_empty_with_a_notice(self, capsys, active_project) -> None:
        active_project(None)
        assert teamup_cli._active_project_id(_client("")) == ""
        assert "no active project" in capsys.readouterr().err


class TestSpecFile:
    def test_spec_file_passes_context_and_plan_through(self, tmp_path, active_project) -> None:
        active_project("proj-active")
        raw = {
            "run_name": "from-file",
            "agents": [{"name": "a", "role": "pm", "instructions": "x"}],
            "context": {"default_model": "m1"},
            "plan": {"plan_version": 1, "name": "p"},
        }
        path = tmp_path / "spec.json"
        path.write_text(json.dumps(raw))
        args = _launch_args("--spec", str(path))
        spec = teamup_cli._team_launch_spec(args, teamup_cli._launch_project(args, _client()))
        assert spec.project == "proj-active"
        assert spec.context == {"default_model": "m1"}
        assert spec.plan == {"plan_version": 1, "name": "p"}

    def test_file_project_beats_active_and_flag_beats_file(self, tmp_path, active_project) -> None:
        active_project("proj-active")
        path = tmp_path / "spec.json"
        path.write_text(json.dumps({"run_name": "f", "project": "from-file"}))
        args = _launch_args("--spec", str(path))
        spec = teamup_cli._team_launch_spec(args, teamup_cli._launch_project(args, _client()))
        assert spec.project == "from-file"
        args = _launch_args("--spec", str(path), "--project", "flag")
        spec = teamup_cli._team_launch_spec(args, teamup_cli._launch_project(args, _client()))
        assert spec.project == "flag"


# ---------------------------------------------------------------------------
# TeamService: attempts and resume
# ---------------------------------------------------------------------------


class _Compiled:
    plan_hash = "hash-1"


class _FakeRun:
    def __init__(self, services) -> None:
        self.services = services


class _FakePlanEngine:
    """Accepts any spec; ``start`` hands back the services it was given."""

    def __init__(self) -> None:
        self.compiled: list[TeamSpec] = []

    def descriptor(self) -> EngineDescriptor:
        return EngineDescriptor(
            name="fakeplan",
            version="0.1.0",
            spi_version="0.1",
            trust_tier=TrustTier.CORE,
            capabilities=EngineCapabilities(
                workflow_styles=(WorkflowStyle.PLAN,), supports_parallel_agents=True
            ),
        )

    def validate(self, _spec):
        return []

    def compile(self, spec, _ctx):
        self.compiled.append(spec)
        return _Compiled()

    async def start(self, _plan, services):
        return _FakeRun(services)


class _FakeRegistry:
    def __init__(self) -> None:
        self.engine = _FakePlanEngine()

    def descriptors(self):
        return {"fakeplan": self.engine.descriptor()}

    def get(self, _name):
        return self.engine

    def list(self):
        return []


class _FakeClient:
    def list_models(self):
        return []


def _plan_spec(run_name: str = "plan-run") -> TeamSpec:
    return TeamSpec(
        spec_version=TEAM_SPEC_VERSION,
        run_name=run_name,
        project="proj-a",
        task=TaskSpec(objective="review"),
        agents=(AgentSpec(name="finder", role="plan-profile", instructions="find"),),
        topology=Topology(style=WorkflowStyle.PLAN, max_parallel=3),
        budgets=BudgetSpec(max_tokens=5000),
        context={"default_model": "m-default"},
        plan={"plan_version": 1, "name": "p", "phases": [{"id": "find"}, {"id": "verify"}]},
    )


@pytest.fixture
def fake_plan_modules(monkeypatch):
    """Stand-ins for ``r4.teams.plan.engine`` and ``r4.teams.plan.journal``."""
    calls: dict[str, list] = {"spec_from_plan": [], "invalidate": []}

    def spec_from_plan(raw_plan, args, **kwargs):
        calls["spec_from_plan"].append((raw_plan, args, kwargs))
        return _plan_spec(kwargs.get("run_name") or "plan-run"), []

    class PlanJournal:
        def __init__(self, run_dir) -> None:
            self.run_dir = run_dir

        def invalidate_from_phase(self, program, phase_id) -> None:
            calls["invalidate"].append((self.run_dir, program, phase_id))

    engine_mod = types.ModuleType("r4.teams.plan.engine")
    engine_mod.spec_from_plan = spec_from_plan
    journal_mod = types.ModuleType("r4.teams.plan.journal")
    journal_mod.PlanJournal = PlanJournal
    # ADR-092 slice 7: the journal module also names the phases a
    # --from-phase drops (written to UMRS as a marker); the stand-in mirrors it.
    journal_mod.phases_from = lambda program, phase_id: [phase_id]
    monkeypatch.setitem(sys.modules, "r4.teams.plan.engine", engine_mod)
    monkeypatch.setitem(sys.modules, "r4.teams.plan.journal", journal_mod)
    return calls


def _manifest(run_dir: Path) -> dict:
    return json.loads((run_dir / "resolution.json").read_text())


def _record_plan_files(run_dir: Path) -> None:
    """What the plan engine writes on its first events() iteration."""
    (run_dir / "plan.json").write_text(json.dumps({"plan_version": 1, "name": "raw"}))
    (run_dir / "params.json").write_text(json.dumps({"target": "src/"}))


class TestAttempts:
    async def test_fresh_submit_is_attempt_one(self, tmp_path: Path) -> None:
        service = TeamService(_FakeClient(), registry=_FakeRegistry(), run_root=tmp_path)
        handle = await service.submit(_plan_spec(), engine="fakeplan")
        assert handle.services.run.attempt_id == "attempt-1"
        # ADR-092 D-3: every run gets the governed role-turn broker.
        assert isinstance(handle.services.agents, RoleTurnAgentBroker)
        manifest = _manifest(handle.run_dir)
        assert (manifest["attempt"], manifest["project"], manifest["run_name"]) == (
            1,
            "proj-a",
            "plan-run",
        )

    async def test_explicit_run_id_reuses_the_run_dir(self, tmp_path: Path) -> None:
        service = TeamService(_FakeClient(), registry=_FakeRegistry(), run_root=tmp_path)
        handle = await service.submit(
            _plan_spec(), engine="fakeplan", run_id="fixed-run", attempt=4
        )
        assert handle.run_dir == tmp_path / "fixed-run"
        assert handle.services.run.attempt_id == "attempt-4"
        assert _manifest(handle.run_dir)["attempt"] == 4

    async def test_attempt_id_increments_on_resume(self, tmp_path: Path, fake_plan_modules) -> None:
        service = TeamService(_FakeClient(), registry=_FakeRegistry(), run_root=tmp_path)
        first = await service.submit(_plan_spec(), engine="fakeplan")
        first.services.events.emit("run_started", ("engine", "fakeplan"), {})
        _record_plan_files(first.run_dir)

        second = await service.resume(first.run_id)
        assert second.run_id == first.run_id and second.run_dir == first.run_dir
        assert second.services.run.attempt_id == "attempt-2"
        assert _manifest(first.run_dir)["attempt"] == 2
        assert second.services.events.emit("run_started", ("engine", "fakeplan"), {}).seq == 1

        third = await service.resume(first.run_id)
        assert third.services.run.attempt_id == "attempt-3"

    async def test_resume_rebuilds_the_spec_from_recorded_files(
        self, tmp_path: Path, fake_plan_modules
    ) -> None:
        service = TeamService(_FakeClient(), registry=_FakeRegistry(), run_root=tmp_path)
        first = await service.submit(_plan_spec(), engine="fakeplan")
        _record_plan_files(first.run_dir)
        await service.resume(first.run_id)

        raw_plan, args, kwargs = fake_plan_modules["spec_from_plan"][0]
        assert raw_plan == {"plan_version": 1, "name": "raw"}
        assert args == {"target": "src/"}
        assert kwargs == {
            "project": "proj-a",
            "run_name": "plan-run",
            "max_tokens": 5000,
            "concurrency": 3,
            "default_model": "m-default",
            # ADR-092 D-6 (2026-10-11): the MCP declarations the run was
            # validated with ride the manifest back into the rebuild.
            "tool_capabilities": {},
        }
        assert fake_plan_modules["invalidate"] == []

    async def test_resume_from_phase_invalidates_the_journal(
        self, tmp_path: Path, fake_plan_modules
    ) -> None:
        service = TeamService(_FakeClient(), registry=_FakeRegistry(), run_root=tmp_path)
        first = await service.submit(_plan_spec(), engine="fakeplan")
        _record_plan_files(first.run_dir)
        await service.resume(first.run_id, from_phase="verify")
        run_dir, program, phase = fake_plan_modules["invalidate"][0]
        assert (run_dir, phase) == (first.run_dir, "verify")
        assert program == _plan_spec().plan

    async def test_resume_without_recorded_plan_names_the_missing_file(
        self, tmp_path: Path, fake_plan_modules
    ) -> None:
        service = TeamService(_FakeClient(), registry=_FakeRegistry(), run_root=tmp_path)
        first = await service.submit(_plan_spec(), engine="fakeplan")
        with pytest.raises(FileNotFoundError, match="plan.json"):
            await service.resume(first.run_id)

    async def test_resume_with_invalid_plan_raises_every_problem(
        self, tmp_path: Path, fake_plan_modules, monkeypatch
    ) -> None:
        from r4_contracts.team_engine.plan import PlanInvalid, PlanProblem

        problem = PlanProblem("phases", "unknown_key", "bad")
        monkeypatch.setattr(
            sys.modules["r4.teams.plan.engine"], "spec_from_plan", lambda *a, **k: (None, [problem])
        )
        service = TeamService(_FakeClient(), registry=_FakeRegistry(), run_root=tmp_path)
        first = await service.submit(_plan_spec(), engine="fakeplan")
        _record_plan_files(first.run_dir)
        with pytest.raises(PlanInvalid) as excinfo:
            await service.resume(first.run_id)
        assert excinfo.value.problems == [problem]

    async def test_resume_round_trips_through_the_real_plan_engine(self, tmp_path: Path) -> None:
        """What the plan run records (the expanded program and its bound
        params) rebuilds the identical spec, so journal keys still match."""
        engine_mod = pytest.importorskip("r4.teams.plan.engine")
        template = Path(engine_mod.__file__).parent / "templates" / "adversarial-review.json"
        spec, problems = engine_mod.spec_from_plan(
            json.loads(template.read_text()),
            {"target": "src/"},
            project="proj-a",
            run_name="review",
            default_model="m1",
        )
        assert problems == []
        registry = _FakeRegistry()
        service = TeamService(_FakeClient(), registry=registry, run_root=tmp_path)
        first = await service.submit(spec, engine="fakeplan")
        (first.run_dir / "plan.json").write_text(json.dumps(spec.plan))
        (first.run_dir / "params.json").write_text(json.dumps(spec.plan["_bound_params"]))

        second = await service.resume(first.run_id)
        assert second.services.run.attempt_id == "attempt-2"
        # ADR-092 slice 5: submit pins the project root into the spec's
        # context (resolution.json records it), so the resumed spec is the
        # submitted one, root included.
        pinned = dataclasses.replace(
            spec, context={**spec.context, "project_root": os.path.realpath(os.getcwd())}
        )
        assert registry.engine.compiled[0] == pinned
        assert registry.engine.compiled[-1] == pinned

    @pytest.mark.parametrize("bad", ["../escape", "a/b", "..", "", "a\\b"])
    async def test_run_ids_that_leave_the_run_root_are_refused(
        self, tmp_path: Path, fake_plan_modules, bad: str
    ) -> None:
        service = TeamService(_FakeClient(), registry=_FakeRegistry(), run_root=tmp_path / "runs")
        with pytest.raises(ValueError, match="invalid team run id"):
            await service.resume(bad)
        if bad:  # "" means "mint a fresh id" for submit
            with pytest.raises(ValueError, match="invalid team run id"):
                await service.submit(_plan_spec(), engine="fakeplan", run_id=bad)
        assert not (tmp_path / "escape").exists()

    def test_governance_handles_are_held(self, tmp_path: Path) -> None:
        hooks = (lambda *_: None, lambda *_: None)
        service = TeamService(
            _FakeClient(),
            registry=_FakeRegistry(),
            run_root=tmp_path,
            permission_manager="pm",
            session_model="m",
            usage_hooks=hooks,
        )
        assert (service._permission_manager, service._session_model) == ("pm", "m")
        assert service._usage_hooks is hooks
