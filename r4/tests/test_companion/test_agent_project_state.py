"""D-28 follow-up pins: the project-state block in the context assembly."""

from __future__ import annotations

from types import SimpleNamespace


def _companion(monkeypatch, status_text=None):
    from r4.companion import agent as agent_mod

    monkeypatch.setattr(agent_mod, "_load_r4md_briefing", lambda: None)
    companion = object.__new__(agent_mod.ResearchCompanion)
    companion.client = SimpleNamespace()
    return companion, agent_mod


def test_project_state_block_reaches_the_turn_message(monkeypatch):
    """The bounded snapshot rides the context assembly inside its tags —
    since wave 80 in the turn's ``<turn_context>`` block, never the system
    message (it changes every turn and would break the cached prefix)."""
    companion, agent_mod = _companion(monkeypatch)
    monkeypatch.setattr(
        "r4.companion.status_report.build_project_status",
        lambda client, include_local=False: "PROJECT: mof-defects\nJOBS: 3 running",
    )
    companion._load_project_state()
    # and through the assembly:
    messages = companion._build_messages("hello", [], "")
    assert "<project_state>" not in messages[0]["content"]
    turn = messages[-1]["content"]
    assert turn.startswith("hello\n\n<turn_context>")
    assert "<project_state>" in turn
    assert "mof-defects" in turn


def test_block_is_budget_bounded(monkeypatch):
    companion, agent_mod = _companion(monkeypatch)
    monkeypatch.setattr(
        "r4.companion.status_report.build_project_status",
        lambda client, include_local=False: "x" * 100_000,
    )
    assert len(companion._load_project_state()) == agent_mod._PROJECT_STATE_BUDGET


def test_failure_is_absent_never_fabricated(monkeypatch):
    companion, agent_mod = _companion(monkeypatch)

    def boom(client, include_local=False):
        raise RuntimeError("surfaces down")

    monkeypatch.setattr("r4.companion.status_report.build_project_status", boom)
    assert companion._load_project_state() == ""
    messages = companion._build_messages("hello", [], "")
    assert "<project_state>" not in messages[0]["content"]
    assert messages[-1]["content"] == "hello"  # no state, no turn block
