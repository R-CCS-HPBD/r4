"""Pins for root-agent prompt tailoring (B3, 2026-09-27)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from r4.teamup.prompt_tailor import parse_tailored_sections, tailor_role_prompts
from r4.teamup.roles import AgentRole
from r4.teamup.session import TeamupSession, TeamupSessionConfig


def _client(content: str):
    return SimpleNamespace(
        chat=lambda **kwargs: SimpleNamespace(content=content, tool_calls=[], usage={})
    )


def test_parse_only_accepts_configured_roles_with_real_bodies():
    content = (
        "### ROLE: pm\nYou are the PM running the defect-analysis sweep. " + "q" * 90 + "\n"
        "### ROLE: hacker\nHa. " + "x" * 200 + "\n"
    )
    parsed = parse_tailored_sections(content, ["pm", "se", "pg"])
    assert list(parsed) == ["pm"]  # hacker never lands
    assert "defect-analysis" in parsed["pm"]


def test_parse_rejects_thin_bodies():
    content = "### ROLE: pm\nshort"
    assert parse_tailored_sections(content, ["pm"]) == {}


def test_tailor_writes_overrides_and_persists(tmp_path):
    pm_prompt = "You run the ridge-defect sweep with a strict budget. " + "x" * 180
    pg_prompt = "You implement and try variants for the same sweep. " + "y" * 180
    answer = f"### ROLE: pm\n{pm_prompt}\n\n### ROLE: pg\n{pg_prompt}\n"
    cfg = TeamupSessionConfig(
        name="sweep",
        roles=[AgentRole.PM, AgentRole.PG],
        optimization_goal="find ridge defects",
        hardware_target="rccs-gb10",
    )
    session = TeamupSession(cfg, _client(answer), session_dir=tmp_path / "t")

    result = tailor_role_prompts(session, session.client)

    assert result["root"] == "pm"
    assert sorted(result["tailored"]) == ["pg", "pm"]
    assert result["kept_default"] == []
    assert session.config.role_prompts["pm"] == pm_prompt
    assert session.config.role_prompts["pg"] == pg_prompt
    # Persisted: the session.json on disk carries the overrides.
    session._ensure_session_dir()
    session._save_snapshot()
    on_disk = json.loads((session.session_dir / "session.json").read_text())
    assert on_disk["config"]["role_prompts"]["pm"] == pm_prompt
    status = session.get_status()
    assert status["agents"]["pm"]["prompt"] == pm_prompt
    assert status["agents"]["pm"]["prompt_source"] == "override"


def test_tailor_keeps_default_for_roles_the_answer_skips(tmp_path):
    pm_prompt = "Custom PM for this project. " + "x" * 200
    answer = f"### ROLE: pm\n{pm_prompt}\n"  # pg missing
    cfg = TeamupSessionConfig(name="t", roles=[AgentRole.PM, AgentRole.PG])
    session = TeamupSession(cfg, _client(answer), session_dir=tmp_path / "t2")

    result = tailor_role_prompts(session, session.client)

    assert result["tailored"] == ["pm"]
    assert result["kept_default"] == ["pg"]
    assert session.config.role_prompts.get("pg") is None  # template default stands
    assert session.get_status()["agents"]["pg"]["prompt_source"] == "default"


def test_turn_uses_session_model_override_without_a_role_model(tmp_path):
    answers = []

    def _factory(*a, **kw):
        answers.append(kw.get("model"))

        class _FakeCompanion:
            def on_event(self, cb):
                pass

            def send(self, context, *, extra_system, tools, stream):
                return "ok"

        return _FakeCompanion()

    cfg = TeamupSessionConfig(name="t", roles=[AgentRole.PM])
    session = TeamupSession(cfg, SimpleNamespace(), session_dir=tmp_path / "m1")
    session._subcompanion_factory = _factory
    session._session_model = "deepseek-v4.1-flash"
    session._run_agent_turn_sync(AgentRole.PM)
    assert answers == ["deepseek-v4.1-flash"]

    cfg2 = TeamupSessionConfig(name="t", roles=[AgentRole.PM], role_models={"pm": "model-x"})
    session2 = TeamupSession(cfg2, SimpleNamespace(), session_dir=tmp_path / "m2")
    session2._subcompanion_factory = _factory
    session2._session_model = "deepseek-v4.1-flash"
    session2._run_agent_turn_sync(AgentRole.PM)
    assert answers[-1] == "model-x"  # role_models still wins

    cfg3 = TeamupSessionConfig(name="t", roles=[AgentRole.PM])
    session3 = TeamupSession(cfg3, SimpleNamespace(), session_dir=tmp_path / "m3")
    session3._subcompanion_factory = _factory
    session3._run_agent_turn_sync(AgentRole.PM)
    assert answers[-1] is None  # honest fallback: broker default


def test_status_carries_crew_pack_when_configured(tmp_path):
    from r4.teamup.bootstrap import build_team_config

    # ADR-092 slice 4: a bootstrapped team is the hpc crew; "code" is a plan
    # template now, and only a legacy snapshot still carries it.
    cfg = build_team_config("goal text")
    session = TeamupSession(cfg, SimpleNamespace(), session_dir=tmp_path / "t")
    assert session.get_status()["crew_pack"] == "hpc"
    legacy = TeamupSessionConfig(name="old", roles=[AgentRole.PM], crew_pack="code")
    session_legacy = TeamupSession(legacy, SimpleNamespace(), session_dir=tmp_path / "t3")
    assert session_legacy.get_status()["crew_pack"] == "code"

    cfg2 = TeamupSessionConfig(name="plain", roles=[AgentRole.PM])
    session2 = TeamupSession(cfg2, SimpleNamespace(), session_dir=tmp_path / "t2")
    assert session2.get_status()["crew_pack"] is None


def test_goal_slug_names_sessions_by_object() -> None:
    from r4.teamup.bootstrap import _goal_slug

    assert (
        _goal_slug("add unit tests for the mcp store", "code")
        == "code:add unit tests for the mcp store"
    )
    assert _goal_slug(" prove defect class A ", "research") == "research:prove defect class A"
    assert _goal_slug("   ", "hpc") == "hpc team"


def test_spec_baseline_uses_the_sessions_current_prompts_not_the_hpc_template(tmp_path):
    """The reported bug: a session whose roles run their own prompts (a roster
    spec's instructions, a legacy code/research crew snapshot) must carry
    those prompts as baselines — never the HPC template — so its tailor stays
    in shape. Default sessions keep the template baseline. (Slice 4: the code
    crew itself is a plan template now and never reaches the tailor.)"""
    from r4.teams.crews import CREW_ROLE_PROMPTS
    from r4.teamup.prompt_tailor import _spec_baseline

    code = CREW_ROLE_PROMPTS["code"]
    legacy_code = TeamupSessionConfig(
        name="old code team",
        roles=[AgentRole.PM, AgentRole.PG],
        crew_pack="code",
        role_prompts={"pm": code["architect"], "pg": code["implementer"]},
    )
    code_session = TeamupSession(legacy_code, SimpleNamespace(), session_dir=tmp_path / "c")
    baseline = _spec_baseline(code_session, list(code_session.config.roles))
    assert "You are the architect of a coding team" in baseline
    assert "You are the implementer of a coding team" in baseline
    assert "HPC code optimization" not in baseline
    assert "crew/spec baseline:" in baseline

    default_session = TeamupSession(
        TeamupSessionConfig(name="plain", roles=[AgentRole.PM]),
        SimpleNamespace(),
        session_dir=tmp_path / "d",
    )
    default_baseline = _spec_baseline(default_session, [AgentRole.PM])
    assert "HPC code optimization" in default_baseline
    assert "template baseline:" in default_baseline


def test_tailor_meta_call_failure_degrades_to_baselines_named(tmp_path):
    """Endpoint down (503 class) must NOT kill the start: baselines stay,
    every role is kept_default, and the reason rides the payload (2026-09-29)."""

    def _explode(**_kwargs):
        raise RuntimeError("API error: 503: No active endpoint for model 'rivault-gpt-oss-static'")

    client = SimpleNamespace(chat=_explode)
    cfg = TeamupSessionConfig(
        name="degraded",
        roles=[AgentRole.PM, AgentRole.PG],
        optimization_goal="probe degradation",
    )
    session = TeamupSession(cfg, client, session_dir=tmp_path / "t")

    result = tailor_role_prompts(session, client)

    assert result["tailored"] == []
    assert sorted(result["kept_default"]) == ["pg", "pm"]
    assert "503" in result["degraded"]
    assert "rivault-gpt-oss-static" in result["degraded"]
    # Baselines never overwritten by a failed meta call.
    assert "pm" not in session.config.role_prompts
