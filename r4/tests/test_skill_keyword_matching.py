"""W3 (2026-10-05): whole-word skill keywords and platform-gated default skills."""

from __future__ import annotations

from r4.companion.skills import Skill, SkillRegistry


def _skill(*keywords: str) -> Skill:
    return Skill(name="s", description="d", system_prompt="", keywords=list(keywords))


class TestWholeWordKeywords:
    def test_substrings_no_longer_fire(self) -> None:
        assert not _skill("ara").matches("compare the two runs")
        assert not _skill("rag").matches("report the average speed")
        assert not _skill("gate").matches("investigate the failure")

    def test_whole_words_and_plurals_fire(self) -> None:
        assert _skill("file").matches("read the file")
        assert _skill("file").matches("read both files")
        assert _skill("patch").matches("apply the patches")
        assert _skill("rag").matches("query the RAG index")

    def test_punctuation_is_a_boundary(self) -> None:
        assert _skill("json").matches("write /app/result.json")
        assert _skill("knowledge graph").matches("use the knowledge graph, then answer")


class TestPlatformDefaults:
    def test_umrs_preactivated_only_with_a_live_platform(self) -> None:
        live = SkillRegistry()
        live._load_builtin_skills()
        live.apply_defaults(platform_live=True)
        assert "umrs" in live._active
        offline = SkillRegistry()
        offline._load_builtin_skills()
        offline.apply_defaults(platform_live=False)
        assert "umrs" not in offline._active


class TestWorkspaceToolsSurviveSkillFilters:
    """TB 4.0 re-run 2026-10-05: a task prompt activating only domain skills
    (vision, research, data_management) left 7 tools and no shell."""

    def test_domain_only_filter_keeps_the_agents_hands(self) -> None:
        from r4.companion.session_turns import active_tools
        from r4.companion.skills import WORKSPACE_TOOL_NAMES
        from r4.companion.tools import get_builtin_tools

        registry = SkillRegistry()
        registry._load_builtin_skills()
        registry.apply_defaults(platform_live=False)
        for name in ("data_management", "research", "vision"):  # the failing trial's set
            registry.activate(name)
        assert "shell_exec" not in registry.active_tool_names()
        offered = {
            tool["name"]
            for tool in active_tools(
                tools=get_builtin_tools(client=None),
                active_tool_names=registry.active_tool_names(),
            )
        }
        registered = {tool["name"] for tool in get_builtin_tools(client=None)}
        assert WORKSPACE_TOOL_NAMES & registered <= offered
        assert {"shell_exec", "python_exec", "file_read", "file_edit"} <= offered


class TestActivationLimit:
    """H2 (2026-10-24): the terminal profile limits keyword activation."""

    def test_only_limits_keyword_activation(self) -> None:
        registry = SkillRegistry()
        text = "fix the bug in this script, then run the workflow pipeline"
        assert set(registry.auto_activate(text, only={"code"})) == {"code"}

    def test_without_only_every_match_activates(self) -> None:
        registry = SkillRegistry()
        text = "fix the bug in this script, then run the workflow pipeline"
        assert {"code", "workflow_ops"} <= set(registry.auto_activate(text))


class TestPlatformSkills:
    """H19 (2026-10-24): which builtin bodies stay out without a platform."""

    def test_builtin_classification(self) -> None:
        from r4.companion.tool_availability import is_platform_skill

        registry = SkillRegistry()
        platform = {s.name for s in registry.list_all() if is_platform_skill(s.tool_names)}
        assert {"workflow_ops", "data_management", "model_management", "platform_ops"} <= platform
        assert not platform & {"code", "polyglot_coding", "teamup", "hpc", "research", "analysis"}
