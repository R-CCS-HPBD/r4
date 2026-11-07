"""Tests for SkillRegistry."""

from __future__ import annotations

import pytest

from r4.companion.skills import DEFAULT_ACTIVE_SKILLS, Skill, SkillRegistry


def _serving_client():
    """A client whose platform serves the decision model (full tool inventory:
    the decision tool registers only where the model is served)."""
    from unittest.mock import MagicMock

    from r4.decisions.settings import DEFAULT_DECISION_MODEL

    client = MagicMock(**{"list_gateway_models.return_value": [DEFAULT_DECISION_MODEL]})
    client.config.decision_model = DEFAULT_DECISION_MODEL
    return client


@pytest.fixture
def registry():
    return SkillRegistry()


def test_builtin_skills_loaded(registry):
    all_skills = registry.list_all()
    names = [s.name for s in all_skills]
    assert "research" in names
    assert "hpc" in names
    assert "analysis" in names
    assert "polyglot_coding" in names


def test_activate_deactivate(registry):
    assert not registry.list_active()
    registry.activate("hpc")
    assert "hpc" in [s.name for s in registry.list_active()]
    registry.deactivate("hpc")
    assert not registry.list_active()


def test_umrs_skill_is_default(registry):
    """umrs baseline skill exists, is not active on a bare registry, and
    becomes active (with its baseline tools) after apply_defaults()."""
    assert "umrs" in DEFAULT_ACTIVE_SKILLS
    skill = registry.get("umrs")
    assert skill is not None
    assert not registry.list_active()  # defaults are opt-in per session factory
    registry.apply_defaults()
    assert "umrs" in [s.name for s in registry.list_active()]
    tools = registry.active_tool_names()
    assert {"platform_health", "endpoint_list", "job_list", "list_models"} <= tools


def test_umrs_skill_wires_contract_guidance(registry):
    from r4_contracts.agent_tool_playbook import UMRS_OPS_AGENT_GUIDANCE

    registry.apply_defaults()
    prompt = registry.active_system_prompt()
    assert UMRS_OPS_AGENT_GUIDANCE in prompt
    assert "model_details" in prompt


def test_skill_tool_names_resolve(registry):
    """Every tool referenced by a builtin skill must be a registered tool."""

    from r4.companion.tools import get_builtin_tools

    real = {t["name"] for t in get_builtin_tools(client=_serving_client())}
    for skill in registry.list_all():
        if skill.source != "builtin":
            continue
        missing = [t for t in skill.tool_names if t not in real]
        assert not missing, f"skill {skill.name}: unknown tools {missing}"


def test_auto_activate(registry):
    activated = registry.auto_activate("I need to submit a slurm job on the gpu partition")
    assert "hpc" in activated


def test_research_loop_skill_auto_activates_and_owns_the_spine(registry):
    """WP-07: the canonical research_loop skill replaces the retired RC1 skill."""
    activated = registry.auto_activate("start the research loop on this project and show findings")
    assert "research_loop" in activated
    tools = registry.active_tool_names()
    assert {
        "research_loop_start",
        "research_loop_approvals",
        "knowledge_ingest",
        "workflow_list",
    } <= tools
    assert not [t for t in tools if t.startswith("project_research_")]


def test_skills_wire_contract_playbook(registry):
    from r4_contracts.agent_tool_playbook import (
        CATALOG_AGENT_GUIDANCE,
        WORKFLOW_AGENT_GUIDANCE,
        WORKFLOW_AUTHORING_AGENT_GUIDANCE,
    )

    registry.activate("workflow_ops")
    registry.activate("data_management")
    prompt = registry.active_system_prompt()
    assert "project_research" not in prompt  # RC1 spine retired (WP-07)
    assert WORKFLOW_AGENT_GUIDANCE in prompt
    assert WORKFLOW_AUTHORING_AGENT_GUIDANCE in prompt
    assert CATALOG_AGENT_GUIDANCE in prompt


def test_authoring_guidance_facts():
    """Authoring guidance matches the StepSpec + lint contracts."""
    from r4_contracts.agent_tool_playbook import (
        WORKFLOW_AUTHORING_AGENT_GUIDANCE,
        platform_guide,
    )

    # entrypoint format + gate semantics mirror StepSpec.validate()
    assert "pkg.module:Class" in WORKFLOW_AUTHORING_AGENT_GUIDANCE
    assert '"gate": true' in WORKFLOW_AUTHORING_AGENT_GUIDANCE
    # template inventory matches umrs.pipeline_sdk.templates
    for cls in (
        "llm_step:LLMStep",
        "vision_step",
        "code_step",
        "notebook_step",
        "ocr_step",
        "audio_step",
        "video_step",
    ):
        assert cls in WORKFLOW_AUTHORING_AGENT_GUIDANCE
    # reachable via platform_guide topic
    text = platform_guide("authoring")
    assert "WorkflowDraft" in text and "step_id" in text


def test_builtins_come_from_skill_md_files(registry):
    """Every builtin skill has a general-format SKILL.md on disk with
    matching name (agentskills.io spec: name == directory name)."""
    from r4.companion.skills import BUILTIN_SKILLS_DIR

    builtins = registry.builtin_skills
    assert len(builtins) >= 15
    for skill in builtins:
        skill_dir = BUILTIN_SKILLS_DIR / skill.name
        skill_file = skill_dir / "SKILL.md"
        assert skill_file.is_file(), f"{skill.name}: missing {skill_file}"
        head = skill_file.read_text().split("---", 2)
        assert len(head) == 3, f"{skill.name}: SKILL.md missing front-matter"
        assert f"name: {skill.name}" in head[1]


def test_builtin_prompts_have_no_contract_placeholders(registry):
    """Contract placeholders resolve at load time for all builtins."""
    for skill in registry.builtin_skills:
        assert "{{contract:" not in skill.system_prompt, skill.name


def test_parse_general_format_skill_md(tmp_path, registry):
    """General Agent Skills front-matter parses: allowed-tools + metadata."""
    d = tmp_path / "my-skill"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\n"
        "name: my-skill\n"
        "description: Does a thing when asked\n"
        'allowed-tools: "file_read grep_search shell_exec"\n'
        "metadata:\n"
        '  x-r4-keywords: "do the thing, thing mode"\n'
        '  x-r4-dependencies: "code"\n'
        "---\n\n"
        "Body instructions here.\n"
    )
    skill = registry._parse_skill_md(d / "SKILL.md")
    assert skill is not None
    assert skill.name == "my-skill"
    assert skill.description == "Does a thing when asked"
    assert skill.tool_names == ["file_read", "grep_search", "shell_exec"]
    assert skill.keywords == ["do the thing", "thing mode"]
    assert skill.dependencies == ["code"]
    assert skill.system_prompt == "Body instructions here."


def test_parse_legacy_format_skill_md(tmp_path, registry):
    """Legacy inline-list front-matter (tools/keywords) still parses."""
    d = tmp_path / "legacy"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\n"
        "name: legacy\n"
        "description: old style\n"
        "tools: [file_read, shell_exec]\n"
        "keywords: [legacy, old]\n"
        "dependencies: [code]\n"
        "---\n\n"
        "Legacy body.\n"
    )
    skill = registry._parse_skill_md(d / "SKILL.md")
    assert skill is not None
    assert skill.tool_names == ["file_read", "shell_exec"]
    assert skill.keywords == ["legacy", "old"]
    assert skill.dependencies == ["code"]


def test_load_user_skills_uses_general_parser(tmp_path, registry):
    """User skills load through the same general-format parser."""
    d = tmp_path / "extra-skill"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: extra-skill\ndescription: extra\n"
        'allowed-tools: "file_read"\n'
        'metadata:\n  x-r4-keywords: "extra"\n---\n\nBody.\n'
    )
    n = registry.load_user_skills(tmp_path)
    assert n == 1
    skill = registry.get("extra-skill")
    assert skill is not None and skill.tool_names == ["file_read"]
    assert skill.source == "user"


def test_every_tool_is_reachable_via_some_skill(registry):
    """Zero-orphan invariant: every registered tool is claimed by at least
    one builtin skill (or is an always-on meta tool). A tool added to
    tools.py without a skill home silently becomes unreachable — this
    test fails first."""

    from r4.companion.skills import ALWAYS_ON_TOOL_NAMES
    from r4.companion.tools import get_builtin_tools

    real = {t["name"] for t in get_builtin_tools(client=_serving_client())}
    claimed = {t for s in registry.list_all() if s.source == "builtin" for t in s.tool_names}
    meta = set(ALWAYS_ON_TOOL_NAMES) | {"WebFetch", "web_fetch", "web_search"}
    orphans = sorted(real - claimed - meta)
    assert not orphans, f"tools with no skill home: {orphans}"


def test_platform_guide_is_always_on():
    from r4.companion.skills import ALWAYS_ON_TOOL_NAMES

    assert "platform_guide" in ALWAYS_ON_TOOL_NAMES


def _builtin_tools():
    from r4.companion.tools import get_builtin_tools

    return {t["name"]: t for t in get_builtin_tools(client=None)}


def test_platform_guide_tool_wires_the_contract_description():
    from r4_contracts.agent_tool_playbook import PLATFORM_GUIDE_TOOL_DESCRIPTION

    tools = _builtin_tools()
    assert "platform_guide" in tools
    assert tools["platform_guide"]["description"] is PLATFORM_GUIDE_TOOL_DESCRIPTION
    assert not [name for name in tools if name.startswith("project_research_")]


def test_platform_guide_tool_returns_contract_sections():
    from r4_contracts.agent_tool_playbook import (
        AGENT_TOOL_PLAYBOOK,
        RESEARCH_LOOP_AGENT_GUIDANCE,
        platform_guide,
    )

    execute = _builtin_tools()["platform_guide"]["execute"]
    assert execute(topic="all") == AGENT_TOOL_PLAYBOOK
    assert execute(topic="loops") == platform_guide("loops")
    assert RESEARCH_LOOP_AGENT_GUIDANCE.strip() in execute(topic="loops")


def test_workflow_descriptions_wire_contracts():
    from unittest.mock import MagicMock

    from r4_contracts.workflows import WORKFLOW_TOOL_HELP

    from r4.companion.workflow_tools import build_workflow_tools

    client = MagicMock()
    wf = {t["name"]: t for t in build_workflow_tools(client)}
    for op, help_text in WORKFLOW_TOOL_HELP.items():
        assert wf[f"workflow_{op}"]["description"] is help_text
    assert wf["workflow_trigger"]["description"] is WORKFLOW_TOOL_HELP["run"]


def test_auto_activate_no_match(registry):
    activated = registry.auto_activate("Hello, how are you?")
    assert len(activated) == 0


def test_active_system_prompt(registry):
    registry.activate("research")
    prompt = registry.active_system_prompt()
    assert prompt  # Should have content from research skill


def test_active_system_prompt_is_stable_by_skill_name(registry):
    registry.register(
        Skill(
            name="zeta",
            description="Z",
            system_prompt="z prompt",
            tool_names=["z_tool"],
        )
    )
    registry.register(
        Skill(
            name="alpha",
            description="A",
            system_prompt="a prompt",
            tool_names=["a_tool"],
        )
    )

    registry.activate("zeta")
    registry.activate("alpha")

    assert [skill.name for skill in registry.list_active()] == ["alpha", "zeta"]
    assert registry.active_system_prompt().startswith("[Skill: alpha]")


def test_active_tool_names(registry):
    registry.activate("hpc")
    tools = registry.active_tool_names()
    assert "hpc_submit" in tools
    assert "hpc_job_status" in tools
    assert "shell_exec" in tools


def test_polyglot_coding_auto_activation(registry):
    activated = registry.auto_activate("compile this Fortran f90 code with gfortran")

    assert "polyglot_coding" in activated
    assert "code" in activated
    tools = registry.active_tool_names()
    assert "detect_code_context" in tools
    assert "detect_language_servers" in tools
    assert "start_language_servers" in tools
    assert "compile_code" in tools
    assert "test_code" in tools


def test_skill_matches():
    skill = Skill(
        name="test",
        description="Test skill",
        system_prompt="",
        keywords=["quantum", "espresso"],
    )
    assert skill.matches("Run quantum espresso calculation")
    assert not skill.matches("Make a cup of coffee")


def test_load_user_skills_empty_dir(registry, tmp_path):
    """Loading from empty dir should not fail."""
    registry.load_user_skills(tmp_path)


def test_load_user_skills_from_skill_md(registry, tmp_path):
    skill_dir = tmp_path / "my_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: my_skill\n"
        "description: A custom skill\n"
        "keywords: [custom, testing]\n"
        "tools: [shell_exec]\n"
        "---\n"
        "You are a custom research assistant.\n"
    )
    registry.load_user_skills(tmp_path)
    names = [s.name for s in registry.list_all()]
    assert "my_skill" in names

    # Verify it can be activated and used
    registry.activate("my_skill")
    prompt = registry.active_system_prompt()
    assert "custom research assistant" in prompt
