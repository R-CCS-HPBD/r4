"""Tests for companion turn helper functions."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.companion.session_models import SessionMessage
from r4.companion.session_turns import (
    active_tools,
    assemble_extra_system,
    assemble_legacy_extra_system,
    assemble_turn_reminder,
    build_history,
    prepare_turn_payload,
)


def test_build_history_excludes_latest_message():
    history = build_history(
        [
            SessionMessage(role="system", content="s"),
            SessionMessage(role="user", content="u"),
            SessionMessage(role="assistant", content="a"),
        ]
    )

    assert history == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]


def test_active_tools_filters_by_active_tool_names():
    tools = [{"name": "vision_submit"}, {"name": "hpc_submit"}]

    assert active_tools(tools=tools, active_tool_names=set()) == [
        {"name": "hpc_submit"},
        {"name": "vision_submit"},
    ]
    assert active_tools(tools=tools, active_tool_names={"hpc_submit"}) == [{"name": "hpc_submit"}]


def test_active_tools_never_filters_the_workspace_tools():
    """W3 (2026-10-05): a skill filter narrows domain tools, not shell/file tools."""
    tools = [{"name": "shell_exec"}, {"name": "file_edit"}, {"name": "vision_submit"}]
    names = {t["name"] for t in active_tools(tools=tools, active_tool_names={"hpc_submit"})}
    assert names == {"shell_exec", "file_edit"}


def test_active_tools_keeps_always_on_meta_tools():
    tools = [
        {"name": "ask_user"},
        {"name": "file_read"},
        {"name": "final_output"},
        {"name": "platform_guide"},
        {"name": "todo_write"},
        {"name": "tool_search"},
    ]
    filtered = active_tools(tools=tools, active_tool_names={"file_read"})
    names = {t["name"] for t in filtered}
    assert names == {
        "ask_user",
        "file_read",
        "final_output",
        "platform_guide",
        "todo_write",
        "tool_search",
    }


def test_active_tools_filtered_order_is_stable_by_name():
    tools = [{"name": "shell_exec"}, {"name": "file_read"}, {"name": "python_exec"}]

    assert active_tools(
        tools=tools,
        active_tool_names={"python_exec", "shell_exec", "file_read"},
    ) == [{"name": "file_read"}, {"name": "python_exec"}, {"name": "shell_exec"}]


def _turn_reminder_with_everything() -> str:
    harness = MagicMock()
    harness.feedforward.return_value = "guides"
    return assemble_turn_reminder(
        skills_prompt="skills",
        memory_context="memory",
        session_context="session",
        harness=harness,
        messages=[SessionMessage(role="user", content="hello")],
        active_endpoints_prompt="endpoints",
        logger=MagicMock(),
    )


def test_turn_reminder_includes_harness_and_endpoint_context():
    reminder = _turn_reminder_with_everything()
    assert reminder == "skills\n\nmemory\n\nsession\n\nguides\n\nendpoints"


def test_assemble_extra_system_is_the_stable_spine_and_catalog():
    """Wave 80: the stable block carries no per-turn part."""
    from r4_contracts.agent_tool_playbook import PLATFORM_GUIDE_POINTER, SPINE_DISAMBIGUATION

    extra_system = assemble_extra_system(skills_discovery_prompt="[Skills available]")
    assert extra_system.startswith(SPINE_DISAMBIGUATION.strip())
    assert PLATFORM_GUIDE_POINTER in extra_system
    assert extra_system.endswith("[Skills available]")
    assert assemble_extra_system(platform_guidance=False) == ""


def test_legacy_extra_system_keeps_the_old_order():
    """``R4_COMPANION_STABLE_PREFIX=0`` / hygiene off: the pre-wave-80 block."""
    from r4_contracts.agent_tool_playbook import PLATFORM_GUIDE_POINTER, SPINE_DISAMBIGUATION

    legacy = assemble_legacy_extra_system(
        skills_prompt="skills", skills_discovery_prompt="catalog", turn_parts="rest"
    )
    spine = f"{SPINE_DISAMBIGUATION.strip()}\n\n{PLATFORM_GUIDE_POINTER}"
    assert legacy == f"{spine}\n\nskills\n\ncatalog\n\nrest"


def test_assemble_extra_system_keeps_skill_scoped_guidance_out():
    """Skill-scoped guidance is injected by its skill, never always-on."""
    from r4_contracts.agent_tool_playbook import (
        CATALOG_AGENT_GUIDANCE,
        RESEARCH_LOOP_AGENT_GUIDANCE,
    )

    blocks = (assemble_extra_system(), _turn_reminder_with_everything())
    for block in blocks:
        assert "simulate=true → fabricated placeholder findings" not in block
        assert "Lab catalog two-step flow" not in block
        assert RESEARCH_LOOP_AGENT_GUIDANCE.strip() not in block
        assert CATALOG_AGENT_GUIDANCE.strip() not in block


def test_session_assemble_extra_system_workspace_comes_first(tmp_path, monkeypatch):
    """Workspace declaration leads the extra system block — the agent must
    see its project root before workflow guidance (prevents discovery
    scans of the home directory)."""
    from r4.companion import session as session_module
    from r4.companion.session import CompanionSession

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "assemble_extra_system", lambda **kwargs: "BASE")
    fake_self = SimpleNamespace(
        skills=SimpleNamespace(discovery_catalog_prompt=lambda: ""),
        metadata={},
        _workspace_root_prompt=CompanionSession._workspace_root_prompt,
    )
    fake_self._system_block = lambda body: CompanionSession._system_block(fake_self, body)
    prompt = CompanionSession._assemble_extra_system(fake_self)
    assert prompt.startswith("## Workspace root")
    assert f"root: {os.path.realpath(tmp_path)}" in prompt
    assert prompt.endswith("BASE")


def test_session_workspace_root_prompt_matches_cwd(tmp_path, monkeypatch):
    """Bridge chdirs into --fs-root before session creation, so the declared
    workspace root is always equal to the live process cwd."""
    from r4.companion.session import CompanionSession

    monkeypatch.chdir(tmp_path)
    prompt = CompanionSession._workspace_root_prompt()
    expected_root = os.path.realpath(tmp_path)
    assert f"root: {expected_root}" in prompt
    assert "never scan" in prompt


def test_prepare_turn_payload_returns_fit_error_when_budget_exceeded():
    context_manager = MagicMock()
    context_manager.needs_compaction_for_payload.return_value = False
    context_manager.fit_payload.return_value = (
        "system",
        [{"role": "user", "content": "x"}],
        [{"name": "shell_exec"}],
        SimpleNamespace(hard_limit_exceeded=True),
    )

    payload, error = prepare_turn_payload(
        context_manager=context_manager,
        agent=MagicMock(),
        user_message="run x",
        base_extra_system="system",
        history=[{"role": "user", "content": "x"}],
        active_tools=[{"name": "shell_exec"}],
    )

    assert payload is None
    assert error == (
        "Context budget exceeded even after trimming prompt context. "
        "Please shorten the request or reduce active context."
    )
