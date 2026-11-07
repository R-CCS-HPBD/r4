"""H19 (TB 4.0 harness plan, 2026-10-24): prompt defects every user saw.

Measured on the TB 4.0 dumps: the skill catalog appeared twice (the discovery
catalog and "Companion Skills" in <project_state>), bodies of platform skills
were injected while their tools were gated, an "Ask the user" line contradicted
the unattended run (nobody to ask), and the code skill steered to compile_code/
run_code/test_code, which no trial used. ``R4_COMPANION_PROMPT_HYGIENE=0``
restores the old assembly (review 2026-10-24: byte-identical, see
``test_agent_profile.py``).
"""

from __future__ import annotations

import pytest

from r4.companion import status_report
from r4.companion.skills import Skill, SkillRegistry

from .offline_session import first_request

_PLATFORM_SKILLS = ("workflow_ops", "data_management", "model_management", "platform_ops")


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    for name in ("R4_COMPANION_PROMPT_HYGIENE", "R4_AGENT_PROFILE", "R4_WEB_ACCESS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("R4_AUTONOMOUS", raising=False)


def test_skill_catalog_is_listed_once(monkeypatch, tmp_path):
    system, _, _ = first_request(monkeypatch, tmp_path)
    assert system.count("[Skills available (bodies load on activation only)]") == 1
    assert "## Companion Skills" not in system
    assert system.count("- academic-plotting") == 1


def test_project_status_outside_a_turn_keeps_the_skills_section():
    """The TUI's /status (no agent turn running) still lists the skills."""
    from .offline_session import OfflineClient

    text = status_report.build_project_status(OfflineClient(), include_local=True)
    assert "## Companion Skills" in text
    with status_report.skills_listed_in_prompt():
        inside = status_report.build_project_status(OfflineClient(), include_local=True)
    assert "## Companion Skills" not in inside


def test_platform_skill_bodies_stay_out_while_the_platform_is_down(monkeypatch, tmp_path):
    system, _, session = first_request(monkeypatch, tmp_path)
    active = {skill.name for skill in session.skills.list_active()}
    assert set(_PLATFORM_SKILLS) <= active  # still active: the task's words matched
    for name in _PLATFORM_SKILLS:
        assert f"[Skill: {name}]" not in system
    assert "[Skills not loaded: " in system
    assert "workflow_ops" in system.split("[Skills not loaded: ", 1)[1].split("]", 1)[0]
    # teamup runs local team plans: its tools stay offered, so its body stays.
    assert "[Skill: teamup]" in system
    assert "[Skill: code]" in system


def test_platform_skill_bodies_load_with_a_live_platform(monkeypatch, tmp_path):
    from r4.companion import tool_availability

    monkeypatch.setattr(tool_availability, "platform_live", lambda _client: True)
    system, _, _ = first_request(monkeypatch, tmp_path)
    assert "[Skill: workflow_ops]" in system
    assert "[Skills not loaded: " not in system


def test_unattended_run_never_says_ask_the_user(monkeypatch, tmp_path):
    monkeypatch.setenv("R4_AUTONOMOUS", "1")
    system, _, _ = first_request(monkeypatch, tmp_path, trust=True)
    assert "Ask the user before reading or writing paths outside this root." not in system
    assert "Stay inside this root unless the task names another path." in system


def test_interactive_trust_keeps_the_ask_line(monkeypatch, tmp_path):
    """Review 2026-10-24: the TUI's autopilot and --allow-all-tools run TRUST with the
    user present; the line is the last guard for writes outside the root there."""
    system, _, _ = first_request(monkeypatch, tmp_path, trust=True)
    assert "Ask the user before reading or writing paths outside this root." in system


def test_ask_mode_keeps_the_ask_line(monkeypatch, tmp_path):
    system, _, _ = first_request(monkeypatch, tmp_path, trust=False)
    assert "Ask the user before reading or writing paths outside this root." in system


def test_code_skill_verifies_with_the_projects_own_commands():
    body = SkillRegistry().get("code").system_prompt
    assert "Prefer compile_code, run_code, and test_code" not in body
    assert "6. Verify with the project's own build and test commands" in body
    assert "8. Never push code or run destructive shell commands without confirmation." in body


def _code_prompt() -> str:
    registry = SkillRegistry()
    registry.activate("code")
    return registry.active_system_prompt()


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_off_switch_restores_the_code_skills_old_wording(monkeypatch, value):
    """Review 2026-10-24: the rewrite was unconditional, so ``=0`` was not the old prompt."""
    monkeypatch.setenv("R4_COMPANION_PROMPT_HYGIENE", value)
    prompt = _code_prompt()
    assert "6. Prefer compile_code, run_code, and test_code for local verification" in prompt
    assert "re-verify with compile_code or test_code before\n  presenting the result" in prompt
    assert "Verify with the project's own build" not in prompt


def test_project_status_inside_a_tool_worker_thread_leaves_out_the_skills():
    """Review 2026-10-24: tools run in a ThreadPoolExecutor and the marker (a
    ContextVar) did not reach it; the agent now runs them in the turn's context."""
    from types import SimpleNamespace

    from r4.companion.agent import ResearchCompanion

    from .offline_session import OfflineClient

    agent = ResearchCompanion(SimpleNamespace(_config=SimpleNamespace(model="m")), model="m")
    agent._turn_cancel = object()  # sessions always set it: the pool path
    agent._check_turn_cancel = lambda: None
    client = OfflineClient()
    tool = {"execute": lambda: status_report.build_project_status(client, include_local=True)}
    with status_report.skills_listed_in_prompt():
        text = agent._invoke_tool_executor_cancellable("project_status", tool, {})
    assert "## Companion Skills" not in text


def test_body_filter_names_the_omitted_skills():
    registry = SkillRegistry()
    registry.register(Skill(name="p", description="d", system_prompt="P-BODY"))
    registry.register(Skill(name="k", description="d", system_prompt="K-BODY"))
    registry.activate("p")
    registry.activate("k")
    registry.set_body_filter(lambda skill: "offline" if skill.name == "p" else "")
    prompt = registry.active_system_prompt()
    assert "K-BODY" in prompt
    assert "P-BODY" not in prompt
    assert "[Skills not loaded: p (offline)]" in prompt


def test_off_switch_restores_the_old_assembly(monkeypatch, tmp_path):
    monkeypatch.setenv("R4_COMPANION_PROMPT_HYGIENE", "0")
    system, _, _ = first_request(monkeypatch, tmp_path, trust=True)
    assert "## Companion Skills" in system
    assert "[Skill: workflow_ops]" in system
    assert "Ask the user before reading or writing paths outside this root." in system
