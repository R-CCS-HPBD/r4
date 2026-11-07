"""H2 (TB 4.0 harness plan, 2026-10-24): ``R4_AGENT_PROFILE=terminal``.

An ablation for unattended terminal tasks: lean prompt, fixed tool allowlist,
keyword activation limited to the coding skills. It is never implied by
``R4_AUTONOMOUS``: with the variable unset (or empty, or ``default``) the
assembled prompt and tool list are byte-identical to the snapshot of the same
run mode, interactive (``default_prompt_offline.txt``) or unattended
(``autonomous_prompt_offline.txt``).

``R4_COMPANION_PROMPT_HYGIENE=0`` restores the pre-H15 offer (web tools shown
even in an unattended run whose web access refuses every call). The pins for
that contract used to byte-compare the whole request with a frozen
``pre_fix_prompt_offline.json`` from origin/main ``07cf674b2`` (M2's per-fix
attribution, 2026-10-24); catalog growth (27 → 60+ tools) made byte-parity
unfixable-by-design, so 2026-10-29 (wave 46, maintainer-approved) replaced it
with semantic pins of the behavior the switch restores. The frozen json is
retired with them.

Regenerate the two mode snapshots only for a deliberate default-prompt change:
``R4_UPDATE_PROMPT_SNAPSHOT=1 pytest tests/test_companion/test_agent_profile.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from r4.companion import agent_profile

from .offline_session import first_request, normalized

_SNAPSHOTS = Path(__file__).parent / "snapshots"
_SNAPSHOT = _SNAPSHOTS / "default_prompt_offline.txt"
_AUTONOMOUS_SNAPSHOT = _SNAPSHOTS / "autonomous_prompt_offline.txt"


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    for name in (
        "R4_COMPANION_PROMPT_HYGIENE",
        "R4_AGENT_PROFILE",
        "R4_WEB_ACCESS",
        "R4_AUTONOMOUS",
        "R4_TOOL_SELECTION_STRATEGY",
        "R4_SHELL_DETACH_ON_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)


def _snapshot_text(monkeypatch, tmp_path) -> str:
    system, names, _ = first_request(monkeypatch, tmp_path)
    return normalized(system) + "\n--- offered tools ---\n" + "\n".join(names) + "\n"


def _matches_snapshot(text: str, snapshot: Path) -> bool:
    if os.environ.get("R4_UPDATE_PROMPT_SNAPSHOT") == "1":
        snapshot.parent.mkdir(exist_ok=True)
        snapshot.write_text(text)
    return text == snapshot.read_text()


def _web_fetch_gate_reason(monkeypatch, tmp_path) -> str | None:
    """The availability gate's verdict on ``web_fetch`` for a fresh offline
    session (no turn sent — the verdict is environment-deterministic)."""
    import r4.companion.model_resolution as model_resolution
    from r4.companion.permissions import PermissionMode
    from r4.companion.session import CompanionSession
    from r4.companion.tool_availability import unavailable_reason

    from .offline_session import OfflineClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("R4_HARNESS_ABLATE_MEMORY", "1")
    monkeypatch.setattr(model_resolution, "ensure_model_ready", lambda *_a, **_k: None)
    session = CompanionSession(
        session_id="profile-gate",
        name="profile-gate",
        client=OfflineClient(),
        model="test-model",
        permission_mode=PermissionMode.TRUST,
        endpoint_refresh_interval_sec=86_400,
    )
    tool = next(t for t in session.tools if t.get("name") == "web_fetch")
    return unavailable_reason(tool, session.client)


class TestDefaultUnchanged:
    """Profile unset: the H19-fixed default of each run mode."""

    @pytest.mark.parametrize("profile", [None, "", "default"])
    def test_interactive_prompt_matches_the_snapshot(self, monkeypatch, tmp_path, profile):
        if profile is not None:
            monkeypatch.setenv("R4_AGENT_PROFILE", profile)
        text = _snapshot_text(monkeypatch, tmp_path)
        assert _matches_snapshot(text, _SNAPSHOT), "default prompt changed; see module docstring"

    @pytest.mark.parametrize("profile", [None, "", "default"])
    def test_unattended_prompt_matches_the_snapshot(self, monkeypatch, tmp_path, profile):
        monkeypatch.setenv("R4_AUTONOMOUS", "1")
        if profile is not None:
            monkeypatch.setenv("R4_AGENT_PROFILE", profile)
        text = _snapshot_text(monkeypatch, tmp_path)
        assert _matches_snapshot(text, _AUTONOMOUS_SNAPSHOT), "see module docstring"

    @pytest.mark.parametrize("value", ["0", "false", "off"])
    def test_hygiene_off_restores_the_web_offer(self, monkeypatch, tmp_path, value):
        """Hygiene off = the pre-H15 offer: the availability gate keeps
        ``web_fetch`` in an unattended run whose web access refuses every
        call (semantic replacement for the byte-parity pre-fix pins,
        2026-10-29; the gate-level pin survives catalog growth and budget
        trimming of the wire tool list). ``web_search`` stays out either
        way unless a search backend is configured (``R4_WEB_SEARCH_URL``,
        2026-10-30), a separate state from the H15 hiding this switch
        controls."""
        monkeypatch.setenv("R4_AUTONOMOUS", "1")
        monkeypatch.setenv("R4_COMPANION_PROMPT_HYGIENE", value)
        assert _web_fetch_gate_reason(monkeypatch, tmp_path) is None

    def test_hygiene_on_hides_the_unusable_web_offer(self, monkeypatch, tmp_path):
        """Control: hygiene on (default) removes ``web_fetch`` at the gate
        in an unattended run whose web access is off — nobody can answer
        /web there."""
        monkeypatch.setenv("R4_AUTONOMOUS", "1")
        reason = _web_fetch_gate_reason(monkeypatch, tmp_path)
        assert reason is not None and "web access off" in reason


class TestTerminalProfile:
    @pytest.fixture(autouse=True)
    def _profile(self, monkeypatch):
        monkeypatch.setenv("R4_AGENT_PROFILE", "terminal")

    def test_offered_tools_are_the_allowlist(self, monkeypatch, tmp_path):
        from r4.companion import agent as agent_module

        if not hasattr(agent_module, "_profile_allowlisted"):
            pytest.skip("the agent-side allowlist hook lands with lane L2b")
        _, names, session = first_request(monkeypatch, tmp_path)
        assert set(names) <= agent_profile.tool_allowlist()
        assert {"shell_exec", "file_edit", "tool_result_fetch"} <= set(names)
        assert session.agent._selection_ctx.extra_filtered_out["profile"]["count"] > 0

    def test_prompt_is_lean(self, monkeypatch, tmp_path):
        system, _, session = first_request(monkeypatch, tmp_path)
        for absent in (
            "[Skills available",
            "One research spine",
            "[Skill: teamup]",
            "[Skill: workflow_ops]",
            "[Skill: data_management]",
            "## Companion Skills",
        ):
            assert absent not in system
        assert "[Skill: code]" in system
        assert {s.name for s in session.skills.list_active()} <= agent_profile.skill_allowlist()

    def test_profile_drops_skills_activated_before_it(self, monkeypatch, tmp_path):
        from r4.companion.session_flow import _activate_turn_skills
        from r4.companion.skills import SkillRegistry

        registry = SkillRegistry()
        registry.activate("umrs")
        registry.activate("teamup")
        _activate_turn_skills(registry, "fix the bug in this script and run the workflow")
        assert {s.name for s in registry.list_active()} == {"code"}

    def test_no_workflow_templates(self):
        from r4.companion.harness import ResearchHarness

        harness = object.__new__(ResearchHarness)
        harness._snapshot = None
        harness.probe = SimpleNamespace(as_prompt=lambda _snapshot: "")
        harness.progress = SimpleNamespace(orientation=lambda: "")
        harness.steering = SimpleNamespace(as_prompt=lambda: "")
        harness._template_for_messages = lambda _messages: "[Workflow: model_training]"
        assert harness.feedforward([]) == ""

    def test_templates_without_the_profile(self, monkeypatch):
        monkeypatch.delenv("R4_AGENT_PROFILE")
        from r4.companion.harness import ResearchHarness

        harness = object.__new__(ResearchHarness)
        harness._snapshot = None
        harness.probe = SimpleNamespace(as_prompt=lambda _snapshot: "")
        harness.progress = SimpleNamespace(orientation=lambda: "")
        harness.steering = SimpleNamespace(as_prompt=lambda: "")
        harness._template_for_messages = lambda _messages: "[Workflow: model_training]"
        assert harness.feedforward([]) == "[Workflow: model_training]"


class TestModule:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("terminal", True), (" Terminal ", True), ("", False), ("default", False)],
    )
    def test_active_only_for_terminal(self, monkeypatch, value, expected):
        monkeypatch.setenv("R4_AGENT_PROFILE", value)
        assert agent_profile.active() is expected

    def test_unset_is_inactive(self):
        assert agent_profile.active() is False

    @pytest.mark.parametrize(("value", "expected"), [("0", False), ("off", False), ("1", True)])
    def test_hygiene_switch(self, monkeypatch, value, expected):
        monkeypatch.setenv("R4_COMPANION_PROMPT_HYGIENE", value)
        assert agent_profile.hygiene_enabled() is expected

    def test_core_prompt_names_only_offered_tools(self):
        prompt = agent_profile.core_prompt()
        assert 1500 < len(prompt) < 3000
        for absent in ("ask_user", "final_output", "compile_code", "web_fetch", "teamup"):
            assert absent not in prompt

    def test_shell_cap(self):
        assert agent_profile.SHELL_OUTPUT_CAP == 16_000
