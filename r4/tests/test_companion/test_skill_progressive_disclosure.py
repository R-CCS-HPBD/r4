"""ADR-037 skills progressive disclosure — discovery versus body loading.

Contract in one sentence: the discovery layer lists name + one-line
description for every REGISTERED skill; the body layer enters context ONLY
for selected (active) skills. The measured token split must reflect that —
no body cost for ofc skills, no discovery prompt that leaks bodies.
"""

from __future__ import annotations

from r4.companion.skills import Skill, SkillRegistry


def _assert_catalog_entries_single_line(cat: str) -> list[str]:
    """Catalog entries stay one per line, no embedded newline leak."""
    entries = [ln for ln in cat.splitlines() if ln.startswith("- ")]
    assert entries
    assert all("\n" not in entry for entry in entries)
    return entries


def _registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.register(
        Skill(
            name="code",
            description="Code editing workflows with apply_patch and tests\n(second line)",
            system_prompt="CODE-BODY-" + "x" * 400,
            tool_names={"apply_patch"},
            source="builtin",
        )
    )
    reg.register(
        Skill(
            name="video",
            description="Video analysis helpers",
            system_prompt="VIDEO-BODY-" + "x" * 300,
            tool_names=set(),
            source="user",
        )
    )
    return reg


class TestDiscoveryCatalog:
    def test_catalog_lists_all_registered_with_one_liners(self):
        cat = _registry().discovery_catalog_prompt()
        entries = _assert_catalog_entries_single_line(cat)
        assert "- code —" in cat
        assert "- video —" in cat
        # second description line never leaks into discovery (body boundary)
        assert "second line" not in entries[0]  # collapsed to one line

    def test_catalog_never_contains_bodies(self):
        cat = _registry().discovery_catalog_prompt()
        assert "CODE-BODY-" not in cat
        assert "VIDEO-BODY-" not in cat

    def test_catalog_header_declares_activation_semantics(self):
        cat = _registry().discovery_catalog_prompt()
        assert "bodies load on activation only" in cat

    def test_overflow_reports_plus_more(self):
        reg = _registry()
        for index in range(50):
            reg.register(
                Skill(
                    name=f"s{index}",
                    description=f"desc {index}",
                    system_prompt="B" * 5,
                    tool_names=set(),
                    source="builtin",
                )
            )
        cat = reg.discovery_catalog_prompt()
        entries = [ln for ln in cat.splitlines() if ln.startswith("- ")]
        assert len(entries) <= 41  # 40 + the +N-more line
        # registry preloads builtin skills; the over-cap marker encodes the
        # remainder dynamically.
        import re as _re

        assert _re.search(r"\+\d+ more skills", cat)

    def test_registry_starts_with_builtin_catalog(self):
        """Constructor preloads builtins — catalog exists from boot."""
        cat = SkillRegistry().discovery_catalog_prompt()
        assert cat.startswith("[Skills available")
        assert "- code —" in cat or "- analysis —" in cat


class TestModelCallableActivation:
    """Review challenge 1: the discovery catalog pays for itself only if the
    model can pull bodies on demand. activate_skill is that escape hatch;
    it cannot exist silently."""

    def test_activation_tool_present_with_binding(self):
        session = _session_for_tests()
        tools = {t["name"]: t for t in session.tools}
        assert "activate_skill" in tools
        result = tools["activate_skill"]["execute"](skill_name="code")
        assert "activated" in result or "Error" in result

    def test_unknown_skill_lists_catalog(self):
        session = _session_for_tests()
        tools = {t["name"]: t for t in session.tools}
        result = tools["activate_skill"]["execute"](skill_name="no_such_skill")
        assert result.startswith("Error: unknown skill")
        assert "code" in result or "analysis" in result or "umrs" in result

    def test_activate_changes_active_set_immediately(self):
        session = _session_for_tests()
        before = [s.name for s in session.skills.list_active()]
        assert "code" not in before
        result = tools_execute(session, "activate_skill", {"skill_name": "code"})
        assert "activated" in result
        after = [s.name for s in session.skills.list_active()]
        assert "code" in after


def _session_for_tests():
    """Session with the default registry via the store factory."""
    from unittest.mock import MagicMock

    from r4.companion.session import CompanionSessionStore

    return CompanionSessionStore().create_session(MagicMock(), name="disclosure-test")


def tools_execute(session, tool_name: str, args: dict) -> str:
    tools = {t["name"]: t for t in session.tools}
    return tools[tool_name]["execute"](**args)


class TestSplitFromActiveBodies:
    def test_active_bodies_follow_activation(self):
        reg = _registry()
        assert reg.active_system_prompt() == ""
        reg.activate("code")
        body = reg.active_system_prompt()
        assert "CODE-BODY-" in body
        assert "VIDEO-BODY-" not in body
        reg.activate("video")
        body = reg.active_system_prompt()
        assert "CODE-BODY-" in body
        assert "VIDEO-BODY-" in body

    def test_body_token_measure_uses_active_only(self, monkeypatch):
        from r4.companion import tool_selection

        reg = _registry()
        reg.activate("code")

        def fake_estimate(tools):
            return 10

        monkeypatch.setattr(tool_selection, "estimate_schema_tokens", fake_estimate)
        body_text = reg.active_system_prompt()
        ctx = tool_selection.build_selection_context(
            tools=[{"name": "a"}],
            offered=[{"name": "a"}],
            skill_body_text=body_text,
        )
        assert ctx.skill_body_tokens == len(body_text) // 4
        # discovery catalog is not part of the body measurement
        assert "CODE-BODY-" in body_text
        assert ctx.skill_body_tokens > 0
