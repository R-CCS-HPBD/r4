"""One team seam (ADR-072 D-1, closed by ADR-092 slice 3).

The chat surfaces (the ``teamup_start`` tool, the bridge's ``teamup.start``
and ``teamup.create``, the teamup CLIs) built the teamup runtime directly,
beside the Team Engine SPI. They now ask ``TeamService.open_team_session``,
which takes the engine from the registry (SPI check, site policy) and lets
the engine build the session. These pins keep it that way:

- a source scan: outside the engines' own modules, nothing in ``r4/src``
  constructs the team runtime or an engine run (``TeamupSession(...)``,
  ``TeamupSession.create(...)``, ``NativeRun(...)``, ``PlanRun(...)``,
  ``NativeEngine(...)``, ``PlanEngine(...)``);
- each former bypass goes through the service;
- the service applies the site policy and the lead's handles, and lists
  every run (plan runs and teams) for the Work pane.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from r4_teamengine_spi.errors import PluginTrustError

import r4
from r4.teams.service import TeamService
from r4.teamup.session import SessionStatus, TeamupSessionConfig

SRC = Path(r4.__file__).resolve().parent

# Constructor name -> the only modules (relative to r4/) that may call it.
ALLOWED_CONSTRUCTIONS: dict[str, frozenset[str]] = {
    "TeamupSession": frozenset({"teams/native.py"}),
    "NativeRun": frozenset({"teams/native.py"}),
    "PlanRun": frozenset({"teams/plan/engine.py"}),
    "NativeEngine": frozenset(),
    "PlanEngine": frozenset(),
}
# Factory methods on the runtime that construct one (``cls(...)`` inside).
FORBIDDEN_FACTORIES = {("TeamupSession", "create")}


def _constructions() -> set[tuple[str, str]]:
    """Every (module, name) where a guarded constructor or factory is called."""
    found: set[tuple[str, str]] = set()
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in ALLOWED_CONSTRUCTIONS:
                found.add((rel, func.id))
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and (func.value.id, func.attr) in FORBIDDEN_FACTORIES
            ):
                found.add((rel, f"{func.value.id}.{func.attr}"))
    return found


def test_nothing_outside_the_engines_constructs_the_team_runtime() -> None:
    found = _constructions()
    bypasses = sorted(
        (module, name)
        for module, name in found
        if module not in ALLOWED_CONSTRUCTIONS.get(name, frozenset())
    )
    assert bypasses == [], (
        "construct teams through TeamService (open_team_session / submit), "
        f"never directly (ADR-072 D-1, ADR-092 slice 3): {bypasses}"
    )
    # The scan is not vacuous: it sees the one sanctioned construction.
    assert ("teams/native.py", "TeamupSession") in found


# ── each former bypass goes through the service ─────────────────────────────


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.session = SimpleNamespace(
            session_id="team-1",
            config=SimpleNamespace(name="n", roles=[], crew_pack="research"),
            status=SessionStatus.PAUSED,
            _session_model=None,
        )

    def __call__(self, service: TeamService, config: Any, **kwargs: Any) -> Any:
        self.calls.append({"service": service, "config": config, **kwargs})
        return self.session


@pytest.fixture
def recorder(monkeypatch) -> _Recorder:
    rec = _Recorder()

    def _open(self: TeamService, config: Any, **kwargs: Any) -> Any:
        return rec(self, config, **kwargs)

    monkeypatch.setattr(TeamService, "open_team_session", _open)
    return rec


def test_bootstrap_team_opens_the_team_through_the_service(recorder, monkeypatch) -> None:
    import r4.teamup.bootstrap as bootstrap

    monkeypatch.setattr(bootstrap, "tailor_role_prompts", lambda *a, **kw: {"tailored": []})
    client = SimpleNamespace()
    session, _tailor = bootstrap.bootstrap_team(client, goal="map the defect class")
    assert session is recorder.session
    (call,) = recorder.calls
    assert call["config"].optimization_goal == "map the defect class"
    assert call["chat_client"] is client

    mine = TeamService(client, session_model="m")
    bootstrap.bootstrap_team(client, goal="g", service=mine)
    assert recorder.calls[-1]["service"] is mine


def test_bridge_teamup_create_opens_the_team_through_the_service(recorder) -> None:
    from r4.companion.ui.bridge_server import CompanionBridgeServer

    bridge = CompanionBridgeServer.__new__(CompanionBridgeServer)
    bridge._session = None
    bridge._team_client = SimpleNamespace()
    bridge._client = None
    reply = bridge.teamup_create({"name": "crew-a", "roles": "pm,se"})
    (call,) = recorder.calls
    assert call["config"].name == "crew-a"
    assert reply["session_id"] == "team-1" and reply["roles"] == ["pm", "se"]


def test_bridge_teamup_start_bootstraps_through_a_service_with_the_chat_model(
    recorder, monkeypatch
) -> None:
    import r4.teamup.bootstrap as bootstrap
    from r4.companion.ui.bridge_server import CompanionBridgeServer

    monkeypatch.setattr(bootstrap, "tailor_role_prompts", lambda *a, **kw: {})
    bridge = CompanionBridgeServer.__new__(CompanionBridgeServer)
    lines: list[dict[str, Any]] = []
    bridge._emit_line = lines.append
    bridge._session = SimpleNamespace(client=SimpleNamespace())
    bridge._teamup_bootstrapping = True
    bridge._teamup_bootstrap_thread(spec={"goal": "g", "crew": "hpc"}, run=False, model="m-1")
    (call,) = recorder.calls
    assert call["service"]._session_model == "m-1"
    assert lines[-1]["team_event"]["kind"] == "team_started"


def test_the_chat_tool_opens_the_team_through_a_service_with_the_lead_handles(
    recorder, monkeypatch
) -> None:
    import r4.teamup.bootstrap as bootstrap
    from r4.companion.permissions import PermissionManager
    from r4.companion.tools import get_builtin_tools

    monkeypatch.setattr(bootstrap, "tailor_role_prompts", lambda *a, **kw: {})
    manager = PermissionManager()
    hooks = (lambda *_a: None, lambda *_a: None)
    tools = get_builtin_tools(
        SimpleNamespace(), session_model="m", permission_manager=manager, usage_hooks=hooks
    )
    start = next(t for t in tools if t["name"] == "teamup_start")
    assert json.loads(start["execute"](goal="g", crew="hpc", run=False))["ok"] is True
    service = recorder.calls[-1]["service"]
    assert (service._permission_manager, service._session_model, service._usage_hooks) == (
        manager,
        "m",
        hooks,
    )


def test_the_teamup_cli_create_opens_the_team_through_the_service(recorder, monkeypatch) -> None:
    import r4.project
    from r4._cli import teamup as teamup_cli

    monkeypatch.setattr(
        r4.project, "get_project_manager", lambda: SimpleNamespace(get_current=lambda: None)
    )
    args = SimpleNamespace(
        name="cli-crew",
        roles="pm,se,pg",
        hardware="local",
        budget=10,
        max_iterations=2,
        code=None,
        goal="g",
        target_perf=None,
        auto_approve=False,
        crew=None,
        start=False,
    )
    teamup_cli._cmd_teamup_create(args, SimpleNamespace())
    assert recorder.calls[-1]["config"].name == "cli-crew"


# ── the service itself ──────────────────────────────────────────────────────


@pytest.fixture
def team_store(monkeypatch, tmp_path) -> Path:
    import r4.teamup.session as session_module

    root = tmp_path / "teams"
    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", root)
    return root


def test_open_team_session_persists_and_applies_the_lead_handles(team_store) -> None:
    manager, hooks = object(), (object(), object())
    service = TeamService(
        SimpleNamespace(), permission_manager=manager, session_model="m", usage_hooks=hooks
    )
    session = service.open_team_session(TeamupSessionConfig(name="crew"), running=True)
    assert (team_store / session.session_id / "session.json").is_file()
    assert session.status is SessionStatus.RUNNING
    assert (session._permissions, session._session_model, session._usage_hooks) == (
        manager,
        "m",
        hooks,
    )


def test_a_site_that_disables_native_refuses_interactive_teams_by_name(team_store) -> None:
    service = TeamService(SimpleNamespace(), policy={"enabled": {"native": False}})
    with pytest.raises(PluginTrustError, match="disabled by site policy"):
        service.open_team_session(TeamupSessionConfig(name="crew"))
    assert not team_store.exists() or not any(team_store.iterdir())


def test_list_runs_reads_each_run_from_its_own_records(team_store, tmp_path) -> None:
    runs = tmp_path / "runs"
    native = runs / "native-run"
    native.mkdir(parents=True)
    (native / "resolution.json").write_text(
        json.dumps({"run_id": "native-run", "engine": "native", "run_name": "r", "attempt": 1})
    )
    (native / "events.jsonl").write_text(
        json.dumps({"kind": "run_started"}) + "\n" + json.dumps({"kind": "run_failed"}) + "\n"
    )
    orphan = runs / "orphan"
    orphan.mkdir()
    (orphan / "resolution.json").write_text(json.dumps({"engine": "plan", "run_name": "o"}))
    (runs / "not-a-run").mkdir()
    service = TeamService(SimpleNamespace(), run_root=runs)
    team = service.open_team_session(TeamupSessionConfig(name="crew"))

    rows = {row["run_id"]: row for row in service.list_runs()}
    assert set(rows) == {"native-run", "orphan", team.session_id}
    assert (rows["native-run"]["kind"], rows["native-run"]["status"]) == ("spec", "failed")
    assert (rows["orphan"]["kind"], rows["orphan"]["status"]) == ("plan", "unknown")
    assert "cost" not in rows[team.session_id]  # the compute budget is retired (slice 4)
    assert len(service.list_runs(limit=1)) == 1
