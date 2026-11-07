"""A native team run says when its roles failed.

Live on the stand-alone stack 2026-10-09 (`r4 team launch --engine native
--roles pm,se`): the PM's turn failed (a gateway 503) and the SE's turn failed
reading the client's ``Usage`` as a dict, after the model had answered — and the
run reported ``status: success`` with exit 0. Plan §3c.2 had predicted exactly
this class: "a timed-out role is reported inside a *successful* team run".
"""

from __future__ import annotations

from types import SimpleNamespace

from r4_contracts.team_engine.spec import (
    TEAM_SPEC_VERSION,
    AgentSpec,
    TaskSpec,
    TeamSpec,
    Topology,
    WorkflowStyle,
)
from r4_teamengine_spi.testing import make_engine_context, make_services

from r4.teams.native import NativePlan, NativeRun

# The direct ``_chat_via_inner_client`` path and its usage-metering tests are
# gone (ADR-092 slice 9): every native role turn is an AgentTurnRequest to the
# run's AgentBroker, which meters each round (test_native_through_broker.py).


# ── the run's verdict ───────────────────────────────────────────────────────


def _run_with(states):
    spec = TeamSpec(
        spec_version=TEAM_SPEC_VERSION,
        run_name="t",
        project="p",
        task=TaskSpec(objective="o"),
        agents=(
            AgentSpec(name="pm", role="pm", instructions="m"),
            AgentSpec(name="se", role="se", instructions="s"),
        ),
        topology=Topology(style=WorkflowStyle.FILE_BASED),
    )
    run = NativeRun(NativePlan(spec, make_engine_context()), make_services())
    run._status = "running"
    run._session = SimpleNamespace(agent_states=states, get_summary=lambda: "summary")
    run._complete()
    return run._result


def _state(errors=(), sent=0):
    return SimpleNamespace(errors=list(errors), messages_sent=sent)


def test_every_role_failing_fails_the_run():
    result = _run_with(
        {"pm": _state(["API error: 503"]), "se": _state(["'Usage' object has no attribute 'get'"])}
    )
    assert result.status == "failed"
    assert "pm: API error: 503" in result.error and "se: 'Usage'" in result.error


def test_some_roles_failing_is_partial_and_named():
    result = _run_with({"pm": _state(["API error: 503"]), "se": _state(sent=1)})
    assert result.status == "partial"
    assert result.error == "roles_failed: pm: API error: 503"


def test_a_clean_run_is_a_success():
    result = _run_with({"pm": _state(sent=1), "se": _state(sent=1)})
    assert (result.status, result.error) == ("success", None)


def test_launch_exits_non_zero_on_a_failed_run(monkeypatch, capsys):
    import pytest

    from r4._cli import teamup

    failed = SimpleNamespace(status="failed", error="roles_failed: no role completed a turn")

    async def fake_stream(*_a):
        return failed

    class _Service:
        def __init__(self, _client, **_kwargs):
            pass

        def resolve(self, _spec, requested=None):
            return SimpleNamespace(engine_name="native", reason="explicit request")

    monkeypatch.setattr(teamup, "_launch_stream", fake_stream)
    monkeypatch.setattr("r4.teams.TeamService", _Service)
    args = SimpleNamespace(
        spec=None,
        team=None,
        name="t",
        goal="g",
        roles="pm,se",
        engine="native",
        max_iterations=1,
        max_tokens=None,
        dry_run=False,
    )
    with pytest.raises(SystemExit) as exit_info:
        teamup._cmd_team_launch(args, None)
    assert exit_info.value.code == 1
    assert "roles_failed" in capsys.readouterr().err


def test_runs_live_beside_the_rest_of_r4s_state(monkeypatch, tmp_path):
    import importlib

    from r4.teams import service

    monkeypatch.delenv("R4_TEAM_RUNS_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    try:
        assert importlib.reload(service).TEAM_RUNS_DIR == tmp_path / "r4" / "team_runs"
    finally:
        monkeypatch.undo()
        importlib.reload(service)
